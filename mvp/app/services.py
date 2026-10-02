# -*- coding: utf-8 -*-
"""
سرویس‌های دامنه MVP:
  • PricingService   — قیمت‌گذاری پویا با سقف، Breakdown شفاف و تغییر قیمت (Change Order)
  • DispatchService  — تولید کاندید، امتیازدهی چندمتغیره، موج‌ها، تشدید، پذیرش اتمی
  • LedgerService    — دفتر کل دوعاملی، نگهداشت Escrow، آزادسازی با کمیسیون و مالیات، بازگشت
  • ReviewService    — امتیاز پنج‌بعدی و بازمحاسبه رتبه متخصص
  • events/audit     — Outbox و لاگ حسابرسی
"""
from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import Any, Iterable, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .core import (aware, clamp, hash_secret, haversine_km, jdump, money, new_id, now, settings)
from .models import (Assignment, AssignmentRejection, Booking, DispatchRequest, DispatchWeight,
                     EscrowHold, EscrowRelease, EventOutbox, AuditLog, Invoice, JobOffer,
                     LedgerAccount, LedgerEntry, Order, OrderTimeline, PriceQuote, PriceQuoteRevision,
                     ProviderProfile, Review, ReviewDimension, ServiceItem, Wallet, Warranty,
                     ZoneDemandMetric)

# ====================================================================== pricing
URGENCY_FACTOR = {"EMERGENCY": 1.35, "SAME_DAY": 1.15, "SCHEDULED": 1.00}
SURGE_MIN, SURGE_MAX = 0.95, 1.50            # سقف سخت: بیش از ۱.۵ برابر هرگز


class PricingService:
    """موتور قیمت‌گذاری: Deterministic و قابل بازتولید (هر تخمین با نسخه موتور امضا می‌شود)."""

    ENGINE_VERSION = "pricing-v1.0"

    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------ surge
    def surge_for_zone(self, zone_id: str | None) -> float:
        if not zone_id:
            return 1.0
        row = self.db.execute(
            select(ZoneDemandMetric).where(ZoneDemandMetric.zone_id == zone_id)
            .order_by(ZoneDemandMetric.window_start.desc()).limit(1)
        ).scalar_one_or_none()
        if not row:
            return 1.0
        if row.surge_override:
            return clamp(row.surge_override, SURGE_MIN, SURGE_MAX)
        supply = max(row.providers_online, 1)
        ratio = row.orders_open / supply
        surge = 1.0 + 0.40 * (ratio - 1.0)
        return round(clamp(surge, SURGE_MIN, SURGE_MAX), 3)

    def resolve_price(self, item: ServiceItem, zone_id: str | None, org_id: str | None) -> int:
        """تعرفه پایه: اولویت با تعرفه سازمانی، سپس منطقه‌ای، در نهایت تعرفه پیش‌فرض خدمت."""
        from sqlalchemy import or_
        from .models import RateCard

        q = select(RateCard).where(RateCard.service_item_id == item.id, RateCard.active.is_(True))
        if org_id:
            q_org = q.where(RateCard.org_id == org_id).limit(1)
            row = self.db.execute(q_org).scalar_one_or_none()
            if row:
                return row.base_price
        if zone_id:
            row = self.db.execute(q.where(RateCard.zone_id == zone_id).limit(1)).scalar_one_or_none()
            if row:
                return row.base_price
        return item.base_price

    # ------------------------------------------------------------------ quote
    def quote(self, *, item: ServiceItem, urgency: str, zone_id: str | None, org_id: str | None,
              severity: int = 2, distance_km: float = 4.0, coupon_percent: float = 0.0,
              travel_per_km: int = 12_000, apply_surge: bool = True,
              emergency_service: bool = False) -> dict[str, Any]:
        base = self.resolve_price(item, zone_id, org_id)
        urgency_factor = URGENCY_FACTOR.get(urgency, 1.0)
        complexity = 1.0 + 0.12 * clamp(severity - 1, 0, 4)
        surge = self.surge_for_zone(zone_id) if apply_surge else 1.0
        if emergency_service:                      # خدمات امدادی از افزایش پیک معاف‌اند
            surge = 1.0

        labor = money(base * urgency_factor * complexity)
        surge_amount = money(labor * (surge - 1.0))
        travel = money(distance_km * travel_per_km)
        materials_allowance = money(item.base_price * 0.18)   # برآورد اقلام مصرفی (با رسید)
        subtotal = labor + surge_amount + travel + materials_allowance
        discount = money(subtotal * clamp(coupon_percent, 0, 0.5))
        taxable = subtotal - discount
        vat = money(taxable * settings.vat_rate)
        total = taxable + vat

        if item.pricing_model == "FIXED":
            low = high = total
        else:
            low, high = money(total * 0.92), money(total * 1.08)

        breakdown = [
            {"title": f"اجرت پایه: {item.title_fa}", "amount": money(base), "note": "از Rate Card نسخه‌دار"},
            {"title": f"ضریب فوریت ({urgency})", "amount": money(labor - base), "note": f"×{urgency_factor}"},
            {"title": "ضریب پیچیدگی (شدت اعلام‌شده)", "amount": money(labor - base * urgency_factor),
             "note": f"×{round(complexity, 2)}"},
            {"title": f"ضریب تقاضای منطقه (×{surge})", "amount": surge_amount,
             "note": "معاف برای خدمات امدادی" if emergency_service else "سقف ۱.۵ برابر"},
            {"title": f"ایاب و ذهاب ({distance_km:.1f} کیلومتر)", "amount": travel, "note": "بر پایه فاصله"},
            {"title": "برآورد اقلام مصرفی", "amount": materials_allowance, "note": "تسویه با رسید"},
        ]
        if discount:
            breakdown.append({"title": "تخفیف/کوپن", "amount": -discount, "note": "قابل ابطال در صورت تخلف"})
        breakdown.append({"title": f"مالیات ارزش افزوده ({int(settings.vat_rate * 100)}٪)", "amount": vat,
                          "note": "طبق قواعد جاری"})

        quote = PriceQuote(
            service_item_id=item.id, zone_id=zone_id, urgency=urgency,
            min_amount=low, max_amount=high, final_amount=total, breakdown=breakdown,
            engine_version=self.ENGINE_VERSION, surge=surge,
            confidence=round(clamp(0.9 - 0.15 * (severity / 5), 0.4, 0.95), 2),
            expires_at=now() + timedelta(minutes=30),
        )
        self.db.add(quote)
        self.db.flush()
        return {
            "quote_id": quote.id, "model": item.pricing_model, "currency": "IRR",
            "final_amount": total, "min_amount": low, "max_amount": high,
            "surge": surge, "urgency_factor": urgency_factor, "complexity_factor": round(complexity, 2),
            "breakdown": breakdown, "engine_version": self.ENGINE_VERSION,
            "rate_card_version": "v1", "confidence": quote.confidence,
            "guardrail": "افزایش قیمت پس از این مرحله فقط با تأیید صریح مشتری (Change Order) اعمال می‌شود.",
        }

    def revise(self, order: Order, *, reason_code: str, new_amount: int, actor_id: str) -> PriceQuoteRevision:
        rev = PriceQuoteRevision(order_id=order.id, reason_code=reason_code,
                                 old_amount=order.final_amount or 0, new_amount=new_amount,
                                 created_by=actor_id)
        self.db.add(rev)
        self.db.flush()
        return rev

    def approve_revision(self, rev: PriceQuoteRevision, order: Order, approved: bool) -> None:
        rev.approved = approved
        rev.approved_at = now()
        if approved:
            order.final_amount = rev.new_amount


