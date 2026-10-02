# -*- coding: utf-8 -*-
"""
سناریوهای اجرایی برای دموی زنده و آزمون‌های خودکار.

هر سناریو یک لیست «گام» با توضیح فارسی برمی‌گرداند که می‌تواند در کنسول نمایش داده شود:
  ۱) accept               — مسیر خوش‌بینانه: تخصیص، اجرا، آزادسازی Escrow، امتیازدهی
  ۲) reject_then_escalate — رد موج اول، تشدید خودکار (شعاع و انگیزه بیشتر)، پذیرش در موج دوم
  ۳) no_provider          — نبود متخصص واجد شرط ⇒ تشدید تا ارجاع به اپراتور و تخصیص دستی
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .core import geohash6, money, now, settings
from .models import (Assignment, Booking, EscrowHold, EventOutbox, Invoice, JobOffer, LedgerEntry,
                     Order, OrderChecklist, OrderItem, OrderMedia, OrderTimeline, ProviderProfile,
                     Review, ReviewDimension, ServiceItem, User, Warranty, ZoneDemandMetric)
from .seed import (DEMO_LAT, DEMO_LNG, DEMO_PHONE_CUSTOMER, DEMO_PHONE_OPS,
                   DEMO_PHONE_PROVIDER, ensure_seed)
from .services import DispatchService, LedgerService, PricingService, ReviewService, audit, timeline

SERVICE_SLUG = "plumbing.leak.sink"
CUSTOMER_TOPUP = 5_000_000
DIMENSIONS_DEMO = {"PUNCTUALITY": 5, "QUALITY": 5, "TIDINESS": 4, "PRICE_ACCURACY": 5, "CONDUCT": 5}


# ------------------------------------------------------------------ utilities
def _user(db: Session, phone: str) -> User:
    u = db.execute(select(User).where(User.phone == phone)).scalar_one_or_none()
    if not u:
        ensure_seed(db)
        u = db.execute(select(User).where(User.phone == phone)).scalar_one()
    return u


def _step(out: list[dict], n: int, title: str, detail: str, **data: Any) -> None:
    out.append({"step": n, "title_fa": title, "detail_fa": detail, "data": data})


def create_order_for(db: Session, *, customer: User, service_slug: str = SERVICE_SLUG,
                     urgency: str = "SAME_DAY", severity: int = 3,
                     lat: float = 35.7448, lng: float = 51.4261, slot_minutes: int = 120,
                     media: list[dict] | None = None,
                     address: str = "تهران، سعادت‌آباد، خیابان نمونه، پلاک ۱۲") -> tuple[Order, dict]:
    item = db.execute(select(ServiceItem).where(ServiceItem.slug == service_slug)).scalar_one()
    from .core import geohash6

    zone = geohash6(lat, lng)
    quote_res = PricingService(db).quote(item=item, urgency=urgency, zone_id=zone, org_id=None,
                                        severity=severity)
    seq = db.execute(select(func.count()).select_from(Order)).scalar_one() + 1
    start = now() + timedelta(minutes=slot_minutes)
    order = Order(code=f"SR-{now().strftime('%y%m')}-{seq:05d}", customer_id=customer.id,
                  org_id=customer.org_id, service_item_id=item.id, status="CREATED", urgency=urgency,
                  severity=severity, skills_required=item.required_skills,
                  equipment_required=item.required_equipment, zone_id=zone, lat=lat, lng=lng,
                  address_text=address, intake_text="آب از زیر سینک می‌آید و کابینت خیس شده",
                  intake_media_count=len(media or []), slot_start=start,
                  slot_end=start + timedelta(minutes=item.duration_minutes),
                  price_quote_id=quote_res["quote_id"], final_amount=quote_res["final_amount"])
    db.add(order)
    db.flush()
    db.add(OrderItem(order_id=order.id, kind="SERVICE", title=item.title_fa, qty=1,
                     unit_price=quote_res["final_amount"], total=quote_res["final_amount"]))
    for m in media or []:
        db.add(OrderMedia(order_id=order.id, kind=m.get("kind", "IMAGE"),
                          file_ref=m.get("file_ref", "s3://demo/leak.jpg"), ai_tags=m.get("ai_tags", []),
                          transcript=m.get("transcript")))
    db.add_all([OrderChecklist(order_id=order.id, phase="BEFORE", item_code="photo_before"),
                OrderChecklist(order_id=order.id, phase="AFTER", item_code="photo_after")])
    timeline(db, order.id, "CREATED", actor_id=customer.id,
             payload={"urgency": urgency, "amount": order.final_amount, "zone": zone,
                      "quote": quote_res})
    db.flush()
    return order, quote_res


def pay_and_hold(db: Session, order: Order, customer: User, method: str = "WALLET") -> EscrowHold:
    ledger = LedgerService(db)
    amount = order.final_amount or 0
    if ledger.balance(ledger.wallet_account_code("USER", customer.id)) < amount:
        ledger.topup(customer.id, CUSTOMER_TOPUP)
    return ledger.hold_escrow(order, amount)


def run_job(db: Session, order: Order, provider_id: str) -> None:
    """Check-in، Check-out، تکمیل چک‌لیست‌ها و اعلام اتمام کار."""
    b = db.get(Booking, order.booking_id) if order.booking_id else None
    if not b:
        b = db.execute(select(Booking).where(Booking.order_id == order.id)).scalar_one_or_none()
    if b:
        b.check_in_at = now()
        b.check_in_lat, b.check_in_lng = order.lat, order.lng
        b.check_out_at = now() + timedelta(minutes=58)
        b.minutes_worked = 58
    order.status = "IN_PROGRESS"
    for c in db.execute(select(OrderChecklist).where(OrderChecklist.order_id == order.id)).scalars().all():
        c.photo_ref = f"s3://demo/{order.code}-{c.phase.lower()}.jpg"
        c.done_at, c.done_by = now(), provider_id
    item = db.get(ServiceItem, order.service_item_id)
    from .models import ServiceCategory
    cat = db.get(ServiceCategory, item.category_id) if item else None
    days = cat.warranty_days if cat else 30
    order.status = "COMPLETED"
    order.completed_at = now()
    order.approve_deadline = now() + timedelta(hours=settings.escrow_hold_hours)
    db.add(Warranty(order_id=order.id, days=days, expires_at=now() + timedelta(days=days)))
    timeline(db, order.id, "CHECKED_IN", actor_id=provider_id)
    timeline(db, order.id, "CHECKED_OUT", actor_id=provider_id, payload={"minutes": 58})
    timeline(db, order.id, "COMPLETED", actor_id=provider_id,
             payload={"photos": "before/after ثبت شد"})
    from .services import events
    events.emit(db, topic="order.lifecycle", event_type="order.completed", aggregate_id=order.id,
                payload={"provider_id": provider_id})
    db.flush()


def approve_and_close(db: Session, order: Order, customer: User) -> dict:
    hold = db.execute(select(EscrowHold).where(EscrowHold.order_id == order.id)).scalar_one()
    provider_id = order.assigned_provider_id or ""
    p = db.get(ProviderProfile, provider_id)
    res = LedgerService(db).release_escrow(order, hold,
                                           provider_id=provider_id,
                                           gross=min(order.final_amount or 0, hold.amount),
                                           commission_rate=p.commission_rate if p else None,
                                           triggered_by="CUSTOMER_APPROVAL", approver=customer.id)
    order.status = "CLOSED"
    db.add(Invoice(number=f"INV-{order.code}", order_id=order.id,
                   subtotal=(order.final_amount or 0) - money((order.final_amount or 0) * settings.vat_rate),
                   vat=money((order.final_amount or 0) * settings.vat_rate),
                   total=order.final_amount or 0))
    timeline(db, order.id, "CLOSED", actor_id=customer.id, payload=res)
    db.flush()
    return res


def submit_review(db: Session, order: Order, customer: User, dims: dict[str, int] | None = None) -> Review:
    review = ReviewService(db).create(order, customer.id, dims or DIMENSIONS_DEMO,
                                      "کار سریع، مرتب و با قیمت توافق‌شده انجام شد.")
    db.flush()
    return review


# ------------------------------------------------------------------ scenarios
def run_scenario(db: Session, *, tactic: str = "accept", offline_providers: int = 0,
                 surge: float | None = None) -> dict[str, Any]:
    """اجرای یک سناریوی کامل و بازگرداندن روایت گام‌به‌گام + تصویر وضعیت."""
    ensure_seed(db)
    steps: list[dict] = []
    customer = _user(db, DEMO_PHONE_CUSTOMER)
    ops = _user(db, DEMO_PHONE_OPS)
    dispatcher = DispatchService(db)

    # ۰) پیش‌تنظیم صحنه بر پایه تاکتیک
    if tactic == "no_provider":
        rows = db.execute(select(ProviderProfile).where(ProviderProfile.is_online.is_(True))).scalars().all()
        for p in rows:
            p.is_online = False
        db.flush()
        _step(steps, 0, "شبیه‌سازی کمبود عرضه",
              f"همه {len(rows)} متخصص فعال آفلاین شدند تا سناریوی «بدون متخصص واجد شرط» ساخته شود.",
              offline=len(rows))
    elif offline_providers:
        rows = db.execute(select(ProviderProfile).where(ProviderProfile.is_online.is_(True))
                          .limit(offline_providers)).scalars().all()
        for p in rows:
            p.is_online = False
        db.flush()
        _step(steps, 0, "تنظیم صحنه", f"{len(rows)} متخصص برای شبیه‌سازی کمبود عرضه آفلاین شدند.",
              offline=len(rows))

    # ۱) شارژ کیف پول مشتری (در صورت نیاز)
    ledger = LedgerService(db)
    wallet = ledger._sync_wallet("USER", customer.id)
    if wallet.balance_cached < CUSTOMER_TOPUP // 2:
        top = ledger.topup(customer.id, CUSTOMER_TOPUP)
        _step(steps, 1, "شارژ کیف پول مشتری",
              f"{CUSTOMER_TOPUP:,} ریال به کیف پول مشتری دمو اضافه شد (دفتر کل: بدهکار به نقد، بستانکار به کیف پول).",
              txn_id=top["txn_id"], balance=top["wallet_balance"])

    # ۲) تنظیم ضریب تقاضای منطقه (پیش از قیمت‌گذاری)
    if surge is not None:
        zone = geohash6(DEMO_LAT, DEMO_LNG)
        z = db.execute(select(ZoneDemandMetric).where(ZoneDemandMetric.zone_id == zone)
                       .order_by(ZoneDemandMetric.window_start.desc()).limit(1)).scalar_one_or_none()
        if z is None:
            z = ZoneDemandMetric(zone_id=zone, orders_open=8, providers_online=8)
            db.add(z)
        z.surge_override = surge
        db.flush()
        _step(steps, len(steps) + 1, "اعمال ضریب تقاضا (Surge)",
              f"ضریب تقاضای ناحیه {zone} روی ×{surge} تنظیم شد (بازه مجاز ۰.۹۵ تا ۱.۵؛ "
              f"خدمات امدادی معاف‌اند).", zone=zone, surge=surge)

    # ۳) ثبت سفارش + برآورد قیمت شفاف
    order, quote_payload = create_order_for(
        db, customer=customer, urgency="SAME_DAY", severity=3,
        media=[{"kind": "IMAGE", "file_ref": "s3://demo/leak-1.jpg",
                "ai_tags": ["water_leak", "under_sink"]},
               {"kind": "AUDIO", "file_ref": "s3://demo/voice.m4a",
                "transcript": "آب از زیر سینک می‌آید"}])
    _step(steps, 2, "ثبت سفارش و برآورد قیمت",
          f"سفارش {order.code} با مبلغ {order.final_amount:,} ریال ثبت شد "
          f"(ضریب تقاضای منطقه ×{quote_payload.get('surge', 1.0)}، اطمینان مدل "
          f"{quote_payload.get('confidence', 0)}, بازه اعلام‌شده "
          f"{quote_payload.get('min_amount', 0):,} تا {quote_payload.get('max_amount', 0):,}).",
          order_id=order.id, order_code=order.code, amount=order.final_amount, quote=quote_payload)

    # ۳) پرداخت و نگهداشت Escrow
    hold = pay_and_hold(db, order, customer)
    _step(steps, len(steps) + 1, "پرداخت و نگهداشت (Escrow)",
          f"{hold.amount:,} ریال از کیف پول مشتری کسر و در حساب واسط (۲۲۰۰) بلوکه شد؛ "
          f"دفتر کل متوازن: {ledger.trial_balance()['balanced']}.",
          hold_id=hold.id, escrow_amount=hold.amount, oracle="بدهکار = بستانکار")

    # ۵) ورود به صف تخصیص (موج ۱)
    mode = "EMERGENCY" if order.urgency == "EMERGENCY" else "NORMAL"
    res = dispatcher.start(order, mode=mode, radius_m=6000 if mode == "EMERGENCY" else 4000)
    order.dispatch_wave = res["wave"]
    order.dispatch_radius_m = res["radius_m"]
    db.flush()
    _step(steps, len(steps) + 1, f"موج {res['wave']} تخصیص",
          f"{res['candidates_scored']} متخصص واجد شرط ارزیابی و {len(res['offers'])} پیشنهاد هم‌زمان "
          f"ارسال شد (شعاع {res['radius_m']} متر).",
          wave=res["wave"], radius_m=res["radius_m"], candidates=res["candidates_scored"],
          offers=res["offers"])

    escalated = False
    if tactic == "no_provider":
        for _ in range(dispatcher.MAX_WAVES):
            adv = dispatcher.advance(order)
            if adv.get("escalated_to_ops"):
                escalated = True
                break
            if adv.get("offers"):
                break
        _step(steps, len(steps) + 1, "نردبان تشدید کامل شد",
              "در هر سه موج (با شعاع فزاینده و انگیزه مالی) هیچ متخصص واجد شرایطی یافت نشد؛ "
              "سفارش برای تخصیص دستی به صف اپراتور ارجاع شد.",
              escalated=escalated, waves=order.dispatch_wave)
        p_demo = db.get(ProviderProfile, _user(db, DEMO_PHONE_PROVIDER).id)
        dispatcher.operator_assign(order, p_demo.user_id, ops.id, "تخصیص دستی: کمبود عرضه در منطقه")
        audit(db, actor=ops, action="order.assign.manual", resource_type="order",
              resource_id=order.id, reason="کمبود عرضه در منطقه")
        db.flush()
        _step(steps, len(steps) + 1, "تخصیص دستی اپراتور",
              f"اپراتور «{ops.full_name}» سفارش را به «{p_demo.display_name}» سپرد؛ "
              f"دلیل و شناسه اپراتور در audit_log ثبت شد.",
              provider_id=p_demo.user_id, provider_name=p_demo.display_name)

    elif tactic == "reject_then_escalate":
        rejected = 0
        for o in res["offers"]:
            offer = db.get(JobOffer, o["offer_id"])
            if offer:
                dispatcher.reject(order, offer, offer.provider_id, "NO_TIME")
                rejected += 1
        db.flush()
        _step(steps, len(steps) + 1, "رد پیشنهادهای موج اول",
              f"{rejected} متخصص پیشنهاد را با دلیل NO_TIME رد کردند (دلیل‌ها برای یادگیری الگوریتم ذخیره شد).",
              rejected=rejected)
        adv = dispatcher.advance(order)
        if "offers" in adv:
            order.dispatch_wave = adv["wave"]
            order.dispatch_radius_m = adv["radius_m"]
            db.flush()
            inc = adv["offers"][0]["incentive"] if adv["offers"] else 0
            _step(steps, len(steps) + 1, f"تشدید خودکار → موج {adv['wave']}",
                  f"شعاع جست‌وجو به {adv['radius_m']} متر افزایش یافت و انگیزه مالی {inc:,} ریال "
                  f"برای این موج فعال شد.",
                  wave=adv["wave"], radius_m=adv["radius_m"], incentive=inc, offers=adv["offers"])
            res = {"offers": adv["offers"]}
        else:
            res = {"offers": []}
        offer = _first_pending(db, order)
        if offer is None:                     # تور ایمنی: ادامه نردبان تشدید تا یافتن متخصص
            for _ in range(dispatcher.MAX_WAVES):
                adv2 = dispatcher.advance(order)
                if adv2.get("escalated_to_ops") or adv2.get("offers"):
                    break
            offer = _first_pending(db, order)
        if offer:
            acc = dispatcher.accept(order, offer, offer.provider_id)
            db.flush()
            p = db.get(ProviderProfile, offer.provider_id)
            _step(steps, len(steps) + 1, f"پذیرش در موج {offer.wave_no}",
                  f"«{p.display_name}» با امتیاز تطبیق {offer.rank_score:.2f} و فاصله "
                  f"{offer.distance_km} کیلومتر پذیرفت؛ سایر پیشنهادها به‌صورت اتمی منقضی شدند.",
                  provider_id=p.user_id, provider_name=p.display_name, score=offer.rank_score,
                  breakdown=offer.score_breakdown, assignment_id=acc.get("assignment_id"))
        else:
            p_demo = db.get(ProviderProfile, _user(db, DEMO_PHONE_PROVIDER).id)
            dispatcher.operator_assign(order, p_demo.user_id, ops.id, "تخصیص دستی پس از رد موج‌ها")
            audit(db, actor=ops, action="order.assign.manual", resource_type="order",
                  resource_id=order.id, reason="رد شدن همه پیشنهادها در همه موج‌ها")
            db.flush()
            _step(steps, len(steps) + 1, "تخصیص دستی اپراتور",
                  f"چون هیچ متخصصی در موج‌ها نپذیرفت، اپراتور سفارش را به «{p_demo.display_name}» سپرد.",
                  provider_id=p_demo.user_id, provider_name=p_demo.display_name)

    if tactic in ("accept", "no_provider"):
        offer = _first_pending(db, order)
        if offer:
            acc = dispatcher.accept(order, offer, offer.provider_id)
            db.flush()
            p = db.get(ProviderProfile, offer.provider_id)
            _step(steps, len(steps) + 1, "پذیرش متخصص",
                  f"«{p.display_name}» با امتیاز تطبیق {offer.rank_score:.2f}، فاصله "
                  f"{offer.distance_km} کیلومتر و کیفیت {offer.score_breakdown.get('quality', 0):.2f} "
                  f"سفارش را پذیرفت؛ قفل تخصیص اتمی اعمال شد.",
                  provider_id=p.user_id, provider_name=p.display_name, score=offer.rank_score,
                  breakdown=offer.score_breakdown, assignment_id=acc.get("assignment_id"))

    provider_id = order.assigned_provider_id
    if not provider_id:
        raise RuntimeError("SCENARIO_FAILED_NO_PROVIDER")

    # ۶) اجرای کار + مستندسازی
    run_job(db, order, provider_id)
    w = db.execute(select(Warranty).where(Warranty.order_id == order.id)).scalar_one()
    _step(steps, len(steps) + 1, "اجرا و مستندسازی",
          "Check-in با GPS، عکس اجباری قبل/بعد و Check-out ثبت شد؛ "
          f"گارانتی دسته (لوله‌کشی: {w.days} روز) فعال شد.",
          status=order.status, warranty_days=w.days, minutes_worked=58)

    # ۷) تأیید مشتری، آزادسازی پول، صدور فاکتور
    rel = approve_and_close(db, order, customer)
    _step(steps, len(steps) + 1, "تأیید مشتری و آزادسازی Escrow",
          f"از {rel['gross']:,} ریال نگهداشته: کمیسیون {rel['commission']:,}، بیمه {rel['insurance']:,}، "
          f"مالیات {rel['tax']:,} و خالص {rel['net_to_provider']:,} ریال به کیف پول متخصص منتقل شد؛ "
          f"فاکتور صادر شد.",
          **rel)

    # ۸) امتیاز چندبعدی و اثر آن بر رتبه
    review = submit_review(db, order, customer)
    p = db.get(ProviderProfile, provider_id)
    _step(steps, len(steps) + 1, "امتیازدهی چندبعدی",
          f"امتیاز وزنی این سفارش {review.overall:.2f} از ۵ ثبت شد و رتبه نمایشی متخصص با فرمول بیزی به "
          f"{p.rating_avg:.3f} به‌روزرسانی شد.",
          review_id=review.id, overall=review.overall, provider_rating=p.rating_avg,
          dimensions=DIMENSIONS_DEMO)

    # ۹) کنترل سلامت مالی و رویدادها
    tb = LedgerService(db).trial_balance()
    event_count = db.execute(select(func.count()).select_from(EventOutbox)).scalar_one()
    _step(steps, len(steps) + 1, "کنترل سلامت مالی",
          f"دفتر کل دوعاملی متوازن است (بدهکار = بستانکار = {tb['total_debit']:,} ریال) و "
          f"{event_count} رویداد در جدول Outbox برای انتشار به Kafka ثبت شده است.",
          balanced=tb["balanced"], total_debit=tb["total_debit"], total_credit=tb["total_credit"],
          events=event_count)

    for i, st in enumerate(steps, start=1):      # شماره‌گذاری پیوسته گام‌ها
        st["step"] = i
    return {"tactic": tactic, "order_id": order.id, "order_code": order.code,
            "provider_id": provider_id, "escalated_to_ops": escalated,
            "steps": steps, "snapshot": snapshot(db)}


def _first_pending(db: Session, order: Order) -> JobOffer | None:
    return db.execute(select(JobOffer).where(JobOffer.order_id == order.id,
                                             JobOffer.response == "PENDING")
                      .order_by(JobOffer.rank_score.desc())).scalars().first()


# ------------------------------------------------------------------- snapshot
def snapshot(db: Session, limit: int = 12) -> dict[str, Any]:
    ledger = LedgerService(db)
    orders = db.execute(select(Order).order_by(Order.created_at.desc()).limit(limit)).scalars().all()
    holds = db.execute(select(EscrowHold)).scalars().all()
    events = db.execute(select(EventOutbox).order_by(EventOutbox.created_at.desc()).limit(15)).scalars().all()
    tb = ledger.trial_balance()
    reviews = db.execute(select(Review)).scalars().all()
    return {
        "orders": [{"code": o.code, "status": o.status, "urgency": o.urgency,
                    "amount": o.final_amount, "payment": o.payment_state, "escrow": o.escrow_state,
                    "provider_id": o.assigned_provider_id, "wave": o.dispatch_wave} for o in orders],
        "escrow": [{"order_id": h.order_id, "amount": h.amount, "released": h.released_amount,
                    "refunded": h.refunded_amount, "status": h.status} for h in holds],
        "ledger": {"balanced": tb["balanced"], "total_debit": tb["total_debit"],
                   "total_credit": tb["total_credit"], "accounts": tb["accounts"]},
        "events": [{"topic": e.topic, "event": e.event_type, "at": e.created_at.isoformat(),
                    "payload": e.payload} for e in events],
        "reviews": [{"order_id": r.order_id, "provider_id": r.provider_id, "overall": r.overall}
                    for r in reviews],
        "counts": {
            "orders": db.execute(select(func.count()).select_from(Order)).scalar_one(),
            "providers_online": db.execute(select(func.count()).select_from(ProviderProfile)
                                           .where(ProviderProfile.is_online.is_(True))).scalar_one(),
            "offers": db.execute(select(func.count()).select_from(JobOffer)).scalar_one(),
            "ledger_entries": db.execute(select(func.count()).select_from(LedgerEntry)).scalar_one(),
        },
    }
