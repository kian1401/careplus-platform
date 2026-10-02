# -*- coding: utf-8 -*-
"""
لایه هوشمندی «سرویسا» — چهار ستون موفقیت پروژه:
  ۱) استانداردسازی و سفارش با AI  →  StandardizationService (کاتالوگ استاندارد خدمت + تحلیل ورودی)
  ۲) هوشمندسازی تطبیق            →  MatchingIntelligence (مرحله ۲ رتبه‌بندی، مکمل DispatchService)
  ۳) کنترل کیفیت پیش‌بینانه       →  PredictiveQualityService (کارت امتیاز شفاف + موتور مداخله)
  ۴) تجربه روان                   →  ExperienceService (اقدام بعدی، حداقل تصمیم، حداکثر اطمینان)

اصل طراحی: هیچ‌کدام از این سرویس‌ها فرمول ۹مؤلفه‌ای تخصیص را تغییر نمی‌دهد (تا سناریوهای طلایی و
آزمون‌های رگرسیون معتبر بمانند)؛ بلکه به‌عنوان «لایه ۲» روی خروجی موتور تخصیص می‌نشیند و خروجی
توضیح‌پذیر و قابل ممیزی تولید می‌کند. در محیط عملیاتی، همین قراردادها با مدل‌های آموزش‌دیده
(LTR/GBDT + کالیبراسیون) جایگزین می‌شوند؛ ساختار ورودی/خروجی و آزمون‌ها ثابت می‌ماند.
"""
from __future__ import annotations

import math
from datetime import datetime, time
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .core import aware, clamp, haversine_km, money, now
from .models import (JobOffer, Order, OrderChecklist, OrderItem, OrderMedia, OrderTimeline,
                     PriceQuote, ProviderProfile, Review, ServiceCategory, ServiceItem, Warranty)
from .services import DispatchService, PricingService, ReviewService, timeline

AI_VERSION = "careplus-ai-1.0.0"

# =============================================================================
# ۱) کاتالوگ استاندارد خدمت (Standard Service Catalog)
#    دامنه استاندارد = عملیات اجباری + اقلام + مهارت + تجهیز حیاتی + زمان + گارانتی
#    این کاتالوگ، «زبان مشترک» مشتری، متخصص، قیمت‌گذاری، فاکتور و حل اختلاف است.
# =============================================================================
STANDARD_CATALOG: dict[str, dict[str, Any]] = {
    "plumbing.leak.sink": {
        "code": "STD-PLB-014", "title_fa": "کنترل و رفع نشت زیر سینک / کابینت",
        "scope": ["قطع آب و تخلیه فشار خط", "بازرسی اتصالات و شیلنگ‌ها",
                  "تعویض واشر/شیلنگ معیوب", "تست فشار ۱۰ دقیقه‌ای", "خشک‌کردن و ضدآب موضعی کابینت"],
        "materials": [("واشر/شیلنگ استاندارد", 180_000), ("نوار تفلون و چسب ضدآب", 90_000),
                      ("سیلیکون ضدآب", 140_000)],
        "skills": ["plumbing.basic"], "equipment": ["req:pipe_wrench", "leak_detector"],
        "duration_min": 60, "duration_max": 110, "warranty_days": 30,
        "determinants_fa": ["نشت قطره‌ای یا جریان‌دار", "دسترسی به شیر اصلی",
                            "آسیب کابینت (نمناکی/ورق‌شدگی)"],
        "safety_fa": ["بستن شیر اصلی پیش از بازکردن اتصال", "تخلیه برق موضعی در صورت نشت روی پریز"],
    },
    "plumbing.clog.emergency": {
        "code": "STD-PLB-021", "title_fa": "رفع آب‌گرفتگی فوری",
        "scope": ["بازرسی مسیر و تعیین نقطه انسداد", "لوله‌بازکنی مکانیکی/فشاری",
                  "شست‌وشوی خط", "تست تخلیه", "پاک‌سازی محل"],
        "materials": [("محلول رسوب‌زدای صنعتی", 220_000), ("کارتریج فیلتر/درپوش", 120_000)],
        "skills": ["plumbing.basic"], "equipment": ["req:pipe_wrench", "flexible_rod"],
        "duration_min": 45, "duration_max": 100, "warranty_days": 30,
        "determinants_fa": ["تعداد نقاط تخلیه درگیر", "طبقه و دسترسی به لوله اصلی",
                            "سابقه تکرار انسداد"],
        "safety_fa": ["پوشیدن دستکش و ماسک در تماس با پساب"],
    },
    "electrical.short_circuit": {
        "code": "STD-ELC-007", "title_fa": "رفع اتصال کوتاه / قطع مکرر برق",
        "scope": ["قطع کامل مدار و ایمن‌سازی", "عیب‌یابی مدار با مولتی‌متر",
                  "تعویض فیوز/کلید معیوب", "تست بار", "برچسب‌گذاری مدار اصلاح‌شده"],
        "materials": [("فیوز/کلید مینیاتوری", 320_000), ("مهار سیم و وایر", 130_000),
                      ("چسب برق حرارتی", 60_000)],
        "skills": ["electrical.basic"], "equipment": ["req:multimeter", "insulated_tools"],
        "duration_min": 50, "duration_max": 120, "warranty_days": 90,
        "determinants_fa": ["شماره مدار درگیر", "نوع ساختمان (قدیمی/نوساز)", "وجود ارت"],
        "safety_fa": ["قطع کلید اصلی پیش از هر تماس", "استفاده از ابزار عایق‌دار استاندارد"],
    },
    "hvac.service.ac": {
        "code": "STD-HVC-003", "title_fa": "سرویس کولر گازی (اسپلیت)",
        "scope": ["شست‌وشوی فیلترها", "بررسی فشار گاز", "سرویس تخلیه و لوله درین",
                  "تمیزکاری کندانسور", "تست عملکرد سرمایش"],
        "materials": [("فیلتر پاک‌کننده کویل", 150_000), ("روغن/اسپری سرویس", 110_000)],
        "skills": ["hvac.service"], "equipment": ["req:vacuum_pump", "manifold"],
        "duration_min": 45, "duration_max": 90, "warranty_days": 7,
        "determinants_fa": ["تعداد یونیت داخلی", "سابقه شارژ گاز", "دسترسی به یونیت بیرونی"],
        "safety_fa": ["تخلیه فشار پیش از بازکردن اتصال گاز"],
    },
    "locksmith.open_door": {
        "code": "STD-LCK-001", "title_fa": "باز کردن درِ قفل‌شده",
        "scope": ["احراز مالکیت (کارت ملی/سند)", "بازکردن بدون آسیب ترجیحی",
                  "تعویض سیلندر در صورت آسیب", "روغن‌کاری و تنظیم قفل", "تست کلید و قفل"],
        "materials": [("سیلندر قفل استاندارد", 1_400_000), ("روغن قفل", 80_000)],
        "skills": ["locksmith.open"], "equipment": ["req:lockpick_set"],
        "duration_min": 20, "duration_max": 60, "warranty_days": 14,
        "determinants_fa": ["نوع قفل (سوزنی/لولایی)", "در باز یا قفل‌شده", "ساعت مراجعه"],
        "safety_fa": ["ثبت مدرک مالکیت پیش از اقدام — الزام صنفی و قانونی"],
    },
    "painting.wall.meter": {
        "code": "STD-PNT-011", "title_fa": "نقاشی دیوار ساختمان (متر مربع)",
        "scope": ["بازدید و برآورد متراژ", "آماده‌سازی و پرداخت ترک‌ها", "بتونه و سنباده",
                  "پرایمر و دوبار رنگ", "پوشش و جمع‌آوری"],
        "materials": [("رنگ پایه آب", 220_000), ("بتونه و پرایمر", 90_000), ("نوار و پوشش", 60_000)],
        "skills": ["painting.wall"], "equipment": ["sprayer", "scaffold"],
        "duration_min": 180, "duration_max": 480, "warranty_days": 30,
        "determinants_fa": ["متراژ دقیق", "وضعیت ترک‌خوردگی", "رنگ تک‌لایه/دولایه"],
        "safety_fa": ["تهویه فضای بسته", "استفاده از ماسک و دستکش"],
    },
    "carpentry.cabinet.damage": {
        "code": "STD-CRP-005", "title_fa": "ترمیم کابینت آسیب‌دیده",
        "scope": ["بررسی آسیب (نمناکی/شکستگی)", "تخلیه و جداسازی قطعه",
                  "ترمیم/تعویض صفحه", "نصب مجدد و تنظیم لولا", "سیلیکون‌کاری لبه‌ها"],
        "materials": [("ورق MDF/HDF ضدانرژیه", 950_000), ("لولا و پیچ استاندارد", 180_000),
                      ("چسب چوب و سیلیکون", 150_000)],
        "skills": ["carpentry.basic"], "equipment": ["req:saw_set", "clamps"],
        "duration_min": 120, "duration_max": 300, "warranty_days": 30,
        "determinants_fa": ["متراژ آسیب‌دیده", "آسیب سطحی یا سطری", "هم‌رنگ‌شدن با کابینت موجود"],
        "safety_fa": ["تخلیه وسایل کابینت پیش از شروع"],
    },
}

