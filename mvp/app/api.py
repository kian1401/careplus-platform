# -*- coding: utf-8 -*-
"""
لایه API — معادل ساده‌شده مسیرهای سند (بخش ۹) برای اسکلت MVP.
همه پاسخ‌ها ساخت‌یافته و خطاها با `code` معنایی برگردانده می‌شوند.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .core import (Settings, aware, constant_time_eq, geohash6, get_db, hash_secret,
                   haversine_km, jload, make_token, money, new_id, now, read_token, settings)
from .models import (Assignment, AuditLog, Booking, Dispute, DispatchRequest, EscrowHold, FeatureFlag,
                     IdempotencyRecord, Invoice, JobOffer, LedgerEntry, Order, OrderChecklist, OrderItem,
                     OrderMedia, OrderTimeline, OtpChallenge, PaymentIntent, PriceQuote,
                     ProviderProfile, Review, ReviewDimension, ServiceCategory, ServiceItem, User,
                     Wallet, Warranty, ZoneDemandMetric)
from .services import (DIMENSIONS, DispatchService, LedgerService, PricingService, ReviewService,
                       audit, events, timeline)

# ==================================================================== schemas
class OtpRequest(BaseModel):
    phone: str = Field(examples=["+989121234567"])


class OtpVerify(BaseModel):
    phone: str
    code: str
    full_name: str | None = None


class ProviderRegistration(BaseModel):
    display_name: str
    skills: list[str]
    equipment: list[str] = []
    lat: float
    lng: float
    zone_id: str = "zone-tehran-3"
    tier: str = "BASE"


class QuoteRequest(BaseModel):
    service_item_id: str
    urgency: str = "SAME_DAY"
    severity: int = Field(default=2, ge=1, le=5)
    lat: float = 35.7448
    lng: float = 51.4261
    distance_km: float = 4.0
    coupon_percent: float = 0.0
    org_id: str | None = None


class CreateOrderRequest(BaseModel):
    service_item_id: str
    quote_id: str | None = None
    urgency: str = "SAME_DAY"
    severity: int = Field(default=2, ge=1, le=5)
    lat: float
    lng: float
    address_text: str | None = None
    intake_text: str | None = None
    media: list[dict] = []
    org_id: str | None = None
    slot_in_minutes: int = 120
    idempotency_key: str | None = None


class CheckInRequest(BaseModel):
    lat: float
    lng: float
    photo_ref: str = "checkin-photo.jpg"


class CheckOutRequest(BaseModel):
    photo_ref: str = "checkout-photo.jpg"
    minutes_worked: int | None = None


class RevisionApproval(BaseModel):
    approve: bool
    note: str | None = None


class OfferReject(BaseModel):
    reason_code: str = Field(examples=["TOO_FAR", "NO_TIME", "NO_SKILL", "PRICE_LOW"])


class OperatorAssign(BaseModel):
    provider_id: str
    reason: str


class RefundRequest(BaseModel):
    amount: int | None = None
    reason_code: str = "QUALITY_ISSUE"
    full: bool = False


class ReviewRequest(BaseModel):
    dimensions: dict[str, int]
    text: str | None = None


class DisputeRequest(BaseModel):
    category: str = "QUALITY"
    description: str


class WalletTopup(BaseModel):
    amount: int = Field(gt=0, le=500_000_000)


class IntakeRequest(BaseModel):
    text: str = Field(examples=["آب از زیر سینک می‌آید و کابینت خیس شده"])
    media: list[dict[str, Any]] = []
    category_hint: str | None = None
    lat: float = 35.7448
    lng: float = 51.4261
    urgency: str = "SAME_DAY"


class FlagUpdate(BaseModel):
    rollout_percent: int | None = None
    kill_switch: bool | None = None


# ================================================================ dependencies
def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(401, {"code": "AUTH_REQUIRED", "message_fa": "برای این عملیات باید وارد شوید."})
    payload = read_token(auth.split(" ", 1)[1].strip())
    if not payload:
        raise HTTPException(401, {"code": "AUTH_INVALID_TOKEN", "message_fa": "توکن نامعتبر یا منقضی است."})
    user = db.get(User, payload["sub"])
    if not user:
        raise HTTPException(401, {"code": "AUTH_USER_NOT_FOUND", "message_fa": "کاربر یافت نشد."})
    return user


OPS_DEMO_PHONE = "+989120000009"          # کاربر اپراتور دمو (برای دسترسی کنسول محلی)


def require(*roles: str):
    """محافظ نقش‌محور؛ در محیط غیرعملیاتی هدر `x-ops-token` هم پذیرفته می‌شود (کنسول دمو)."""

    def dep(request: Request = None, db: Session = Depends(get_db)) -> User:
        if (request and settings.env != "prod"
                and request.headers.get("x-ops-token") == settings.approve_token):
            ops_user = db.execute(select(User).where(User.phone == OPS_DEMO_PHONE)).scalar_one_or_none()
            if ops_user:
                return ops_user
        user = current_user(request, db)
        if not set(roles) & set(user.role_list):
            raise HTTPException(403, {"code": "FORBIDDEN", "message_fa": f"این عملیات نیازمند نقش {roles} است."})
        return user

    return dep


def idempotent(db: Session, key: str | None, scope: str, compute):
    """الگوی Idempotency: تکرار همان درخواست ⇒ همان پاسخ."""
    if not key:
        return compute(), False
    row = db.get(IdempotencyRecord, key)
    if row and aware(row.expires_at) > now():
        return row.response, True
    result = compute()
    db.add(IdempotencyRecord(key=key, scope=scope, response=result,
                             expires_at=now() + timedelta(days=7)))
    db.flush()
    return result, False


def get_order_or_404(db: Session, order_id: str) -> Order:
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, {"code": "ORDER_NOT_FOUND", "message_fa": "سفارش یافت نشد."})
    return order


# ====================================================================== routers
auth = APIRouter(prefix="/auth", tags=["Auth"])
catalog = APIRouter(prefix="/catalog", tags=["Catalog"])
orders = APIRouter(prefix="/orders", tags=["Orders"])
providers = APIRouter(prefix="/providers", tags=["Providers"])
payments = APIRouter(tags=["Payments"])
trust = APIRouter(tags=["Trust"])
ops = APIRouter(prefix="/admin", tags=["Admin/Ops"])
demo = APIRouter(prefix="/demo", tags=["Demo"])
ai = APIRouter(prefix="/ai", tags=["AI Layer"])


# ----------------------------------------------------------------------- auth
@auth.post("/otp/request")
def otp_request(payload: OtpRequest, db: Session = Depends(get_db)):
    recent = db.execute(select(func.count()).select_from(OtpChallenge)
                        .where(OtpChallenge.phone == payload.phone,
                               OtpChallenge.expires_at > now() - timedelta(minutes=1))).scalar_one()
    if recent >= 3:
        raise HTTPException(429, {"code": "AUTH_RATE_LIMITED", "message_fa": "تعداد درخواست‌ها زیاد است؛ کمی بعد تلاش کنید."})
    ch = OtpChallenge(phone=payload.phone, code_hash=hash_secret(settings.dev_otp_code),
                      expires_at=now() + timedelta(seconds=settings.otp_ttl_seconds))
    db.add(ch)
    db.flush()
    return {"challenge_id": ch.id, "expires_in": settings.otp_ttl_seconds, "attempts_left": settings.otp_max_attempts,
            "dev_hint": f"کد در محیط توسعه: {settings.dev_otp_code}"}


@auth.post("/otp/verify")
def otp_verify(payload: OtpVerify, db: Session = Depends(get_db)):
    ch = db.execute(select(OtpChallenge).where(OtpChallenge.phone == payload.phone)
                    .order_by(OtpChallenge.expires_at.desc()).limit(1)).scalar_one_or_none()
    if not ch or ch.consumed_at:
        raise HTTPException(400, {"code": "AUTH_NO_CHALLENGE", "message_fa": "درخواست کد معتبری وجود ندارد."})
    if aware(ch.expires_at) < now():
        raise HTTPException(400, {"code": "AUTH_OTP_EXPIRED", "message_fa": "کد منقضی شده است."})
    if ch.attempts >= settings.otp_max_attempts:
        raise HTTPException(429, {"code": "AUTH_OTP_TOO_MANY_ATTEMPTS", "message_fa": "تعداد تلاش‌ها بیش از حد مجاز است."})
    if not constant_time_eq(hash_secret(payload.code), ch.code_hash):
        ch.attempts += 1
        raise HTTPException(401, {"code": "AUTH_INVALID_OTP", "message_fa": "کد وارد‌شده صحیح نیست."})
    ch.consumed_at = now()
    user = db.execute(select(User).where(User.phone == payload.phone)).scalar_one_or_none()
    if not user:
        user = User(phone=payload.phone, full_name=payload.full_name or "کاربر جدید", phone_verified=True)
        db.add(user)
        db.flush()
        events.emit(db, topic="identity.user", event_type="user.registered", aggregate_id=user.id,
                    payload={"phone_masked": payload.phone[:4] + "***" + payload.phone[-4:]})
    user.phone_verified = True
    role = "CUSTOMER" if not user.roles else user.roles.split(",")[0]
    token = make_token(user.id, role)
    return {"access_token": token, "token_type": "Bearer", "expires_in": settings.access_ttl_seconds,
            "user_id": user.id, "roles": user.role_list}


@providers.post("/register", summary="تبدیل کاربر به متخصص (ساده‌شده فرآیند Onboarding سند بخش ۲.۲)")
def provider_register(payload: ProviderRegistration, user: User = Depends(current_user),
                      db: Session = Depends(get_db)):
    if db.get(ProviderProfile, user.id):
        raise HTTPException(409, {"code": "PROVIDER_ALREADY_EXISTS", "message_fa": "این کاربر قبلاً متخصص شده است."})
    prof = ProviderProfile(user_id=user.id, display_name=payload.display_name,
                           skills=",".join(payload.skills), equipment=",".join(payload.equipment),
                           lat=payload.lat, lng=payload.lng, zone_id=payload.zone_id,
                           tier=payload.tier, status="ACTIVE", is_online=True, verified_level=3
                           if payload.tier == "ELITE" else 2)
    db.add(prof)
    roles = set(user.role_list) | {"PROVIDER"}
    user.roles = ",".join(sorted(roles))
    db.flush()
    audit(db, actor=user, action="provider.onboarded", resource_type="provider", resource_id=user.id,
          after={"tier": payload.tier, "skills": payload.skills})
    return {"provider_id": user.id, "verified_level": prof.verified_level, "tier": prof.tier,
            "message_fa": "در نسخه عملیاتی: مدارک، Face-Match و سوءپیشینه پیش از فعال‌سازی الزامی است."}


@auth.get("/me", tags=["Auth"])
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    prof = db.get(ProviderProfile, user.id)
    return {"id": user.id, "phone_masked": user.phone[:4] + "***" + user.phone[-4:],
            "full_name": user.full_name, "roles": user.role_list,
            "provider": None if not prof else {"display_name": prof.display_name, "tier": prof.tier,
                                               "rating": prof.rating_avg, "online": prof.is_online}}


# -------------------------------------------------------------------- catalog
@catalog.get("/categories")
def list_categories(db: Session = Depends(get_db)):
    rows = db.execute(select(ServiceCategory)).scalars().all()
    return {"items": [{"id": c.id, "slug": c.slug, "title_fa": c.title_fa,
                       "warranty_days": c.warranty_days} for c in rows]}


@catalog.get("/services")
def list_services(q: str | None = None, category_id: str | None = None, db: Session = Depends(get_db)):
    stmt = select(ServiceItem).where(ServiceItem.active.is_(True))
    if category_id:
        stmt = stmt.where(ServiceItem.category_id == category_id)
    if q:
        stmt = stmt.where(ServiceItem.title_fa.contains(q) | ServiceItem.slug.contains(q))
    rows = db.execute(stmt).scalars().all()
    return {"items": [{"id": s.id, "slug": s.slug, "title_fa": s.title_fa, "pricing_model": s.pricing_model,
                       "base_price": s.base_price, "min_price": s.min_price, "max_price": s.max_price,
                       "required_skills": s.required_skills.split(",") if s.required_skills else [],
                       "required_equipment": s.required_equipment.split(",") if s.required_equipment else [],
                       "duration_minutes": s.duration_minutes} for s in rows]}


@catalog.get("/zones/{zone_id}/surge")
def zone_surge(zone_id: str, db: Session = Depends(get_db)):
    svc = PricingService(db)
    metric = db.execute(select(ZoneDemandMetric).where(ZoneDemandMetric.zone_id == zone_id)
                        .order_by(ZoneDemandMetric.window_start.desc()).limit(1)).scalar_one_or_none()
    return {"zone_id": zone_id, "surge": svc.surge_for_zone(zone_id),
            "orders_open": metric.orders_open if metric else 0,
            "providers_online": metric.providers_online if metric else 0,
            "cap": 1.5}


# -------------------------------------------------------------------- pricing
@payments.post("/pricing/quote")
def pricing_quote(payload: QuoteRequest, db: Session = Depends(get_db)):
    item = db.get(ServiceItem, payload.service_item_id)
    if not item:
        raise HTTPException(404, {"code": "SERVICE_NOT_FOUND", "message_fa": "خدمت یافت نشد."})
    zone = geohash6(payload.lat, payload.lng)
    svc = PricingService(db)
    return svc.quote(item=item, urgency=payload.urgency, zone_id=zone, org_id=payload.org_id,
                     severity=payload.severity, distance_km=payload.distance_km,
                     coupon_percent=payload.coupon_percent,
                     emergency_service=payload.urgency == "EMERGENCY")


@orders.post("/{order_id}/change-order")
def change_order(order_id: str, payload: dict = Body(...), user: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    order = get_order_or_404(db, order_id)
    if user.id not in (order.customer_id, order.assigned_provider_id):
        raise HTTPException(403, {"code": "FORBIDDEN", "message_fa": "شما به این سفارش دسترسی ندارید."})
    svc = PricingService(db)
    rev = svc.revise(order, reason_code=payload.get("reason_code", "SCOPE_CHANGE"),
                     new_amount=int(payload["new_amount"]), actor_id=user.id)
    timeline(db, order.id, "CHANGE_ORDER_REQUESTED", actor_id=user.id,
             payload={"new_amount": rev.new_amount, "reason": rev.reason_code})
    return {"revision_id": rev.id, "old_amount": rev.old_amount, "new_amount": rev.new_amount,
            "status": "PENDING_CUSTOMER_APPROVAL",
            "message_fa": "مبلغ اضافه فقط پس از تأیید صریح مشتری از Escrow آزاد می‌شود."}


@orders.post("/{order_id}/change-order/{revision_id}/approve")
def approve_change_order(order_id: str, revision_id: str, payload: RevisionApproval,
                         user: User = Depends(current_user), db: Session = Depends(get_db)):
    order = get_order_or_404(db, order_id)
    if user.id != order.customer_id:
        raise HTTPException(403, {"code": "ONLY_CUSTOMER_CAN_APPROVE", "message_fa": "تأیید تغییر قیمت فقط توسط مشتری."})
    from .models import PriceQuoteRevision
    rev = db.get(PriceQuoteRevision, revision_id)
    if not rev or rev.order_id != order.id:
        raise HTTPException(404, {"code": "REVISION_NOT_FOUND", "message_fa": "درخواست تغییر یافت نشد."})
    PricingService(db).approve_revision(rev, order, payload.approve)
    timeline(db, order.id, "CHANGE_ORDER_DECIDED", actor_id=user.id,
             payload={"approved": payload.approve, "amount": rev.new_amount})
    return {"approved": rev.approved, "final_amount": order.final_amount}


# --------------------------------------------------------------------- orders
@orders.post("", summary="ثبت سفارش (با Idempotency-Key)")
def create_order(payload: CreateOrderRequest, request: Request, user: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    idem = payload.idempotency_key or request.headers.get("idempotency-key")

    def compute() -> dict[str, Any]:
        item = db.get(ServiceItem, payload.service_item_id)
        if not item:
            raise HTTPException(404, {"code": "SERVICE_NOT_FOUND", "message_fa": "خدمت یافت نشد."})
        zone = geohash6(payload.lat, payload.lng)
        svc = PricingService(db)
        quote = db.get(PriceQuote, payload.quote_id) if payload.quote_id else None
        if not quote:
            q = svc.quote(item=item, urgency=payload.urgency, zone_id=zone, org_id=payload.org_id,
                          severity=payload.severity)
            quote = db.get(PriceQuote, q["quote_id"])

        count = db.execute(select(func.count()).select_from(Order)).scalar_one() + 1
        start = now() + timedelta(minutes=payload.slot_in_minutes)
        order = Order(code=f"SR-{now().strftime('%y%m')}-{count:05d}", customer_id=user.id,
                      org_id=payload.org_id, service_item_id=item.id, status="CREATED",
                      urgency=payload.urgency, severity=payload.severity,
                      skills_required=item.required_skills, equipment_required=item.required_equipment,
                      zone_id=zone, lat=payload.lat, lng=payload.lng, address_text=payload.address_text,
                      intake_text=payload.intake_text, intake_media_count=len(payload.media or []),
                      slot_start=start, slot_end=start + timedelta(minutes=item.duration_minutes),
                      price_quote_id=quote.id, final_amount=quote.final_amount)
        db.add(order)
        db.flush()
        order_items = [
            OrderItem(order_id=order.id, kind="SERVICE", title=item.title_fa, qty=1,
                      unit_price=quote.final_amount or 0, total=quote.final_amount or 0),
            OrderItem(order_id=order.id, kind="TRAVEL", title="ایاب و ذهاب", qty=1, unit_price=0, total=0),
        ]
        db.add_all(order_items)
        for m in payload.media or []:
            db.add(OrderMedia(order_id=order.id, kind=m.get("kind", "IMAGE"),
                              file_ref=m.get("file_ref", "s3://bucket/demo.jpg"),
                              ai_tags=m.get("ai_tags", []), transcript=m.get("transcript")))
        db.add_all([
            OrderChecklist(order_id=order.id, phase="BEFORE", item_code="photo_before",
                           photo_required=True),
            OrderChecklist(order_id=order.id, phase="AFTER", item_code="photo_after",
                           photo_required=True),
        ])
        timeline(db, order.id, "CREATED", actor_id=user.id,
                 payload={"urgency": payload.urgency, "amount": order.final_amount, "zone": zone})
        events.emit(db, topic="order.lifecycle", event_type="order.created", aggregate_id=order.id,
                    payload={"code": order.code, "amount": order.final_amount, "urgency": payload.urgency})
        return {"order_id": order.id, "code": order.code, "status": order.status,
                "final_amount": order.final_amount, "currency": "IRR", "zone_id": zone,
                "next_step": "پرداخت/نگهداشت Escrow سپس submit-for-dispatch"}

    result, replayed = idempotent(db, idem, "order.create", compute)
    return {**result, "idempotent_replay": replayed}


@orders.get("")
def list_orders(status: str | None = None, mine: bool = True, limit: int = Query(20, le=100),
                user: User = Depends(current_user), db: Session = Depends(get_db)):
    stmt = select(Order).order_by(Order.created_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(Order.status == status)
    rows = db.execute(stmt).scalars().all()
    if mine and "OPS" not in user.role_list and "ADMIN" not in user.role_list:
        rows = [o for o in rows if o.customer_id == user.id or o.assigned_provider_id == user.id]
    return {"items": [order_brief(o) for o in rows]}


def order_brief(o: Order) -> dict[str, Any]:
    return {"id": o.id, "code": o.code, "status": o.status, "urgency": o.urgency,
            "final_amount": o.final_amount, "payment_state": o.payment_state,
            "escrow_state": o.escrow_state, "assigned_provider_id": o.assigned_provider_id,
            "booking_id": o.booking_id, "wave": o.dispatch_wave,
            "created_at": o.created_at.isoformat()}


@orders.get("/{order_id}")
def order_detail(order_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    order = get_order_or_404(db, order_id)
    items = db.execute(select(OrderItem).where(OrderItem.order_id == order.id)).scalars().all()
    media = db.execute(select(OrderMedia).where(OrderMedia.order_id == order.id)).scalars().all()
    tl = db.execute(select(OrderTimeline).where(OrderTimeline.order_id == order.id)
                    .order_by(OrderTimeline.created_at.asc())).scalars().all()
    hold = db.execute(select(EscrowHold).where(EscrowHold.order_id == order.id)).scalar_one_or_none()
    provider = db.get(ProviderProfile, order.assigned_provider_id) if order.assigned_provider_id else None
    return {
        "order": {**order_brief(order), "lat": order.lat, "lng": order.lng, "zone_id": order.zone_id,
                  "address_text": order.address_text, "severity": order.severity,
                  "slot_start": order.slot_start.isoformat() if order.slot_start else None,
                  "intake_text": order.intake_text, "media_count": order.intake_media_count,
                  "skills_required": order.skills_required, "equipment_required": order.equipment_required},
        "items": [{"kind": i.kind, "title": i.title, "total": i.total} for i in items],
        "media": [{"kind": m.kind, "tags": m.ai_tags, "transcript": m.transcript} for m in media],
        "timeline": [{"event": t.event_code, "at": t.created_at.isoformat(), "payload": t.payload} for t in tl],
        "provider": None if not provider else {"id": provider.user_id, "name": provider.display_name,
                                               "tier": provider.tier, "rating": provider.rating_avg,
                                               "lat": provider.lat, "lng": provider.lng},
        "escrow": None if not hold else {"amount": hold.amount, "released": hold.released_amount,
                                        "refunded": hold.refunded_amount, "status": hold.status},
    }


@orders.get("/{order_id}/tracking")
def order_tracking(order_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    order = get_order_or_404(db, order_id)
    if not order.assigned_provider_id:
        return {"order_id": order.id, "status": order.status, "provider": None,
                "message_fa": "در حال یافتن متخصص…"}
    p = db.get(ProviderProfile, order.assigned_provider_id)
    dist = haversine_km(p.lat or order.lat, p.lng or order.lng, order.lat, order.lng)
    eta = max(3, int(dist / 22 * 60)) if order.status in ("ASSIGNED",) else 0
    return {"order_id": order.id, "status": order.status, "stage_fa": {
        "CREATED": "ایجاد‌شده", "DISPATCHING": "در حال تخصیص", "ASSIGNED": "در راه",
        "IN_PROGRESS": "در حال اجرا", "COMPLETED": "پایان‌یافته", "CLOSED": "بسته‌شده"}.get(order.status, order.status),
        "provider": {"id": p.user_id, "name": p.display_name, "rating": p.rating_avg,
                     "distance_km": round(dist, 2), "lat": p.lat, "lng": p.lng},
        "eta_minutes": eta, "updated_at": now().isoformat()}


@orders.post("/{order_id}/pay")
def pay_order(order_id: str, method: str = "WALLET", user: User = Depends(current_user),
              db: Session = Depends(get_db)):
    order = get_order_or_404(db, order_id)
    if order.customer_id != user.id:
        raise HTTPException(403, {"code": "ONLY_CUSTOMER_CAN_PAY", "message_fa": "پرداخت فقط توسط مشتری سفارش."})
    ledger = LedgerService(db)
    hold = db.execute(select(EscrowHold).where(EscrowHold.order_id == order.id)).scalar_one_or_none()
    if hold and hold.status in ("HELD", "PARTIAL"):
        return {"order_id": order.id, "escrow": {"status": hold.status, "amount": hold.amount},
                "message_fa": "پیش‌تر پرداخت شده است."}
    amount = order.final_amount or 0
    wallet = ledger._sync_wallet("USER", user.id)
    if method == "WALLET" and ledger.balance(ledger.wallet_account_code("USER", user.id)) < amount:
        raise HTTPException(402, {"code": "INSUFFICIENT_WALLET_BALANCE",
                                  "message_fa": "موجودی کیف پول کافی نیست؛ ابتدا شارژ کنید.",
                                  "needed": amount})
    if method != "WALLET":                       # شبکه پرداخت (دمو): ورود وجه از درگاه
        ledger.post(ref_type="topup", ref_id=user.id, memo="پرداخت آنلاین (دمو)",
                    entries=[("1100-CASH", "DEBIT", amount, "ورود وجه از درگاه"),
                             (ledger.wallet_account_code("USER", user.id), "CREDIT", amount, "شارژ موقت")])
    hold = ledger.hold_escrow(order, amount)
    intent = PaymentIntent(order_id=order.id, payer_id=user.id, amount=amount, method=method,
                           purpose="ORDER_PAYMENT", status="SUCCEEDED")
    db.add(intent)
    db.flush()
    return {"order_id": order.id, "payment_intent_id": intent.id, "escrow":
            {"hold_id": hold.id, "amount": hold.amount, "status": hold.status},
            "message_fa": "مبلغ در حساب واسط بلوکه شد و پس از تأیید کار آزاد می‌شود."}


@orders.post("/{order_id}/submit-for-dispatch")
def submit_for_dispatch(order_id: str, mode: str | None = None, user: User = Depends(current_user),
                        db: Session = Depends(get_db)):
    order = get_order_or_404(db, order_id)
    hold = db.execute(select(EscrowHold).where(EscrowHold.order_id == order.id)).scalar_one_or_none()
    if not hold or hold.status not in ("HELD", "PARTIAL"):
        raise HTTPException(402, {"code": "ESCROW_NOT_HELD",
                                  "message_fa": "پیش از تخصیص، باید مبلغ در حساب واسط بلوکه شود."})
    if order.status not in ("CREATED",):
        raise HTTPException(409, {"code": "ORDER_NOT_DISPATCHABLE", "message_fa": f"وضعیت سفارش {order.status} است."})
    mode = mode or ("EMERGENCY" if order.urgency == "EMERGENCY" else "NORMAL")
    result = DispatchService(db).start(order, mode=mode,
                                      radius_m=6000 if mode == "EMERGENCY" else None)
    order.dispatch_wave = result["wave"]
    return {"order_id": order.id, **result}


@orders.post("/{order_id}/dispatch/advance")
def advance_dispatch(order_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    order = get_order_or_404(db, order_id)
    result = DispatchService(db).advance(order)
    if "wave" in result:
        order.dispatch_wave = result["wave"]
        order.dispatch_radius_m = result["radius_m"]
    return {"order_id": order.id, **result}


@orders.get("/{order_id}/offers")
def order_offers(order_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    order = get_order_or_404(db, order_id)
    rows = db.execute(select(JobOffer).where(JobOffer.order_id == order.id)
                      .order_by(JobOffer.offered_at.desc())).scalars().all()
    return {"items": [{"offer_id": o.id, "provider_id": o.provider_id, "wave": o.wave_no,
                       "score": o.rank_score, "distance_km": o.distance_km,
                       "incentive": o.incentive_amount, "response": o.response,
                       "breakdown": o.score_breakdown,
                       "expires_at": o.expires_at.isoformat()} for o in rows]}


@orders.post("/{order_id}/complete")
def complete_order(order_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    order = get_order_or_404(db, order_id)
    if user.id != order.assigned_provider_id:
        raise HTTPException(403, {"code": "ONLY_ASSIGNED_PROVIDER", "message_fa": "فقط متخصص تخصیص‌یافته."})
    checklist = db.execute(select(OrderChecklist).where(OrderChecklist.order_id == order.id,
                                                       OrderChecklist.required.is_(True))).scalars().all()
    missing = [c.item_code for c in checklist if not c.photo_ref]
    if missing:
        raise HTTPException(422, {"code": "CHECKLIST_PHOTO_REQUIRED",
                                  "message_fa": "پیش از اتمام کار، عکس‌های اجباری (قبل/بعد) ثبت شود.",
                                  "missing": missing})
    order.status = "COMPLETED"
    order.completed_at = now()
    order.approve_deadline = now() + timedelta(hours=settings.escrow_hold_hours)
    order.escrow_state = "PARTIALLY_RELEASED" if order.escrow_state == "HELD_IN_ESCROW" else order.escrow_state
    item = db.get(ServiceItem, order.service_item_id)
    cat = db.get(ServiceCategory, item.category_id) if item else None
    days = cat.warranty_days if cat else 30
    if not db.execute(select(Warranty).where(Warranty.order_id == order.id)).scalar_one_or_none():
        db.add(Warranty(order_id=order.id, days=days, expires_at=now() + timedelta(days=days)))
    timeline(db, order.id, "COMPLETED", actor_id=user.id, payload={"minutes": None})
    events.emit(db, topic="order.lifecycle", event_type="order.completed", aggregate_id=order.id,
                payload={"provider_id": user.id})
    return {"order_id": order.id, "status": order.status,
            "approve_deadline": order.approve_deadline.isoformat(),
            "warranty_days": days,
            "message_fa": f"کار تمام شد. پنجره تأیید/اعتراض {settings.escrow_hold_hours} ساعت است."}


@orders.post("/{order_id}/approve")
def approve_order(order_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    order = get_order_or_404(db, order_id)
    if user.id != order.customer_id:
        raise HTTPException(403, {"code": "ONLY_CUSTOMER_CAN_APPROVE", "message_fa": "تأیید کار فقط توسط مشتری."})
    if order.status not in ("COMPLETED", "QUALITY_WINDOW"):
        raise HTTPException(409, {"code": "ORDER_NOT_COMPLETED", "message_fa": f"وضعیت فعلی: {order.status}"})
    hold = db.execute(select(EscrowHold).where(EscrowHold.order_id == order.id)).scalar_one_or_none()
    if not hold:
        raise HTTPException(409, {"code": "ESCROW_NOT_FOUND", "message_fa": "نگهداشت مالی برای این سفارش نیست."})
    res = LedgerService(db).release_escrow(order, hold, provider_id=order.assigned_provider_id or "",
                                          gross=min(order.final_amount or 0, hold.amount),
                                          triggered_by="CUSTOMER_APPROVAL", approver=user.id)
    order.status = "CLOSED"
    db.add(Invoice(number=f"INV-{order.code}", order_id=order.id, org_id=order.org_id,
                   subtotal=(order.final_amount or 0) - money((order.final_amount or 0) * settings.vat_rate),
                   vat=money((order.final_amount or 0) * settings.vat_rate),
                   total=order.final_amount or 0))
    timeline(db, order.id, "CLOSED", actor_id=user.id, payload=res)
    return {"order_id": order.id, "status": order.status, **res}


@orders.post("/{order_id}/dispute")
def open_dispute(order_id: str, payload: DisputeRequest, user: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    order = get_order_or_404(db, order_id)
    if user.id not in (order.customer_id, order.assigned_provider_id):
        raise HTTPException(403, {"code": "FORBIDDEN", "message_fa": "دسترسی ندارید."})
    d = Dispute(order_id=order.id, opened_by=user.id, category=payload.category,
                description=payload.description)
    db.add(d)
    hold = db.execute(select(EscrowHold).where(EscrowHold.order_id == order.id)).scalar_one_or_none()
    if hold and hold.status in ("HELD", "PARTIAL"):
        hold.status = "FROZEN"
    order.status = "DISPUTED"
    order.escrow_state = "DISPUTED"
    db.flush()
    timeline(db, order.id, "DISPUTE_OPENED", actor_id=user.id, payload={"category": payload.category})
    events.emit(db, topic="trust", event_type="dispute.opened", aggregate_id=order.id,
                payload={"dispute_id": d.id, "category": payload.category})
    return {"dispute_id": d.id, "escrow_frozen": True,
            "message_fa": "آزادسازی وجه متوقف شد؛ کارشناس کیفیت حداکثر یک روز کاری بررسی می‌کند."}


# ------------------------------------------------------------------ providers
@providers.get("/me/offers")
def my_offers(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(JobOffer).where(JobOffer.provider_id == user.id,
                                             JobOffer.response == "PENDING")
                      .order_by(JobOffer.offered_at.desc())).scalars().all()
    out = []
    for o in rows:
        order = db.get(Order, o.order_id)
        out.append({"offer_id": o.id, "order_code": order.code, "order_id": order.id,
                    "wave": o.wave_no, "score": o.rank_score, "breakdown": o.score_breakdown,
                    "distance_km": o.distance_km, "amount": order.final_amount,
                    "incentive": o.incentive_amount, "urgency": order.urgency,
                    "expires_at": o.expires_at.isoformat(),
                    "expired": aware(o.expires_at) < now(),
                    "why_me_fa": explain_offer(o)})
    return {"items": out}


def explain_offer(o: JobOffer) -> str:
    b = o.score_breakdown or {}
    top = sorted(b.items(), key=lambda kv: kv[1], reverse=True)[:2]
    names = {"distance": "نزدیکی", "quality": "کیفیت", "acceptance": "نرخ قبولی", "reliability": "پایبندی",
             "equipment": "تجهیزات", "price": "قیمت", "history": "سابقه مشتری", "fairness": "عدالت توزیع",
             "retention": "نگهداشت"}
    parts = [f"{names.get(k, k)} {v:.2f}" for k, v in top]
    return "این پیشنهاد به دلیل " + " و ".join(parts) + f" برای شما ارسال شد (امتیاز کل {o.rank_score:.2f})."


@providers.post("/me/offers/{offer_id}/accept")
def accept_offer(offer_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    offer = db.get(JobOffer, offer_id)
    if not offer or offer.provider_id != user.id:
        raise HTTPException(404, {"code": "OFFER_NOT_FOUND", "message_fa": "پیشنهاد یافت نشد."})
    order = get_order_or_404(db, offer.order_id)
    res = DispatchService(db).accept(order, offer, user.id)
    if "error" in res:
        raise HTTPException(409, {"code": res["error"],
                                  "message_fa": {"OFFER_EXPIRED": "پیشنهاد منقضی شده است.",
                                                 "OFFER_NOT_PENDING": "این پیشنهاد قبلاً پاسخ داده شده است.",
                                                 "ORDER_ALREADY_ASSIGNED": "سفارش به متخصص دیگری تخصیص یافته است."}
                                  .get(res["error"], "امکان پذیرش نیست."), **res})
    return res


@providers.post("/me/offers/{offer_id}/reject")
def reject_offer(offer_id: str, payload: OfferReject, user: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    offer = db.get(JobOffer, offer_id)
    if not offer or offer.provider_id != user.id:
        raise HTTPException(404, {"code": "OFFER_NOT_FOUND", "message_fa": "پیشنهاد یافت نشد."})
    order = get_order_or_404(db, offer.order_id)
    return DispatchService(db).reject(order, offer, user.id, payload.reason_code)


@providers.post("/me/online")
def go_online(lat: float, lng: float, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = db.get(ProviderProfile, user.id)
    if not p:
        raise HTTPException(404, {"code": "PROVIDER_NOT_FOUND", "message_fa": "ابتدا پروفایل متخصص بسازید."})
    p.is_online = True
    p.lat, p.lng, p.zone_id = lat, lng, geohash6(lat, lng)
    p.status = "ACTIVE"
    return {"online": True, "zone_id": p.zone_id}


@providers.post("/me/offline")
def go_offline(user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = db.get(ProviderProfile, user.id)
    if p:
        p.is_online = False
    return {"online": False}


@providers.put("/me/skills")
def update_skills(skills: list[str] = Body(...), equipment: list[str] = Body([]),
                  user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = db.get(ProviderProfile, user.id)
    if not p:
        raise HTTPException(404, {"code": "PROVIDER_NOT_FOUND", "message_fa": "پروفایل متخصص یافت نشد."})
    p.skills = ",".join(skills)
    p.equipment = ",".join(equipment)
    return {"skills": skills, "equipment": equipment}


@providers.post("/bookings/{booking_id}/check-in")
def check_in(booking_id: str, payload: CheckInRequest, user: User = Depends(current_user),
             db: Session = Depends(get_db)):
    b = db.get(Booking, booking_id)
    if not b or b.provider_id != user.id:
        raise HTTPException(404, {"code": "BOOKING_NOT_FOUND", "message_fa": "رزرو یافت نشد."})
    order = get_order_or_404(db, b.order_id)
    b.check_in_at = now()
    b.check_in_lat, b.check_in_lng = payload.lat, payload.lng
    order.status = "IN_PROGRESS"
    checklist = db.execute(select(OrderChecklist).where(OrderChecklist.order_id == order.id,
                                                       OrderChecklist.phase == "BEFORE")).scalars().all()
    for c in checklist:
        c.photo_ref, c.done_at, c.done_by = payload.photo_ref, now(), user.id
    p = db.get(ProviderProfile, user.id)
    if p:
        p.lat, p.lng = payload.lat, payload.lng
    timeline(db, order.id, "CHECKED_IN", actor_id=user.id, payload={"photo": payload.photo_ref})
    return {"booking_id": b.id, "order_status": order.status, "checked_in_at": b.check_in_at.isoformat()}


@providers.post("/bookings/{booking_id}/check-out")
def check_out(booking_id: str, payload: CheckOutRequest, user: User = Depends(current_user),
              db: Session = Depends(get_db)):
    b = db.get(Booking, booking_id)
    if not b or b.provider_id != user.id:
        raise HTTPException(404, {"code": "BOOKING_NOT_FOUND", "message_fa": "رزرو یافت نشد."})
    order = get_order_or_404(db, b.order_id)
    b.check_out_at = now()
    b.minutes_worked = payload.minutes_worked or int(
        ((b.check_out_at - b.check_in_at).total_seconds() / 60) if b.check_in_at else 60)
    checklist = db.execute(select(OrderChecklist).where(OrderChecklist.order_id == order.id,
                                                       OrderChecklist.phase == "AFTER")).scalars().all()
    for c in checklist:
        c.photo_ref, c.done_at, c.done_by = payload.photo_ref, now(), user.id
    timeline(db, order.id, "CHECKED_OUT", actor_id=user.id,
             payload={"minutes": b.minutes_worked, "photo": payload.photo_ref})
    return {"booking_id": b.id, "minutes_worked": b.minutes_worked,
            "message_fa": "برای اتمام کار، درخواست complete را ارسال کنید."}


@providers.get("/me/dashboard")
def provider_dashboard(days: int = Query(30, le=365), user: User = Depends(current_user),
                       db: Session = Depends(get_db)):
    p = db.get(ProviderProfile, user.id)
    if not p:
        raise HTTPException(404, {"code": "PROVIDER_NOT_FOUND", "message_fa": "پروفایل متخصص یافت نشد."})
    ledger = LedgerService(db)
    wallet = ledger.balance(ledger.wallet_account_code("PROVIDER", user.id))
    since = now() - timedelta(days=days)
    rows = db.execute(select(LedgerEntry).where(
        LedgerEntry.account_code == ledger.wallet_account_code("PROVIDER", user.id),
        LedgerEntry.direction == "CREDIT", LedgerEntry.created_at >= since)).scalars().all()
    earnings = sum(e.amount for e in rows)
    heat = [{"weekday": d, "hour": h, "demand_index": round(0.4 + 0.6 * abs(((h - 18) / 8)), 2)}
            for d in range(7) for h in (12, 15, 18, 21)]
    return {"provider_id": user.id, "display_name": p.display_name, "tier": p.tier,
            "rating": p.rating_avg, "rating_count": p.rating_count,
            "online": p.is_online, "acceptance_rate": p.acceptance_rate, "on_time_rate": p.on_time_rate,
            "cancel_rate": p.cancel_rate, "completed": p.completion_count,
            "wallet_balance": wallet, "earnings_window": earnings, "window_days": days,
            "heatmap": heat,
            "advice_fa": ["اگر دوشنبه‌ها ۱۸ تا ۲۱ فعال باشی، حدود ۲۴٪ پیشنهاد بیشتر می‌گیری.",
                          "افزودن مهارت دوم، تعداد پیشنهادهای واجد شرط را ۱۹٪ افزایش می‌دهد."]}


@providers.get("/{provider_id}/public-profile")
def provider_public(provider_id: str, db: Session = Depends(get_db)):
    p = db.get(ProviderProfile, provider_id)
    if not p:
        raise HTTPException(404, {"code": "PROVIDER_NOT_FOUND", "message_fa": "متخصص یافت نشد."})
    reviews = db.execute(select(Review).where(Review.provider_id == provider_id)
                         .order_by(Review.published_at.desc()).limit(5)).scalars().all()
    dims: dict[str, list[int]] = {}
    for r in reviews:
        for d in db.execute(select(ReviewDimension).where(ReviewDimension.review_id == r.id)).scalars():
            dims.setdefault(d.dimension, []).append(d.score)
    return {"provider_id": provider_id, "display_name": p.display_name, "tier": p.tier,
            "verified_level": p.verified_level, "rating": p.rating_avg, "rating_count": p.rating_count,
            "skills": list(p.skill_set), "equipment": list(p.equipment_set),
            "completed": p.completion_count,
            "dimension_averages": {k: round(sum(v) / len(v), 2) for k, v in dims.items()},
            "recent_reviews": [{"text": r.text, "overall": r.overall,
                                "at": r.published_at.isoformat()} for r in reviews]}


# ------------------------------------------------------------------ payments
@payments.get("/wallets/me")
def my_wallet(user: User = Depends(current_user), db: Session = Depends(get_db)):
    ledger = LedgerService(db)
    code = ledger.wallet_account_code("USER", user.id)
    balance = ledger.balance(code)
    holds = db.execute(select(EscrowHold).join(Order, Order.id == EscrowHold.order_id)
                       .where(Order.customer_id == user.id, EscrowHold.status.in_(("HELD", "PARTIAL", "FROZEN")))
                       ).scalars().all()
    return {"balance": balance, "currency": "IRR",
            "escrow_locked": sum(h.amount - h.released_amount - h.refunded_amount for h in holds),
            "escrow_holds": [{"order_id": h.order_id, "amount": h.amount, "status": h.status} for h in holds]}


@payments.post("/wallets/me/topup")
def wallet_topup(payload: WalletTopup, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return LedgerService(db).topup(user.id, payload.amount)


@payments.get("/wallets/me/transactions")
def wallet_txns(user: User = Depends(current_user), db: Session = Depends(get_db)):
    ledger = LedgerService(db)
    code = ledger.wallet_account_code("USER", user.id)
    rows = db.execute(select(LedgerEntry).where(LedgerEntry.account_code == code)
                      .order_by(LedgerEntry.created_at.desc()).limit(50)).scalars().all()
    return {"items": [{"direction": e.direction, "amount": e.amount, "memo": e.memo,
                       "ref_type": e.ref_type, "ref_id": e.ref_id,
                       "at": e.created_at.isoformat()} for e in rows]}


@payments.get("/orders/{order_id}/escrow")
def escrow_state(order_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    order = get_order_or_404(db, order_id)
    hold = db.execute(select(EscrowHold).where(EscrowHold.order_id == order.id)).scalar_one_or_none()
    if not hold:
        return {"order_id": order.id, "status": "PENDING_PAYMENT", "amount": 0}
    return {"order_id": order.id, "escrow_state": order.escrow_state, "amount": hold.amount,
            "released": hold.released_amount, "refunded": hold.refunded_amount,
            "released_amount": hold.released_amount, "refunded_amount": hold.refunded_amount,
            "status": hold.status}


# --------------------------------------------------------------------- trust
@orders.post("/{order_id}/review")
def create_review(order_id: str, payload: ReviewRequest, user: User = Depends(current_user),
                  db: Session = Depends(get_db)):
    order = get_order_or_404(db, order_id)
    if user.id != order.customer_id:
        raise HTTPException(403, {"code": "ONLY_CUSTOMER_CAN_REVIEW", "message_fa": "ثبت نظر فقط توسط مشتری."})
    if order.status not in ("COMPLETED", "CLOSED", "QUALITY_WINDOW"):
        raise HTTPException(409, {"code": "ORDER_NOT_COMPLETED_YET", "message_fa": "نظر فقط برای سفارش اجراشده."})
    if db.execute(select(Review).where(Review.order_id == order.id)).scalar_one_or_none():
        raise HTTPException(409, {"code": "REVIEW_ALREADY_EXISTS", "message_fa": "نظر این سفارش ثبت شده است."})
    try:
        review = ReviewService(db).create(order, user.id, {k: int(v) for k, v in payload.dimensions.items()},
                                          payload.text)
    except ValueError as exc:
        raise HTTPException(422, {"code": str(exc).split(":")[0], "message_fa": str(exc),
                                  "allowed_dimensions": list(DIMENSIONS)})
    return {"review_id": review.id, "overall": review.overall,
            "weights": {"PUNCTUALITY": 0.25, "QUALITY": 0.30, "TIDINESS": 0.15,
                        "PRICE_ACCURACY": 0.15, "CONDUCT": 0.15}}


@orders.get("/{order_id}/warranty")
def warranty(order_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    order = get_order_or_404(db, order_id)
    w = db.execute(select(Warranty).where(Warranty.order_id == order.id)).scalar_one_or_none()
    if not w:
        return {"order_id": order.id, "status": "NONE",
                "message_fa": "گارانتی پس از اتمام کار فعال می‌شود."}
    return {"order_id": order.id, "status": w.status, "days": w.days,
            "expires_at": w.expires_at.isoformat()}


# --------------------------------------------------------------- ops / admin
@ops.post("/orders/{order_id}/assign")
def ops_assign(order_id: str, payload: OperatorAssign, user: User = Depends(require("OPS", "ADMIN")),
               db: Session = Depends(get_db)):
    order = get_order_or_404(db, order_id)
    res = DispatchService(db).operator_assign(order, payload.provider_id, user.id, payload.reason)
    if "error" in res:
        raise HTTPException(409, {"code": res["error"], "message_fa": "سفارش تخصیص فعال دارد."})
    return {"order_id": order.id, **res}


@ops.post("/orders/{order_id}/refund")
def ops_refund(order_id: str, payload: RefundRequest, user: User = Depends(require("FINANCE", "OPS", "ADMIN")),
               db: Session = Depends(get_db)):
    order = get_order_or_404(db, order_id)
    hold = db.execute(select(EscrowHold).where(EscrowHold.order_id == order.id)).scalar_one_or_none()
    if not hold:
        raise HTTPException(409, {"code": "ESCROW_NOT_FOUND", "message_fa": "نگهداشت مالی برای این سفارش نیست."})
    remaining = hold.amount - hold.released_amount - hold.refunded_amount
    amount = remaining if payload.full else min(payload.amount or 0, remaining)
    if amount <= 0:
        raise HTTPException(422, {"code": "INVALID_REFUND_AMOUNT", "message_fa": "مبلغ بازگشت نامعتبر است."})
    ledger = LedgerService(db)
    res = ledger.refund(order, hold, amount, payload.reason_code, approver=user.id)
    db.add(AuditLog(actor_id=user.id, actor_role=",".join(user.role_list), action="order.refund",
                    resource_type="order", resource_id=order.id, reason=payload.reason_code,
                    after_data=res))
    if hold.status == "REFUNDED":
        order.status = "CLOSED"
    return {**res, "hold_status": hold.status, "order_status": order.status}


@ops.post("/orders/{order_id}/auto-close")
def ops_auto_close(order_id: str, user: User = Depends(require("OPS", "ADMIN", "FINANCE")),
                   db: Session = Depends(get_db)):
    """شبیه‌سازی انقضای پنجره تأیید: آزادسازی خودکار به نفع متخصص."""
    order = get_order_or_404(db, order_id)
    hold = db.execute(select(EscrowHold).where(EscrowHold.order_id == order.id)).scalar_one_or_none()
    if not hold or order.status not in ("COMPLETED", "QUALITY_WINDOW"):
        raise HTTPException(409, {"code": "NOT_ELIGIBLE",
                                  "message_fa": "سفارش در پنجره تأیید نیست یا نگهداشت ندارد."})
    res = LedgerService(db).release_escrow(order, hold, provider_id=order.assigned_provider_id or "",
                                           gross=min(order.final_amount or 0, hold.amount),
                                           triggered_by="WINDOW_EXPIRED")
    order.status = "CLOSED"
    timeline(db, order.id, "AUTO_CLOSED", payload=res)
    return {"order_id": order.id, "status": order.status, **res}


@ops.get("/ledger/trial-balance")
def trial_balance(user: User = Depends(require("FINANCE", "ADMIN", "OPS")), db: Session = Depends(get_db)):
    return LedgerService(db).trial_balance()


@ops.get("/ledger/entries")
def ledger_entries(limit: int = Query(50, le=500), user: User = Depends(require("FINANCE", "ADMIN", "OPS")),
                   db: Session = Depends(get_db)):
    rows = db.execute(select(LedgerEntry).order_by(LedgerEntry.created_at.desc()).limit(limit)).scalars().all()
    return {"items": [{"txn_id": e.txn_id, "account": e.account_code, "direction": e.direction,
                       "amount": e.amount, "memo": e.memo,
                       "at": e.created_at.isoformat()} for e in rows]}


@ops.get("/events")
def list_events(topic: str | None = None, limit: int = Query(50, le=200),
                user: User = Depends(require("OPS", "ADMIN", "FINANCE")), db: Session = Depends(get_db)):
    from .models import EventOutbox
    stmt = select(EventOutbox).order_by(EventOutbox.created_at.desc()).limit(limit)
    if topic:
        stmt = stmt.where(EventOutbox.topic == topic)
    rows = db.execute(stmt).scalars().all()
    return {"items": [{"id": e.id, "topic": e.topic, "event": e.event_type,
                       "aggregate_id": e.aggregate_id, "payload": e.payload,
                       "at": e.created_at.isoformat()} for e in rows]}


@ops.get("/audit-log")
def audit_log(limit: int = Query(50, le=200), user: User = Depends(require("ADMIN", "FINANCE")),
              db: Session = Depends(get_db)):
    rows = db.execute(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)).scalars().all()
    return {"items": [{"actor_role": a.actor_role, "action": a.action, "resource_type": a.resource_type,
                       "resource_id": a.resource_id, "reason": a.reason,
                       "at": a.created_at.isoformat()} for a in rows]}


@ops.get("/flags")
def flags(db: Session = Depends(get_db)):
    rows = db.execute(select(FeatureFlag)).scalars().all()
    return {"items": [{"key": f.key, "rollout_percent": f.rollout_percent,
                       "kill_switch": f.kill_switch, "description": f.description} for f in rows]}


@ops.post("/flags/{key}")
def set_flag(key: str, payload: FlagUpdate, user: User = Depends(require("ADMIN")),
             db: Session = Depends(get_db)):
    f = db.get(FeatureFlag, key)
    if not f:
        raise HTTPException(404, {"code": "FLAG_NOT_FOUND", "message_fa": "فلگ یافت نشد."})
    before = {"rollout_percent": f.rollout_percent, "kill_switch": f.kill_switch}
    if payload.rollout_percent is not None:
        f.rollout_percent = payload.rollout_percent
    if payload.kill_switch is not None:
        f.kill_switch = payload.kill_switch
    db.flush()
    audit(db, actor=user, action="flag.updated", resource_type="feature_flag", resource_id=key,
          before=before, after={"rollout_percent": f.rollout_percent, "kill_switch": f.kill_switch})
    return {"key": f.key, "rollout_percent": f.rollout_percent, "kill_switch": f.kill_switch}


@ops.get("/kpi")
def kpi(zone_id: str | None = None, db: Session = Depends(get_db),
        _: User = Depends(require("OPS", "ADMIN", "FINANCE"))):
    total = db.execute(select(func.count()).select_from(Order)).scalar_one()
    closed = db.execute(select(func.count()).select_from(Order).where(Order.status == "CLOSED")).scalar_one()
    disputed = db.execute(select(func.count()).select_from(Order).where(Order.status == "DISPUTED")).scalar_one()
    gmv = db.execute(select(func.coalesce(func.sum(Order.final_amount), 0))
                     .where(Order.status.in_(("COMPLETED", "CLOSED")))).scalar_one()
    tta_rows = db.execute(select(OrderTimeline.created_at, OrderTimeline.order_id)
                          .where(OrderTimeline.event_code == "ASSIGNED")).all()
    dispatched = db.execute(select(OrderTimeline.order_id, OrderTimeline.created_at)
                            .where(OrderTimeline.event_code == "CREATED")).all()
    d_map = {oid: at for oid, at in dispatched}
    ttas = [abs((at - d_map[oid]).total_seconds()) for at, oid in tta_rows if oid in d_map]
    ttas.sort()
    p50 = ttas[len(ttas) // 2] if ttas else None
    p95 = ttas[int(len(ttas) * 0.95) - 1] if len(ttas) > 1 else (ttas[0] if ttas else None)
    offers = db.execute(select(func.count()).select_from(JobOffer)).scalar_one()
    accepted = db.execute(select(func.count()).select_from(JobOffer).where(JobOffer.response == "ACCEPT")).scalar_one()
    return {"orders_total": total, "orders_closed": closed, "orders_disputed": disputed,
            "fulfillment_rate": round(closed / total, 3) if total else 0,
            "gmv": int(gmv), "tta_seconds_p50": p50, "tta_seconds_p95": p95,
            "offers_sent": offers, "offers_accepted": accepted,
            "acceptance_rate": round(accepted / offers, 3) if offers else 0}


# ---------------------------------------------------------------------- demo
@demo.post("/reset")
def demo_reset(db: Session = Depends(get_db)):
    from .seed import reset_database
    reset_database(db)
    return {"ok": True, "message_fa": "داده‌های دمو بازنشانی شد."}


@demo.post("/scenario")
def demo_scenario(tactic: str = Query("accept", enum=["accept", "reject_then_escalate", "no_provider"]),
                  offline_providers: int = Query(0, ge=0, le=50),
                  surge: float | None = None,
                  db: Session = Depends(get_db)):
    from .scenario import run_scenario
    return run_scenario(db, tactic=tactic, offline_providers=offline_providers, surge=surge)


@demo.get("/ops-token")
def demo_ops_token():
    """کلید عملیات اپراتور برای کنسول محلی؛ در محیط عملیاتی (prod) مسدود است."""
    if settings.env == "prod":
        raise HTTPException(403, {"code": "DISABLED_IN_PROD",
                                  "message_fa": "این مسیر در محیط عملیاتی غیرفعال است."})
    return {"x_ops_token": settings.approve_token, "env": settings.env,
            "note_fa": "فقط برای کنسول دمو؛ در محیط عملیاتی از ورود سازمانی و نقش‌ها استفاده کنید."}


@demo.get("/state")
def demo_state(db: Session = Depends(get_db)):
    from .scenario import snapshot
    return snapshot(db)


# ====================================================================== AI layer
# چهار ستون موفقیت پروژه: تطبیق هوشمند، سفارش استاندارد با AI، کنترل کیفیت پیش‌بینانه، تجربه روان.
@ai.post("/intake")
def ai_intake(payload: IntakeRequest, db: Session = Depends(get_db)):
    """تحلیل متن/رسانه مشتری → دامنه استاندارد، اقلام، مهارت/تجهیز لازم، بازه قیمت و پرسش‌های تعیین‌کننده."""
    from .intelligence import StandardizationService
    return StandardizationService(db).analyze(text=payload.text, media=payload.media,
                                              category_hint=payload.category_hint, lat=payload.lat,
                                              lng=payload.lng, urgency=payload.urgency)


@ai.get("/standards")
def ai_standards(slug: str | None = None):
    """کاتالوگ استاندارد خدمت (شفافیت کامل برای مشتری، متخصص و پیمانکار)."""
    from .intelligence import STANDARD_CATALOG
    if slug:
        std = STANDARD_CATALOG.get(slug)
        if not std:
            raise HTTPException(404, {"code": "STANDARD_NOT_FOUND", "message_fa": "استاندارد خدمت یافت نشد."})
        return {"slug": slug, **std}
    return {"count": len(STANDARD_CATALOG),
            "items": [{"slug": k, "code": v["code"], "title_fa": v["title_fa"],
                       "warranty_days": v["warranty_days"],
                       "duration_band_minutes": [v["duration_min"], v["duration_max"]]}
                      for k, v in STANDARD_CATALOG.items()]}


@ai.get("/match/preview/{order_id}")
def ai_match_preview(order_id: str, top: int = Query(5, le=20),
                     user: User = Depends(require("CUSTOMER", "PROVIDER", "OPS", "ADMIN")),
                     db: Session = Depends(get_db)):
    """رتبه‌بندی مرحله ۲: روی امتیاز ۹مؤلفه‌ای می‌نشیند و ویژگی‌های هوشمند را اضافه می‌کند (بدون تغییر موتور اصلی)."""
    from .intelligence import MatchingIntelligence
    order = get_order_or_404(db, order_id)
    return MatchingIntelligence(db).rerank(order, top_n=top)


@ai.get("/quality/forecast/{order_id}")
def ai_quality_forecast(order_id: str,
                        user: User = Depends(require("CUSTOMER", "PROVIDER", "OPS", "ADMIN")),
                        db: Session = Depends(get_db)):
    """کنترل کیفیت پیش‌بینانه: احتمال بازکار/اختلاف/تأخیر + عوامل مؤثر + مداخله‌های پیشنهادی با اثر مورد انتظار."""
    from .intelligence import PredictiveQualityService
    order = get_order_or_404(db, order_id)
    out = PredictiveQualityService(db).forecast(order)
    order.risk_band = out["risk_band"]
    timeline(db, order.id, "QUALITY_FORECAST", payload={"risk": out["risk_score"],
                                                        "band": out["risk_band"],
                                                        "interventions": [i["trigger"] for i in out["interventions"]]})
    return out


@ai.get("/assistant/{order_id}")
def ai_assistant(order_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """تجربه روان: اقدام بعدی با یک ضربه در هر وضعیت سفارش (حداقل تصمیم، حداکثر اطمینان)."""
    from .intelligence import ExperienceService
    order = get_order_or_404(db, order_id)
    return ExperienceService(db).next_actions(order)


@ai.get("/ux/friction-report")
def ai_ux_friction(user: User = Depends(require("OPS", "ADMIN")), db: Session = Depends(get_db)):
    """گزارش گلوگاه‌های تجربه کاربری بر پایه داده سفارش‌های موجود."""
    from .intelligence import ExperienceService
    return ExperienceService(db).friction_report()


@ai.get("/scorecards")
def ai_scorecards():
    """کارت مدل‌ها و حاکمیت: نسخه، ویژگی‌ها، برنامه تولید، انصاف و محدودیت‌ها (شفافیت برای ممیزی)."""
    from .intelligence import PredictiveQualityService, MatchingIntelligence, STANDARD_CATALOG, AI_VERSION
    return {
        "ai_version": AI_VERSION,
        "models": [
            {"name_fa": "استانداردسازی سفارش (Intake Standardizer)", "phase": "MVP",
             "type": "قاعده‌محور + واژه‌نامه دامنه", "output_fa": "دامنه استاندارد، اقلام، مهارت/تجهیز، بازه قیمت، پرسش‌ها",
             "catalog_size": len(STANDARD_CATALOG),
             "production_plan_fa": "جایگزینی تشخیص دسته/شدت با مدل چندوجهی (متن+تصویر) و کالیبراسیون بازه قیمت با داده واقعی"},
            {"name_fa": "رتبه‌بندی مرحله ۲ تطبیق (Re-ranker)", "phase": "MVP",
             "type": "ترکیب خطی وزن‌دار روی ویژگی‌های عملکردی",
             "weights": MatchingIntelligence.AI_WEIGHTS,
             "features_fa": ["تطابق معنایی مهارت (گراف مهارت)", "ریسک عدم‌حضور", "انصاف قیمت", "کیفیت ۹۰ روز اخیر"],
             "production_plan_fa": "Learning-to-Rank (GBDT/LambdaMART) با بازخورد پذیرش/رد/اتمام/امتیاز؛ آزمون A/B با حفظ انصاف"},
            {"name_fa": "کنترل کیفیت پیش‌بینانه (Predictive QC)", "phase": "MVP",
             "type": "کارت امتیاز شفاف (Scorecard)",
             "features": [{"key": k, "weight": w, "label_fa": lbl}
                          for k, w, lbl in PredictiveQualityService.FEATURES],
             "production_plan_fa": "GBDT کالیبره‌شده (AUC ≥ ۰.۷۸، Brier ≤ ۰.۱۰) + پایش drift ماهانه + موتور مداخله A/B"},
            {"name_fa": "اقدام بعدی و روان‌سازی تجربه (Next-Best-Action)", "phase": "MVP",
             "type": "ماشین وضعیت + قواعد اثربخشی",
             "metrics_fa": {"تکمیل سفارش": "< ۶۰ ثانیه", "رهاکردن فرم": "< ۱۵٪", "تماس وضعیت سفارش": "−۵۰٪"},
             "production_plan_fa": "سیاست یادگیری تقویتی سبک (Contextual Bandit) برای انتخاب اقدام با بیشترین اثر بر تکمیل سفارش"},
        ],
        "governance_fa": {
            "explainability": "هر خروجی همراه متن «چرا» و سهم هر ویژگی است (قابل نمایش به مشتری، متخصص و اپراتور).",
            "fairness": "ممیزی خودکار: هیچ ویژگی محافظت‌شده یا جانشین آن (کد پستی کم‌درآمد، نام، جنسیت) در رتبه‌بندی به کار نمی‌رود.",
            "human_in_the_loop": "تصمیم‌های مالی (بلوکه‌کردن گارانتی، بازرسی ویدئویی اجباری) نیازمند تأیید انسان است.",
            "rollback": "هر مدل نسخه‌دار با پرچم فعال/غیرفعال و امکان بازگشت فوری به نسخه قبلی (feature flag).",
            "data": "داده آموزشی فقط از سفارش‌های تکمیل‌شده و با حذف شناسه‌های مستقیم هویت؛ نگهداشت ۱۸ ماه برای ویژگی‌ها.",
        },
    }
