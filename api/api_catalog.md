# فهرست APIها — سرویسا (Care+)

نسخه: ۱.۱ | پایه: `/v1` | قرارداد ماشین‌خوان: [`openapi-core.yaml`](./openapi-core.yaml) (۶۰ مسیر، ۶۲ عملیات)
لایه هوشمندی (بخش AI سند): ۸ عملیات اجرایی در MVP — `mvp/app/intelligence.py` + مسیرهای `/v1/ai/*`.
قواعد عمومی: JWT Bearer، هدر `Idempotency-Key` روی همه نوشتن‌ها، صفحه‌بندی Cursor، خطای ساخت‌یافته با `code` معنایی، وب‌هوک با امضای HMAC-SHA256.

---

## ۱) احراز هویت و کاربران
| متد | مسیر | توضیح |
|---|---|---|
| POST | `/auth/otp/request` | ارسال کد یک‌بارمصرف (Rate-Limit سه‌لایه) |
| POST | `/auth/otp/verify` | تأیید کد و صدور توکن |
| POST | `/auth/oauth/{provider}` | ورود Google/Apple |
| POST | `/auth/passkey/register/options` · `/auth/passkey/login/verify` | ورود بدون رمز (WebAuthn) |
| POST | `/auth/refresh` | تمدید توکن با Rotation و Reuse-Detection |
| POST | `/auth/logout` · `/auth/logout-all` | خروج جاری / همه دستگاه‌ها |
| GET·PATCH | `/me` | پروفایل کاربر |
| GET·DELETE | `/me/sessions` · `/me/sessions/{id}` | مدیریت نشست‌ها |
| GET·POST | `/me/addresses` | آدرس‌ها و برچسب مکان |
| POST | `/me/consents` | ثبت/ابطال رضایت نسخه‌دار |
| POST | `/me/data-export` | خروجی داده کاربر (غیرهمگام) |

## ۲) متخصص و تأیید صلاحیت
| متد | مسیر | توضیح |
|---|---|---|
| POST | `/providers/apply` | شروع فرآیند جذب |
| POST | `/providers/me/documents` | ثبت مدرک (پیش‌آپلود S3) + OCR |
| POST | `/providers/me/identity/selfie` | سلفی زنده و Face-Match |
| GET | `/providers/me/identity/status` | وضعیت احراز و نقص‌ها |
| POST | `/providers/me/skill-test/{category}/submit` | آزمون مهارت و ویدیو |
| PUT | `/providers/me/skills` · `/equipment` · `/service-areas` · `/availability` | مهارت، تجهیزات، منطقه، تقویم |
| POST | `/providers/me/online` · `/offline` | آماده‌به‌کار و شروع ارسال موقعیت |
| GET | `/providers/me/dashboard` | درآمد، ساعات پرکار، نرخ‌ها |
| POST | `/providers/me/withdrawals` | درخواست برداشت |
| GET | `/providers/{id}/public-profile` | پروفایل عمومی |
| POST | `/admin/providers/{id}/verification-decision` | تصمیم پنل + دلیل |

## ۳) کاتالوگ، تریاژ و قیمت
| متد | مسیر | توضیح |
|---|---|---|
| GET | `/catalog/categories` · `/catalog/services` | دسته‌بندی و جست‌وجو |
| GET | `/catalog/services/{id}/rate-card` | تعرفه منطقه‌ای شفاف |
| GET | `/triage/flow/{service_id}` | درخت پرسش تریاژ |
| POST | `/media/presigned-url` | آپلود مستقیم رسانه به S3 |
| POST | `/intake/analyze` | تحلیل چندرسانه‌ای (دسته/شدت/خطر) |
| POST | `/pricing/quote` | تخمین قیمت با Breakdown شفاف |
| POST | `/pricing/quote/{id}/revision` | درخواست تغییر قیمت (Change Order) |
| POST | `/slots/availability` | ظرفیت آزاد و ETA |
| POST | `/emergency/quote` | پیش‌قیمت اعزام فوری |

## ۴) سفارش، اجرا و تخصیص
| متد | مسیر | توضیح |
|---|---|---|
| POST·GET | `/orders` | ثبت و فهرست سفارش |
| GET | `/orders/{id}` | جزئیات سفارش |
| POST | `/orders/{id}/submit-for-dispatch` | ورود به صف تخصیص |
| GET | `/orders/{id}/offers` | پیشنهادها (حالت Bid) |
| POST | `/orders/{id}/assign` | تخصیص دستی اپراتور + دلیل |
| POST | `/orders/{id}/reschedule` · `/cancel` | تغییر زمان / لغو با محاسبه جریمه |
| POST | `/providers/me/offers/{offer_id}/accept` · `/reject` | پاسخ پیشنهاد (اتمی) |
| POST | `/bookings/{id}/check-in` · `/check-out` | ورود/پایان کار با GPS و عکس |
| POST | `/orders/{id}/milestones/{seq}/submit` | پیشرفت مرحله پروژه |
| POST | `/orders/{id}/complete` · `/approve` · `/dispute` | اتمام / تأیید / اختلاف |
| GET | `/orders/{id}/tracking` + WS `/v1/ws/orders/{id}` | ردیابی زنده و ETA |
| POST | `/orders/{id}/call-session` · `/video-inspection` | تماس امن / بازدید ویدیویی |
| POST | `/recurring-plans` | سفارش دوره‌ای |