# ===================================================================== dispatch
DEFAULT_WEIGHTS: dict[str, float] = {
    "distance": 0.26,        # نزدیکی متخصص به مشتری
    "quality": 0.18,         # امتیاز چندبعدی + آن‌تایم بودن
    "acceptance": 0.14,      # نرخ قبولی در همین دسته (در MVP: نرخ کلی)
    "reliability": 0.12,     # نرخ لغو و تکمیل
    "equipment": 0.08,       # تطابق تجهیزات ثبت‌شده
    "price": 0.10,           # نزدیکی قیمت پیشنهادی به بازه پلتفرم (MVP: خنثی)
    "history": 0.06,         # سابقه کار با همین مشتری
    "fairness": 0.04,        # جبران کم‌کاری متخصصان حاشیه‌ای
    "retention": 0.02,       # ریسک ریزش متخصص (کمبود کار اخیر)
}


class DispatchService:
    """
    الگوریتم تخصیص:
      ۱) تولید کاندید با فیلتر سخت (مهارت، تجهیزات، فاصله، ظرفیت، تعارض تقویم، مسدودی)
      ۲) امتیازدهی نرم با وزن‌های قابل تنظیم (جدول dispatch_weights)
      ۳) موج‌های پیشنهاد با پنجره زمانی و نردبان تشدید
      ۴) پذیرش اتمی: اولین پذیرنده برنده؛ سایر پیشنهادها EXPIRED
    """

    WAVE_SIZE = 3                 # اندازه هر موج (۳–۵ پیشنهاد هم‌زمان)
    OFFER_TTL = {"EMERGENCY": 20, "SAME_DAY": 90, "SCHEDULED": 90}
    MAX_WAVES = 3
    RADIUS_STEP = 1.5             # ضریب افزایش شعاع در هر موج
    INCENTIVE_RATE = 0.03         # سهم افزایشی هر موج برای انگیزه‌دهی

    def __init__(self, db: Session):
        self.db = db

    # ---------------------------------------------------------------- weights
    def weights_for(self, order: Order) -> dict[str, float]:
        for scope in (f"category:{order.service_item_id}", f"zone:{order.zone_id}", "global"):
            row = self.db.execute(
                select(DispatchWeight).where(DispatchWeight.scope == scope)
                .order_by(DispatchWeight.updated_at.desc()).limit(1)
            ).scalar_one_or_none()
            if row:
                merged = dict(DEFAULT_WEIGHTS)
                merged.update({k: float(v) for k, v in (row.weights or {}).items()})
                return merged
        return dict(DEFAULT_WEIGHTS)

    # -------------------------------------------------------------- candidate
    def candidates(self, order: Order, radius_m: int, limit: int = 200) -> list[tuple[ProviderProfile, float]]:
        radius_km = radius_m / 1000.0
        rows = self.db.execute(
            select(ProviderProfile).where(
                ProviderProfile.status == "ACTIVE",
                ProviderProfile.is_online.is_(True),
                ProviderProfile.verified_level >= 2,
            )
        ).scalars().all()

        out: list[tuple[ProviderProfile, float]] = []
        for p in rows:
            if p.lat is None or p.lng is None:
                continue
            if p.blocked_until and aware(p.blocked_until) > now():
                continue
            dist = haversine_km(order.lat, order.lng, p.lat, p.lng)
            if dist > radius_km:                                     # فیلتر سخت فاصله
                continue
            if not self._skill_ok(order, p):                          # فیلتر سخت مهارت
                continue
            if not self._equipment_ok(order, p):                      # فیلتر سخت تجهیزات
                continue
            if p.accepted_today >= p.capacity_per_day:                # فیلتر ظرفیت
                continue
            if self._recently_offered(order, p):                      # ضد اسپم پیشنهاد تکراری
                continue
            if p.consecutive_rejections >= 3:                         # جریمه رفتار
                continue
            out.append((p, dist))
        out.sort(key=lambda x: x[1])
        return out[:limit]

    @staticmethod
    def _skill_ok(order: Order, p: ProviderProfile) -> bool:
        need = {s.strip() for s in (order.skills_required or "").split(",") if s.strip()}
        return need.issubset(p.skill_set) if need else True

    @staticmethod
    def _equipment_ok(order: Order, p: ProviderProfile) -> bool:
        need = {s.strip() for s in (order.equipment_required or "").split(",") if s.strip()}
        # تجهیزات «مطلوب» است نه اجباری: نبود آن امتیاز را کم می‌کند اما حذف نمی‌کند،
        # مگر برای تجهیزات حیاتی که با پیشوند `req:` علامت خورده باشند.
        hard = {x[4:] for x in need if x.startswith("req:")}
        return hard.issubset(p.equipment_set) if hard else True

    def _recently_offered(self, order: Order, p: ProviderProfile) -> bool:
        prev = self.db.execute(
            select(JobOffer).where(JobOffer.order_id == order.id, JobOffer.provider_id == p.user_id,
                                   JobOffer.response == "REJECT").limit(1)
        ).scalar_one_or_none()
        if prev:                       # متخصصی که این سفارش را رد کرده در همان سفارش دوباره دعوت نمی‌شود
            return True
        return False

    # ------------------------------------------------------------------ score
    def score(self, order: Order, p: ProviderProfile, dist_km: float, radius_m: int,
              weights: dict[str, float], wave_no: int = 1) -> dict[str, Any]:
        r_km = max(radius_m / 1000.0, 0.5)
        s_distance = clamp(1.0 - (dist_km / r_km), 0.0, 1.0)
        s_quality = clamp((p.rating_avg / 5.0 if p.rating_count else 0.78) * 0.6 + p.on_time_rate * 0.4, 0, 1)
        s_acceptance = clamp(p.acceptance_rate, 0, 1)
        s_reliability = clamp((1 - p.cancel_rate) * 0.5 + min(p.completion_count, 100) / 100 * 0.5, 0, 1)
        need_eq = {x[4:] if x.startswith("req:") else x
                   for x in (order.equipment_required or "").split(",") if x.strip()}
        s_equipment = 1.0 if not need_eq else len(need_eq & p.equipment_set) / len(need_eq)
        s_price = 0.6                                   # MVP: مزایده فعال نیست ⇒ مقدار خنثی
        s_history = self._history_score(order, p)
        s_fairness = self._fairness_score(p)
        s_retention = self._retention_score(p)

        penalty = 0.0
        if p.consecutive_rejections:
            penalty += 0.02 * min(p.consecutive_rejections, 3)      # جریمه ردهای متوالی
        if self.db.execute(
            select(func.count()).select_from(JobOffer).where(
                JobOffer.order_id == order.id, JobOffer.provider_id == p.user_id,
                JobOffer.response == "IGNORE")
        ).scalar_one():
            penalty += 0.05                                        # بی‌پاسخ ماندن پیشنهاد قبلی همین سفارش

        components = {
            "distance": s_distance, "quality": s_quality, "acceptance": s_acceptance,
            "reliability": s_reliability, "equipment": s_equipment, "price": s_price,
            "history": s_history, "fairness": s_fairness, "retention": s_retention,
        }
        raw = sum(weights.get(k, 0.0) * v for k, v in components.items())
        total = clamp(raw - penalty, 0.0, 1.0)
        return {
            "total": round(total, 4),
            "components": {k: round(v, 4) for k, v in components.items()},
            "weights": weights,
            "penalty": round(penalty, 4),
            "distance_km": round(dist_km, 2),
            "explain_fa": (
                f"فاصله {dist_km:.1f} کیلومتر · کیفیت {s_quality:.2f} · نرخ قبولی {s_acceptance:.2f} · "
                f"تجهیزات {s_equipment:.2f} · جریمه {penalty:.2f}"
            ),
        }

    def _history_score(self, order: Order, p: ProviderProfile) -> float:
        count = self.db.execute(
            select(func.count()).select_from(Order).where(
                Order.customer_id == order.customer_id,
                Order.assigned_provider_id == p.user_id,
                Order.status.in_(("COMPLETED", "CLOSED")))
        ).scalar_one()
        return clamp(min(count, 3) / 3 * 1.0, 0, 1)

    def _fairness_score(self, p: ProviderProfile) -> float:
        """متخصصان کم‌کار در ۲۴ ساعت گذشته امتیاز بیشتری می‌گیرند (عدالت توزیع)."""
        since = now() - timedelta(hours=24)
        served = self.db.execute(
            select(func.count()).select_from(Assignment).where(
                Assignment.provider_id == p.user_id, Assignment.assigned_at >= since)
        ).scalar_one()
        target = max(p.capacity_per_day, 1)
        return clamp(1.0 - served / target, 0.0, 1.0)

    def _retention_score(self, p: ProviderProfile) -> float:
        """اگر متخصص چند روز کار نگرفته، امتیاز نگهداشت افزایش می‌یابد (کاهش ریزش عرضه)."""
        if not p.last_offer_at:
            return 1.0
        idle_hours = (now() - aware(p.last_offer_at)).total_seconds() / 3600.0
        return clamp(idle_hours / 24.0, 0.0, 1.0)

    # ------------------------------------------------------------------- wave
    def start(self, order: Order, *, mode: str = "NORMAL", radius_m: int | None = None) -> dict[str, Any]:
        request = DispatchRequest(order_id=order.id, mode=mode,
                                  radius_m=radius_m or order.dispatch_radius_m or 4000,
                                  policy={"wave_size": self.WAVE_SIZE, "max_waves": self.MAX_WAVES})
        self.db.add(request)
        self.db.flush()
        order.status = "DISPATCHING"
        offers = self._send_wave(order, request, wave_no=1)
        return {"dispatch_id": request.id, "wave": 1, "radius_m": request.radius_m,
                "offers": offers, "candidates_scored": request.policy.get("scored", 0)}

    def _send_wave(self, order: Order, request: DispatchRequest, wave_no: int) -> list[dict[str, Any]]:
        weights = self.weights_for(order)
        cands = self.candidates(order, request.radius_m)
        scored = [self.score(order, p, d, request.radius_m, weights, wave_no) | {
            "provider_id": p.user_id, "provider_name": p.display_name, "tier": p.tier} for p, d in cands]
        scored.sort(key=lambda x: x["total"], reverse=True)
        request.policy = {**(request.policy or {}), "scored": len(scored),
                          "weights": weights, "wave": wave_no}
        ttl = self.OFFER_TTL.get(order.urgency, 90)
        expires = now() + timedelta(seconds=ttl)
        incentive = money((order.final_amount or 0) * self.INCENTIVE_RATE * (wave_no - 1))

        created: list[dict[str, Any]] = []
        for cand in scored[:self.WAVE_SIZE]:
            offer = JobOffer(
                id=new_id(),                       # شناسه صریح تا پیش از flush نیز قابل استفاده باشد
                dispatch_id=request.id, order_id=order.id, provider_id=cand["provider_id"],
                wave_no=wave_no, rank_score=cand["total"], score_breakdown=cand["components"],
                distance_km=cand["distance_km"], incentive_amount=incentive, expires_at=expires,
            )
            self.db.add(offer)
            p = self.db.get(ProviderProfile, cand["provider_id"])
            if p:
                p.last_offer_at = now()
            created.append({"offer_id": offer.id, "provider_name": cand["provider_name"],
                            "score": cand["total"], "distance_km": cand["distance_km"],
                            "incentive": incentive, "expires_at": expires.isoformat(),
                            "explain": cand["explain_fa"]})
        self.db.flush()
        events.emit(self.db, topic="dispatch", event_type="dispatch.wave.sent", aggregate_id=order.id,
                    payload={"wave": wave_no, "offers": len(created), "radius_m": request.radius_m})
        timeline(self.db, order.id, f"DISPATCH_WAVE_{wave_no}",
                 payload={"offers": len(created), "radius_m": request.radius_m, "incentive": incentive})
        return created

    def advance(self, order: Order) -> dict[str, Any]:
        """انقضای موج جاری و اجرای نردبان تشدید: شعاع بیشتر → انگیزه بیشتر → ارجاع به اپراتور."""
        request = self.db.execute(
            select(DispatchRequest).where(DispatchRequest.order_id == order.id)
            .order_by(DispatchRequest.started_at.desc()).limit(1)
        ).scalar_one_or_none()
        if not request:
            return {"error": "DISPATCH_NOT_STARTED"}
        if request.status == "ASSIGNED" or order.status != "DISPATCHING":
            return {"error": "ORDER_ALREADY_ASSIGNED", "status": order.status}

        pending = self.db.execute(
            select(JobOffer).where(JobOffer.dispatch_id == request.id, JobOffer.response == "PENDING")
        ).scalars().all()
        for o in pending:
            o.response = "EXPIRED"
            o.responded_at = now()

        wave = (request.policy or {}).get("wave", 1)
        if wave >= self.MAX_WAVES:
            request.status = "FAILED"
            order.status = "DISPATCHING"
            events.emit(self.db, topic="dispatch", event_type="dispatch.escalated",
                        aggregate_id=order.id, payload={"reason": "no_provider_found", "waves": wave})
            timeline(self.db, order.id, "DISPATCH_ESCALATED_TO_OPS",
                     payload={"waves": wave, "action": "تخصیص دستی اپراتور"})
            return {"escalated_to_ops": True, "waves": wave}
        request.wave_no = wave + 1
        request.radius_m = int(request.radius_m * self.RADIUS_STEP)
        offers = self._send_wave(order, request, wave_no=wave + 1)
        return {"wave": wave + 1, "radius_m": request.radius_m, "offers": offers}

    # ----------------------------------------------------------------- accept
    def accept(self, order: Order, offer: JobOffer, provider_id: str) -> dict[str, Any]:
        if offer.response != "PENDING":
            return {"error": "OFFER_NOT_PENDING", "response": offer.response}
        if aware(offer.expires_at) < now():
            offer.response = "EXPIRED"
            return {"error": "OFFER_EXPIRED"}
        active = self.db.execute(
            select(Assignment).where(Assignment.order_id == order.id, Assignment.released_at.is_(None))
        ).scalar_one_or_none()
        if active:
            return {"error": "ORDER_ALREADY_ASSIGNED", "provider_id": active.provider_id}

        offer.response = "ACCEPT"
        offer.responded_at = now()
        assignment = Assignment(order_id=order.id, provider_id=provider_id, assigned_by="AUTO")
        self.db.add(assignment)
        self.db.flush()

        others = self.db.execute(
            select(JobOffer).where(JobOffer.dispatch_id == offer.dispatch_id,
                                   JobOffer.id != offer.id, JobOffer.response == "PENDING")
        ).scalars().all()
        for o in others:
            o.response = "EXPIRED"
            o.responded_at = now()

        order.assigned_provider_id = provider_id
        order.status = "ASSIGNED"
        p = self.db.get(ProviderProfile, provider_id)
        if p:
            p.accepted_today += 1
            p.consecutive_rejections = 0
            p.acceptance_rate = round(clamp(0.9 * p.acceptance_rate + 0.1 * 1.0, 0, 1), 3)
        b = Booking(order_id=order.id, provider_id=provider_id,
                    slot_start=order.slot_start or now(), slot_end=order.slot_end or now() + timedelta(hours=2))
        self.db.add(b)
        self.db.flush()
        order.booking_id = b.id

        events.emit(self.db, topic="order.lifecycle", event_type="order.assigned", aggregate_id=order.id,
                    payload={"provider_id": provider_id, "offer_id": offer.id, "score": offer.rank_score})
        timeline(self.db, order.id, "ASSIGNED", actor_id=provider_id,
                 payload={"provider_name": p.display_name if p else provider_id, "score": offer.rank_score})
        return {"assignment_id": assignment.id, "booking_id": b.id, "provider_id": provider_id,
                "order_status": order.status}

    def reject(self, order: Order, offer: JobOffer, provider_id: str, reason_code: str) -> dict[str, Any]:
        offer.response = "REJECT"
        offer.responded_at = now()
        self.db.add(AssignmentRejection(offer_id=offer.id, provider_id=provider_id, reason_code=reason_code))
        p = self.db.get(ProviderProfile, provider_id)
        if p:
            p.consecutive_rejections += 1
            p.acceptance_rate = round(clamp(0.9 * p.acceptance_rate, 0, 1), 3)
        events.emit(self.db, topic="dispatch", event_type="dispatch.offer.rejected", aggregate_id=order.id,
                    payload={"provider_id": provider_id, "reason": reason_code})
        return {"offer_id": offer.id, "response": "REJECT", "consecutive_rejections": p.consecutive_rejections if p else None}

    def operator_assign(self, order: Order, provider_id: str, operator_id: str, reason: str) -> dict[str, Any]:
        active = self.db.execute(
            select(Assignment).where(Assignment.order_id == order.id, Assignment.released_at.is_(None))
        ).scalar_one_or_none()
        if active:
            return {"error": "ORDER_ALREADY_ASSIGNED"}
        a = Assignment(order_id=order.id, provider_id=provider_id, assigned_by="MANUAL",
                       assigned_by_user=operator_id, reason=reason)
        self.db.add(a)
        self.db.flush()
        order.assigned_provider_id = provider_id
        order.status = "ASSIGNED"
        b = Booking(order_id=order.id, provider_id=provider_id,
                    slot_start=order.slot_start or now(), slot_end=order.slot_end or now() + timedelta(hours=2))
        self.db.add(b)
        self.db.flush()
        order.booking_id = b.id
        self.db.add(AuditLog(actor_id=operator_id, actor_role="OPS", action="order.assign.manual",
                             resource_type="order", resource_id=order.id, reason=reason))
        timeline(self.db, order.id, "ASSIGNED_MANUAL", actor_id=operator_id, payload={"reason": reason})
        return {"assignment_id": a.id, "booking_id": b.id}


