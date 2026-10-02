# -*- coding: utf-8 -*-
"""داده پایه MVP: دسته‌بندی، خدمات، تعرفه، متخصصان نمونه (شهر تهران) و فلگ‌ها."""
from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .core import Base, engine, geohash6, now
from .models import (FeatureFlag, ProviderProfile, RateCard, ServiceCategory, ServiceItem, User,
                     ZoneDemandMetric, DispatchWeight)
from .services import DEFAULT_WEIGHTS, LedgerService

DEMO_PHONE_CUSTOMER = "+989120000001"
DEMO_PHONE_PROVIDER = "+989120000002"
DEMO_PHONE_OPS = "+989120000009"

# مختصات نمونه در تهران (محله‌های مختلف)
PROVIDERS = [
    # (نام, tier, skills, equipment, lat, lng, rating, count, acceptance, on_time, cancel, completed)
    ("علی رضایی", "ELITE", ["plumbing.basic", "cabinet.water_damage", "pipe.replacement"],
     ["pipe_wrench", "leak_detector", "heater"], 35.7460, 51.4270, 4.87, 312, 0.86, 0.94, 0.02, 312),
    ("مهدی کریمی", "PRO", ["plumbing.basic", "sink.installation"],
     ["pipe_wrench", "leak_detector"], 35.7415, 51.4180, 4.72, 198, 0.79, 0.90, 0.03, 198),
    ("حسین صادقی", "PRO", ["plumbing.basic", "cabinet.water_damage"],
     ["pipe_wrench"], 35.7510, 51.4330, 4.61, 142, 0.74, 0.88, 0.05, 142),
    ("رضا محمدی", "BASE", ["plumbing.basic"], ["pipe_wrench"], 35.7380, 51.4120, 4.40, 61, 0.68, 0.83, 0.07, 61),
    ("سعید نجفی", "PRO", ["electrical.basic", "electrical.short_circuit"],
     ["multimeter", "insulated_tools"], 35.7455, 51.4390, 4.68, 176, 0.81, 0.91, 0.03, 176),
    ("امیر حسینی", "ELITE", ["electrical.basic", "electrical.panel"], ["multimeter", "panel_tools"],
     35.7530, 51.4155, 4.81, 265, 0.84, 0.93, 0.02, 265),
    ("کاظم فتحی", "BASE", ["locksmith.open", "door.repair"], ["lockpick_set"], 35.7435, 51.4300,
     4.55, 88, 0.72, 0.86, 0.04, 88),
    ("یاسر عطایی", "PRO", ["hvac.service", "hvac.gas_charge"], ["vacuum_pump", "manifold"],
     35.7492, 51.4215, 4.75, 203, 0.80, 0.89, 0.04, 203),
    ("بهرام قاسمی", "BASE", ["painting.wall"], ["sprayer", "scaffold"], 35.7350, 51.4265,
     4.32, 45, 0.65, 0.80, 0.08, 45),
    ("نوید اسدی", "PRO", ["carpentry.basic", "cabinet.water_damage"], ["saw_set", "clamps"],
     35.7470, 51.4100, 4.63, 154, 0.78, 0.87, 0.04, 154),
    ("فرهاد بیگی", "BASE", ["plumbing.basic", "hvac.service"], ["pipe_wrench"], 35.7560, 51.4250,
     4.28, 39, 0.61, 0.78, 0.09, 39),
    ("کامران رستمی", "PRO", ["cleaning.deep", "post_construction.clean"], ["vacuum_industrial"],
     35.7420, 51.4405, 4.70, 188, 0.83, 0.92, 0.03, 188),
]

CATEGORIES = [
    ("plumbing", "لوله‌کشی و تأسیسات آب", 30),
    ("electrical", "برقکاری", 90),
    ("hvac", "کولر و تهویه", 7),
    ("locksmith", "کلیدسازی", 14),
    ("painting", "نقاشی ساختمان", 30),
    ("carpentry", "کابینت و چوب", 30),
    ("cleaning", "نظافت", 3),
]