# گراف مهارت: فاصله معنایی مهارت‌ها (۱ = همان مهارت، مقادیر کمتر = همسایگی حرفه‌ای)
SKILL_GRAPH: dict[str, dict[str, float]] = {
    "plumbing.basic": {"cabinet.water_damage": 0.6, "pipe.replacement": 0.85, "hvac.service": 0.25},
    "cabinet.water_damage": {"plumbing.basic": 0.6, "carpentry.basic": 0.55},
    "electrical.basic": {"electrical.short_circuit": 0.95, "electrical.panel": 0.7,
                         "hvac.service": 0.2},
    "hvac.service": {"electrical.basic": 0.2, "hvac.gas_charge": 0.8, "plumbing.basic": 0.2},
    "locksmith.open": {"door.repair": 0.7, "carpentry.basic": 0.25},
    "painting.wall": {"carpentry.basic": 0.2, "post_construction.clean": 0.3},
    "carpentry.basic": {"cabinet.water_damage": 0.55, "painting.wall": 0.2},
    "cleaning.deep": {"post_construction.clean": 0.9, "painting.wall": 0.3},
}

# واژه‌نامه تشخیص متن مشتری → خدمت استاندارد
INTAKE_KEYWORDS: dict[str, dict[str, float]] = {
    "plumbing.leak.sink": {"نشت": 2.0, "آب": 1.0, "سینک": 1.5, "شیر": 1.0, "لوله": 1.2,
                           "خیس": 1.5, "چکه": 2.0, "کابینت": 1.0},
    "plumbing.clog.emergency": {"گرفتگی": 2.5, "آبگرفتگی": 2.5, "تخلیه": 1.5, "توالت": 1.8,
                                "سینک": 0.6, "بالا": 0.8},
    "electrical.short_circuit": {"برق": 1.5, "اتصال": 1.2, "فیوز": 2.0, "کلید": 0.8,
                                 "قطع": 1.5, "جرقه": 2.2, "جریان": 1.0},
    "hvac.service.ac": {"کولر": 2.5, "اسپلیت": 2.5, "تهویه": 1.5, "سرمایش": 1.6, "گاز": 0.8},
    "locksmith.open_door": {"قفل": 2.5, "کلید": 2.0, "در": 1.5, "باز": 0.5},
    "painting.wall": {"رنگ": 2.5, "دیوار": 2.0, "کناف": 1.2, "نقاشی": 2.5},
    "carpentry.cabinet.damage": {"کابینت": 2.2, "MDF": 1.5, "لولا": 2.0, "چوب": 1.5, "درِ": 0.6},
}
SEVERITY_SIGNALS = {
    "جریان‌دار": 2.0, "فوری": 1.5, "همین الان": 1.5, "شدید": 1.5, "زیرآب": 2.0, "بوی سوختگی": 2.0,
    "جرقه": 1.5, "خشک": -0.5, "قطره‌ای": -0.5, "سطحی": -0.5, "کوچک": -0.5,
}


