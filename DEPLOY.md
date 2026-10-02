# راهنمای استقرار «سرویسا / Care+» روی GitHub و Render

این سند، مسیر کامل انتشار پکیج برای «بررسی بیرونی» را گام‌به‌گام توضیح می‌دهد. سه بخش دارد:
**۱) انتشار روی GitHub**، **۲) استقرار روی Render**، **۳) بررسی پس از استقرار و رفع اشکال**.
همه فرمان‌ها از ریشه مخزن (`achareh-plus/`) اجرا می‌شوند.

---

## گام ۰ — پیش‌نیازهای امنیتی (مهم)

- کلید GitHub باید دسترسی **Contents: Read & Write** و **Administration: Read & Write** روی مخزن هدف داشته باشد
  (یا یک کلاسیک PAT با محدوده `repo`). توکن فعلی فقط هویت را تأیید می‌کند و **اجازه ساخت مخزن یا push ندارد**
  (خطای `Resource not accessible by personal access token`). تا زمانی که چنین توکنی صادر نشود، گام‌های ۱ و ۲
  به‌صورت خودکار اجرا نمی‌شوند و فقط دستورهای آماده ارائه می‌گردند.
- **کلید را هرگز در فایل نگذارید.** اسکریپت‌ها آن را از متغیر محیطی `GITHUB_TOKEN` یا فایل `~/.github_token`
  می‌خوانند و هیچ‌گاه چاپ نمی‌کنند.
- کلید Render را در `~/.render_api_key` یا متغیر `RENDER_API_KEY` بگذارید.
- کلید لو رفته قبلی (پیام اول گفتگو) را در GitHub → Settings → Developer settings → Personal access tokens
  **باطل (Revoke)** کنید.

---

## گام ۱ — انتشار روی GitHub

### راه اول: مخزن را از قبل در وب بسازید (ساده‌ترین و پیشنهادی)
1. در GitHub یک مخزن **خالی** بسازید، مثلاً `kian1401/careplus-platform` (بدون README، بدون .gitignore).
2. سپس:

```bash
cd achareh-plus
git remote add origin https://github.com/kian1401/careplus-platform.git
git push -u origin main
```

### راه دوم: اسکریپت خودکار (ساخت مخزن + push با API)
```bash
export GITHUB_TOKEN=<توکن با دسترسی Contents+Administration>
bash scripts/auto_push.sh careplus-platform "سرویسا / Care+ — پکیج کامل طراحی و MVP"
```
اسکریپت: مخزن را می‌سازد (اگر نبود)، `origin` را تنظیم می‌کند، `main` را push می‌کند و
برای push از همان توکن در URL موقت استفاده می‌کند بدون آنکه آن را در `.git/config` ذخیره کند.

### راه سوم: حلقه انتشار خودکار (Auto-rebuild)
```bash
GITHUB_TOKEN=... AUTO_EVERY=120 bash scripts/auto_push.sh careplus-platform
```
هر ۲ دقیقه تغییرات را commit و push می‌کند؛ Render با `autoDeploy: true` بلافاصله دوباره مستقر می‌کند.
خروجی هر دور در `scripts/push.log` ثبت می‌شود (بدون هیچ کلیدی).

---

## گام ۲ — استقرار روی Render

### راه اول: Blueprint (پیشنهادی — با فایل `render.yaml` همین مخزن)
1. Render Dashboard → **New +** → **Blueprint** → انتخاب مخزن → **Apply**.
2. Render خودش `render.yaml` را می‌خواند و سرویس `careplus-mvp` را با این مشخصات می‌سازد:
   - `rootDir: mvp` · `build: pip install -r requirements.txt` · `start: uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - health check: `/health` · منطقه: frankfurt · پلن: free · autoDeploy: true
   - متغیرها: `CP_ENV=demo`, `CP_SECRET_KEY` تولیدخودکار, `CP_DATABASE_URL=sqlite:////tmp/careplus.sqlite`
3. پس از پایان بیلد، آدرس عمومی به شکل `https://careplus-mvp.onrender.com` فعال می‌شود.

### راه دوم: با API (بدون داشبورد)
```bash
export RENDER_API_KEY=<کلید>
bash scripts/deploy_render.sh careplus-platform      # owner را از API می‌خواند
```
این اسکریپت سرویس را می‌سازد (یا اگر موجود باشد به‌روزرسانی می‌کند)، سپس استقرار را راه می‌اندازد و
وضعیت را با `GET /v1/services/{id}/deploys` پیگیری می‌کند. شناسه سرویس در `scripts/` ذخیره نمی‌شود.