SERVICES = [
    # (slug, category, title, model, base, min, max, skills, equipment, duration)
    ("plumbing.leak.sink", "plumbing", "تعمیر نشت لوله زیر سینک", "BAND",
     900_000, 700_000, 2_400_000, "plumbing.basic", "req:pipe_wrench,leak_detector", 90),
    ("plumbing.clog.emergency", "plumbing", "رفع آب‌گرفتگی فوری", "BAND",
     1_200_000, 900_000, 2_800_000, "plumbing.basic", "req:pipe_wrench,flexible_rod", 90),
    ("electrical.short_circuit", "electrical", "رفع اتصال کوتاه", "BAND",
     850_000, 650_000, 2_100_000, "electrical.basic,electrical.short_circuit", "multimeter", 75),
    ("hvac.service.ac", "hvac", "سرویس کولر گازی (اسپلیت)", "FIXED",
     950_000, 950_000, 950_000, "hvac.service", "vacuum_pump", 60),
    ("locksmith.open_door", "locksmith", "باز کردن درِ قفل‌شده", "FIXED",
     750_000, 650_000, 1_400_000, "locksmith.open", "lockpick_set", 30),
    ("painting.wall.meter", "painting", "نقاشی دیوار (هر متر مربع)", "INSPECTION",
     420_000, 320_000, 780_000, "painting.wall", "sprayer", 240),
    ("carpentry.cabinet.damage", "carpentry", "ترمیم کابینت آسیب‌دیده", "BAND",
     1_600_000, 1_200_000, 4_200_000, "carpentry.basic,cabinet.water_damage", "saw_set", 180),
]

ZONES = ["zone-tehran-3", "zone-tehran-6", "zone-tehran-2"]
# ناحیه دمو: همان چیزی که موتور قیمت‌گذاری از روی مختصات سفارش می‌سازد (geohash سطح ۶)
DEMO_LAT, DEMO_LNG = 35.7448, 51.4261
DEMO_ZONE = geohash6(DEMO_LAT, DEMO_LNG)


def reset_database(db: Session) -> None:
    """پاک‌سازی کامل و ساخت دوباره داده پایه (فقط برای محیط دمو/آزمون)."""
    from . import models as m

    for table in reversed(Base.metadata.sorted_tables):
        db.execute(delete(table))
    db.flush()
    # اطمینان از وجود کاربران دمو
    seed_all(db, force_users=True)