## ۵) پرداخت، کیف پول و Escrow
| متد | مسیر | توضیح |
|---|---|---|
| POST | `/payments/intents` | ایجاد تعهد پرداخت (IPG/Wallet/BNPL/Credit) |
| POST | `/payments/intents/{id}/confirm` | تأیید نتیجه پرداخت (Idempotent) |
| POST | `/payments/callback/{psp}` | وب‌هوک درگاه (DRY: HMAC) |
| GET | `/wallets/me` · `/wallets/me/transactions` | کیف پول و گردش |
| POST | `/wallets/me/topup` | شارژ کیف پول |
| POST | `/refunds` · `/admin/refunds/{id}/approve` | بازگشت وجه + تأیید |
| GET | `/orders/{id}/escrow` | وضعیت نگهداشت و آزادسازی |
| POST | `/organizations/{id}/credit-line/request` | درخواست اعتبار شرکتی |
| POST | `/bnpl/eligibility` · `/bnpl/contracts` | بررسی و ایجاد اقساط |
| GET | `/invoices/{id}` · `/invoices/{id}/pdf` | فاکتور و دانلود |
| GET | `/organizations/{id}/billing-report` | گزارش مالی B2B |
| GET | `/admin/ledger/accounts/{code}/entries` | دفتر کل (دسترسی محدود + لاگ) |
| POST | `/admin/payout-batches` · `/{id}/approve` | دوره تسویه و تأیید |

## ۶) کیفیت، بیمه، اختلاف و ریسک
| متد | مسیر | توضیح |
|---|---|---|
| POST | `/orders/{id}/review` | نظر و امتیاز چندبعدی |
| POST | `/reviews/{id}/appeal` | اعتراض متخصص به نظر |
| POST·GET | `/claims` · `/claims/{id}` · `/claims/{id}/evidence` | پرونده خسارت |
| POST | `/admin/claims/{id}/assess` | ارزیابی و تصمیم |
| POST | `/disputes` · `/disputes/{id}/messages` · `/resolve` | چرخه اختلاف |
| GET | `/orders/{id}/warranty` | وضعیت گارانتی |
| GET | `/admin/risk/cases` · POST `/admin/risk/cases/{id}/decision` | صف و تصمیم ریسک |
| GET | `/admin/fraud/collusion-alerts` | هشدارهای تبانی |

## ۷) پشتیبانی، ارتباطات و CRM
| متد | مسیر | توضیح |
|---|---|---|
| POST·GET | `/support/tickets` · `/support/tickets/{id}` | تیکت و پیگیری |
| POST | `/support/chatbot/sessions` · `/messages` | چت‌بات با Handoff |
| WS | `/v1/ws/support/{ticket_id}` | گفتگوی زنده با اپراتور |
| POST | `/support/calls/mask` | شماره پاسدار تماس |
| GET | `/support/tickets/{id}/timeline` | تاریخچه یکپارچه |
| POST | `/ops/crisis/broadcast` | پیام عمومی منطقه |
| GET | `/ops/live-board` | نمای عملیات زنده |
| POST | `/crm/segments` · `/crm/campaigns` | بخش‌بندی و کمپین |
| GET | `/knowledge/articles` | پایه دانش |

## ۸) لایه هوشمندی (AI Layer) — اجراشده در MVP
| متد | مسیر | توضیح | وضعیت |
|---|---|---|---|
| POST | `/ai/intake` | استانداردسازی درخواست مشتری: نگاشت متن/رسانه به کد استاندارد خدمت، دامنه اجباری، اقلام برآوردی، مهارت/تجهیز لازم، بازه قیمت و حداکثر ۳ پرسش تعیین‌کننده | ✅ MVP |
| GET | `/ai/standards` | کاتالوگ استاندارد خدمت (شفافیت برای مشتری، متخصص و پیمانکار)؛ با `?slug=` یک خدمت | ✅ MVP |
| GET | `/ai/match/preview/{order_id}` | مرحله ۲ تطبیق: بازچینش کاندیدهای مرحله ۱ با ویژگی‌های رفتاری (تطابق معنایی، ریسک عدم‌حضور، انصاف قیمت، کیفیت ۹۰ روزه) + متن «چرا» | ✅ MVP |
| GET | `/ai/quality/forecast/{order_id}` | کنترل کیفیت پیش‌بینانه: امتیاز ریسک، باند (LOW/MEDIUM/HIGH)، احتمال بازکار/اختلاف/تأخیر/ارجاع، عوامل مؤثر و مداخله‌های پیشنهادی با اثر مورد انتظار | ✅ MVP |
| GET | `/ai/assistant/{order_id}` | تجربه روان: اقدام‌های بعدی یک‌ضربه‌ای به‌تفکیک وضعیت سفارش (پرداخت، تعیین بازه، عکس، رهگیری، تأیید، امتیاز) + «چرا» | ✅ MVP |
| GET | `/ai/ux/friction-report` | گزارش گلوگاه‌های تجربه کاربری بر پایه داده سفارش‌های موجود + توصیه‌های اجرایی (نیازمند نقش OPS/ADMIN) | ✅ MVP |
| GET | `/ai/scorecards` | کارت مدل‌ها و حاکمیت: نسخه، ویژگی‌ها و وزن‌ها، برنامه تولید (V1/V2)، معیار پذیرش، انصاف، بازگشت‌پذیری، داده | ✅ MVP |
| GET | `/health` · `/ready` | سلامت سرویس و آمادگی داده + **شناسه بیلد** (`build.git_sha`, `ai_version`) برای رهگیری نسخه مستقر | ✅ MVP |