### راه سوم: Docker (هر میزبان دیگر)
```bash
cd mvp
docker build -t careplus-mvp .
docker run -p 8000:8000 -v careplus-data:/data careplus-mvp
# سپس: http://localhost:8000
```

### نکته پایداری داده
پلن free دیسک موقت دارد ⇒ با هر استقرار، داده دمو بازنشانی و سناریو از نو اجرا می‌شود (برای بررسی، مطلوب است).
برای داده پایدار: یک **Disk** بسازید، به مسیر `/data` وصل کنید و `CP_DATABASE_URL=sqlite:////data/careplus.sqlite` بگذارید
(یا برای بار واقعی، به PostgreSQL مهاجرت کنید: سند بخش ۸ و `db/schema.sql`).

---

## گام ۳ — بررسی پس از استقرار (Checklist)

| # | بررسی | فرمان / مسیر | معیار قبولی |
|---|-------|--------------|--------------|
| ۱ | سلامت سرویس | `GET /health` | `status=ok` و `build.git_sha` با آخرین کامیت یکی باشد |
| ۲ | آمادگی داده | `GET /ready` | `db=ok, seeded=true` |
| ۳ | کنسول دمو | `GET /` | صفحه فارسی بارگذاری شود و سناریو ۹ گام اجرا کند |
| ۴ | مستندات API | `GET /docs` | فهرست مسیرها باز شود |
| ۵ | لایه هوشمندی | `GET /v1/ai/scorecards` | ۴ مدل + بخش حاکمیت |
| ۶ | استانداردسازی | `POST /v1/ai/intake` با متن نمونه | کد استاندارد + بازه قیمت + ≤۳ پرسش |
| ۷ | کیفیت پیش‌بینانه | `GET /v1/ai/quality/forecast/{order_id}` | باند ریسک + مداخله‌ها |
| ۸ | صف تخصیص | `GET /v1/demo/scenario?tactic=accept` | `steps=9` و دفتر کل متوازن |
| ۹ | آزمون‌ها | `python3 -m pytest tests -q` (محلی) | ۲۲ آزمون سبز |

---

## اشکال‌یابی مسائل رایج

| نشانه | علت | راه‌حل |
|-------|-----|--------|
| `Resource not accessible by personal access token` | توکن فاقد مجوز ساخت مخزن/نوشتن محتوا | توکن جدید با Contents+Administration (یا ساخت دستی مخزن و push) |
| بیلد Render: `No module named app` | `rootDir` اشتباه | `rootDir` باید `mvp` باشد (سرویس را از ریشه مخزن اجرا نکنید) |
| بیلد Render: `No open ports detected` | گوش‌دادن روی `127.0.0.1` یا پورت ثابت | باید `--host 0.0.0.0 --port $PORT` باشد (در `render.yaml` تنظیم شده) |
| `GET /` خطای ۴۰۴ | مونت نبودن مسیر استاتیک | مطمئن شوید `app/static/console.html` در مخزن است (پوشه `static` نباید در `.gitignore` باشد) |
| داده دمو ناپدید می‌شود | دیسک موقت پلن free | طبیعی است؛ برای پایداری، Disk به `/data` وصل کنید |
| `sqlite unable to open database file` | مسیر نبودن پوشه | مسیر `/tmp/...` یا `/data/...` استفاده شود و پوشه در Docker با `mkdir -p` ساخته شود |
| هشدار CORS در کنسول مرورگر | سیاست CORS | در دمو `allow_origins=["*"]` است؛ در محیط عملیاتی فقط دامنه‌های مجاز |

---

## گام‌های بعد از استقرار (برای جلسه بررسی)

1. در کنسول دمو، دکمه **«تحلیل هوشمند درخواست»** را بزنید ⇒ استاندارد خدمت + اقلام + بازه قیمت + پرسش‌ها.
2. سناریوی **accept** را اجرا کنید ⇒ در همان پنل، پیش‌بینی کیفیت و «اقدام بعدی مشتری» ظاهر می‌شود.
3. `GET /v1/ai/match/preview/{order_id}` را باز کنید ⇒ سه کاندید برتر مرحله ۲ با متن «چرا».
4. `GET /v1/ai/ux/friction-report` ⇒ گلوگاه‌های تجربه بر پایه داده سفارش‌های موجود.