# =============================================================================
# ۲) استانداردسازی و سفارش با AI
# =============================================================================
class StandardizationService:
    """تبدیل درخواست مبهم و چندرسانه‌ای مشتری به «سفارش استاندارد» با دامنه، اقلام و بازه قیمت."""

    MAX_QUESTIONS = 3

    def __init__(self, db: Session):
        self.db = db

    # ---------------------------------------------------------------- helpers
    def _match_service(self, text: str, category_hint: str | None, media_tags: list[str]):
        text = (text or "").strip()
        scores: dict[str, float] = {}
        for slug, words in INTAKE_KEYWORDS.items():
            for w, wt in words.items():
                if w in text:
                    scores[slug] = scores.get(slug, 0.0) + wt
        for tag in media_tags or []:                       # برچسب‌های تحلیل تصویر/صدا (نمونه)
            for slug, words in INTAKE_KEYWORDS.items():
                for w, wt in words.items():
                    if w in str(tag):
                        scores[slug] = scores.get(slug, 0.0) + wt * 1.4
        if category_hint:
            for slug in scores:
                if slug.startswith(category_hint):
                    scores[slug] += 1.0
        if not scores:
            return None, 0.0, []
        best = max(scores, key=scores.get)
        total = sum(scores.values())
        confidence = clamp(scores[best] / total if total else 0, 0.25, 0.98)
        alternatives = sorted((s for s in scores if s != best), key=scores.get, reverse=True)[:2]
        return best, round(confidence, 2), alternatives

    def _severity(self, text: str, media_tags: list[str]) -> tuple[int, list[str]]:
        raw, drivers = 2.0, []
        for w, wt in SEVERITY_SIGNALS.items():
            if w in (text or ""):
                raw += wt
                drivers.append(f"واژه «{w}» ({'+' if wt > 0 else ''}{wt})")
        for tag in media_tags or []:
            if "water_leak" in str(tag) or "flow" in str(tag):
                raw += 0.8
                drivers.append(f"برچسب تصویر «{tag}» (+0.8)")
        sev = int(clamp(round(raw), 1, 5))
        return sev, drivers or ["شدت پیش‌فرض بر پایه توصیف مشتری (۲)"]

    def _questions(self, slug: str, text: str, severity: int) -> list[dict[str, str]]:
        """فقط سؤال‌هایی که دامنه/قیمت را بیش از ۵٪ جابه‌جا می‌کنند (حداقل تصمیم، حداکثر دقت)."""
        pool: list[tuple[str, str, str]] = []
        if slug == "plumbing.leak.sink":
            pool = [("flow", "نشت قطره‌ای است یا جریان‌دار؟", "±۲۵٪ مبلغ و افزودن بند تخلیه و خشک‌کردن"),
                    ("cabinet", "کابینت آب‌خورده یا ورق‌شده است؟", "افزودن ترمیم چوب (+۱۵ تا ۳۰٪)"),
                    ("access", "شیر اصلی آب در دسترس است؟", "±۱۰ دقیقه زمان کار")]
        elif slug == "plumbing.clog.emergency":
            pool = [("points", "چند نقطه تخلیه گرفته است؟", "±۳۰٪ زمان و مبلغ"),
                    ("floor", "طبقه چندم است؟", "±۱۰٪ ایاب‌وذهاب"),
                    ("repeat", "قبلاً هم گرفته بود؟", "افزودن ویدئو-بازرسی لوله")]
        elif slug == "electrical.short_circuit":
            pool = [("circuit", "برق کدام قسمت خانه قطع است؟", "تعیین مدار و ±۲۰٪ زمان"),
                    ("spark", "بوی سوختگی یا جرقه دیده می‌شود؟", "افزودن بازرسی ایمنی اجباری"),
                    ("age", "ساختمان قدیمی است؟", "افزودن بند تعویض سیم فرسوده")]
        elif slug == "hvac.service.ac":
            pool = [("units", "چند یونیت داخلی دارید؟", "ضریب مبلغ × تعداد"),
                    ("gas", "شارژ گاز در یک سال گذشته انجام شده؟", "افزودن بند شارژ (+۲۰٪)"),
                    ("access", "یونیت بیرونی دسترسی آسان دارد؟", "±۱۵ دقیقه")]
        elif slug == "locksmith.open_door":
            pool = [("type", "قفل سوزنی است یا لولایی؟", "تغییر ابزار و ±۱۰ دقیقه"),
                    ("damage", "قفل پس از بازکردن باید تعویض شود؟", "افزودن سیلندر (+۸۰٪)"),
                    ("doc", "مدرک مالکیت همراست؟", "الزام قانونی برای شروع کار")]
        elif slug == "painting.wall":
            pool = [("area", "متراژ تقریبی دیوار چقدر است؟", "معیار اصلی قیمت"),
                    ("crack", "ترک‌خوردگی عمیق دارد؟", "افزودن پرداخت و بتونه (+۱۵٪)"),
                    ("color", "تغییر رنگ روشن/تیره؟", "±۱ دست رنگ")]
        elif slug == "carpentry.cabinet.damage":
            pool = [("extent", "آسیب سطحی است یا سطری؟", "تعیین تعویض صفحه (×۲.۵ مبلغ)"),
                    ("water", "علت نمناکی برطرف شده؟", "وگرنه مهمانکاری تضمین نمی‌شود"),
                    ("match", "رنگ کابینت موجود باید منطبق شود؟", "±۱۵٪")]
        answered = 0
        out: list[dict[str, str]] = []
        for code, q, impact in pool:
            if answered >= self.MAX_QUESTIONS:
                break
            out.append({"code": code, "question_fa": q, "impact_fa": impact})
            answered += 1
        return out

    # ------------------------------------------------------------------ public
    def analyze(self, *, text: str, media: list[dict] | None = None, category_hint: str | None = None,
                lat: float = 35.7448, lng: float = 51.4261, urgency: str = "SAME_DAY") -> dict[str, Any]:
        media_tags = [t for m in (media or []) for t in (m.get("ai_tags") or [])]
        slug, confidence, alternatives = self._match_service(text, category_hint, media_tags)
        if not slug:
            return {"matched": False, "message_fa": "دسته تشخیص داده نشد؛ پرسش‌های راهنما ارسال می‌شود.",
                    "clarifying_questions": [
                        {"code": "domain", "question_fa": "کدام حوزه است؟ آب/برق/تهویه/قفل/نقاشی/چوب",
                         "impact_fa": "انتخاب کاتالوگ استاندارد"},
                        {"code": "photo", "question_fa": "یک عکس از محل مشکل بگیرید.",
                         "impact_fa": "تشخیص خودکار دسته و شدت (±۳۰٪ دقت قیمت)"}],
                    "ai_version": AI_VERSION}
        std = STANDARD_CATALOG[slug]
        item = self.db.execute(select(ServiceItem).where(ServiceItem.slug == slug)).scalar_one_or_none()
        severity, sev_drivers = self._severity(text, media_tags)
        quote = None
        if item:
            quote = PricingService(self.db).quote(item=item, urgency=urgency,
                                                  zone_id=None, org_id=None, severity=severity,
                                                  distance_km=3.0)
        materials_sum = sum(amount for _, amount in std["materials"])
        low = high = None
        if quote:
            # اقلام بر پایه شدت: ۶۰٪ در شدت ۱ تا ۱۸۰٪ در شدت ۵
            mat_factor = 0.6 + 0.3 * (severity - 1)
            materials_est = money(materials_sum * mat_factor)
            labor = quote["final_amount"] - money(item.base_price * 0.18 if item else 0)
            low = money(labor + materials_est * 0.85)
            high = money(labor + materials_est * 1.18)
        return {
            "matched": True,
            "standard_code": std["code"],
            "standard_title_fa": std["title_fa"],
            "service_slug": slug,
            "category": slug.split(".")[0],
            "confidence": confidence,
            "alternative_slugs": alternatives,
            "severity": severity,
            "severity_drivers_fa": sev_drivers,
            "scope_standard": std["scope"],
            "materials_estimate": [{"title": t, "amount": money(a * (0.6 + 0.3 * (severity - 1)))}
                                   for t, a in std["materials"]],
            "materials_total": money(sum(m["amount"] for m in
                                         [{"amount": money(a * (0.6 + 0.3 * (severity - 1)))}
                                          for _, a in std["materials"]])),
            "skills_required": std["skills"],
            "equipment_required": std["equipment"],
            "duration_band_minutes": [std["duration_min"], std["duration_max"]],
            "warranty_days": std["warranty_days"],
            "price_band": {"low": low, "high": high, "quote_id": quote["quote_id"] if quote else None,
                           "note_fa": "بازه شامل اقلام برآوردی است؛ تسویه اقلام با رسید."},
            "clarifying_questions": self._questions(slug, text, severity),
            "determinants_fa": std["determinants_fa"],
            "safety_fa": std["safety_fa"],
            "ai_version": AI_VERSION,
            "why_fa": (f"متن و رسانه مشتری با واژگان کلیدی «{std['title_fa']}» هم‌خوانی داشت "
                       f"(اطمینان {confidence}). شدت {severity} از ۵ تعیین شد."),
        }


