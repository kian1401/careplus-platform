#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
golden_cases.py — ساخت «مجموعه آزمون طلایی» برای موتور تخصیص.

خروجی این اسکریپت، فایل `analysis/golden_dispatch_cases.json` است: شش سفارش کنترل‌شده روی
هشت متخصص با مشخصات ثابت (مختصات، مهارت، تجهیزات، نرخ قبولی، کیفیت). برای هر سفارش:

  • ترتیب و امتیاز کامل کاندیدها همان‌طور که `DispatchService` سرویس اجرایی محاسبه می‌کند،
  • پیشنهادهای ارسالی موج اول (۳ پیشنهاد)،
  • همان محاسبه با شبیه‌ساز تحلیلی (tools/dispatch_sim.py) برای اثبات هم‌ارزی.

پیمانکار می‌تواند همین فایل را به‌عنوان **fixure آزمون رگرسیون** مصرف کند: با هر تغییر وزن یا فرمول،
ترتیب و امتیازها باید با این مقادیر (با تلورانس ۱e-۳) یکسان بماند؛ تغییر عمدی، باید مستند شود.

اجرا:  python3 tools/golden_cases.py
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUTFILE = ROOT / "analysis" / "golden_dispatch_cases.json"

# جدول ثابت عرضه: (نام، lat, lng, مهارت‌ها، تجهیزات، رتبه، شمار نظرات، نرخ قبولی، وقت‌شناسی، لغو، تکمیل)
SUPPLY = [
    ("پ-۱", 35.7460, 51.4270, "plumbing.basic,cabinet.water_damage", "pipe_wrench,leak_detector",
     4.87, 312, 0.86, 0.94, 0.02, 312),
    ("پ-۲", 35.7415, 51.4180, "plumbing.basic", "pipe_wrench", 4.72, 198, 0.79, 0.90, 0.03, 198),
    ("پ-۳", 35.7510, 51.4330, "plumbing.basic,cabinet.water_damage", "pipe_wrench", 4.61, 142, 0.74, 0.88,
     0.05, 142),
    ("پ-۴", 35.7380, 51.4120, "plumbing.basic", "pipe_wrench,leak_detector", 4.40, 61, 0.68, 0.83, 0.07, 61),
    ("پ-۵", 35.7455, 51.4390, "electrical.basic", "multimeter", 4.68, 176, 0.81, 0.91, 0.03, 176),
    ("پ-۶", 35.7530, 51.4155, "electrical.basic,hvac.service", "multimeter,vacuum_pump", 4.81, 265, 0.84,
     0.93, 0.02, 265),
    ("پ-۷", 35.7350, 51.4265, "plumbing.basic", "pipe_wrench", 4.32, 45, 0.65, 0.80, 0.08, 45),   # دورافتاده
    ("پ-۸", 35.7448, 51.4260, "plumbing.basic,cabinet.water_damage", "pipe_wrench,leak_detector",
     4.95, 24, 0.90, 0.97, 0.01, 24),                                                          # بسیار نزدیک
]

# سفارش‌های آزمون: (کد، lat, lng, مهارت لازم، تجهیزات لازم، فوریت)
ORDERS = [
    ("GC-01", 35.7448, 51.4261, "plumbing.basic", "req:pipe_wrench", "SAME_DAY"),
    ("GC-02", 35.7448, 51.4261, "plumbing.basic", "req:pipe_wrench,leak_detector", "SAME_DAY"),
    ("GC-03", 35.7440, 51.4400, "electrical.basic", "req:multimeter", "EMERGENCY"),
    ("GC-04", 35.7400, 51.4260, "plumbing.basic,cabinet.water_damage", "req:pipe_wrench", "SCHEDULED"),
    ("GC-05", 35.7470, 51.4100, "plumbing.basic", "req:pipe_wrench", "SAME_DAY"),
    ("GC-06", 35.7520, 51.4200, "electrical.basic,hvac.service", "req:multimeter,vacuum_pump", "FLEXIBLE"),
]