# ======================================================================= ledger
PLATFORM_ACCOUNTS = {
    "1100-CASH": ("نقد و درگاه پرداخت", "ASSET", "PLATFORM", "DEBIT"),
    "1150-CUSTOMER-WALLET": ("بدهی به کیف پول مشتریان", "LIABILITY", "USER", "CREDIT"),
    "2200-ESCROW-LIABILITY": ("بدهی نگهداشت (Escrow)", "LIABILITY", "PLATFORM", "CREDIT"),
    "2300-PROVIDER-WALLET": ("بدهی به متخصصان", "LIABILITY", "PROVIDER", "CREDIT"),
    "2400-PAYABLE-TAX": ("مالیات قابل پرداخت", "LIABILITY", "TAX", "CREDIT"),
    "2500-PAYABLE-INSURER": ("سهم بیمه‌گذار", "LIABILITY", "PLATFORM", "CREDIT"),
    "4100-COMMISSION-REVENUE": ("درآمد کمیسیون پلتفرم", "REVENUE", "PLATFORM", "CREDIT"),
    "5200-COMPENSATION-EXPENSE": ("جبران خسارت و تخفیف جبرانی", "EXPENSE", "PLATFORM", "DEBIT"),
}


class LedgerService:
    """
    دفتر کل دوعاملی: هر تراکنش چند قید متوازن دارد (جمع بدهکار = جمع بستانکار).
    موجودی = مشتق از قیدها؛ `Wallet.balance_cached` فقط برای نمایش سریع است.
    """

    def __init__(self, db: Session):
        self.db = db
        self._ensure_accounts()

    def _ensure_accounts(self) -> None:
        existing = {a.code for a in self.db.execute(select(LedgerAccount)).scalars()}
        for code, (name, kind, owner_type, normal) in PLATFORM_ACCOUNTS.items():
            if code not in existing:
                self.db.add(LedgerAccount(code=code, name=name, kind=kind,
                                          owner_type=owner_type, normal_balance=normal))
        self.db.flush()

    @staticmethod
    def wallet_account_code(owner_type: str, owner_id: str) -> str:
        return f"{'1150' if owner_type == 'USER' else '2300'}-{owner_type}:{owner_id}"

    def _account(self, code: str, *, name: str = "", kind: str = "LIABILITY",
                 owner_type: str = "PLATFORM", normal_balance: str = "CREDIT") -> LedgerAccount:
        acc = self.db.execute(select(LedgerAccount).where(LedgerAccount.code == code)).scalar_one_or_none()
        if not acc:
            acc = LedgerAccount(code=code, name=name or code, kind=kind,
                                owner_type=owner_type, normal_balance=normal_balance)
            self.db.add(acc)
            self.db.flush()
        return acc

    def post(self, *, ref_type: str, ref_id: str, entries: Sequence[tuple[str, str, int, str]],
             memo: str = "") -> str:
        """entries: [(account_code, DEBIT|CREDIT, amount, note)]"""
        debit = sum(a for _, d, a, _ in entries if d == "DEBIT")
        credit = sum(a for _, d, a, _ in entries if d == "CREDIT")
        if debit != credit:
            raise ValueError(f"UNBALANCED_TXN: debit={debit} credit={credit}")
        if debit <= 0:
            raise ValueError("TXN_AMOUNT_MUST_BE_POSITIVE")

        txn_id = new_id()
        prev = self.db.execute(select(LedgerEntry).order_by(LedgerEntry.created_at.desc()).limit(1)).scalar_one_or_none()
        chain = prev.hash_self if prev else "genesis"
        for code, direction, amount, note in entries:
            self._account(code, name=code)
            payload = f"{txn_id}|{code}|{direction}|{amount}|{chain}"
            h = hashlib.sha256(payload.encode()).hexdigest()
            self.db.add(LedgerEntry(txn_id=txn_id, account_code=code, direction=direction,
                                    amount=amount, ref_type=ref_type, ref_id=ref_id,
                                    memo=note or memo, hash_prev=chain, hash_self=h))
            chain = h
        self.db.flush()
        events.emit(self.db, topic="payment", event_type="ledger.txn.posted", aggregate_id=ref_id,
                    payload={"txn_id": txn_id, "ref_type": ref_type, "debit": debit})
        return txn_id

    # ------------------------------------------------------------ operations
    def balance(self, code: str) -> int:
        rows = self.db.execute(select(LedgerEntry).where(LedgerEntry.account_code == code)).scalars().all()
        acc = self._account(code)
        total = 0
        for e in rows:
            total += e.amount if e.direction == acc.normal_balance else -e.amount
        return total

    def trial_balance(self) -> dict[str, Any]:
        rows = self.db.execute(select(LedgerEntry)).scalars().all()
        accounts: dict[str, dict[str, int]] = {}
        for e in rows:
            a = accounts.setdefault(e.account_code, {"DEBIT": 0, "CREDIT": 0})
            a[e.direction] += e.amount
        debit = sum(v["DEBIT"] for v in accounts.values())
        credit = sum(v["CREDIT"] for v in accounts.values())
        return {"accounts": accounts, "total_debit": debit, "total_credit": credit,
                "balanced": debit == credit}

    def topup(self, user_id: str, amount: int) -> dict[str, Any]:
        acc = self.wallet_account_code("USER", user_id)
        txn = self.post(ref_type="topup", ref_id=user_id, memo="شارژ کیف پول (دمو)",
                        entries=[("1100-CASH", "DEBIT", amount, "ورود وجه"),
                                 (acc, "CREDIT", amount, "افزایش کیف پول")])
        self._sync_wallet("USER", user_id)
        return {"txn_id": txn, "wallet_balance": self.balance(acc)}

    def hold_escrow(self, order: Order, amount: int, hold_hours: int | None = None) -> EscrowHold:
        customer_acc = self.wallet_account_code("USER", order.customer_id)
        txn = self.post(ref_type="order", ref_id=order.id, memo=f"نگهداشت سفارش {order.code}",
                        entries=[(customer_acc, "DEBIT", amount, "کسر از کیف پول مشتری"),
                                 ("2200-ESCROW-LIABILITY", "CREDIT", amount, "نگهداشت در حساب واسط")])
        hold = EscrowHold(order_id=order.id, amount=amount, status="HELD",
                          expires_at=now() + timedelta(hours=hold_hours or settings.escrow_hold_hours))
        self.db.add(hold)
        order.escrow_state = "HELD_IN_ESCROW"
        order.payment_state = "PAID"
        self.db.flush()
        self._sync_wallet("USER", order.customer_id)
        timeline(self.db, order.id, "ESCROW_HELD", payload={"amount": amount, "txn_id": txn})
        return hold

    def release_escrow(self, order: Order, hold: EscrowHold, *, provider_id: str, gross: int,
                       commission_rate: float | None = None, triggered_by: str = "CUSTOMER_APPROVAL",
                       approver: str | None = None) -> dict[str, Any]:
        rate = commission_rate if commission_rate is not None else settings.platform_commission
        commission = money(gross * rate)
        insurance = money(gross * 0.006)                    # حق بیمه گروهی
        tax = money(gross * settings.vat_rate)               # مالیات ارزش افزوده (نمونه)
        net = gross - commission - insurance - tax
        provider_acc = self.wallet_account_code("PROVIDER", provider_id)
        txn = self.post(ref_type="order", ref_id=order.id, memo=f"آزادسازی سفارش {order.code}",
                        entries=[
                            ("2200-ESCROW-LIABILITY", "DEBIT", gross, "کاهش بدهی نگهداشت"),
                            (provider_acc, "CREDIT", net, "سهم خالص متخصص"),
                            ("4100-COMMISSION-REVENUE", "CREDIT", commission, f"کمیسیون {int(rate*100)}٪"),
                            ("2500-PAYABLE-INSURER", "CREDIT", insurance, "حق بیمه گروهی"),
                            ("2400-PAYABLE-TAX", "CREDIT", tax, "مالیات ارزش افزوده"),
                        ])
        hold.released_amount += gross
        hold.status = "RELEASED" if hold.released_amount + hold.refunded_amount >= hold.amount else "PARTIAL"
        order.escrow_state = "RELEASED_TO_PROVIDER"
        self.db.add(EscrowRelease(hold_id=hold.id, amount=gross, kind="RELEASE_TO_PROVIDER",
                                  triggered_by=triggered_by, approved_by=approver))
        p = self.db.get(ProviderProfile, provider_id)
        if p:
            p.completion_count += 1
        self.db.flush()
        self._sync_wallet("PROVIDER", provider_id)
        timeline(self.db, order.id, "ESCROW_RELEASED",
                 payload={"gross": gross, "commission": commission, "net": net, "txn_id": txn})
        events.emit(self.db, topic="payment", event_type="payment.settled", aggregate_id=order.id,
                    payload={"gross": gross, "net": net, "provider_id": provider_id})
        return {"txn_id": txn, "gross": gross, "commission": commission, "insurance": insurance,
                "tax": tax, "net_to_provider": net, "provider_wallet": self.balance(provider_acc)}

    def refund(self, order: Order, hold: EscrowHold, amount: int, reason_code: str,
               approver: str | None = None) -> dict[str, Any]:
        customer_acc = self.wallet_account_code("USER", order.customer_id)
        txn = self.post(ref_type="order", ref_id=order.id, memo=f"بازگشت وجه {order.code}",
                        entries=[("2200-ESCROW-LIABILITY", "DEBIT", amount, "کاهش بدهی نگهداشت"),
                                 (customer_acc, "CREDIT", amount, f"بازگشت به کیف پول — {reason_code}")])
        hold.refunded_amount += amount
        hold.status = "REFUNDED" if hold.refunded_amount + hold.released_amount >= hold.amount else "PARTIAL"
        order.escrow_state = "PARTIALLY_REFUNDED" if hold.status == "PARTIAL" else "REFUNDED"
        order.payment_state = "REFUNDED"
        self.db.add(EscrowRelease(hold_id=hold.id, amount=amount, kind="REFUND",
                                  triggered_by="SUPPORT_DECISION", approved_by=approver))
        self.db.flush()
        self._sync_wallet("USER", order.customer_id)
        timeline(self.db, order.id, "REFUNDED", payload={"amount": amount, "reason": reason_code, "txn_id": txn})
        return {"txn_id": txn, "refunded": amount, "customer_wallet": self.balance(customer_acc)}

    def _sync_wallet(self, owner_type: str, owner_id: str) -> Wallet:
        code = self.wallet_account_code(owner_type, owner_id)
        bal = self.balance(code)
        w = self.db.execute(select(Wallet).where(Wallet.owner_type == owner_type,
                                                 Wallet.owner_id == owner_id)).scalar_one_or_none()
        if not w:
            w = Wallet(owner_type=owner_type, owner_id=owner_id, balance_cached=bal)
            self.db.add(w)
        else:
            w.balance_cached = bal
        self.db.flush()
        return w


