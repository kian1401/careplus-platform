# -*- coding: utf-8 -*-
"""Assembles spec/index.html from _parts/*.html + _assets/(style.css, nav.html)."""
import glob, os, re, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../spec
PARTS = sorted(glob.glob(os.path.join(ROOT, '_parts', '*.html')))
CSS = open(os.path.join(ROOT, '_assets', 'style.css'), encoding='utf-8').read()
NAV = open(os.path.join(ROOT, '_assets', 'nav.html'), encoding='utf-8').read()

header = """<header class="doc">
<h1>سند جامع طراحی و توسعه پلتفرم خدمات آنلاین «سرویسا»</h1>
<p>نسخه ارتقایافته و بهبودیافته از مدل آچاره — نیازمندی‌ها، معماری داده، API، وایرفریم و RFP</p>
<div class="meta">
  <span>نسخه ۱.۰</span><span>تاریخ: ۱۴۰۵/۰۷/۱۰</span>
  <span>مخاطب: تیم محصول، تیم فنی، پیمانکار</span>
  <span>دامنه: ۶ ماژول + لایه هوشمندی AI + مدل داده + API + نمونه اولیه + RFP</span>
  <span>حجم: ۱۱۳ جدول در اسکیمای اجرایی، ۷۰ اندپوینت مستندشده، ۵۰ وایرفریم</span>
</div>
</header>"""

parts = []
for p in PARTS:
    html = open(p, encoding='utf-8').read()
    anchor = re.search(r'<section id="(s\d+)"', html)
    name = os.path.basename(p)
    parts.append(f"<!-- ===== {name} ({anchor.group(1) if anchor else '?'}) ===== -->\n{html}")
    print(f"included {name}")

cover = """<div class="print-only print-cover" dir="rtl">
<div class="pc-kicker">RFP · مجموعه اسناد واگذاری کار</div>
<h1>سند جامع طراحی و توسعه پلتفرم خدمات آنلاین «سرویسا»</h1>
<p class="pc-sub">نسخه ارتقایافتهٔ مدل آچاره — نیازمندی‌ها، مدل داده، API، وایرفریم و برآورد زمان و هزینه</p>
<div class="pc-meta">
  <div><b>نسخه سند:</b> ۱.۰</div><div><b>تاریخ انتشار:</b> ۱۴۰۵/۰۷/۱۰</div>
  <div><b>مخاطب:</b> تیم محصول و فنی، پیمانکار توسعه</div><div><b>دامنه:</b> ۶ ماژول + لایه هوشمندی AI + داده + API + نمونه اولیه + RFP</div>
  <div><b>حجم محتوا:</b> ۱۱۳ جدول اسکیمای اجرایی، ۷۰ اندپوینت، ۵۰ وایرفریم</div>
  <div><b>مبنا:</b> تحلیل ساختاری پلتفرم آچاره + شکاف‌های تجربه کاربری</div>
</div>
<div class="pc-note">این نسخه برای چاپ و مذاکره با پیمانکار آماده شده است. پیوست‌های فنی قابل اجرا:
<code>db/schema.sql</code> (۱۱۳ جدول)، <code>api/openapi-core.yaml</code> (۶۲ عملیات)،
<code>prototype/index.html</code> (نمونه اولیه کلیک‌پذیر) و <code>mvp/</code> (اسکلت اجرایی سفارش، Escrow و تخصیص).
همه اعداد هزینه، برنامه‌ریزی‌محور و نیازمند تأیید بازار است.</div>
</div>"""

out = f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>سند جامع پلتفرم خدمات آنلاین سرویسا — نیازمندی، معماری داده، API و RFP</title>
<style>
{CSS}
</style>
</head>
<body>
{cover}
<div class="layout">
{NAV}
<main>
{header}
{chr(10).join(parts)}
<footer class="doc">سند تهیه‌شده برای تیم محصول/فنی و پیمانکار — قابل چاپ (Print to PDF). همه قیمت‌ها و برآوردها برنامه‌ریزی‌محور و نیازمند تأیید بازار هستند.</footer>
</main>
</div>
</body>
</html>
"""

dst = os.path.join(ROOT, 'index.html')
open(dst, 'w', encoding='utf-8').write(out)
print("WROTE", dst, len(out), "bytes")