def build():
    sys.path.insert(0, str(ROOT / "mvp"))
    sys.path.insert(0, str(ROOT / "tools"))
    os.environ["CP_DATABASE_URL"] = f"sqlite:///{ROOT / 'mvp' / 'var' / 'golden.sqlite'}"
    db_file = ROOT / "mvp" / "var" / "golden.sqlite"
    if db_file.exists():
        db_file.unlink()

    from app.core import Base, SessionLocal, engine
    from app.models import (ServiceCategory, ServiceItem, Order, ProviderProfile, User,
                            DispatchWeight)
    from app.services import DEFAULT_WEIGHTS, DispatchService
    import dispatch_sim as sim

    Base.metadata.create_all(engine)
    db = SessionLocal()
    cases = []
    mismatches = []
    try:
        cat = ServiceCategory(slug="gc", title_fa="آزمون طلایی", warranty_days=30)
        db.add(cat)
        db.flush()
        item = ServiceItem(category_id=cat.id, slug="gc.item", title_fa="خدمت آزمون", pricing_model="BAND",
                           base_price=1_650_000, min_price=1_200_000, max_price=2_400_000,
                           required_skills="plumbing.basic", required_equipment="req:pipe_wrench",
                           duration_minutes=60)
        db.add(item)
        db.add(DispatchWeight(scope="global", weights=DEFAULT_WEIGHTS, preset_name="golden-v1"))
        db.flush()

        providers = []
        for i, (name, lat, lng, skills, equip, rating, cnt, acc, on_time, cancel, done) in enumerate(SUPPLY):
            u = User(phone=f"+9891200900{i:02d}", full_name=name, roles="PROVIDER")
            db.add(u)
            db.flush()
            p = ProviderProfile(user_id=u.id, display_name=name, skills=skills, equipment=equip,
                                lat=lat, lng=lng, tier="PRO", status="ACTIVE", is_online=True,
                                verified_level=3, rating_avg=rating, rating_count=cnt,
                                acceptance_rate=acc, on_time_rate=on_time, cancel_rate=cancel,
                                completion_count=done, capacity_per_day=10)
            db.add(p)
            providers.append(p)
        db.flush()

        for code, lat, lng, skills, equip, urgency in ORDERS:
            cust = User(phone=f"+9891200800{len(cases):02d}", full_name=f"مشتری {code}")
            db.add(cust)
            db.flush()
            order = Order(code=code, customer_id=cust.id, service_item_id=item.id, status="CREATED",
                          urgency=urgency, severity=3, skills_required=skills,
                          equipment_required=equip, zone_id="golden", lat=lat, lng=lng)
            db.add(order)
            db.flush()
            # هر سناریو روی «وضعیت یکسان عرضه» سنجیده می‌شود: صفر کردن متغیرهای تاریخی زمان‌دار
            # (نگهداشت، ظرفیت مصرف‌شده، ردهای متوالی) تا نتایج تکرارپذیر و قابل استناد باشد.
            for pr in providers:
                pr.last_offer_at = None
                pr.accepted_today = 0
                pr.consecutive_rejections = 0
            db.flush()

            svc = DispatchService(db)
            radius = 6000 if urgency == "EMERGENCY" else 4000
            # رتبه‌بندی کامل روی «وضعیت دست‌نخورده عرضه» (پیش از ارسال هر پیشنهادی) محاسبه می‌شود؛
            # چون ارسال پیشنهاد، مؤلفه‌های «نگهداشت متخصص» و «ضد اسپم» را در پروفایل تغییر می‌دهد.
            wave_cands = sorted(
                ({"provider": p.display_name,
                  **svc.score(order, p, d, radius, DEFAULT_WEIGHTS, 1)}
                 for p, d in svc.candidates(order, radius)),
                key=lambda x: x["total"], reverse=True)
            res = svc.start(order, mode="EMERGENCY" if urgency == "EMERGENCY" else "NORMAL",
                            radius_m=radius)
            offers = [{"provider": o["provider_name"], "score": o["score"],
                       "distance_km": o["distance_km"]} for o in res["offers"]]

            # اجرای همان سفارش با شبیه‌ساز تحلیلی برای اثبات هم‌ارزی
            sim_providers = [sim.Provider(
                pid=i, name=n, lat=la, lng=ln, skills=set(sk.split(",")), equipment=set(eq.split(",")),
                rating=r, rating_count=c, acceptance_rate=a, on_time_rate=ot, cancel_rate=ca,
                completion_count=dn, capacity=10, archetype="NORMAL") for
                i, (n, la, ln, sk, eq, r, c, a, ot, ca, dn) in enumerate(SUPPLY)]
            engine_sim = sim.SimEngine(sim_providers, rng=__import__("random").Random(0),
                                       strategy="smart", radius_m=res["radius_m"], escalating=False,
                                       max_waves=1)
            # در سرویس، ظرفیت روزانه و «پیشنهاد تکراری» فیلتر می‌شوند؛ شبیه‌ساز فقط فیلترهای سخت را می‌زند
            sim_order = sim.SimOrder(oid=0, lat=lat, lng=lng,
                                     skills={x.strip() for x in skills.split(",")},
                                     equipment={x.strip().replace("req:", "")
                                                for x in equip.split(",")},
                                     urgency=urgency, price=1_650_000, customer_id=0, created_min=0)
            sim_scored = []
            for p, d in engine_sim.eligible(sim_order, res["radius_m"]):
                total, comp, _ = engine_sim.components(sim_order, p, d, res["radius_m"])
                sim_scored.append((p.name, round(total, 4)))
            sim_scored.sort(key=lambda x: x[1], reverse=True)
            svc_order = [(c["provider"], c["total"]) for c in wave_cands]
            same_order = [n for n, _ in sim_scored] == [n for n, _ in svc_order]
            same_scores = all(abs(a[1] - b[1]) < 1e-3 for a, b in zip(sim_scored, svc_order))
            if not (same_order and same_scores):
                mismatches.append({"code": code, "service": svc_order, "sim": sim_scored})

            cases.append({
                "case": code,
                "order": {"lat": lat, "lng": lng, "skills_required": skills,
                          "equipment_required": equip, "urgency": urgency,
                          "radius_m": res["radius_m"]},
                "candidates_scored": res["candidates_scored"],
                "ranking": [{"rank": i + 1, "provider": c["provider"], "total_score": c["total"],
                             "distance_km": c["distance_km"], "components": c["components"],
                             "penalty": c["penalty"], "explain_fa": c["explain_fa"]}
                            for i, c in enumerate(wave_cands)],
                "wave1_offers": offers,
                "simulator_match": bool(same_order and same_scores),
            })
        db.rollback()
        payload = {
            "generated_by": "tools/golden_cases.py",
            "weights": DEFAULT_WEIGHTS,
            "urgency_ttl_seconds": {"EMERGENCY": 20, "SAME_DAY": 90, "SCHEDULED": 90},
            "tolerance": 0.001,
            "note_fa": ("ترتیب و امتیازها باید با هر تغییر در وزن‌ها یا فرمول‌ها بازتولید شود؛ "
                        "افزایش/کاهش امتیاز بیش از ۰.۰۰۱ یا جابه‌جایی رتبه‌ها = تغییر رفتاری عمدی "
                        "که باید مستند و تأیید شود."),
            "supply": [{"name": s[0], "lat": s[1], "lng": s[2], "skills": s[3], "equipment": s[4],
                        "rating": s[5], "acceptance_rate": s[7]} for s in SUPPLY],
            "cases": cases,
            "mismatches": mismatches,
            "status": "OK" if not mismatches else "MISMATCH",
        }
    finally:
        db.close()

    OUTFILE.parent.mkdir(parents=True, exist_ok=True)
    OUTFILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ {OUTFILE.relative_to(ROOT)} — {len(cases)} سناریوی طلایی، وضعیت: {payload['status']}")
    for c in cases:
        top = c["ranking"][0] if c["ranking"] else None
        print(f"  {c['case']}: کاندید={c['candidates_scored']} | بهترین='{top['provider']}' "
              f"({top['total_score']:.4f}) | پیشنهادها={[o['provider'] for o in c['wave1_offers']]}")
    if mismatches:
        print("  ⚠ ناهم‌خوانی شبیه‌ساز و سرویس:", mismatches)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(build())