def seed_all(db: Session, *, force_users: bool = False) -> dict:
    LedgerService(db)  # حساب‌های دفتر کل

    # ---------------------------------------------------------------- flags
    for key, desc, rollout in [
        ("dynamic_pricing", "قیمت‌گذاری پویا", 100),
        ("surge_pricing", "افزایش قیمت پیک (سقف ۱.۵)", 100),
        ("emergency_dispatch", "اعزام فوری", 100),
        ("smart_dispatch_v2", "موتور تطبیق نسخه ۲", 100),
        ("escrow_milestone", "آزادسازی مرحله‌ای پروژه‌ها", 50),
    ]:
        if not db.get(FeatureFlag, key):
            db.add(FeatureFlag(key=key, description=desc, rollout_percent=rollout))

    # ----------------------------------------------------------- categories
    cat_ids: dict[str, str] = {}
    for slug, title, warranty in CATEGORIES:
        c = db.execute(select(ServiceCategory).where(ServiceCategory.slug == slug)).scalar_one_or_none()
        if not c:
            c = ServiceCategory(slug=slug, title_fa=title, warranty_days=warranty)
            db.add(c)
            db.flush()
        cat_ids[slug] = c.id

    # -------------------------------------------------------------- services
    svc_ids: dict[str, str] = {}
    for slug, cat, title, model, base, low, high, skills, equip, dur in SERVICES:
        s = db.execute(select(ServiceItem).where(ServiceItem.slug == slug)).scalar_one_or_none()
        if not s:
            s = ServiceItem(category_id=cat_ids[cat], slug=slug, title_fa=title, pricing_model=model,
                            base_price=base, min_price=low, max_price=high, required_skills=skills,
                            required_equipment=equip, duration_minutes=dur)
            db.add(s)
            db.flush()
        svc_ids[slug] = s.id
        if not db.execute(select(RateCard).where(RateCard.service_item_id == s.id,
                                                 RateCard.zone_id == ZONES[0])).scalar_one_or_none():
            db.add(RateCard(version="v1", zone_id=ZONES[0], service_item_id=s.id, base_price=base,
                            travel_fee_per_km=12_000))

    # --------------------------------------------------------------- weights
    if not db.execute(select(DispatchWeight).where(DispatchWeight.scope == "global")).scalar_one_or_none():
        db.add(DispatchWeight(scope="global", weights=DEFAULT_WEIGHTS, preset_name="default-v1"))

    # ----------------------------------------------------------------- zones
    for z in [DEMO_ZONE, *ZONES]:
        if not db.execute(select(ZoneDemandMetric).where(ZoneDemandMetric.zone_id == z)).scalar_one_or_none():
            db.add(ZoneDemandMetric(zone_id=z, orders_open=8, providers_online=8))   # نسبت ۱ → بدون افزایش پیک

    # ------------------------------------------------------------ demo users
    customer = db.execute(select(User).where(User.phone == DEMO_PHONE_CUSTOMER)).scalar_one_or_none()
    if not customer or force_users:
        customer = customer or User(phone=DEMO_PHONE_CUSTOMER)
        customer.full_name = "سارا محمدی (مشتری دمو)"
        customer.roles = "CUSTOMER"
        customer.phone_verified = True
        db.add(customer)

    ops = db.execute(select(User).where(User.phone == DEMO_PHONE_OPS)).scalar_one_or_none()
    if not ops or force_users:
        ops = ops or User(phone=DEMO_PHONE_OPS)
        ops.full_name = "اپراتور دیسپچ (دمو)"
        ops.roles = "OPS,ADMIN,FINANCE"
        ops.phone_verified = True
        db.add(ops)
    db.flush()

    # -------------------------------------------------------------- providers
    for idx, (name, tier, skills, equip, lat, lng, rating, cnt, acc, on_time, cancel, completed) in enumerate(PROVIDERS):
        phone = f"+9891200001{idx:02d}"
        u = db.execute(select(User).where(User.phone == phone)).scalar_one_or_none()
        if not u:
            u = User(phone=phone, full_name=name, roles="CUSTOMER,PROVIDER", phone_verified=True)
            db.add(u)
            db.flush()
        p = db.get(ProviderProfile, u.id)
        if not p:
            p = ProviderProfile(user_id=u.id, display_name=name, skills=",".join(skills),
                                equipment=",".join(equip), lat=lat, lng=lng,
                                zone_id=geohash6(lat, lng), tier=tier, status="ACTIVE", is_online=True,
                                verified_level=3 if tier == "ELITE" else 2,
                                rating_avg=rating, rating_count=cnt, acceptance_rate=acc,
                                on_time_rate=on_time, cancel_rate=cancel, completion_count=completed,
                                commission_rate=0.13 if tier == "ELITE" else (0.15 if tier == "PRO" else 0.18))
            db.add(p)

    # متخصص دمو (برای سناریوها با یک شماره ثابت)
    demo_provider_user = db.execute(select(User).where(User.phone == DEMO_PHONE_PROVIDER)).scalar_one_or_none()
    if not demo_provider_user:
        demo_provider_user = User(phone=DEMO_PHONE_PROVIDER, full_name="علی رضایی (متخصص دمو)",
                                  roles="CUSTOMER,PROVIDER", phone_verified=True)
        db.add(demo_provider_user)
        db.flush()
        db.add(ProviderProfile(user_id=demo_provider_user.id, display_name="علی رضایی (متخصص دمو)",
                               skills="plumbing.basic,cabinet.water_damage,pipe.replacement",
                               equipment="pipe_wrench,leak_detector", lat=35.7460, lng=51.4270,
                               zone_id=ZONES[0], tier="ELITE", status="ACTIVE", is_online=True,
                               verified_level=3, rating_avg=4.87, rating_count=312, acceptance_rate=0.88,
                               on_time_rate=0.95, cancel_rate=0.02, completion_count=312,
                               commission_rate=0.13))
    db.flush()
    return {"customer_id": customer.id, "ops_id": ops.id, "demo_provider_id": demo_provider_user.id,
            "services": svc_ids, "categories": cat_ids,
            "providers_online": db.query(ProviderProfile).filter(ProviderProfile.is_online.is_(True)).count()}


def ensure_seed(db: Session) -> dict:
    Base.metadata.create_all(engine)
    count = db.execute(select(ServiceItem)).scalars().first()
    if count is None:
        return seed_all(db)
    return {"already_seeded": True}