# =============================================================================
# ۳) هوشمندسازی تطبیق — رتبه‌بندی مرحله ۲
# =============================================================================
class MatchingIntelligence:
    """لایه ۲ روی DispatchService: تطبیق معنایی مهارت، ریسک عدم‌حضور، انصاف قیمت، پاسخ‌دهی زمانی."""

    AI_WEIGHTS = {"stage1": 0.45, "semantic": 0.20, "no_show": 0.20, "price_fairness": 0.10,
                  "recent_quality": 0.05}

    def __init__(self, db: Session):
        self.db = db

    # --------------------------------------------------------------- features
    @staticmethod
    def semantic_fit(required: str, provider_skills: str) -> tuple[float, str]:
        need = {s.strip() for s in (required or "").split(",") if s.strip()}
        have = {s.strip() for s in (provider_skills or "").split(",") if s.strip()}
        if not need:
            return 0.8, "نیاز مهارتی خاصی ثبت نشده"
        best = []
        for n in need:
            if n in have:
                best.append(1.0)
                continue
            near = max((SKILL_GRAPH.get(n, {}).get(h, 0.0) for h in have), default=0.0)
            best.append(near)
        fit = sum(best) / len(best)
        note = ("تطابق کامل مهارت" if fit >= 0.99 else
                ("مهارت همسایه/قابل‌انتقال" if fit >= 0.4 else "فاصله مهارتی — نیازمند نظارت"))
        return clamp(fit, 0, 1), note

    def _avg_response_delay_sec(self, provider_id: str) -> float | None:
        """میانگین تأخیر پاسخ به پیشنهادها (از جدول job_offer) — شاخص در دسترس در MVP."""
        rows = self.db.execute(
            select(JobOffer.offered_at, JobOffer.responded_at)
            .where(JobOffer.provider_id == provider_id, JobOffer.responded_at.is_not(None))
            .order_by(JobOffer.offered_at.desc()).limit(30)
        ).all()
        if not rows:
            return None
        vals = []
        for offered, responded in rows:
            o, r = aware(offered), aware(responded)
            if o and r:
                vals.append(max((r - o).total_seconds(), 0))
        return sum(vals) / len(vals) if vals else None

    def no_show_risk(self, p: ProviderProfile) -> float:
        """ریسک عدم‌حضور/لغو: پایه تاریخی + تأخیر پاسخ + بار کاری روز."""
        base = clamp(p.cancel_rate * 2.2, 0, 0.5)
        delay = self._avg_response_delay_sec(p.user_id)
        late = clamp((delay / 900.0) if delay else 0.08, 0, 0.25)     # نبود داده ⇒ مقدار محافظه‌کارانه
        load = clamp(p.accepted_today / max(p.capacity_per_day, 1), 0, 1)
        return round(clamp(base + late + 0.15 * load, 0.01, 0.85), 3)

    def price_fairness(self, p: ProviderProfile, zone_id: str | None, service_item_id: str) -> float:
        """انصاف قیمت متخصص: میانه اظهارنظرها/تسویه‌های اخیر در برابر میانه ناحیه (۰.۵ تا ۱)."""
        rows = self.db.execute(
            select(PriceQuote.final_amount).join(Order, Order.price_quote_id == PriceQuote.id)
            .where(Order.assigned_provider_id == p.user_id,
                   Order.status.in_(("COMPLETED", "CLOSED"))).limit(20)
        ).scalars().all()
        if len(rows) < 3:
            return 0.7                                     # داده ناکافی ⇒ مقدار محافظه‌کارانه خنثی
        vals = sorted(rows)
        median_own = vals[len(vals) // 2]
        zone_rows = self.db.execute(
            select(Order.final_amount).join(PriceQuote, Order.price_quote_id == PriceQuote.id)
            .where(Order.zone_id == zone_id, Order.status.in_(("COMPLETED", "CLOSED"))).limit(200)
        ).scalars().all()
        if len(zone_rows) < 5:
            return 0.7
        zv = sorted(zone_rows)
        median_zone = zv[len(zv) // 2]
        ratio = median_own / max(median_zone, 1)
        return round(clamp(1.0 - abs(ratio - 1.0) * 1.2, 0.4, 1.0), 3)

    def recent_quality(self, p: ProviderProfile) -> float:
        """کیفیت اخیر ۹۰ روز (به‌جای میانگین تاریخی) برای واکنش سریع‌تر به افت."""
        rows = self.db.execute(
            select(Review.overall).join(Order, Review.order_id == Order.id)
            .where(Order.assigned_provider_id == p.user_id)
            .order_by(Review.published_at.desc()).limit(10)
        ).scalars().all()
        if not rows:
            return clamp(p.rating_avg / 5.0 if p.rating_count else 0.78, 0, 1)
        return clamp(sum(rows) / len(rows) / 5.0, 0, 1)

    # ------------------------------------------------------------------ public
    def rerank(self, order: Order, top_n: int = 5) -> dict[str, Any]:
        svc = DispatchService(self.db)
        weights = svc.weights_for(order)
        radius = order.dispatch_radius_m or 4000
        pairs = svc.candidates(order, radius)
        scored = []
        for p, dist in pairs:
            s1 = svc.score(order, p, dist, radius, weights, 1)
            semantic, sem_note = self.semantic_fit(order.skills_required or "", p.skills or "")
            no_show = self.no_show_risk(p)
            fairness = self.price_fairness(p, order.zone_id, order.service_item_id)
            recent = self.recent_quality(p)
            ai = (self.AI_WEIGHTS["stage1"] * s1["total"]
                  + self.AI_WEIGHTS["semantic"] * semantic
                  + self.AI_WEIGHTS["no_show"] * (1 - no_show)
                  + self.AI_WEIGHTS["price_fairness"] * fairness
                  + self.AI_WEIGHTS["recent_quality"] * recent)
            scored.append({
                "provider_id": p.user_id, "provider_name": p.display_name, "tier": p.tier,
                "distance_km": round(dist, 2), "stage1_score": s1["total"],
                "ai_score": round(ai, 4),
                "ai_features": {"semantic_fit": round(semantic, 3), "no_show_risk": no_show,
                                "price_fairness": fairness, "recent_quality": round(recent, 3)},
                "ai_explain_fa": (f"{sem_note} · ریسک عدم‌حضور {no_show:.0%} · "
                                  f"انصاف قیمت {fairness:.2f} · کیفیت اخیر {recent * 5:.2f}"),
            })
        scored.sort(key=lambda x: x["ai_score"], reverse=True)
        for i, row in enumerate(scored, start=1):
            row["ai_rank"] = i
        base_order = sorted(scored, key=lambda x: x["stage1_score"], reverse=True)
        base_rank = {r["provider_id"]: i for i, r in enumerate(base_order, start=1)}
        for row in scored:
            row["rank_change"] = base_rank[row["provider_id"]] - row["ai_rank"]
        return {
            "order_id": order.id, "ai_version": AI_VERSION,
            "weights": self.AI_WEIGHTS, "radius_m": radius,
            "items": scored[:top_n],
            "fairness_audit_fa": ("رتبه‌بندی فقط از ویژگی‌های عملکردی استفاده می‌کند "
                                  "(مهارت، کیفیت، ریسک، انصاف قیمت)؛ جنسیت، سن، قومیت، نام و "
                                  "منطقه کم‌درآمد در هیچ ویژگی‌ای حضور ندارند و به‌صورت خودکار ممیزی می‌شوند."),
            "stage1_preserved_fa": "امتیاز ۹مؤلفه‌ای موتور اصلی دست‌نخورده است؛ این لایه فقط بازچینش می‌کند.",
        }


# =============================================================================
# ۴) کنترل کیفیت پیش‌بینانه + موتور مداخله
# =============================================================================
class PredictiveQualityService:
    """
    کارت امتیاز شفاف (MVP). در محیط عملیاتی همین ویژگی‌ها با GBDT کالیبره جایگزین می‌شوند،
    اما قرارداد خروجی (احتمال‌ها، عوامل، مداخله‌ها) ثابت می‌ماند تا UI و فرآیند پشتیبانی تغییر نکند.
    """

    FEATURES = [
        ("severity_high", 0.16, "شدت اعلام‌شده بالا (۴ یا ۵ از ۵)"),
        ("price_gap", 0.14, "فاصله مبلغ سفارش از میانه ناحیه"),
        ("first_time_pair", 0.13, "نخستین همکاری این مشتری و متخصص"),
        ("provider_cancel_rate", 0.12, "نرخ لغو تاریخی متخصص"),
        ("equipment_shortfall", 0.11, "کمبود تجهیزات حیاتی نسبت به استاندارد خدمت"),
        ("media_absent", 0.10, "نبود عکس/ویدئوی ورودی (ابهام دامنه)"),
        ("urgency_extreme", 0.09, "فوریت افراطی (Emergency) با دامنه استاندارد کم"),
        ("off_hours", 0.08, "مراجعه در ساعات غیرمتعارف (۲۲ تا ۷)"),
        ("warranty_risk_category", 0.07, "دسته پرریسک گارانتی (برق/نقاشی)"),
    ]

    INTERVENTIONS = {
        "severity_high": ("بازرسی ویدئویی پیش از شروع", "کاهش بازکار ≈۱۸٪", "V1"),
        "price_gap": ("تأیید شفاف تفکیک قیمت با مشتری پیش از شروع", "کاهش اختلاف ≈۱۲٪", "MVP"),
        "first_time_pair": ("پیام آشناسازی + چک‌لیست استاندارد اجباری", "کاهش نارضایتی ≈۹٪", "MVP"),
        "provider_cancel_rate": ("تخصیص به متخصص با پایبندی بالاتر + جریمه لغو", "کاهش لغو ≈۲۲٪", "MVP"),
        "equipment_shortfall": ("الزام تکمیل تجهیزات یا جایگزینی متخصص", "کاهش دوباره‌کاری ≈۲۵٪", "MVP"),
        "media_absent": ("درخواست یک عکس از مشتری (۳۰ ثانیه)", "افزایش دقت دامنه ≈۱۵٪", "MVP"),
        "urgency_extreme": ("تأیید تلفنی دامنه توسط اپراتور (Security-Check)", "کاهش نارضایتی ≈۱۰٪", "V1"),
        "off_hours": ("بررسی دسترسی ساختمان/همسایه پیش از اعزام", "کاهش عدم‌حضور ≈۱۴٪", "V1"),
        "warranty_risk_category": ("نگهداشت ۱۰٪ مبلغ تا پایان گارانتی + بازبینی ۷ روزه",
                                   "کاهش تعهد گارانتی ≈۲۰٪", "MVP"),
    }

    def __init__(self, db: Session):
        self.db = db

    # ---------------------------------------------------------------- features
    def _zone_median(self, order: Order) -> int | None:
        rows = self.db.execute(select(Order.final_amount).where(
            Order.zone_id == order.zone_id, Order.status.in_(("COMPLETED", "CLOSED"))).limit(200)
        ).scalars().all()
        if len(rows) < 3:
            return None
        rows = sorted(rows)
        return rows[len(rows) // 2]

    def _features(self, order: Order) -> dict[str, float]:
        p = self.db.get(ProviderProfile, order.assigned_provider_id) if order.assigned_provider_id else None
        item = self.db.get(ServiceItem, order.service_item_id)
        std = STANDARD_CATALOG.get(item.slug if item else "", {})
        f: dict[str, float] = {}

        f["severity_high"] = clamp((order.severity - 3) / 2, 0, 1)
        med = self._zone_median(order)
        f["price_gap"] = clamp(abs((order.final_amount or 0) - med) / max(med or 1, 1), 0, 1) if med else 0.0
        pair_first = 1.0
        if p:
            prior = self.db.execute(select(func.count()).select_from(Order).where(
                Order.customer_id == order.customer_id, Order.assigned_provider_id == p.user_id,
                Order.status.in_(("COMPLETED", "CLOSED")))).scalar_one()
            pair_first = 0.0 if prior else 1.0
        f["first_time_pair"] = pair_first
        f["provider_cancel_rate"] = clamp((p.cancel_rate if p else 0.05) / 0.20, 0, 1)
        need = {x.strip().replace("req:", "") for x in (std.get("equipment") or [])}
        have = set((p.equipment or "").split(",")) if p else set()
        f["equipment_shortfall"] = 0.0 if not need else (1.0 - len(need & have) / len(need))
        f["media_absent"] = 1.0 if not order.intake_media_count else clamp(1.0 - order.intake_media_count / 3, 0, 1)
        f["urgency_extreme"] = 1.0 if order.urgency == "EMERGENCY" else 0.0
        hour = (order.slot_start or now()).hour
        f["off_hours"] = 1.0 if hour >= 22 or hour < 7 else 0.0
        f["warranty_risk_category"] = 1.0 if (item and item.slug.split(".")[0] in ("electrical", "painting")) else 0.25
        return f

    # ------------------------------------------------------------------ public
    def forecast(self, order: Order) -> dict[str, Any]:
        f = self._features(order)
        contributions = {k: round(w * f[k], 4) for k, w, _ in self.FEATURES}
        risk = clamp(sum(contributions.values()), 0, 1)
        band = "LOW" if risk < 0.28 else ("MEDIUM" if risk < 0.50 else "HIGH")
        drivers = sorted(contributions.items(), key=lambda kv: kv[1], reverse=True)
        labels = {k: lbl for k, _, lbl in self.FEATURES}
        interventions = []
        for key, value in drivers:
            if value <= 0.02 or len(interventions) >= 3:
                continue
            title, effect, phase = self.INTERVENTIONS[key]
            interventions.append({"trigger": key, "trigger_fa": labels[key],
                                  "action_fa": title, "expected_effect_fa": effect, "phase": phase,
                                  "contribution": value})
        p_rework = round(clamp(risk * 0.55, 0.01, 0.6), 3)
        p_dispute = round(clamp(risk * 0.42, 0.005, 0.5), 3)
        p_late = round(clamp(0.06 + risk * 0.45, 0.02, 0.7), 3)
        return {
            "order_id": order.id, "ai_version": AI_VERSION,
            "risk_score": round(risk, 4), "risk_band": band,
            "probabilities": {"rework": p_rework, "dispute": p_dispute, "late_finish": p_late,
                              "escalation_to_support": round(clamp(risk * 0.30, 0.005, 0.4), 3)},
            "drivers": [{"feature": k, "label_fa": labels[k], "value": round(f[k], 3),
                         "contribution": v} for k, v in drivers[:4]],
            "interventions": interventions,
            "model_card": {
                "type": "کارت امتیاز شفاف (Scorecard) — فاز MVP",
                "production_plan_fa": "جایگزینی با GBDT کالیبره‌شده روی داده پایلوت (هدف: AUC ≥ ۰.۷۸، "
                                      "Brier ≤ ۰.۱۰) با پایش drift ماهانه و نسخه‌دار",
                "features_count": len(self.FEATURES),
                "human_in_the_loop_fa": "تصمیم‌های «بازرسی ویدئویی» و «نگهداشت گارانتی» نیازمند تأیید "
                                        "اپراتور یا مشتری است؛ مدل هرگز به‌تنهایی پول را بلوکه نمی‌کند.",
                "limits_fa": "خروجی این مدل قطعی نیست؛ برای سفارش‌های کم‌ریسک صرفاً پایش غیرمزاحم انجام می‌شود.",
            },
            "kpi_targets_fa": {"rework": "−۲۵٪", "dispute": "−۳۰٪",
                               "support_calls": "−۱۵٪", "added_tta": "۰ (بدون کندکردن تخصیص)"},
        }


# =============================================================================
# ۵) تجربه روان — اقدام بعدی (Next Best Action)
# =============================================================================
class ExperienceService:
    """حداقل تصمیم، حداکثر اطمینان: در هر لحظه فقط اقدام‌های اثرگذار و با یک ضربه."""

    def __init__(self, db: Session):
        self.db = db

    def _slots(self, order: Order) -> list[dict[str, str]]:
        base = order.slot_start or now()
        out = []
        for i, (label, minutes) in enumerate([("در اولین فرصت (۴۵ دقیقه دیگر)", 45),
                                              ("امروز عصر", 300), ("فردا صبح", 1020)]):
            t = base if i == 0 else base.replace(microsecond=0)
            out.append({"code": f"slot-{i + 1}", "label_fa": label,
                        "score_fa": ["سریع‌ترین", "کم‌تراکم‌ترین (تخفیف پیک)", "ارزان‌ترین (تعرفه غیرپیک)"][i],
                        "effect_fa": ["کوتاه‌ترین زمان انتظار", "−۵٪ نسبت به پیک",
                                      "−۱۰٪ نسبت به پیک"][i]})
        return out

    def next_actions(self, order: Order) -> dict[str, Any]:
        st = order.status
        paid = str(order.payment_state) in ("PAID", "HELD") or str(order.escrow_state) == "HELD_IN_ESCROW"
        actions: list[dict[str, Any]] = []
        if st in ("CREATED", "PENDING_PAYMENT") and not paid:
            actions = [
                {"code": "pay", "title_fa": "پرداخت و بلوکه‌سازی در Escrow", "one_tap": True,
                 "why_fa": "پول تا تأیید شما نزد پلتفرم می‌ماند؛ متخصص بعد از پرداخت اعزام می‌شود.",
                 "deep_link": f"/v1/orders/{order.id}/pay?method=WALLET"},
                {"code": "edit_scope", "title_fa": "بازبینی دامنه استاندارد کار", "one_tap": True,
                 "why_fa": "دامنه استاندارد، مبنای فاکتور و حل اختلاف است.",
                 "deep_link": f"/v1/ai/intake"},
                {"code": "slots", "title_fa": "انتخاب بازه زمانی", "one_tap": True,
                 "why_fa": "انتخاب بازه غیرپیک تا ۱۰٪ ارزان‌تر است.", "slots": self._slots(order)},
            ]
        elif st in ("CREATED", "PENDING_PAYMENT", "PAID") and paid:
            actions = [
                {"code": "schedule", "title_fa": "انتخاب بازه زمانی اعزام", "one_tap": True,
                 "why_fa": "پول در Escrow بلوکه شده؛ با انتخاب بازه، سفارش به صف تخصیص می‌رود.",
                 "slots": self._slots(order)},
                {"code": "send_photo", "title_fa": "افزودن یک عکس از محل", "one_tap": True,
                 "why_fa": "عکس، دامنه استاندارد را قطعی‌تر و برآورد را دقیق‌تر می‌کند (≈۱۵٪).",
                 "deep_link": f"/v1/ai/intake"},
                {"code": "scope", "title_fa": "بازبینی دامنه استاندارد و اقلام", "one_tap": False,
                 "why_fa": "پیش از اعزام، اختیاری‌ها را کم کنید تا هزینه نهایی پایین‌تر شود.",
                 "deep_link": f"/v1/ai/standards"},
            ]
        elif st == "DISPATCHING":
            actions = [
                {"code": "live", "title_fa": "رهگیری زنده تخصیص", "one_tap": False,
                 "why_fa": "تا لحظه پذیرش، پیشنهادها هم‌زمان برای چند متخصص ارسال است.",
                 "deep_link": f"/v1/orders/{order.id}/tracking"},
                {"code": "widen", "title_fa": "گسترش شعاع جست‌وجو", "one_tap": True,
                 "why_fa": "به‌جای پرداخت انگیزه، دامنه بیشتری از متخصصان بررسی می‌شود.",
                 "deep_link": f"/v1/orders/{order.id}/dispatch/advance"},
            ]
        elif st in ("ASSIGNED", "IN_PROGRESS"):
            actions = [
                {"code": "track", "title_fa": "مسیر و زمان رسیدن متخصص", "one_tap": False,
                 "why_fa": "زمان تخمینی بر پایه فاصله و ترافیک محاسبه می‌شود.",
                 "deep_link": f"/v1/orders/{order.id}/tracking"},
                {"code": "scope_check", "title_fa": "تأیید دامنه و اقلام پیش از شروع", "one_tap": True,
                 "why_fa": "هر تغییر دامنه، پیش از اجرا ثبت و تأیید می‌شود (بدون اختلاف بعدی).",
                 "deep_link": f"/v1/orders/{order.id}/change-order"},
            ]
        elif st == "COMPLETED":
            actions = [
                {"code": "approve", "title_fa": "تأیید کار و آزادسازی پول", "one_tap": True,
                 "why_fa": f"پس از تأیید، پول به متخصص می‌رسد؛ تا {48} ساعت فرصت اعتراض دارید.",
                 "deep_link": f"/v1/orders/{order.id}/approve"},
                {"code": "review", "title_fa": "امتیاز پنج‌بعدی (۱۵ ثانیه)", "one_tap": True,
                 "why_fa": "امتیاز شما مستقیماً بر تخصیص سفارش‌های بعدی اثر می‌گذارد.",
                 "deep_link": f"/v1/orders/{order.id}/review"},
            ]
        else:
            actions = [
                {"code": "warranty", "title_fa": "گارانتی فعال سفارش", "one_tap": False,
                 "why_fa": "در صورت تکرار مشکل، مراجعه مجدد بدون هزینه است.",
                 "deep_link": f"/v1/orders/{order.id}/warranty"},
                {"code": "rebook", "title_fa": "سفارش دوباره همان خدمت", "one_tap": True,
                 "why_fa": "دامنه و اقلام سفارش قبلی به‌صورت پیش‌فرض پر می‌شود (سفارش در ۲۰ ثانیه).",
                 "deep_link": "/v1/orders"},
            ]
        return {
            "order_id": order.id, "status": order.status,
            "next_best_actions": actions[:3],
            "friction_kpi_fa": {
                "order_completion_target": "< ۶۰ ثانیه",
                "form_abandon_target": "< ۱۵٪",
                "status_call_reduction_target": "−۵۰٪",
                "sus_target": "≥ ۸۰",
            },
            "ai_version": AI_VERSION,
        }

    def friction_report(self) -> dict[str, Any]:
        """گزارش گلوگاه‌های تجربه بر پایه داده سفارش‌های موجود (ورودی بهبود مستمر UX)."""
        orders = self.db.execute(select(Order)).scalars().all()
        media_missing = sum(1 for o in orders if not o.intake_media_count)
        no_quote = sum(1 for o in orders if not o.price_quote_id)
        late = 0
        for o in orders:
            if o.slot_start and o.created_at and aware(o.slot_start) < aware(o.created_at):
                late += 1
        return {
            "orders": len(orders),
            "media_absent_share": round(media_missing / len(orders), 3) if orders else 0,
            "quote_missing_share": round(no_quote / len(orders), 3) if orders else 0,
            "slot_before_creation_share": round(late / len(orders), 3) if orders else 0,
            "recommendations_fa": [
                "الزام نرم: درخواست یک عکس در گام دوم سفارش (افزایش دقت دامنه ≈۱۵٪)",
                "پیشنهاد خودکار بازه‌های غیرپیک در گام پرداخت (کاهش هزینه مشتری تا ۱۰٪)",
                "نمایش «زمان تخمینی رسیدن» در همه گام‌ها (کاهش تماس وضعیت سفارش ≈۵۰٪)",
            ],
        }
