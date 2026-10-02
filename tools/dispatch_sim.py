#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dispatch_sim.py — شبیه‌ساز موتور تخصیص «سرویسا» برای تحلیل عمیق و آزمون حساسیت.

هدف: پاسخ کمی به این پرسش‌ها پیش از کدنویسی عملیاتی:
  ۱) موتور امتیازمحور چقدر بهتر از «صف ساده» و «نزدیک‌ترین متخصص» عمل می‌کند؟
  ۲) هر یک از ۹ وزن چقدر بر نرخ تخصیص و زمان تا تخصیص اثر دارد؟ (تحلیل حساسیت)
  ۳) اندازه موج (۳ در برابر ۵ در برابر ۱) و شعاع فزاینده چه اثری دارند؟
  ۴) ضریب تقاضا (Surge) چه تعادلی بین پذیرش، درآمد متخصص و رضایت مشتری می‌سازد؟
  ۵) مؤلفه‌های «عدالت توزیع» و «نگهداشت» تا چه حد از گرسنگی متخصصان (starvation) جلوگیری می‌کنند؟

سازگاری با کد اجرایی: فرمول‌های امتیازدهی دقیقاً همان فرمول‌های `mvp/app/services.py` هستند و
تابع `verify_against_service()` صحت این هم‌ارزی را روی یک نمونهٔ کنترل‌شده با خود سرویس بررسی می‌کند.

اجرا:
    python3 tools/dispatch_sim.py                 # همه سناریوها + خروجی JSON/CSV/نمودار
    python3 tools/dispatch_sim.py --quick         # اجرای سریع (نمونه کوچک)