# ====================================================================== reviews
DIMENSIONS = ("PUNCTUALITY", "QUALITY", "TIDINESS", "PRICE_ACCURACY", "CONDUCT")
DIMENSION_WEIGHTS = {"PUNCTUALITY": 0.25, "QUALITY": 0.30, "TIDINESS": 0.15,
                     "PRICE_ACCURACY": 0.15, "CONDUCT": 0.15}


class ReviewService:
    def __init__(self, db: Session):
        self.db = db

    def create(self, order: Order, customer_id: str, dimensions: dict[str, int], text: str | None) -> Review:
        missing = [d for d in DIMENSIONS if d not in dimensions]
        if missing:
            raise ValueError("MISSING_DIMENSIONS:" + ",".join(missing))
        for d, v in dimensions.items():
            if d not in DIMENSIONS:
                raise ValueError(f"UNKNOWN_DIMENSION:{d}")
            if not 1 <= int(v) <= 5:
                raise ValueError(f"INVALID_SCORE:{d}")
        overall = sum(DIMENSION_WEIGHTS[d] * int(dimensions[d]) for d in DIMENSIONS)
        review = Review(order_id=order.id, customer_id=customer_id,
                        provider_id=order.assigned_provider_id or "", text=text, overall=round(overall, 3))
        self.db.add(review)
        self.db.flush()
        for d, v in dimensions.items():
            self.db.add(ReviewDimension(review_id=review.id, dimension=d, score=int(v)))
        self._recompute_rating(order.assigned_provider_id)
        timeline(self.db, order.id, "REVIEWED", actor_id=customer_id,
                 payload={"overall": review.overall, "dimensions": dimensions})
        return review

    def _recompute_rating(self, provider_id: str | None) -> None:
        if not provider_id:
            return
        p = self.db.get(ProviderProfile, provider_id)
        if not p:
            return
        rows = self.db.execute(select(Review).where(Review.provider_id == provider_id)).scalars().all()
        if not rows:
            return
        n = len(rows)
        avg = sum(r.overall for r in rows) / n
        prior, m = 3.9, 10                      # فرمول بیزی: جلوگیری از سوءاستفاده با نمونه کم
        p.rating_avg = round((n / (n + m)) * avg + (m / (n + m)) * prior, 3)
        p.rating_count = n


# ================================================================= events/audit
class EventService:
    def emit(self, db: Session, *, topic: str, event_type: str, aggregate_id: str | None,
             payload: dict[str, Any], correlation_id: str | None = None) -> str:
        ev = EventOutbox(topic=topic, event_type=event_type, aggregate_id=aggregate_id,
                         payload=payload, correlation_id=correlation_id)
        db.add(ev)
        db.flush()
        return ev.id


events = EventService()


def timeline(db: Session, order_id: str, event_code: str, *, actor_id: str | None = None,
             payload: dict[str, Any] | None = None) -> None:
    db.add(OrderTimeline(order_id=order_id, event_code=event_code, actor_id=actor_id,
                         payload=payload or {}))


def audit(db: Session, *, actor: Any, action: str, resource_type: str, resource_id: str | None,
          reason: str | None = None, before: dict | None = None, after: dict | None = None) -> None:
    db.add(AuditLog(actor_id=getattr(actor, "id", None),
                    actor_role=",".join(getattr(actor, "role_list", []) or []),
                    action=action, resource_type=resource_type, resource_id=resource_id,
                    reason=reason, before_data=before, after_data=after))