### قرارداد خروجی نمونه (استانداردسازی)
```json
POST /v1/ai/intake
{ "text": "آب از زیر سینک می‌آید و کابینت خیس شده، نشت قطره‌ای است" }

200 OK
{
  "matched": true, "standard_code": "STD-PLB-014",
  "standard_title_fa": "کنترل و رفع نشت زیر سینک / کابینت",
  "confidence": 0.62, "severity": 2,
  "scope_standard": ["قطع آب و تخلیه فشار خط", "…"],
  "materials_estimate": [{"title_fa": "واشر/شیلنگ", "qty": 1, "unit_price": 180000}],
  "materials_total": 380000,
  "price_band": {"low": 1644570, "high": 1766340, "currency": "IRR"},
  "duration_band_minutes": [60, 110], "warranty_days": 30,
  "skills_required": ["plumbing.basic"], "equipment_required": ["wrench"],
  "determinants": ["نشت قطره‌ای یا جریان‌دار", "آب‌خوردگی کابینت"],
  "clarifying_questions": [
    {"code": "flow_rate", "question_fa": "نشت قطره‌ای است یا جریان‌دار؟", "impact_fa": "±۲۵٪ مبلغ نهایی"}
  ]
}
```

### قرارداد خروجی نمونه (کنترل کیفیت پیش‌بینانه)
```json
GET /v1/ai/quality/forecast/{order_id}
200 OK
{
  "risk_score": 0.3875, "risk_band": "MEDIUM",
  "probabilities": {"rework": 0.11, "dispute": 0.16, "late_finish": 0.09, "escalation_to_support": 0.12},
  "drivers": [{"key": "first_time_pair", "value": 1, "weight": 0.13, "contribution": 0.13,
               "label_fa": "نخستین همکاری مشتری–متخصص"}],
  "interventions": [{"trigger": "first_time_pair", "action_fa": "پیام آشناسازی + چک‌لیست استاندارد اجباری",
                     "expected_effect_fa": "−۹٪ نارضایتی", "phase": "MVP"}],
  "model_card": {"version": "careplus-ai-1.0.0", "features_count": 9,
                 "human_in_the_loop_fa": "تصمیم‌های مالی/محدودکننده با انسان است."}
}
```

## ۹) تحلیل و ادمین
| متد | مسیر | توضیح |
|---|---|---|
| GET | `/admin/analytics/overview` · `/admin/analytics/funnel` | داشبورد و قیف |
| POST | `/admin/feature-flags/{key}` | فلگ و Kill-Switch |
| POST | `/admin/users/{id}/sanctions` | تعلیق/جریمه |
| GET | `/admin/audit-log` | لاگ حسابرسی |

---

## رویدادهای Kafka (افزوده‌های لایه هوشمندی)
`identity.user.*` · `provider.lifecycle.*` · `order.lifecycle.*` · `dispatch.*` · `payment.*` · `trust.*` · `risk.*` · `comm.*`

قواعد: نام‌گذاری `<domain>.<entity>.<event>`، فیلد `schema_version`، سازگاری رو به عقب، و `correlation_id` برای ردیابی سرتاسری.

## وب‌هوک‌ها (خروجی به پارتنر)
| رویداد | گیرنده | کاربرد |
|---|---|---|
| `order.status_changed` | پنل B2B / ERP مشتری | به‌روزرسانی وضعیت سفارش‌های سازمانی |
| `payment.settled` | سیستم‌های مالی | تسویه و تطبیق حساب |

## کدهای خطای نمونه
`AUTH_INVALID_OTP` · `AUTH_RATE_LIMITED` · `PROVIDER_NOT_VERIFIED` · `ORDER_NOT_ASSIGNABLE` · `OFFER_EXPIRED_OR_TAKEN` · `ESCROW_INSUFFICIENT_BALANCE` · `ESCROW_LOCKED_BY_DISPUTE` · `PRICE_REVISION_REQUIRES_CUSTOMER_APPROVAL` · `PAYMENT_IDEMPOTENCY_CONFLICT` · `RISK_BLOCKED_ACTION`