خروجی‌ها: analysis/dispatch_sim_results.json · analysis/csv/*.csv · analysis/figs/*.png
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import random
import statistics
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "analysis"
(OUT / "csv").mkdir(parents=True, exist_ok=True)
(OUT / "figs").mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------- اوزان سند
DEFAULT_WEIGHTS = {
    "distance": 0.26, "quality": 0.18, "acceptance": 0.14, "reliability": 0.12,
    "price": 0.10, "equipment": 0.08, "history": 0.06, "fairness": 0.04, "retention": 0.02,
}
WEIGHT_FA = {"distance": "نزدیکی", "quality": "کیفیت", "acceptance": "نرخ قبولی",
             "reliability": "پایبندی", "price": "قیمت", "equipment": "تجهیزات",
             "history": "سابقه با مشتری", "fairness": "عدالت توزیع", "retention": "نگهداشت متخصص"}
STRATEGY_FA = {"smart": "هوشمند (۹مؤلفه)", "distance": "نزدیک‌ترین متخصص",
               "queue": "صف سنتی (چرخشی)"}


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def haversine_km(lat1, lng1, lat2, lng2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def sigmoid(x):
    return 1.0 / (1.0 + math.exp(-x))


# ------------------------------------------------------------------ داده‌های آزمون
@dataclass
class Provider:
    pid: int
    name: str
    lat: float
    lng: float
    skills: set
    equipment: set
    rating: float
    rating_count: int
    acceptance_rate: float
    on_time_rate: float
    cancel_rate: float
    completion_count: int
    capacity: int
    archetype: str
    online: bool = True
    served_today: int = 0
    jobs_assigned: int = 0
    last_offer_min: float = -1e9        # دقیقه شبیه‌سازی‌شده آخرین پیشنهاد
    consecutive_rejections: int = 0
    earnings: int = 0
    offers_received: int = 0
    accepts: int = 0

    @property
    def equipment_set(self):
        return self.equipment

    @property
    def skill_set(self):
        return self.skills


@dataclass
class SimOrder:
    oid: int
    lat: float
    lng: float
    skills: set
    equipment: set
    urgency: str
    price: int
    customer_id: int
    created_min: float
    surge: float = 1.0
    assigned_provider: int | None = None
    tta_min: float | None = None
    waves: int = 0
    radius_m: int = 4000
    offers_sent: int = 0
    expired_offers: int = 0
    first_wave_best_score: float | None = None
    assigned_score: float | None = None
    assigned_quality: float | None = None
    assigned_distance: float | None = None
    accepted_rank: int | None = None
    rejected_ids: list = field(default_factory=list)
    ignored_ids: list = field(default_factory=list)

    @property
    def history_for(self):        # سازگاری با نام‌گذاری سرویس
        return self.customer_id


SKILL_POOL = ["plumbing.basic", "cabinet.water_damage", "electrical.basic", "hvac.service",
              "locksmith.open", "painting.wall", "carpentry.basic", "cleaning.deep"]
EQUIP_POOL = ["pipe_wrench", "leak_detector", "multimeter", "vacuum_pump", "lockpick_set",
              "sprayer", "saw_set", "vacuum_industrial"]
# کهن‌الگوهای عرضه: (پایه‌پذیری، وزن انگیزه مالی، وزن فاصله، وزن فراغت، حساسیت به سطح دستمزد)
ARCHETYPES = {
    "EAGER":     (0.60, 1.2, 0.9, 0.6, 0.4),
    "NORMAL":    (-0.90, 1.6, 1.1, 1.1, 0.9),
    "SELECTIVE": (-2.00, 2.4, 0.6, 1.5, 1.8),
}
BASE_TICKET = 1_650_000        # میانگین سبد سفارش (ریال) برای سنجش «سطح دستمزد»

URGENCY_TTL = {"EMERGENCY": 20, "SAME_DAY": 90, "SCHEDULED": 90, "FLEXIBLE": 120}
URGENCY_WEIGHT = {"EMERGENCY": 0.30, "SAME_DAY": 0.42, "SCHEDULED": 0.22, "FLEXIBLE": 0.06}


def make_zone(center_lat, center_lng, spread_km, rng):
    dlat = rng.gauss(0, 1) * spread_km / 111.0
    dlng = rng.gauss(0, 1) * spread_km / (111.0 * math.cos(math.radians(center_lat)))
    return center_lat + dlat, center_lng + dlng


def build_providers(n: int, rng: random.Random, online_ratio: float = 0.85):
    zones = [(35.7448, 51.4261), (35.7010, 51.3900), (35.7800, 51.4500)]   # سعادت‌آباد، شهرک غرب، نیاوران
    provs: list[Provider] = []
    for i in range(n):
        z = rng.choice(zones)
        lat, lng = make_zone(*z, 3.2, rng)
        arch = rng.choices(list(ARCHETYPES), weights=[0.28, 0.54, 0.18])[0]
        n_skills = rng.choices([1, 2, 3], weights=[0.42, 0.42, 0.16])[0]
        skills = set(rng.sample(SKILL_POOL, n_skills))
        equip = set(rng.sample(EQUIP_POOL, rng.choice([1, 2, 3])))
        rating = clamp(rng.gauss(4.45, 0.35), 3.2, 5.0)
        provs.append(Provider(
            pid=i, name=f"P-{i:03d}", lat=lat, lng=lng, skills=skills, equipment=equip,
            rating=round(rating, 2), rating_count=rng.randint(8, 400),
            acceptance_rate=clamp(rng.gauss(0.72, 0.13), 0.35, 0.98),
            on_time_rate=clamp(rng.gauss(0.90, 0.055), 0.68, 1.0),
            cancel_rate=clamp(rng.gauss(0.045, 0.03), 0.0, 0.25),
            completion_count=rng.randint(5, 900), capacity=rng.choice([7, 9, 11, 13]),
            archetype=arch, online=rng.random() < online_ratio))
    return provs


def build_orders(n: int, rng: random.Random, day_profile: bool = True):
    """ساخت تقاضا با پروفایل شبانه‌روزی تهران (پیک صبح ۹-۱۱ و عصر ۱۸-۲۱)."""
    zones = [(35.7448, 51.4261), (35.7010, 51.3900), (35.7800, 51.4500)]
    base_price = {"plumbing.basic": 1_650_000, "cabinet.water_damage": 2_400_000,
                  "electrical.basic": 1_450_000, "hvac.service": 1_900_000,
                  "locksmith.open": 1_100_000, "painting.wall": 3_200_000,
                  "carpentry.basic": 2_800_000, "cleaning.deep": 2_100_000}
    orders: list[SimOrder] = []
    for i in range(n):
        if day_profile:
            hour = rng.choices(list(range(7, 23)),
                               weights=[3, 6, 9, 9, 6, 5, 5, 6, 8, 10, 11, 9, 7, 5, 4, 3])[0]
        else:
            hour = rng.randint(7, 22)
        minute = rng.random() * 60
        t = (hour - 7) * 60 + minute
        skill = rng.choices(SKILL_POOL, weights=[26, 10, 18, 12, 8, 8, 8, 10])[0]
        urgency = rng.choices(list(URGENCY_WEIGHT), weights=list(URGENCY_WEIGHT.values()))[0]
        z = rng.choice(zones)
        lat, lng = make_zone(*z, 3.0, rng)
        price = int(base_price[skill] * rng.uniform(0.85, 1.3))
        orders.append(SimOrder(oid=i, lat=lat, lng=lng, skills={skill},
                               equipment={rng.choice(["pipe_wrench", "multimeter", "vacuum_pump"])},
                               urgency=urgency, price=price, customer_id=rng.randint(0, n // 6),
                               created_min=t))
    orders.sort(key=lambda o: o.created_min)
    return orders


# ------------------------------------------------------------------ منطق سرویس
class SimEngine:
    """بازپیاده‌سازی دقیق فرمول‌های DispatchService روی مدل درون‌حافظه (سریع و تکرارپذیر)."""

    WAVE_SIZE = 3
    MAX_WAVES = 3
    OFFER_TTL = URGENCY_TTL
    RADIUS_STEP = 1.5
    INCENTIVE_RATE = 0.03

    def __init__(self, providers, weights=None, rng=None, strategy="smart",
                 fairness_on=True, wave_size=None, radius_m=4000, escalating=True,
                 max_waves=None, surge=1.0, dynamic_surge=False):
        self.providers = providers
        self.weights = dict(weights or DEFAULT_WEIGHTS)
        self.rng = rng or random.Random(7)
        self.strategy = strategy
        self.fairness_on = fairness_on
        if wave_size:
            self.WAVE_SIZE = wave_size
        self.radius0 = radius_m
        self.escalating = escalating
        if max_waves:
            self.MAX_WAVES = max_waves
        self.surge = surge
        self.dynamic_surge = dynamic_surge
        self.stats = {"orders": 0, "fulfilled": 0, "offers": 0, "waves": 0, "escalated": 0,
                      "radius_sum": 0.0, "tta": [], "quality": [], "distance": [],
                      "revenue": 0, "commission": 0, "ignored": 0, "rejected": 0,
                      "incentive_paid": 0, "accepted_rank": [], "rework": []}

    # -------------------------------------------------- مؤلفه‌های امتیاز (نُه‌گانه)
    @staticmethod
    def _rating(p):
        return getattr(p, "rating", None) or getattr(p, "rating_avg", 0.0)

    @staticmethod
    def _capacity(p):
        return getattr(p, "capacity", None) or getattr(p, "capacity_per_day", 1) or 1

    @staticmethod
    def _idle_hours(p, now_min):
        lo = getattr(p, "last_offer_min", None)
        if lo is not None:
            return (now_min - lo) / 60.0
        stamp = getattr(p, "last_offer_at", None)         # مدل ORM سرویس
        if not stamp:
            return 1e9
        try:
            from app.core import aware
            delta = datetime.now(stamp.tzinfo) - aware(stamp)
            return delta.total_seconds() / 3600.0
        except Exception:
            return 1e9

    def components(self, o: SimOrder, p, dist_km: float, radius_m: int):
        r_km = max(radius_m / 1000.0, 0.5)
        rating = self._rating(p)
        s_distance = clamp(1.0 - dist_km / r_km, 0.0, 1.0)
        s_quality = clamp((rating / 5.0 if p.rating_count else 0.78) * 0.6
                          + clamp(getattr(p, "on_time_rate", 0.9), 0, 1) * 0.4, 0, 1)
        s_acceptance = clamp(p.acceptance_rate, 0, 1)
        s_reliability = clamp((1 - p.cancel_rate) * 0.5 + min(p.completion_count, 100) / 100 * 0.5, 0, 1)
        need_eq = set(o.equipment)
        equip = getattr(p, "equipment", None)
        equip_set = set(equip.split(",")) if isinstance(equip, str) else set(equip or [])
        s_equipment = 1.0 if not need_eq else len(need_eq & equip_set) / len(need_eq)
        s_price = 0.6                                   # مزایده فعال نیست ⇒ مقدار خنثی (مانند MVP)
        pid = getattr(p, "pid", None) or getattr(p, "user_id", None)
        prior = getattr(o, "prior_with_provider", {}).get(pid, 0)
        if hasattr(o, "customer_id") and getattr(o, "history_count", None):
            prior = o.history_count
        s_history = clamp(min(prior, 3) / 3, 0, 1)
        served = getattr(p, "served_today", 0)
        s_fairness = (clamp(1.0 - served / self._capacity(p), 0, 1) if self.fairness_on else 0.5)
        idle_h = self._idle_hours(p, o.created_min)
        s_retention = clamp(idle_h / 24.0, 0.0, 1.0) if self.fairness_on else 0.5
        penalty = 0.02 * min(getattr(p, "consecutive_rejections", 0), 3)
        if (getattr(p, "pid", None) or getattr(p, "user_id", None)) in o.ignored_ids:
            penalty += 0.05
        comp = {"distance": s_distance, "quality": s_quality, "acceptance": s_acceptance,
                "reliability": s_reliability, "equipment": s_equipment, "price": s_price,
                "history": s_history, "fairness": s_fairness, "retention": s_retention}
        total = clamp(sum(self.weights.get(k, 0.0) * v for k, v in comp.items()) - penalty, 0.0, 1.0)
        return total, comp, penalty

    # ------------------------------------------------------------ فیلترهای سخت
    def eligible(self, o: SimOrder, radius_m: int):
        out = []
        for p in self.providers:
            if not p.online or p.served_today >= p.capacity:
                continue
            if not o.skills.issubset(p.skills):
                continue
            if p.pid in o.rejected_ids:
                continue
            d = haversine_km(o.lat, o.lng, p.lat, p.lng)
            if d > radius_m / 1000.0:
                continue
            out.append((p, d))
        return out

    # ------------------------------------------------------------ مدل پذیرش
    def acceptance_probability(self, p: Provider, score: float, dist_km: float, radius_m: int,
                               incentive: int, price: int, wave: int = 1):
        """مدل پاسخ متخصص (مستند در سند تحلیل):
        z = a + 2.6·(score−0.65) + 1.8·(نرخ قبولی−0.72) + c·(انگیزه/مبلغ)·4
            + d·(۱−فاصله/شعاع) + e·فراغت + f·max(0, مبلغ/سبد پایه − ۱)
        """
        a, c, d, e, f = ARCHETYPES[p.archetype]
        incentive_ratio = incentive / max(price, 1)
        slack = 1.0 - p.served_today / max(p.capacity, 1)
        rel_pay = price / BASE_TICKET
        z = (a + 2.6 * (score - 0.65) + 1.8 * (p.acceptance_rate - 0.72)
             + c * incentive_ratio * 4.0 + d * (1.0 - dist_km / max(radius_m / 1000.0, 0.5))
             + e * slack + f * max(0.0, rel_pay - 1.0))
        return sigmoid(z)

    # -------------------------------------------------------------- اجرای موج‌ها
    def run_order(self, o: SimOrder):
        self.stats["orders"] += 1
        radius = self.radius0
        wave = 1
        incentive = 0
        while wave <= self.MAX_WAVES:
            cands = self.eligible(o, radius)
            scored = []
            for p, d in cands:
                total, comp, _ = self.components(o, p, d, radius)
                scored.append((total, p, d, comp))
            if self.strategy == "smart":
                scored.sort(key=lambda x: x[0], reverse=True)
            elif self.strategy == "distance":
                scored.sort(key=lambda x: x[2])
            else:                                       # صف چرخشی سنتی: قدیمی‌ترین پیشنهاد
                scored.sort(key=lambda x: x[1].last_offer_min)
            if not scored:
                wave += 1
                radius = int(radius * self.RADIUS_STEP) if self.escalating else radius
                continue

            o.waves = wave
            o.radius_m = radius
            if wave == 1:
                o.first_wave_best_score = scored[0][0]
            picks = scored[:self.WAVE_SIZE]
            incentive = int(o.price * self.INCENTIVE_RATE * (wave - 1))
            # ترتیب پاسخ‌دهی احتمالی: پیشنهادها هم‌زمان ارسال می‌شوند، پاسخ‌ها با نرخ پذیرش مدل‌شده
            responders = []
            for rank, (total, p, d, comp) in enumerate(picks):
                p.offers_received += 1
                o.offers_sent += 1
                self.stats["offers"] += 1
                prob = self.acceptance_probability(p, total, d, radius, incentive, o.price, wave)
                if self.rng.random() < prob:
                    responders.append((rank, total, p, d))
            if responders:
                # پاسخ‌ها با تأخیر تصادفی می‌رسند؛ نخستین پذیرش برنده است (پذیرش اتمی)
                responders.sort(key=lambda r: self.rng.expovariate(1 / 25.0))
                rank, total, p, d = responders[0]
                o.assigned_provider = p.pid
                o.assigned_score = total
                o.assigned_quality = p.rating
                o.assigned_distance = d
                o.accepted_rank = rank + 1
                u = getattr(o, "urgency", "SAME_DAY")
                base = 0.35 if u == "EMERGENCY" else (0.75 if u == "SAME_DAY" else 2.2)
                o.tta_min = (base + self.rng.uniform(0.0, 0.45) + 0.18 * rank
                             + 1.25 * (wave - 1))       # هر موج تشدید ≈ ۱.۲۵ دقیقه تأخیر
                p.served_today += 1
                p.jobs_assigned += 1
                p.accepts += 1
                p.consecutive_rejections = 0
                p.earnings += int(o.price * 0.85)
                self.stats["fulfilled"] += 1
                self.stats["tta"].append(o.tta_min)
                self.stats["quality"].append(p.rating)
                self.stats["distance"].append(d)
                self.stats["radius_sum"] += radius
                self.stats["waves"] += wave
                self.stats["revenue"] += o.price
                self.stats["commission"] += int(o.price * 0.15)
                self.stats["incentive_paid"] += incentive
                self.stats["accepted_rank"].append(rank + 1)
                # ریسک دوباره‌کاری: متخصص کم‌امتیازتر ⇒ احتمال بازکار بیشتر (پراکسی اقتصادی کیفیت)
                self.stats["rework"].append(clamp(0.085 * (5.0 - p.rating) / 1.5, 0.0, 0.30))
                return True
            # هیچ‌کس نپذیرفت: موج بعدی
            for rank, (total, p, d, comp) in enumerate(picks):
                if self.rng.random() < 0.65:
                    p.consecutive_rejections += 1
                    o.rejected_ids.append(p.pid)
                    self.stats["rejected"] += 1
                else:
                    o.ignored_ids.append(p.pid)
                    self.stats["ignored"] += 1
                p.last_offer_min = o.created_min
            wave += 1
            radius = int(radius * self.RADIUS_STEP) if self.escalating else radius
        self.stats["escalated"] += 1
        return False


# ------------------------------------------------------- اعتبارسنجی هم‌ارزی با سرویس
def verify_against_service() -> dict:
    """بررسی می‌کند فرمول‌های شبیه‌ساز، همان خروجی سرویس اجرایی را می‌دهد."""
    sys.path.insert(0, str(ROOT / "mvp"))
    os.environ.setdefault("CP_DATABASE_URL", f"sqlite:///{ROOT / 'mvp' / 'var' / 'sim_check.sqlite'}")
    from app.core import Base, SessionLocal, engine                      # noqa
    from app.models import (Order, ProviderProfile, ServiceCategory,     # noqa
                            ServiceItem, User)
    from app.services import DispatchService                             # noqa

    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        u = User(phone="+989120009991", full_name="شبیه‌ساز", roles="PROVIDER")
        db.add(u)
        db.flush()
        cat = ServiceCategory(slug="sim-cat", title_fa="دسته شبیه‌سازی", warranty_days=30)
        db.add(cat)
        db.flush()
        item = ServiceItem(category_id=cat.id, slug="sim.service", title_fa="خدمت شبیه‌سازی",
                           pricing_model="BAND", base_price=1_650_000, min_price=1_200_000,
                           max_price=2_400_000, required_skills="plumbing.basic",
                           required_equipment="req:pipe_wrench,multimeter", duration_minutes=60)
        db.add(item)
        db.flush()
        p = ProviderProfile(user_id=u.id, display_name="P-SIM", skills="plumbing.basic,electrical.basic",
                            equipment="pipe_wrench,multimeter", lat=35.7460, lng=51.4270, tier="PRO",
                            status="ACTIVE", is_online=True, verified_level=3, rating_avg=4.62,
                            rating_count=180, acceptance_rate=0.81, on_time_rate=0.93,
                            cancel_rate=0.03, completion_count=420, capacity_per_day=4)
        db.add(p)
        order = Order(code="SIM-1", customer_id=u.id, service_item_id=item.id, status="DISPATCHING",
                      urgency="SAME_DAY", skills_required="plumbing.basic",
                      equipment_required="req:pipe_wrench,multimeter", lat=35.7448, lng=51.4261)
        db.add(order)
        db.flush()
        weights = dict(DEFAULT_WEIGHTS)
        svc = DispatchService(db)
        for dist in (0.4, 1.6, 3.4):
            got = svc.score(order, p, dist, 4000, weights, 1)
            comp = got["components"]
            sim_order = SimOrder(oid=1, lat=35.7448, lng=51.4261, skills={"plumbing.basic"},
                                 equipment={"pipe_wrench", "multimeter"}, urgency="SAME_DAY",
                                 price=1_650_000, customer_id=u.id, created_min=0)
            sim_order.ignored_ids = []
            total, mine_comp, penalty = SimEngine([p], weights=weights, fairness_on=True).components(
                sim_order, p, dist, 4000)
            same = all(abs(comp[k] - mine_comp[k]) < 1e-9 for k in comp)
            assert same, f"ناهم‌خوانی مؤلفه‌ها: {comp} ≠ {mine_comp}"
            assert abs(got["total"] - total) < 1e-4, (
                f"ناهم‌خوانی امتیاز کل در فاصله {dist}: سرویس {got['total']} ≠ شبیه‌ساز {total:.6f}")
        return {"status": "OK", "checked_distances": 3,
                "note_fa": "فرمول‌های شبیه‌ساز با DispatchService سرویس اجرایی مطابقت کامل دارد."}
    finally:
        db.close()


# ------------------------------------------------------------------- شاخص‌ها
def gini(values):
    xs = sorted(values)
    n = len(xs)
    if n == 0 or sum(xs) == 0:
        return 0.0
    cum = 0.0
    for i, x in enumerate(xs, 1):
        cum += i * x
    return (2 * cum) / (n * sum(xs)) - (n + 1) / n


def summarize(engine: SimEngine, orders: list[SimOrder], providers: list[Provider]) -> dict:
    st = engine.stats
    tta = sorted(st["tta"])
    p50 = statistics.median(tta) if tta else None
    p95 = tta[min(len(tta) - 1, int(len(tta) * 0.95))] if tta else None
    jobs = [p.jobs_assigned for p in providers if p.online]
    active = [j for j in jobs if j > 0]
    top_n = max(1, int(len(jobs) * 0.1))
    top_share = sum(sorted(jobs, reverse=True)[:top_n]) / max(sum(jobs), 1)
    return {
        "orders": st["orders"],
        "fulfilled": st["fulfilled"],
        "fulfillment_rate": round(st["fulfilled"] / max(st["orders"], 1), 4),
        "escalated_rate": round(st["escalated"] / max(st["orders"], 1), 4),
        "tta_p50_min": round(p50, 3) if p50 else None,
        "tta_p95_min": round(p95, 3) if p95 else None,
        "tta_mean_min": round(statistics.fmean(tta), 3) if tta else None,
        "offers_per_order": round(st["offers"] / max(st["orders"], 1), 3),
        "mean_waves": round(st["waves"] / max(st["fulfilled"], 1), 2),
        "mean_radius_m": round(st["radius_sum"] / max(st["fulfilled"], 1)),
        "mean_rating_assigned": round(statistics.fmean(st["quality"]), 3) if st["quality"] else None,
        "mean_distance_km": round(statistics.fmean(st["distance"]), 2) if st["distance"] else None,
        "offers_ignored": st["ignored"],
        "offers_rejected": st["rejected"],
        "gini_jobs": round(gini(jobs), 4),
        "top10pct_share": round(top_share, 4),
        "starved_share": round(sum(1 for j in jobs if j == 0) / max(len(jobs), 1), 4),
        "revenue_irr": st["revenue"],
        "commission_irr": st["commission"],
        "accept_rate_offers": round(st["fulfilled"] / max(st["offers"], 1), 4),
        "mean_rank_accepted": round(statistics.fmean(st["accepted_rank"]), 2) if st["accepted_rank"] else None,
        "first_offer_hit_rate": round(sum(1 for r in st["accepted_rank"] if r == 1)
                                      / max(len(st["accepted_rank"]), 1), 4),
        "rework_risk": round(statistics.fmean(st["rework"]), 4) if st["rework"] else None,
        "incentive_spend_irr": st["incentive_paid"],
    }


def run_config(name_fa, providers, orders, **kwargs):
    engine = SimEngine(providers, **kwargs)
    for o in orders:
        engine.run_order(o)
    res = summarize(engine, orders, providers)
    res["config_fa"] = name_fa
    return res, engine


# ------------------------------------------------------------------ سناریوها
def scenario_strategies(rng_seed, n_prov, n_orders):
    """مقایسه راهبردها در دو رژیم عرضه: عادی (۸۵٪ آنلاین) و کمبود عرضه (۴۵٪ آنلاین)."""
    rows = []
    for regime, ratio, label in (("normal", 0.85, "عرضه عادی"), ("scarce", 0.45, "کمبود عرضه")):
        for strat in ("smart", "distance", "queue"):
            rng = random.Random(rng_seed)
            provs = build_providers(n_prov, rng, online_ratio=ratio)
            orders = build_orders(n_orders, rng)
            res, _ = run_config(f"{STRATEGY_FA[strat]} — {label}", provs, orders,
                                rng=random.Random(rng_seed + 1), strategy=strat)
            res.update({"strategy": strat, "regime": regime, "regime_fa": label})
            rows.append(res)
    return rows


def scenario_sensitivity(rng_seed, n_prov, n_orders, deltas=(0.5, -0.5)):
    rows = []
    base_rng = random.Random(rng_seed)
    provs0 = build_providers(n_prov, base_rng, online_ratio=0.45)   # رژیم کمبود عرضه
    orders0 = build_orders(n_orders, base_rng)
    base, _ = run_config("وزن‌های سند (پایه)", provs0, orders0, rng=random.Random(rng_seed + 5))
    rows.append({**base, "changed_weight": "-", "delta": 0})
    for key in DEFAULT_WEIGHTS:
        for delta in deltas:
            w = dict(DEFAULT_WEIGHTS)
            w[key] = clamp(w[key] * (1 + delta), 0.005, 0.99)
            rest = sum(v for k, v in w.items() if k != key)
            scale = (1 - w[key]) / rest if rest else 1.0
            for k in w:
                if k != key:
                    w[k] *= scale
            rng = random.Random(rng_seed)
            provs = build_providers(n_prov, rng, online_ratio=0.45)
            orders = build_orders(n_orders, rng)
            res, _ = run_config(f"{WEIGHT_FA[key]} {(1+delta)*100:.0f}٪", provs, orders,
                                rng=random.Random(rng_seed + 5), weights=w)
            res.update({"changed_weight": key, "delta": delta,
                        "fulfillment_delta": round(res["fulfillment_rate"] - base["fulfillment_rate"], 4),
                        "tta_p95_delta": round((res["tta_p95_min"] or 0) - (base["tta_p95_min"] or 0), 3),
                        "quality_delta": round((res["mean_rating_assigned"] or 0)
                                               - (base["mean_rating_assigned"] or 0), 3),
                        "base_fulfillment": base["fulfillment_rate"],
                        "base_tta_p95": base["tta_p95_min"],
                        "base_quality": base["mean_rating_assigned"]})
            rows.append(res)
    return base, rows


def scenario_waves(rng_seed, n_prov, n_orders):
    rows = []
    configs = [("موج ۱ پیشنهاد", {"wave_size": 1}), ("موج ۳ پیشنهاد (پیشنهاد سند)", {"wave_size": 3}),
               ("موج ۵ پیشنهاد", {"wave_size": 5}), ("بدون تشدید (یک موج)", {"max_waves": 1}),
               ("شعاع ثابت ۴ کیلومتر", {"escalating": False}),
               ("شعاع ۹ کیلومتر ثابت", {"radius_m": 9000, "escalating": False})]
    for label, kw in configs:
        rng = random.Random(rng_seed)
        provs = build_providers(n_prov, rng, online_ratio=0.45)     # کمبود عرضه
        orders = build_orders(n_orders, rng)
        res, _ = run_config(label, provs, orders, rng=random.Random(rng_seed + 3), **kw)
        res["variant_fa"] = label
        rows.append(res)
    return rows


def scenario_surge(rng_seed, n_prov, n_orders):
    """کمبود عرضه: نیمی از متخصصان آفلاین؛ اثر ضریب تقاضا بر پذیرش، درآمد و رضایت."""
    rows = []
    for surge in (0.95, 1.00, 1.10, 1.20, 1.35, 1.50):
        rng = random.Random(rng_seed)
        provs = build_providers(n_prov, rng, online_ratio=0.45)
        orders = build_orders(n_orders, rng)
        for o in orders:
            o.surge = surge
            # ضریب تقاضا از سمت مبلغ سفارش و در نتیجه انگیزه مالی اثر می‌گذارد (طرح سند)
            o.price = int(o.price * surge)
        res, engine = run_config(f"ضریب تقاضا ×{surge:.2f}", provs, orders,
                                 rng=random.Random(rng_seed + 9), surge=surge)
        earnings = statistics.fmean([p.earnings for p in provs if p.online]) if provs else 0
        res.update({"surge": surge, "mean_provider_earnings_irr": int(earnings),
                    "demand_response": round(res["fulfillment_rate"], 4)})
        rows.append(res)
    return rows


def scenario_fairness(rng_seed, n_prov, n_orders):
    """رژیم تنگ: تقاضا نزدیک ظرفیت عرضه؛ اثر مؤلفه‌های عدالت/نگهداشت و وزن آن‌ها."""
    rows = []
    variants = [("بدون عدالت و نگهداشت", False, None),
                ("وزن سند: عدالت ۰.۰۴ + نگهداشت ۰.۰۲", True, None),
                ("عدالت تقویت‌شده ۰.۱۰ + نگهداشت ۰.۰۴", True, 0.10),
                ("عدالت تقویت‌شده ۰.۱۶ + نگهداشت ۰.۰۶", True, 0.16)]
    for label, on, fairness_w in variants:
        rng = random.Random(rng_seed)
        provs = build_providers(n_prov, rng)
        orders = build_orders(n_orders, rng)
        weights = None
        if fairness_w is not None:
            weights = dict(DEFAULT_WEIGHTS)
            weights["fairness"], weights["retention"] = fairness_w, round(fairness_w * 0.4, 3)
            rest = sum(v for k, v in weights.items() if k not in ("fairness", "retention"))
            scale = (1 - weights["fairness"] - weights["retention"]) / rest
            for k in weights:
                if k not in ("fairness", "retention"):
                    weights[k] *= scale
        res, _ = run_config(label, provs, orders, rng=random.Random(rng_seed + 11),
                            fairness_on=on, weights=weights)
        res["fairness_on"] = on
        jobs = [p.jobs_assigned for p in provs if p.online]
        res["jobs_histogram"] = [sum(1 for j in jobs if j == k) for k in range(0, 7)]
        res["provider_jobs"] = jobs
        rows.append(res)
    return rows


def main():
    ap = argparse.ArgumentParser(description="شبیه‌ساز موتور تخصیص سرویسا")
    ap.add_argument("--quick", action="store_true", help="اجرای سریع با نمونه کوچک")
    ap.add_argument("--seed", type=int, default=20261002)
    args = ap.parse_args()

    n_prov = 90 if args.quick else 260
    n_orders = 700 if args.quick else 1900
    print(f"▸ اعتبارسنجی هم‌ارزی با سرویس اجرایی…")
    verification = verify_against_service()
    print(f"  {verification['note_fa']}")

    print("▸ سناریو ۱: مقایسه راهبردها…")
    strategies = scenario_strategies(args.seed, n_prov, n_orders)
    print("▸ سناریو ۲: تحلیل حساسیت وزن‌ها…")
    base, sensitivity = scenario_sensitivity(args.seed, n_prov, n_orders)
    print("▸ سناریو ۳: اندازه موج و شعاع…")
    waves = scenario_waves(args.seed, n_prov, n_orders)
    print("▸ سناریو ۴: ضریب تقاضا در شرایط کمبود عرضه…")
    surge = scenario_surge(args.seed, n_prov, n_orders)
    print("▸ سناریو ۵: عدالت توزیع…")
    fairness = scenario_fairness(args.seed, n_prov, n_orders)

    payload = {
        "meta": {"generated_at": datetime.now().isoformat(timespec="seconds"),
                 "seed": args.seed, "providers": n_prov, "orders": n_orders,
                 "weights": DEFAULT_WEIGHTS, "verification": verification,
                 "urgency_ttl": URGENCY_TTL,
                 "acceptance_model_fa": "σ(a + b·(score−0.65)·2 + c·(incentive/price)·4 + "
                                        "d·(1−d/radius) + e·(1−load)) با سه کهن‌الگوی متخصص "
                                        "(پرشور/معمولی/گزینشی)"},
        "strategies": strategies, "sensitivity": sensitivity, "waves": waves,
        "surge": surge, "fairness": fairness,
    }
    (OUT / "dispatch_sim_results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    _csv(OUT / "csv" / "strategies.csv", strategies,
         ["config_fa", "fulfillment_rate", "tta_p50_min", "tta_p95_min", "offers_per_order",
          "mean_waves", "mean_rating_assigned", "mean_distance_km", "gini_jobs", "top10pct_share",
          "starved_share", "escalated_rate", "commission_irr", "mean_rank_accepted",
          "first_offer_hit_rate", "rework_risk", "incentive_spend_irr"])
    _csv(OUT / "csv" / "sensitivity.csv", sensitivity,
         ["config_fa", "changed_weight", "delta", "fulfillment_rate", "fulfillment_delta",
          "tta_p95_min", "tta_p95_delta", "mean_rating_assigned", "quality_delta",
          "gini_jobs", "starved_share"])
    _csv(OUT / "csv" / "waves_radius.csv", waves,
         ["config_fa", "fulfillment_rate", "tta_p50_min", "tta_p95_min", "offers_per_order",
          "mean_waves", "mean_radius_m", "escalated_rate", "commission_irr"])

    # مرتب‌سازی ورودی‌ها برای خوانایی خروجی متنی
    pass
    _csv(OUT / "csv" / "surge.csv", surge,
         ["config_fa", "surge", "fulfillment_rate", "accept_rate_offers", "tta_p95_min",
          "mean_provider_earnings_irr", "commission_irr", "starved_share"])
    _csv(OUT / "csv" / "fairness.csv", fairness,
         ["config_fa", "fulfillment_rate", "gini_jobs", "top10pct_share", "starved_share",
          "tta_p95_min", "mean_rating_assigned"])

    try:
        make_charts(payload)
    except Exception as exc:                            # نمودارها اختیاری‌اند
        print(f"  ⚠ ساخت نمودارها ناموفق بود: {exc}")
    print(f"✓ خروجی‌ها در {OUT.relative_to(ROOT)} ذخیره شد.")
    return 0


def _csv(path: pathlib.Path, rows, cols):
    import csv
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        wr.writeheader()
        for r in rows:
            wr.writerow(r)


# ------------------------------------------------------------------- نمودارها
def _fa(text: str) -> str:
    """شکل‌دهی متن فارسی برای matplotlib."""
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        return text


def make_charts(payload):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager as fm

    font_path = ROOT / "assets" / "fonts" / "Vazirmatn-Regular.ttf"
    if font_path.exists():
        fm.fontManager.addfont(str(font_path))
        plt.rcParams["font.family"] = fm.FontProperties(fname=str(font_path)).get_name()
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.dpi"] = 130

    # ۱) مقایسه راهبردها در دو رژیم عرضه (میله‌های گروهی)
    rows = payload["strategies"]
    regimes = [("normal", "عرضه عادی", "#0b7a68"), ("scarce", "کمبود عرضه", "#c07a1a")]
    strategies = ["smart", "distance", "queue"]
    panels = [("fulfillment_rate", "نرخ تخصیص موفق (٪)", 100, "{:.1f}%"),
              ("tta_p95_min", "زمان تا تخصیص P95 (دقیقه)", 1, "{:.1f}"),
              ("top10pct_share", "تمرکز کار در ۱۰٪ برتر عرضه (٪)", 100, "{:.1f}%")]
    fig, axes = plt.subplots(1, 3, figsize=(12.6, 3.9))
    width = 0.36
    for axx, (key, title, mult, fmt) in zip(axes, panels):
        for gi, (regime, label, color) in enumerate(regimes):
            vals, xs = [], []
            for si, st in enumerate(strategies):
                r = next(r for r in rows if r["regime"] == regime and r["strategy"] == st)
                vals.append((r[key] or 0) * mult)
                xs.append(si + (gi - 0.5) * width)
            axx.bar(xs, vals, width=width, color=color, label=_fa(label))
            for x, v in zip(xs, vals):
                axx.text(x, v, fmt.format(v), ha="center", va="bottom", fontsize=7.6)
        axx.set_xticks(range(len(strategies)))
        axx.set_xticklabels([_fa(STRATEGY_FA[x].split(" (")[0]) for x in strategies], fontsize=8.4)
        axx.set_title(_fa(title), fontsize=10)
        axx.set_ylim(0, max(axx.get_ylim()[1] * 1.18, 1))
    axes[0].legend(fontsize=8.2, loc="lower left")
    fig.suptitle(_fa("مقایسه راهبردهای تخصیص در دو رژیم عرضه (۱٬۹۰۰ سفارش، ۲۶۰ متخصص)"), fontsize=11.5)
    fig.tight_layout()
    fig.savefig(OUT / "figs" / "strategies.png")
    plt.close(fig)

    # ۲) توفان حساسیت وزن‌ها
    sens = [r for r in payload["sensitivity"] if r["changed_weight"] != "-"]
    keys = list(DEFAULT_WEIGHTS)
    fig, ax = plt.subplots(figsize=(10.5, 4.6))
    for i, k in enumerate(keys):
        plus = next(r for r in sens if r["changed_weight"] == k and r["delta"] > 0)
        minus = next(r for r in sens if r["changed_weight"] == k and r["delta"] < 0)
        ax.barh(i + 0.18, plus["fulfillment_delta"] * 100, height=0.32, color="#0b7a68",
                label="وزن ۱.۵ برابر" if i == 0 else None)
        ax.barh(i - 0.18, minus["fulfillment_delta"] * 100, height=0.32, color="#c0392b",
                label="وزن ۰.۵ برابر" if i == 0 else None)
    ax.set_yticks(range(len(keys)))
    ax.set_yticklabels([_fa(f"{WEIGHT_FA[k]} ({DEFAULT_WEIGHTS[k]:.2f})") for k in keys], fontsize=9)
    ax.axvline(0, color="#333", lw=0.8)
    ax.set_xlabel(_fa("تغییر نرخ تخصیص موفق (درصد)"), fontsize=9.5)
    ax.set_title(_fa("تحلیل حساسیت: اثر ±۵۰٪ تغییر هر وزن بر نرخ تخصیص"), fontsize=11)
    ax.legend(fontsize=8.5, loc="lower right")
    fig.tight_layout()
    fig.savefig(OUT / "figs" / "sensitivity.png")
    plt.close(fig)

    # ۳) اندازه موج و شعاع
    rows = payload["waves"]
    labels = [_fa(r["config_fa"]) for r in rows]
    fig, ax1 = plt.subplots(figsize=(10.5, 3.9))
    x = range(len(rows))
    ax1.bar([i - 0.2 for i in x], [r["fulfillment_rate"] * 100 for r in rows], width=0.4, color="#0b7a68",
            label=_fa("نرخ تخصیص (٪)"))
    ax1.bar([i + 0.2 for i in x], [r["offers_per_order"] for r in rows], width=0.4, color="#b7c9c6",
            label=_fa("پیشنهاد به‌ازای هر سفارش"))
    ax1.set_xticks(list(x)); ax1.set_xticklabels(labels, fontsize=8.5)
    ax1.set_title(_fa("اثر اندازه موج، شمار موج‌ها و شعاع جست‌وجو"), fontsize=11)
    ax1.legend(fontsize=8.5)
    fig.tight_layout()
    fig.savefig(OUT / "figs" / "waves.png")
    plt.close(fig)

    # ۴) ضریب تقاضا
    rows = payload["surge"]
    fig, ax1 = plt.subplots(figsize=(10.5, 3.9))
    xs = [r["surge"] for r in rows]
    ax1.plot(xs, [r["fulfillment_rate"] * 100 for r in rows], marker="o", color="#0b7a68",
             label=_fa("نرخ تخصیص (٪)"))
    ax1.plot(xs, [r["accept_rate_offers"] * 100 for r in rows], marker="s", color="#2b5f9e",
             label=_fa("نرخ پذیرش پیشنهادها (٪)"))
    ax1.set_xlabel(_fa("ضریب تقاضا (Surge)"), fontsize=9.5)
    ax2 = ax1.twinx()
    ax2.plot(xs, [r["mean_provider_earnings_irr"] / 1e6 for r in rows], marker="^", color="#b06a12",
             label=_fa("میانگین درآمد متخصص (میلیون ریال)"))
    ax2.set_ylabel(_fa("درآمد (میلیون ریال)"), fontsize=9)
    ax1.set_title(_fa("کمبود عرضه: اثر ضریب تقاضا بر تخصیص، پذیرش و درآمد متخصص"), fontsize=11)
    h1, l1 = ax1.get_legend_handles_labels(); h2, l2 = ax2.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, fontsize=8.5, loc="lower right")
    fig.tight_layout()
    fig.savefig(OUT / "figs" / "surge.png")
    plt.close(fig)

    # ۵) عدالت توزیع: منحنی لورنز و نرخ گرسنگی عرضه برای چهار گونه وزن‌دهی
    rows = payload["fairness"]
    fig, axes = plt.subplots(1, 2, figsize=(11.2, 3.9))
    palette = ["#c0392b", "#0b7a68", "#2b5f9e", "#8a5cc0"]
    for i, r in enumerate(rows):
        jobs = sorted(r.get("provider_jobs", []))
        if not jobs or sum(jobs) == 0:
            continue
        n = len(jobs)
        cum, ssum, tot = [0.0], 0.0, sum(jobs)
        for j in jobs:
            ssum += j
            cum.append(ssum / tot)
        xs = [k / n for k in range(0, n + 1)]
        axes[0].plot(xs, cum, color=palette[i % len(palette)], lw=1.8,
                     label=_fa(f"{r['config_fa']} — جینی {r['gini_jobs']:.3f}"))
    axes[0].plot([0, 1], [0, 1], "--", color="#888", lw=1.0, label=_fa("برابری کامل"))
    axes[0].set_title(_fa("منحنی لورنز: توزیع کار میان متخصصان"), fontsize=10.5)
    axes[0].set_xlabel(_fa("سهم تجمعی متخصصان (کم‌کار → پرکار)"), fontsize=8.5)
    axes[0].set_ylabel(_fa("سهم تجمعی کارها"), fontsize=8.5)
    axes[0].legend(fontsize=7.4)

    labels = [_fa(r["config_fa"]) for r in rows]
    starved = [r["starved_share"] * 100 for r in rows]
    axes[1].barh(range(len(rows)), starved, color="#b0463a")
    axes[1].set_yticks(range(len(rows)))
    axes[1].set_yticklabels(labels, fontsize=7.6)
    for i, v in enumerate(starved):
        axes[1].text(v, i, f"{v:.1f}%", va="center", fontsize=7.8)
    axes[1].set_title(_fa("سهم متخصصان بدون هیچ کار در روز (گرسنگی عرضه)"), fontsize=10.5)
    fig.tight_layout()
    fig.savefig(OUT / "figs" / "fairness.png")
    plt.close(fig)
    print("  ✓ پنج نمودار ساخته شد.")


if __name__ == "__main__":
    raise SystemExit(main())
