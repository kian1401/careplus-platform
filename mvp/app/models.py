# -*- coding: utf-8 -*-
"""
مدل داده MVP — زیرمجموعه‌ای اجرایی از ۱۱۳ جدول سند (بخش ۸).
هدف: پوشش کامل چرخه «سفارش → تخصیص → اجرا → پول → کیفیت» با حداقل جداول لازم.
خوشه‌ها: identity, catalog, ord, dispatch, pay, trust, platform
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, Index, text
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .core import Base, new_id, now


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


# =============================================================== identity
class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    phone: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    phone_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    full_name: Mapped[str | None] = mapped_column(String(120))
    roles: Mapped[str] = mapped_column(String(200), default="CUSTOMER")   # CUSTOMER,PROVIDER,OPS,FINANCE
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    org_id: Mapped[str | None] = mapped_column(String(36), index=True)     # حساب سازمانی (B2B)
    created_at: Mapped[datetime]

    offers: Mapped[list["JobOffer"]] = relationship(back_populates="provider", foreign_keys="JobOffer.provider_id")

    @property
    def role_list(self) -> list[str]:
        return [r for r in (self.roles or "").split(",") if r]


class OtpChallenge(Base):
    __tablename__ = "otp_challenges"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    phone: Mapped[str] = mapped_column(String(20), index=True)
    code_hash: Mapped[str] = mapped_column(String(128))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProviderProfile(Base, TimestampMixin):
    __tablename__ = "provider_profiles"
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120))
    bio: Mapped[str | None] = mapped_column(Text)
    verified_level: Mapped[int] = mapped_column(Integer, default=2)      # L0..L4
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")    # ACTIVE|OFFLINE|SUSPENDED
    tier: Mapped[str] = mapped_column(String(20), default="BASE")
    is_online: Mapped[bool] = mapped_column(Boolean, default=False)
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    zone_id: Mapped[str | None] = mapped_column(String(36), index=True)
    skills: Mapped[str] = mapped_column(String(400), default="")         # csv: plumbing.basic,...
    equipment: Mapped[str] = mapped_column(String(400), default="")
    rating_avg: Mapped[float] = mapped_column(Float, default=0.0)
    rating_count: Mapped[int] = mapped_column(Integer, default=0)
    acceptance_rate: Mapped[float] = mapped_column(Float, default=0.75)
    on_time_rate: Mapped[float] = mapped_column(Float, default=0.9)
    cancel_rate: Mapped[float] = mapped_column(Float, default=0.03)
    completion_count: Mapped[int] = mapped_column(Integer, default=0)
    commission_rate: Mapped[float] = mapped_column(Float, default=0.15)
    capacity_per_day: Mapped[int] = mapped_column(Integer, default=3)
    accepted_today: Mapped[int] = mapped_column(Integer, default=0)
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_offer_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_rejections: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime]

    @property
    def skill_set(self) -> set[str]:
        return {s.strip() for s in (self.skills or "").split(",") if s.strip()}

    @property
    def equipment_set(self) -> set[str]:
        return {s.strip() for s in (self.equipment or "").split(",") if s.strip()}


# ================================================================ catalog
class ServiceCategory(Base):
    __tablename__ = "service_categories"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    slug: Mapped[str] = mapped_column(String(60), unique=True)
    title_fa: Mapped[str] = mapped_column(String(120))
    warranty_days: Mapped[int] = mapped_column(Integer, default=30)
    insurance_required: Mapped[bool] = mapped_column(Boolean, default=True)


class ServiceItem(Base):
    __tablename__ = "service_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    category_id: Mapped[str] = mapped_column(String(36), ForeignKey("service_categories.id"), index=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    title_fa: Mapped[str] = mapped_column(String(160))
    pricing_model: Mapped[str] = mapped_column(String(20), default="BAND")  # FIXED|BAND|INSPECTION|HOURLY
    base_price: Mapped[int] = mapped_column(Integer)                         # ریال
    min_price: Mapped[int] = mapped_column(Integer)
    max_price: Mapped[int] = mapped_column(Integer)
    required_skills: Mapped[str] = mapped_column(String(300), default="")
    required_equipment: Mapped[str] = mapped_column(String(300), default="")
    duration_minutes: Mapped[int] = mapped_column(Integer, default=60)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    @property
    def skill_set(self) -> set[str]:
        return {s.strip() for s in (self.required_skills or "").split(",") if s.strip()}

    @property
    def equipment_set(self) -> set[str]:
        return {s.strip() for s in (self.required_equipment or "").split(",") if s.strip()}


class RateCard(Base):
    __tablename__ = "rate_cards"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    version: Mapped[str] = mapped_column(String(20), default="v1")
    zone_id: Mapped[str | None] = mapped_column(String(36), index=True)
    org_id: Mapped[str | None] = mapped_column(String(36), index=True)
    service_item_id: Mapped[str] = mapped_column(String(36), ForeignKey("service_items.id"), index=True)
    base_price: Mapped[int] = mapped_column(Integer)
    travel_fee_per_km: Mapped[int] = mapped_column(Integer, default=12000)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ZoneDemandMetric(Base):
    """عرضه/تقاضا در پنجره زمانی برای محاسبه Surge (در عملیات: سری‌زمانی ۵ دقیقه‌ای)."""
    __tablename__ = "zone_demand_metrics"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    zone_id: Mapped[str] = mapped_column(String(36), index=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    orders_open: Mapped[int] = mapped_column(Integer, default=0)
    providers_online: Mapped[int] = mapped_column(Integer, default=0)
    surge_override: Mapped[float | None] = mapped_column(Float)


class PriceQuote(Base):
    __tablename__ = "price_quotes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    service_item_id: Mapped[str] = mapped_column(String(36), ForeignKey("service_items.id"))
    zone_id: Mapped[str | None] = mapped_column(String(36))
    urgency: Mapped[str] = mapped_column(String(20), default="SAME_DAY")
    min_amount: Mapped[int] = mapped_column(Integer)
    max_amount: Mapped[int] = mapped_column(Integer)
    final_amount: Mapped[int | None] = mapped_column(Integer)
    breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    engine_version: Mapped[str] = mapped_column(String(20), default="pricing-v1")
    surge: Mapped[float] = mapped_column(Float, default=1.0)
    confidence: Mapped[float] = mapped_column(Float, default=0.7)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PriceQuoteRevision(Base):
    """Change Order — تغییر قیمت وسط کار با تأیید مشتری."""
    __tablename__ = "price_quote_revisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(String(36), ForeignKey("orders.id"), index=True)
    reason_code: Mapped[str] = mapped_column(String(40))
    old_amount: Mapped[int] = mapped_column(Integer)
    new_amount: Mapped[int] = mapped_column(Integer)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


# ==================================================================== ord
class Order(Base, TimestampMixin):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    customer_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    org_id: Mapped[str | None] = mapped_column(String(36), index=True)
    service_item_id: Mapped[str] = mapped_column(String(36), ForeignKey("service_items.id"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="CREATED", index=True)
    urgency: Mapped[str] = mapped_column(String(20), default="SAME_DAY")
    severity: Mapped[int] = mapped_column(Integer, default=2)
    skills_required: Mapped[str] = mapped_column(String(300), default="")
    equipment_required: Mapped[str] = mapped_column(String(300), default="")
    zone_id: Mapped[str | None] = mapped_column(String(36), index=True)
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    address_text: Mapped[str | None] = mapped_column(String(200))
    slot_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    slot_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    intake_text: Mapped[str | None] = mapped_column(Text)
    intake_media_count: Mapped[int] = mapped_column(Integer, default=0)
    price_quote_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("price_quotes.id"))
    final_amount: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="IRR")
    payment_state: Mapped[str] = mapped_column(String(20), default="UNPAID")
    escrow_state: Mapped[str] = mapped_column(String(24), default="PENDING_PAYMENT")
    assigned_provider_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    booking_id: Mapped[str | None] = mapped_column(String(36))
    dispatch_wave: Mapped[int] = mapped_column(Integer, default=0)
    dispatch_radius_m: Mapped[int] = mapped_column(Integer, default=4000)
    risk_band: Mapped[str] = mapped_column(String(10), default="LOW")
    created_at: Mapped[datetime]
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approve_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OrderItem(Base):
    __tablename__ = "order_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(String(36), ForeignKey("orders.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))          # SERVICE|MATERIAL|TRAVEL|DISCOUNT|TAX
    title: Mapped[str] = mapped_column(String(160))
    qty: Mapped[float] = mapped_column(Float, default=1)
    unit_price: Mapped[int] = mapped_column(Integer)
    total: Mapped[int] = mapped_column(Integer)
    taxable: Mapped[bool] = mapped_column(Boolean, default=True)


class OrderMedia(Base):
    __tablename__ = "order_media"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(String(36), ForeignKey("orders.id"), index=True)
    kind: Mapped[str] = mapped_column(String(10))          # IMAGE|VIDEO|AUDIO|DOC
    file_ref: Mapped[str] = mapped_column(String(200))
    ai_tags: Mapped[list] = mapped_column(JSON, default=list)
    transcript: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class OrderChecklist(Base):
    __tablename__ = "order_checklists"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(String(36), ForeignKey("orders.id"), index=True)
    phase: Mapped[str] = mapped_column(String(10))         # BEFORE|AFTER
    item_code: Mapped[str] = mapped_column(String(40))
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    photo_required: Mapped[bool] = mapped_column(Boolean, default=True)
    photo_ref: Mapped[str | None] = mapped_column(String(200))
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    done_by: Mapped[str | None] = mapped_column(String(36))


class OrderTimeline(Base, TimestampMixin):
    __tablename__ = "order_timeline"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(String(36), ForeignKey("orders.id"), index=True)
    event_code: Mapped[str] = mapped_column(String(60))
    actor_id: Mapped[str | None] = mapped_column(String(36))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime]


class Booking(Base):
    __tablename__ = "bookings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(String(36), ForeignKey("orders.id"), unique=True)
    provider_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    slot_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    slot_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    check_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    check_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    check_in_lat: Mapped[float | None] = mapped_column(Float)
    check_in_lng: Mapped[float | None] = mapped_column(Float)
    minutes_worked: Mapped[int | None] = mapped_column(Integer)


# =============================================================== dispatch
class DispatchRequest(Base):
    __tablename__ = "dispatch_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(String(36), ForeignKey("orders.id"), index=True)
    mode: Mapped[str] = mapped_column(String(16), default="NORMAL")
    wave_no: Mapped[int] = mapped_column(Integer, default=1)
    radius_m: Mapped[int] = mapped_column(Integer, default=4000)
    status: Mapped[str] = mapped_column(String(16), default="RUNNING")
    policy: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobOffer(Base):
    __tablename__ = "job_offers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    dispatch_id: Mapped[str] = mapped_column(String(36), ForeignKey("dispatch_requests.id"), index=True)
    order_id: Mapped[str] = mapped_column(String(36), ForeignKey("orders.id"), index=True)
    provider_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    wave_no: Mapped[int] = mapped_column(Integer, default=1)
    rank_score: Mapped[float] = mapped_column(Float)
    score_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    distance_km: Mapped[float] = mapped_column(Float, default=0)
    incentive_amount: Mapped[int] = mapped_column(Integer, default=0)
    offered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    response: Mapped[str] = mapped_column(String(12), default="PENDING")   # PENDING|ACCEPT|REJECT|EXPIRED
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    provider: Mapped[User] = relationship(back_populates="offers", foreign_keys=[provider_id])


class Assignment(Base):
    __tablename__ = "assignments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(String(36), ForeignKey("orders.id"), index=True)
    provider_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    assigned_by: Mapped[str] = mapped_column(String(16), default="AUTO")   # AUTO|MANUAL|CUSTOMER_PICK
    assigned_by_user: Mapped[str | None] = mapped_column(String(36))
    reason: Mapped[str | None] = mapped_column(String(200))
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # ایندکس یکتای جزئی: یک سفارش در هر زمان فقط یک تخصیص فعال دارد
        Index("uq_active_assignment", "order_id", unique=True,
              sqlite_where=text("released_at IS NULL"),
              postgresql_where=text("released_at IS NULL")),
    )


class AssignmentRejection(Base):
    __tablename__ = "assignment_rejections"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    offer_id: Mapped[str] = mapped_column(String(36), ForeignKey("job_offers.id"))
    provider_id: Mapped[str] = mapped_column(String(36))
    reason_code: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DispatchWeight(Base):
    """وزن‌های الگوریتم تخصیص — قابل تنظیم از پنل و خروجی فرآیند Tuning."""
    __tablename__ = "dispatch_weights"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scope: Mapped[str] = mapped_column(String(60), unique=True)     # global | zone:xxx | category:yyy
    weights: Mapped[dict] = mapped_column(JSON)
    preset_name: Mapped[str] = mapped_column(String(60), default="default")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


# =================================================================== pay
class PaymentIntent(Base):
    __tablename__ = "payment_intents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("orders.id"), index=True)
    payer_id: Mapped[str] = mapped_column(String(36), index=True)
    amount: Mapped[int] = mapped_column(Integer)
    method: Mapped[str] = mapped_column(String(20), default="WALLET")
    purpose: Mapped[str] = mapped_column(String(20), default="ORDER_PAYMENT")
    status: Mapped[str] = mapped_column(String(16), default="INIT")
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LedgerAccount(Base):
    __tablename__ = "ledger_accounts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(16))          # ASSET|LIABILITY|REVENUE|EXPENSE
    owner_type: Mapped[str] = mapped_column(String(16))    # PLATFORM|USER|PROVIDER|ORG|TAX
    owner_id: Mapped[str | None] = mapped_column(String(36), index=True)
    normal_balance: Mapped[str] = mapped_column(String(6))  # DEBIT|CREDIT


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    txn_id: Mapped[str] = mapped_column(String(36), index=True)
    account_code: Mapped[str] = mapped_column(String(40), index=True)
    direction: Mapped[str] = mapped_column(String(6))
    amount: Mapped[int] = mapped_column(Integer)
    ref_type: Mapped[str] = mapped_column(String(20))
    ref_id: Mapped[str | None] = mapped_column(String(36))
    memo: Mapped[str | None] = mapped_column(String(200))
    hash_prev: Mapped[str | None] = mapped_column(String(64))
    hash_self: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Wallet(Base, TimestampMixin):
    __tablename__ = "wallets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_type: Mapped[str] = mapped_column(String(16))
    owner_id: Mapped[str] = mapped_column(String(36), index=True)
    balance_cached: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime]

    __table_args__ = (UniqueConstraint("owner_type", "owner_id", name="uq_wallet_owner"),)


class EscrowHold(Base):
    __tablename__ = "escrow_holds"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(String(36), ForeignKey("orders.id"), index=True)
    amount: Mapped[int] = mapped_column(Integer)
    released_amount: Mapped[int] = mapped_column(Integer, default=0)
    refunded_amount: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="HELD")   # HELD|PARTIAL|RELEASED|REFUNDED|FROZEN
    held_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EscrowRelease(Base):
    __tablename__ = "escrow_releases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    hold_id: Mapped[str] = mapped_column(String(36), ForeignKey("escrow_holds.id"), index=True)
    amount: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))     # RELEASE_TO_PROVIDER|REFUND|PENALTY
    triggered_by: Mapped[str] = mapped_column(String(30))
    approved_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Refund(Base):
    __tablename__ = "refunds"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(String(36), ForeignKey("orders.id"), index=True)
    amount: Mapped[int] = mapped_column(Integer)
    reason_code: Mapped[str] = mapped_column(String(40))
    destination: Mapped[str] = mapped_column(String(10), default="WALLET")
    status: Mapped[str] = mapped_column(String(16), default="PROCESSED")
    approved_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Invoice(Base):
    __tablename__ = "invoices"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    order_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("orders.id"), index=True)
    org_id: Mapped[str | None] = mapped_column(String(36))
    subtotal: Mapped[int] = mapped_column(Integer)
    vat: Mapped[int] = mapped_column(Integer)
    total: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="ISSUED")
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


# ================================================================= trust
class Review(Base):
    __tablename__ = "reviews"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(String(36), ForeignKey("orders.id"), unique=True)
    customer_id: Mapped[str] = mapped_column(String(36))
    provider_id: Mapped[str] = mapped_column(String(36), index=True)
    text: Mapped[str | None] = mapped_column(Text)
    overall: Mapped[float] = mapped_column(Float)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ReviewDimension(Base):
    __tablename__ = "review_dimensions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    review_id: Mapped[str] = mapped_column(String(36), ForeignKey("reviews.id"), index=True)
    dimension: Mapped[str] = mapped_column(String(20))
    score: Mapped[int] = mapped_column(Integer)


class Warranty(Base):
    __tablename__ = "warranties"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(String(36), ForeignKey("orders.id"), unique=True)
    days: Mapped[int] = mapped_column(Integer, default=30)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(12), default="ACTIVE")


class Dispute(Base):
    __tablename__ = "disputes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(String(36), ForeignKey("orders.id"), index=True)
    opened_by: Mapped[str] = mapped_column(String(36))
    category: Mapped[str] = mapped_column(String(20))
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="OPEN")
    freeze_escrow: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


# ============================================================== platform
class EventOutbox(Base):
    """الگوی Outbox: رویداد در همان تراکنش دیتابیس ثبت و سپس به Kafka منتشر می‌شود."""
    __tablename__ = "event_outbox"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    topic: Mapped[str] = mapped_column(String(60), index=True)
    event_type: Mapped[str] = mapped_column(String(60))
    aggregate_id: Mapped[str | None] = mapped_column(String(36), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    correlation_id: Mapped[str | None] = mapped_column(String(40))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    actor_id: Mapped[str | None] = mapped_column(String(36))
    actor_role: Mapped[str | None] = mapped_column(String(30))
    action: Mapped[str] = mapped_column(String(60))
    resource_type: Mapped[str] = mapped_column(String(40))
    resource_id: Mapped[str | None] = mapped_column(String(36))
    reason: Mapped[str | None] = mapped_column(String(200))
    before_data: Mapped[dict | None] = mapped_column(JSON)
    after_data: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_keys"
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    scope: Mapped[str] = mapped_column(String(60))
    response: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FeatureFlag(Base):
    __tablename__ = "feature_flags"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    description: Mapped[str | None] = mapped_column(String(200))
    rollout_percent: Mapped[int] = mapped_column(Integer, default=100)
    kill_switch: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
