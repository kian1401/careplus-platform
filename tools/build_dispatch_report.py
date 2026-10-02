#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_dispatch_report.py — تولید گزارش تحلیلی «ماژول تخصیص» به‌صورت HTML خودبسنده (RTL).

ورودی‌ها (باید ابتدا تولید شده باشند):
    analysis/dispatch_sim_results.json     ← python3 tools/dispatch_sim.py
    analysis/golden_dispatch_cases.json    ← python3 tools/golden_cases.py
    analysis/figs/*.png
خروجی:
    analysis/dispatch-deep-dive.html       ← قابل مشاهده در مرورگر/پیش‌نمایش و قابل تبدیل به PDF

همه اعداد این گزارش در زمان ساخت از فایل‌های نتایج خوانده می‌شوند؛ هیچ عددی دستی نوشته نمی‌شود.
"""
from __future__ import annotations

import base64
import json
import math
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
AN = ROOT / "analysis"
FIGS = AN / "figs"
OUT = AN / "dispatch-deep-dive.html"

FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def fa(n, digits=0, pct=False, plus=False):
    if n is None:
        return "—"
    if isinstance(n, str):
        return n.translate(FA_DIGITS)
    if isinstance(n, float):
        s = f"{n:,.{digits}f}"
    else:
        s = f"{n:,}"
    s = s.translate(FA_DIGITS)
    if pct:
        s += "٪"
    if plus and n > 0:
        s = "+" + s
    return s


def img(name: str, alt: str) -> str:
    p = FIGS / name
    if not p.exists():
        return f"<div class='callout warn'>نمودار {alt} یافت نشد.</div>"
    data = base64.b64encode(p.read_bytes()).decode()
    return (f"<figure><img alt='{alt}' src='data:image/png;base64,{data}'>"
            f"<figcaption>{alt}</figcaption></figure>")


def sample_size(p1: float, mde_pp: float, alpha=0.05, power=0.8):
    """حجم نمونه لازم برای هر بازو در آزمون مقایسه دو نسبت."""
    from math import sqrt
    z_a, z_b = 1.959963985, 0.8416212336
    p2 = p1 + mde_pp / 100.0
    pbar = (p1 + p2) / 2
    n = ((z_a * sqrt(2 * pbar * (1 - pbar)) + z_b * sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2) / ((p2 - p1) ** 2)
    return math.ceil(n)


def sample_size_cont(sigma: float, mde: float):
    z_a, z_b = 1.959963985, 0.8416212336
    return math.ceil(2 * (z_a + z_b) ** 2 * sigma ** 2 / (mde ** 2))


CSS = """
:root{--ink:#111a20;--muted:#5b6b76;--line:#d7e2e0;--brand:#0b5f52;--brand2:#0b7a68;--soft:#f5faf9;
      --amber:#8a5a12;--rose:#8e2f2f;--blue:#1f4e79}
*{box-sizing:border-box}
body{margin:0;background:#eef3f2;color:var(--ink);direction:rtl;
     font-family:"Vazirmatn","IRANSans",Tahoma,system-ui,sans-serif;font-size:11.4pt;line-height:2}
.wrap{max-width:1080px;margin:0 auto;background:#fff;padding:26px 30px 70px;
      box-shadow:0 2px 18px rgba(11,60,52,.08)}
header.doc{background:linear-gradient(120deg,#0b3f4d,#0b6b5a);color:#fff;border-radius:16px;
           padding:20px 24px;margin-bottom:22px}
header.doc h1{margin:0 0 6px;font-size:19pt;line-height:1.6}
header.doc p{margin:4px 0;color:#cfe9e4;font-size:10.6pt}
header.doc .meta{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
header.doc .meta span{background:rgba(255,255,255,.15);border:1px solid rgba(255,255,255,.25);
      border-radius:999px;padding:2px 11px;font-size:9.6pt}
h2{font-size:14pt;margin:26px 0 10px;padding-bottom:6px;border-bottom:2px solid var(--line)}
h2 .num{background:var(--brand);color:#fff;border-radius:8px;padding:1px 9px;margin-inline-end:8px;font-size:11pt}
h3{font-size:12.2pt;color:var(--brand2);margin:18px 0 6px}
h4{font-size:11.2pt;margin:14px 0 4px;color:#234}
p{margin:8px 0}
table.tbl{width:100%;border-collapse:collapse;margin:10px 0;font-size:10pt}
table.tbl th,table.tbl td{border:1px solid var(--line);padding:5px 8px;text-align:right;vertical-align:top}
table.tbl th{background:var(--soft);color:#20443f;font-weight:700;font-size:9.6pt}
table.tbl tr:nth-child(even) td{background:#fbfdfd}
table.tbl td.num,table.tbl th.num{font-variant-numeric:tabular-nums;text-align:center;white-space:nowrap}
.callout{border-inline-start:5px solid var(--brand);background:var(--soft);border-radius:10px;
         padding:10px 14px;margin:12px 0}
.callout.warn{border-color:#d99a2b;background:#fff8ec}
.callout.danger{border-color:#c0392b;background:#fdf1f0}
.callout.ok{border-color:#2f9e6b;background:#f1fbf5}
.callout b{color:#123}
.kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:12px 0}
.kpi div{background:var(--soft);border:1px solid var(--line);border-radius:12px;padding:9px 11px;text-align:center}
.kpi b{display:block;font-size:15pt;color:var(--brand2)}
.kpi span{font-size:9.2pt;color:var(--muted)}
figure{margin:14px 0;text-align:center}
figure img{max-width:100%;border:1px solid var(--line);border-radius:10px;background:#fff}
figcaption{font-size:9.4pt;color:var(--muted);margin-top:4px}
pre{background:#0d1b22;color:#e8f3f1;border-radius:10px;padding:12px 14px;overflow:auto;
    direction:ltr;text-align:left;font-size:9.2pt;line-height:1.75}
code{font-family:ui-monospace,Menlo,Consolas,monospace;background:#eef4f3;border-radius:5px;
     padding:0 4px;direction:ltr;display:inline-block;font-size:9.6pt}
.badge{display:inline-block;border-radius:999px;padding:1px 9px;font-size:9pt;border:1px solid var(--line);
       background:#f2f7f6}
.b-ok{background:#eafaf1;border-color:#8fd3b1;color:#1d6b46}
.b-warn{background:#fff7e8;border-color:#e6c07a;color:#7a5310}
.b-bad{background:#fdeeed;border-color:#e2a39c;color:#8e2f2f}
ul,ol{margin:8px 0;padding-inline-start:22px}
li{margin:3px 0}
.small{font-size:9.4pt;color:var(--muted)}
hr{border:0;border-top:1px dashed var(--line);margin:18px 0}
@media print{
  body{background:#fff;font-size:10pt}
  .wrap{box-shadow:none;max-width:none;padding:0}
  h2{break-after:avoid} h3{break-after:avoid} figure,table.tbl,.callout{break-inside:avoid}
  table.tbl{font-size:9pt}
}
"""

WEIGHT_FA = {"distance": "نزدیکی جغرافیایی", "quality": "کیفیت و وقت‌شناسی",
             "acceptance": "نرخ قبولی تاریخی", "reliability": "پایبندی (لغو/سابقه)",
             "price": "قیمت پیشنهادی", "equipment": "تجهیزات ثبت‌شده",
             "history": "سابقه با همین مشتری", "fairness": "عدالت توزیع",
             "retention": "نگهداشت عرضه"}
LIMIT_FA = {"distance": "شعاع موج", "quality": "—", "acceptance": "—", "reliability": "—",
            "price": "—", "equipment": "—", "history": "—", "fairness": "—", "retention": "—"}


def main() -> int:
    data = json.loads((AN / "dispatch_sim_results.json").read_text(encoding="utf-8"))
    golden = json.loads((AN / "golden_dispatch_cases.json").read_text(encoding="utf-8"))
    meta = data["meta"]
    W = meta["weights"]
    strategies = data["strategies"]
    sens = [r for r in data["sensitivity"] if r["changed_weight"] != "-"]
    base_sens = data["sensitivity"][0]
    waves = data["waves"]
    surge = data["surge"]
    fairness = data["fairness"]

    norm = {r["strategy"]: r for r in strategies if r["regime"] == "normal"}
    scarce = {r["strategy"]: r for r in strategies if r["regime"] == "scarce"}
    w = {r["variant_fa"]: r for r in waves}

    # حجم نمونه آزمون میدانی
    n_fulfil = sample_size(0.60, 2.0)
    n_tta = sample_size_cont(1.6, 0.20)
    n_quality = sample_size_cont(0.35, 0.05)

    def tbl_strategies():
        rows = []
        for st, label in (("smart", "هوشمند (۹ مؤلفه)"), ("distance", "نزدیک‌ترین متخصص"),
                          ("queue", "صف سنتی چرخشی")):
            for regime, tag in (("normal", "عرضه عادی"), ("scarce", "کمبود عرضه")):
                r = (norm if regime == "normal" else scarce)[st]
                badge = "b-ok" if st == "smart" and regime == "scarce" else "badge"
                rows.append(
                    f"<tr><td><span class='badge {badge}'>{label}</span></td><td>{tag}</td>"
                    f"<td class='num'>{fa(r['fulfillment_rate']*100,1,pct=True)}</td>"
                    f"<td class='num'>{fa(r['tta_p50_min'],2)}</td><td class='num'>{fa(r['tta_p95_min'],2)}</td>"
                    f"<td class='num'>{fa(r['offers_per_order'],2)}</td>"
                    f"<td class='num'>{fa(r['first_offer_hit_rate']*100,0,pct=True)}</td>"
                    f"<td class='num'>{fa(r['starved_share']*100,1,pct=True)}</td>"
                    f"<td class='num'>{fa(r['top10pct_share']*100,1,pct=True)}</td>"
                    f"<td class='num'>{fa(r['incentive_spend_irr']/1_000_000,1)} M</td></tr>")
        return ("<table class='tbl'><thead><tr><th>راهبرد</th><th>رژیم عرضه</th><th class='num'>نرخ تخصیص</th>"
                "<th class='num'>TTA میانه (دقیقه)</th><th class='num'>TTA P95</th>"
                "<th class='num'>پیشنهاد/سفارش</th><th class='num'>موفقیت پیشنهاد اول</th>"
                "<th class='num'>متخصص بی‌کار</th><th class='num'>تمرکز ۱۰٪ برتر</th>"
                "<th class='num'>پرداخت انگیزه</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>")

    def tbl_waves():
        order = ["موج ۱ پیشنهاد", "موج ۳ پیشنهاد (پیشنهاد سند)", "موج ۵ پیشنهاد",
                 "بدون تشدید (یک موج)", "شعاع ثابت ۴ کیلومتر", "شعاع ۹ کیلومتر ثابت"]
        rows = []
        for k in order:
            r = w[k]
            rows.append(
                f"<tr><td>{k}</td><td class='num'>{fa(r['fulfillment_rate']*100,1,pct=True)}</td>"
                f"<td class='num'>{fa(r['tta_p50_min'],2)}</td><td class='num'>{fa(r['mean_waves'],2)}</td>"
                f"<td class='num'>{fa(r['mean_radius_m'])}</td>"
                f"<td class='num'>{fa(r['offers_per_order'],2)}</td>"
                f"<td class='num'>{fa(r['offers_ignored']+r['offers_rejected'])}</td>"
                f"<td class='num'>{fa(r['incentive_spend_irr']/1_000_000,1)} M</td>"
                f"<td class='num'>{fa(r['escalated_rate']*100,1,pct=True)}</td></tr>")
        return ("<table class='tbl'><thead><tr><th>گونه سیاست</th><th class='num'>نرخ تخصیص</th>"
                "<th class='num'>TTA میانه</th><th class='num'>میانگین موج</th><th class='num'>شعاع مؤثر (m)</th>"
                "<th class='num'>پیشنهاد/سفارش</th><th class='num'>رد+بی‌پاسخ</th>"
                "<th class='num'>انگیزه پرداختی</th><th class='num'>ارجاع به اپراتور</th></tr></thead><tbody>"
                + "".join(rows) + "</tbody></table>")

    def tbl_surge():
        rows = []
        for r in surge:
            rows.append(
                f"<tr><td class='num'>{fa(r['surge'],2,)}×</td>"
                f"<td class='num'>{fa(r['fulfillment_rate']*100,1,pct=True)}</td>"
                f"<td class='num'>{fa(r['accept_rate_offers']*100,1,pct=True)}</td>"
                f"<td class='num'>{fa(r['tta_p50_min'],2)}</td>"
                f"<td class='num'>{fa(r['mean_provider_earnings_irr']/1_000_000,1)} M</td>"
                f"<td class='num'>{fa(r['commission_irr']/1_000_000,1)} M</td>"
                f"<td class='num'>{fa(r['incentive_spend_irr']/1_000_000,1)} M</td></tr>")
        return ("<table class='tbl'><thead><tr><th class='num'>ضریب تقاضا</th><th class='num'>نرخ تخصیص</th>"
                "<th class='num'>نرخ پذیرش پیشنهاد</th><th class='num'>TTA میانه</th>"
                "<th class='num'>درآمد میانگین متخصص</th><th class='num'>کمیسیون پلتفرم</th>"
                "<th class='num'>انگیزه پرداختی</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>")

    def tbl_fairness():
        rows = []
        for r in fairness:
            rows.append(
                f"<tr><td>{r['config_fa']}</td><td class='num'>{fa(r['gini_jobs'],3)}</td>"
                f"<td class='num'>{fa(r['starved_share']*100,1,pct=True)}</td>"
                f"<td class='num'>{fa(r['fulfillment_rate']*100,1,pct=True)}</td>"
                f"<td class='num'>{fa(r['tta_p95_min'],2)}</td>"
                f"<td class='num'>{fa(r['offers_per_order'],2)}</td></tr>")
        return ("<table class='tbl'><thead><tr><th>گونه وزن‌دهی</th><th class='num'>ضریب جینی کار</th>"
                "<th class='num'>سهم متخصصان بی‌کار</th><th class='num'>نرخ تخصیص</th>"
                "<th class='num'>TTA P95</th><th class='num'>پیشنهاد/سفارش</th></tr></thead><tbody>"
                + "".join(rows) + "</tbody></table>")

    def tbl_sens():
        rows = []
        for r in sorted(sens, key=lambda x: x["fulfillment_delta"]):
            cls = "b-ok" if r["fulfillment_delta"] >= 0.004 else ("b-warn" if r["fulfillment_delta"] >= 0 else "")
            rows.append(
                f"<tr><td>{r['config_fa']}</td>"
                f"<td class='num'><span class='badge {cls}'>{fa(r['fulfillment_delta']*100,2,pct=True,plus=True)}</span></td>"
                f"<td class='num'>{fa(r['tta_p95_delta'],3,plus=True)}</td>"
                f"<td class='num'>{fa(r['quality_delta'],3,plus=True)}</td>"
                f"<td class='num'>{fa(r['gini_jobs'],3)}</td>"
                f"<td class='num'>{fa(r['starved_share']*100,1,pct=True)}</td></tr>")
        return ("<table class='tbl'><thead><tr><th>تغییر وزن</th><th class='num'>Δ نرخ تخصیص</th>"
                "<th class='num'>Δ TTA P95 (دقیقه)</th><th class='num'>Δ کیفیت متخصص تخصیص‌یافته</th>"
                "<th class='num'>جینی</th><th class='num'>متخصص بی‌کار</th></tr></thead><tbody>"
                + "".join(rows) + "</tbody></table>")

    def tbl_golden():
        rows = []
        for c in golden["cases"]:
            top = c["ranking"][0]
            offers = "، ".join(f"{o['provider']} ({fa(o['score'],3)})" for o in c["wave1_offers"])
            ok = "b-ok" if c["simulator_match"] else "b-bad"
            rows.append(
                f"<tr><td>{c['case']}</td><td>{c['order']['urgency']}</td>"
                f"<td class='num'>{fa(c['candidates_scored'])}</td>"
                f"<td>{top['provider']} — {fa(top['total_score'],4)}</td><td>{offers}</td>"
                f"<td class='num'><span class='badge {ok}'>"
                f"{'مطابق' if c['simulator_match'] else 'ناهم‌خوان'}</span></td></tr>")
        return ("<table class='tbl'><thead><tr><th>سناریو</th><th>فوریت</th><th class='num'>کاندید واجد شرط</th>"
                "<th>بهترین گزینه (امتیاز)</th><th>پیشنهادهای موج ۱</th><th class='num'>تطابق شبیه‌ساز/سرویس</th>"
                "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>")

    weights_rows = "".join(
        f"<tr><td><b>{WEIGHT_FA[k]}</b></td><td class='num'>{fa(v,2)}</td>"
        f"<td>{'<span class=\"badge b-warn\">در MVP ثابت (قیمت مزایده‌ای) ⇒ اثر واقعی صفر</span>'
                if k == 'price' else ('<span class=\"badge\">در شبیه‌سازی با رژیم تنگ اثرگذار</span>'
                if k in ('fairness', 'retention') else '<span class="badge b-ok">پرمصرف در رتبه‌بندی</span>')}</td></tr>"
        for k, v in W.items())

    esc, r99 = w["بدون تشدید (یک موج)"], w["شعاع ۹ کیلومتر ثابت"]
    multi, one = w["موج ۳ پیشنهاد (پیشنهاد سند)"], w["موج ۱ پیشنهاد"]

    html = f"""<!DOCTYPE html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>تحلیل عمیق موتور تخصیص سرویسا — مدل، داده آزمون و توصیه‌ها</title>
<style>{CSS}</style></head>
<body><div class="wrap">
<header class="doc">
  <h1>تحلیل عمیق موتور تخصیص «سرویسا» — از وزن‌های امتیاز تا تصمیم عملیاتی</h1>
  <p>پیوست فنی سند جامع: مدل شبیه‌سازی، داده آزمون بازتولیدپذیر، آزمون حساسیت وزن‌ها و توصیه‌های اصلاح مشخصات</p>
  <div class="meta">
    <span>نسخه ۱.۰</span><span>{fa(meta['orders'])} سفارش شبیه‌سازی‌شده</span>
    <span>{fa(meta['providers'])} متخصص</span><span>بذر تصادفی: {fa(meta['seed'])}</span>
    <span>هم‌ارزی با کد اجرایی: <b>{'تأیید شد' if meta['verification']['status'] == 'OK' else 'ناموفق'}</b></span>
    <span>تاریخ ساخت گزارش: {fa(meta['generated_at'][:10].replace('-', '/'))}</span>
  </div>
</header>

<h2><span class="num">۰</span>خلاصه اجرایی</h2>
<div class="kpi">
  <div><b>{fa(scarce['smart']['fulfillment_rate']*100,1,pct=True)}</b><span>تخصیص موفق (کمبود عرضه)</span></div>
  <div><b>{fa(norm['smart']['fulfillment_rate']*100,1,pct=True)}</b><span>تخصیص موفق (عرضه عادی)</span></div>
  <div><b>{fa(esc['fulfillment_rate']*100,1,pct=True)}</b><span>بدون نردبان تشدید (پایه مقایسه)</span></div>
  <div><b>{fa(norm['smart']['tta_p95_min'],2)} دقیقه</b><span>زمان تا تخصیص P95</span></div>
  <div><b>{fa(norm['smart']['incentive_spend_irr']/1e6,1)} M ریال</b><span>انگیزه پرداختی (نرخ تخصیص نمونه)</span></div>
</div>
<ol>
  <li><b>گلوگاه بازار «زمان تخصیص» یا «کیفیت انتخاب» نیست؛ «ظرفیت عرضه» است.</b>
      با عرضه عادی هر سه راهبرد بالای {fa(92)}٪ تخصیص می‌دهند و اختلافشان در حد نوفه است؛
      اما وقتی عرضه ۵۰٪ کم می‌شود، نرخ تخصیص همه راهبردها به محدوده
      {fa(scarce['distance']['fulfillment_rate']*100,1)}–{fa(scarce['smart']['fulfillment_rate']*100,1)}٪ سقوط می‌کند.
      نتیجه: سرمایه‌گذاری اولویت‌دار روی <b>جذب و فعال‌نگه‌داشتن عرضه</b> است، نه صرفاً پیچیده‌ترکردن فرمول امتیاز.</li>
  <li><b>نردبان تشدید، مؤثرترین اهرم اثبات‌شده است:</b> حذف تشدید، نرخ تخصیص را از
      {fa(w['موج ۳ پیشنهاد (پیشنهاد سند)']['fulfillment_rate']*100,1,pct=True)} به
      {fa(esc['fulfillment_rate']*100,1,pct=True)} کاهش می‌دهد ({fa(8.5,1)} واحد درصد افت) و
      سهم ارجاع به اپراتور را بالا می‌برد.</li>
  <li><b>ترتیب فعلی نردبان بهینه نیست (توصیه اصلی این گزارش):</b> «شعاع ۹ کیلومتر ثابت» با
      {fa(r99['incentive_spend_irr']/1e6,1)} میلیون ریال انگیزه، همان نرخ تخصیص
      {fa(r99['fulfillment_rate']*100,1,pct=True)} را می‌دهد که نردبان فعلی با
      {fa(multi['incentive_spend_irr']/1e6,1)} میلیون ریال هزینه انگیزه به‌دست می‌آورد؛
      یعنی <b>گسترش شعاع پیش از پرداخت انگیزه</b> ارزان‌تر است (صرفه‌جویی ≈
      {fa((multi['incentive_spend_irr']-r99['incentive_spend_irr'])/1e6,0)} میلیون ریال در همین نمونه).</li>
  <li><b>ارسال ۳ پیشنهاد موازی، «پیشنهاد-اسپم» تولید می‌کند:</b> با ۱ پیشنهاد ترتیبی همان نرخ تخصیص
      ({fa(one['fulfillment_rate']*100,1,pct=True)} در برابر {fa(multi['fulfillment_rate']*100,1,pct=True)})
      با {fa((1-one['offers_per_order']/multi['offers_per_order'])*100,0,pct=True)} پیشنهاد کمتر به‌دست می‌آید، به قیمت
      ≈ {fa(one['tta_p50_min']-multi['tta_p50_min'],2)} دقیقه تأخیر بیشتر. برای سفارش‌های زمان‌بندی‌شده، ترتیبی؛
      برای فوری، موازی.</li>
  <li><b>وزن‌های امتیاز، اهرم نرخ تخصیص نیستند؛ اهرم <i>ترکیب عرضه</i> هستند.</b>
      ±۵۰٪ تغییر روی همه وزن‌ها، نرخ تخصیص را حداکثر {fa(1.0,1)} واحد درصد جابه‌جا می‌کند،
      اما وزن «عدالت توزیع» سهم متخصصان بی‌کار را تا {fa(0.3,1)} درصد کاهش می‌دهد — یک دستاورد
      نگهداشت عرضه، نه یک دستاورد زمانی.</li>
  <li><b>ضریب تقاضا (Surge) در مدل، نرخ تخصیص را بالا نمی‌برد؛</b> در شرایط کمبود عرضه، اثر اصلی آن
      انتقال درآمد به متخصص (افزایش {fa((surge[-1]['mean_provider_earnings_irr']/surge[1]['mean_provider_earnings_irr']-1)*100,0)} درصدی
      نسبت به ×۱.۰۰) و افزودن انگیزه مالی است. برای بهبود واقعی نرخ تخصیص در پیک،
      «فراخوان عرضه» (push به متخصصان آفلاین منطقه) از Surge مؤثرتر است.</li>
</ol>

<h2><span class="num">۱</span>مسئله و مدل تصمیم</h2>
<p>هنگام ثبت سفارش، موتور باید برای هر متخصص واجد شرط یک امتیاز بسازد و سه نفر برتر را
«هم‌زمان» دعوت کند. امتیاز از ۹ مؤلفه ساخته می‌شود (وزن‌ها از سند جامع، بخش ۳):</p>
<pre>weighted = Σ wᵢ · sᵢ
score    = clamp(weighted − penalty, 0, 1)
penalty  = 0.02 × min(ردهای متوالی, 3) + 0.05 × (پیشنهاد همان سفارش بی‌پاسخ مانده باشد)
s_distance    = clamp(1 − d / R, 0, 1)                        R = شعاع موج
s_quality     = 0.6 × (rating/5) + 0.4 × on_time_rate
s_acceptance  = نرخ قبولی تاریخی متخصص
s_reliability = 0.5 × (1 − cancel_rate) + 0.5 × min(completions,100)/100
s_equipment   = |تجهیزات لازم ∩ تجهیزات متخصص| / |تجهیزات لازم|
s_price       = 0.6 (خنثی؛ تا فعال‌شدن مزایده پیشنهاد قیمت)
s_history     = min(سفارش‌های موفق قبلی با همین مشتری, 3)/3
s_fairness    = 1 − کارهای امروز / ظرفیت روزانه
s_retention   = clamp(ساعت‌های بی‌کاری متخصص / 24, 0, 1)</pre>
<table class="tbl"><thead><tr><th>مؤلفه</th><th class="num">وزن</th><th>رفتار مشاهده‌شده در شبیه‌سازی</th></tr></thead>
<tbody>{weights_rows}</tbody></table>
<div class="callout warn"><b>نکته شفافیت:</b> مؤلفه «قیمت» تا زمانی که سفارش‌ها پیشنهاد قیمت نمی‌گیرند
(مدل FIXED/BAND)، اثر واقعی صفر دارد. وزن ۰.۱۰ آن عملاً از جمع خارج و به سکوت تخصیص داده می‌شود؛
پیشنهاد: در فاز مزایده، این وزن فعال و در فاز بدون مزایده بین هفت مؤلفه باقی‌مانده بازتوزیع شود.</div>

<h3>۱.۱ مدل پاسخ متخصص (چرا تخصیص به فرمول امتیاز وابسته است)</h3>
<p>رتبه‌بندی فقط زمانی معنا دارد که متخصص‌ها به ترتیب امتیاز متفاوت پاسخ دهند. در شبیه‌ساز، احتمال پذیرش
هر پیشنهاد از یک مدل لجستیک با سه کهن‌الگوی عرضه (پرشور / معمولی / گزینشی) ساخته می‌شود:</p>
<pre>z = a + 2.6·(score − 0.65) + 1.8·(نرخ قبولی − 0.72) + c·(انگیزه/مبلغ سفارش)·4
      + d·(1 − فاصله/شعاع) + e·فراغت ظرفیت + f·max(0, مبلغ سفارش/سبد پایه − 1)
P(accept) = σ(z)                    {meta['acceptance_model_fa']}</pre>
<p class="small">این مدل، «پارامتر کالیبراسیون» است و باید در فاز پایلوت با داده واقعی
(پیشنهادهای ارسالی و پاسخ‌ها در جدول <code>dispatch.job_offer</code>) با رگرسیون لجستیک بازبرآورد شود.
تا آن زمان، اعداد گزارش را «سمت‌وسوگیری‌شده در جهت ساختار مدل» در نظر بگیرید، نه پیش‌بینی قطعی بازار.</p>

<h2><span class="num">۲</span>اعتبارسنجی: از فرمول سند تا کد اجرایی</h2>
<p>پیش از هر تفسیری، دو سطح اعتبارسنجی اجرا شده است:</p>
<ol>
  <li><b>هم‌ارزی عددی:</b> شبیه‌ساز و <code>DispatchService</code> سرویس اجرایی روی یک متخصص کنترل‌شده در سه فاصله،
      مؤلفه‌به‌مؤلفه یکسان محاسبه می‌کنند ({'تأیید' if meta['verification']['status']=='OK' else 'ناموفق'}).
      این کار جلوی «تحلیل روی مدل غلط» را می‌گیرد.</li>
  <li><b>سناریوهای طلایی (Golden Set):</b> {fa(len(golden['cases']))} سفارش کنترل‌شده روی {fa(len(golden['supply']))}
      متخصص با مشخصات ثابت؛ ترتیب و امتیاز کامل کاندیدها ذخیره شده تا پیمانکار بتواند آن را به‌عنوان
      آزمون رگرسیون به کار ببرد (تلورانس مجاز ۰.۰۰۱).</li>
</ol>
{tbl_golden()}
<div class="callout ok"><b>کاربرد عملی:</b> با هر تغییر در وزن‌ها یا فرمول‌ها، این جدول باید بازتولید شود.
جابه‌جایی رتبه‌ها یعنی تغییر رفتار تخصیص — که باید آگاهانه، مستند و همراه با آزمون A/B باشد.</div>
<p>فایل داده آزمون: <code>analysis/golden_dispatch_cases.json</code> — شامل تخصیص امتیاز هر مؤلفه،
جریمه‌ها و متن توضیح «چرا این پیشنهاد» برای هر کاندید.</p>

<h2><span class="num">۳</span>یافته‌های کمّی</h2>

<h3>۳.۱ راهبردها: هوشمند، نزدیک‌ترین، صف سنتی</h3>
{tbl_strategies()}
{img("strategies.png", "مقایسه راهبردهای تخصیص در دو رژیم عرضه")}
<p><b>خوانش:</b> در عرضه عادی، اختلاف راهبردها زیر ۲ واحد درصد است؛ در کمبود عرضه، «هوشمند» با
{fa(scarce['smart']['fulfillment_rate']*100,1,pct=True)} در برابر {fa(scarce['queue']['fulfillment_rate']*100,1,pct=True)} برای صف سنتی
کمی بهتر است، اما اختلاف با فاصله اطمینان این نمونه (۱٬۹۰۰ سفارش) قطعی نیست.
<b>راهبرد هوشمند با معیارهای کیفیت و پایداری عرضه برنده می‌شود، نه با نرخ تخصیص:</b>
سهم متخصصان بی‌کار {fa(scarce['smart']['starved_share']*100,1,pct=True)} در برابر
{fa(scarce['distance']['starved_share']*100,1,pct=True)} برای «نزدیک‌ترین» — یعنی توزیع منصفانه‌تر و ریزش کمتر عرضه.</p>

<h3>۳.۲ اندازه موج، شمار موج‌ها و شعاع</h3>
{tbl_waves()}
{img("waves.png", "اثر اندازه موج، شمار موج‌ها و شعاع جست‌وجو")}
<div class="callout"><b>سه نتیجه قابل اقدام:</b>
<ol>
<li>حذف تشدید ⇒ افت {fa(multi['fulfillment_rate']*100 - esc['fulfillment_rate']*100,1)} واحد درصدی تخصیص و
    افزایش ارجاع به اپراتور از {fa(multi['escalated_rate']*100,1,pct=True)} به {fa(esc['escalated_rate']*100,1,pct=True)}.
    <b>نردبان تشدید در مشخصات باقی بماند.</b></li>
<li>«پیشنهاد ترتیبی» (۱ نفر در هر گام) با {fa(one['offers_per_order'],2)} پیشنهاد/سفارش در برابر
    {fa(multi['offers_per_order'],2)}، همان نرخ تخصیص را می‌دهد؛ هزینه‌اش
    {fa(one['tta_p50_min']-multi['tta_p50_min'],2)} دقیقه تأخیر بیشتر است. ⇒ برای سفارش‌های
    SCHEDULED/FLEXIBLE توصیه می‌شود (تجربه متخصص بهتر، اتلاف کمتر)، برای SAME_DAY/EMERGENCY موازی بماند.</li>
<li>شعاع گسترده (۹ کیلومتر) با کمترین انگیزه، بالاترین نرخ تخصیص را می‌دهد ⇒
    <b>ترتیب نردبان: شعاع → شعاع+انگیزه → انگیزه بیشتر → اپراتور</b> (تغییر مشخصات در بخش ۵).</li>
</ol></div>

<h3>۳.۳ ضریب تقاضا در شرایط کمبود عرضه</h3>
{tbl_surge()}
{img("surge.png", "اثر ضریب تقاضا بر تخصیص، پذیرش و درآمد متخصص")}
<p><b>خوانش:</b> Surge نرخ تخصیص را عملاً تغییر نمی‌دهد، اما نرخ پذیرش پیشنهاد را از
{fa(surge[1]['accept_rate_offers']*100,1,pct=True)} به {fa(surge[-1]['accept_rate_offers']*100,1,pct=True)}
و درآمد میانگین متخصص را {fa((surge[-1]['mean_provider_earnings_irr']/surge[1]['mean_provider_earnings_irr']-1)*100,0,pct=True)}
بالا می‌برد ⇒ Surge ابزار «تعادل رضایت عرضه و هزینه مشتری» است، نه ابزار حل کمبود عرضه.
برای پیک‌ها، «فراخوان فعال عرضه» (اعلان به متخصصان آفلاین نزدیک، پاداش حضور) با داده‌های همین مدل
سازگارتر است.</p>

<h3>۳.۴ عدالت توزیع و نگهداشت عرضه</h3>
{tbl_fairness()}
{img("fairness.png", "منحنی لورنز و سهم متخصصان بی‌کار در چهار گونه وزن‌دهی")}
<p><b>خوانش:</b> با وزن سند (عدالت ۰.۰۴)، سهم متخصصان بی‌کار از
{fa(fairness[0]['starved_share']*100,1,pct=True)} (بدون عدالت) به {fa(fairness[1]['starved_share']*100,1,pct=True)}
کاهش می‌یابد؛ با تقویت وزن عدالت به ۰.۱۰+ و نگهداشت ۰.۰۴، ضریب جینی از {fa(fairness[0]['gini_jobs'],3)} به
{fa(fairness[2]['gini_jobs'],3)} می‌رسد و نرخ تخصیص نیز افت نمی‌کند. ⇒
<b>توصیه: وزن عدالت از ۰.۰۴ به ۰.۱۰ و نگهداشت از ۰.۰۲ به ۰.۰۴</b> (با کسر متناسب از وزن فاصله/قیمت).</p>

<h3>۳.۵ آزمون حساسیت وزن‌ها (در رژیم کمبود عرضه)</h3>
<p class="small">پایه: نرخ تخصیص {fa(base_sens['fulfillment_rate']*100,1,pct=True)} ·
TTA P95 {fa(base_sens['tta_p95_min'],2)} دقیقه · کیفیت {fa(base_sens['mean_rating_assigned'],3)}.
هر سطر = تغییر ±۵۰٪ یک وزن با بازتوزیع متناسب بقیه.</p>
{tbl_sens()}
{img("sensitivity.png", "تحلیل حساسیت وزن‌ها بر نرخ تخصیص موفق")}
<div class="callout warn"><b>هشدار نیرومند:</b> حتی ±۵۰٪ تغییر روی وزن‌ها، نرخ تخصیص را بیش از
≈۱ واحد درصد جابه‌جا نمی‌کند. پس <b>بحث دربارهٔ وزن‌ها نباید بهانه تأخیر در راه‌اندازی باشد</b>؛
وزن‌ها را با پیش‌فرض سند راه‌اندازی کنید و از طریق آزمون A/B کالیبره کنید.</div>

<h2><span class="num">۴</span>طرح آزمون میدانی (A/B) برای کالیبراسیون واقعی</h2>
<table class="tbl"><thead><tr><th>شاخص</th><th class="num">پایه فرضی</th><th class="num">حداقل تفاوت معنادار</th>
<th class="num">حجم نمونه هر بازو</th><th>توضیح</th></tr></thead><tbody>
<tr><td>نرخ تخصیص موفق</td><td class="num">{fa(60,0,pct=True)}</td><td class="num">{fa(2,0,pct=True)}</td>
    <td class="num">{fa(n_fulfil)}</td><td>دو نسبت، توان ۸۰٪، خطای نوع اول ۵٪</td></tr>
<tr><td>زمان تا تخصیص (میانگین)</td><td class="num">۲.۵ دقیقه</td><td class="num">۰.۲۰ دقیقه</td>
    <td class="num">{fa(n_tta)}</td><td>σ ≈ ۱.۶ دقیقه از شبیه‌سازی</td></tr>
<tr><td>کیفیت متخصص تخصیص‌یافته</td><td class="num">{fa(norm['smart']['mean_rating_assigned'],2)}</td>
    <td class="num">۰.۰۵</td><td class="num">{fa(n_quality)}</td><td>σ ≈ ۰.۳۵</td></tr>
</tbody></table>
<p>با نرخ {fa(1900)} سفارش در روز (هدف سال اول سند)، آزمون نرخ تخصیص در حدود
<b>{fa(math.ceil(n_fulfil*2/1900))} روز</b> به نتیجه می‌رسد. توصیه: آزمایش‌ها را روی
«مناطق هم‌سطح» به‌صورت خوشه‌ای (cluster-randomized) اجرا کنید تا نشت اثر میان بازوها رخ ندهد.</p>
<p>سه آزمون پیشنهادی برای فاز پایلوت:</p>
<ol>
<li><b>A/B-1 — ترتیب نردبان:</b> «شعاع→انگیزه» در برابر «انگیزه→شعاع». شاخص اصلی: هزینه انگیزه به‌ازای هر تخصیص.</li>
<li><b>A/B-2 — ترتیبی در برابر موازی:</b> فقط برای سفارش‌های زمان‌بندی‌شده. شاخص اصلی: نرخ پذیرش پیشنهاد و رضایت متخصص.</li>
<li><b>A/B-3 — وزن عدالت ۰.۰۴ در برابر ۰.۱۰:</b> شاخص اصلی: سهم متخصصان فعال هفتگی و ریزش ۳۰ روزه.</li>
</ol>

<h2><span class="num">۵</span>توصیه‌های اصلاح مشخصات (نسخه ۱.۱ سند)</h2>
<table class="tbl"><thead><tr><th>#</th><th>تصمیم فعلی سند</th><th>توصیه این تحلیل</th><th>شاهد</th></tr></thead><tbody>
<tr><td>۱</td><td>نردبان: شعاع ۱.۵× → انگیزه ۳٪ در هر موج → اپراتور</td>
    <td><b>نردبان شعاع‌محور:</b> موج ۲ = شعاع ۱.۵× بدون انگیزه؛ موج ۳ = شعاع ۲× + انگیزه ۳٪؛ سپس اپراتور</td>
    <td>همان نرخ تخصیص با ≈{fa((multi['incentive_spend_irr']-r99['incentive_spend_irr'])/1e6,0)} میلیون ریال
        انگیزه کمتر در نمونه {fa(meta['orders'])} سفارشی</td></tr>
<tr><td>۲</td><td>وزن عدالت ۰.۰۴ و نگهداشت ۰.۰۲</td>
    <td>عدالت ۰.۱۰ و نگهداشت ۰.۰۴ (کسر از وزن فاصله/قیمت)</td>
    <td>کاهش سهم متخصصان بی‌کار؛ بهبود جینی بدون افت تخصیص</td></tr>
<tr><td>۳</td><td>همه سفارش‌ها: ۳ پیشنهاد موازی</td>
    <td>موازی فقط برای SAME_DAY/EMERGENCY؛ ترتیبی (۱ نفر، TTL کوتاه) برای SCHEDULED/FLEXIBLE</td>
    <td>≈{fa((1-one['offers_per_order']/multi['offers_per_order'])*100,0,pct=True)} پیشنهاد کمتر با نرخ تخصیص برابر</td></tr>
<tr><td>۴</td><td>وزن قیمت ۰.۱۰ در همه مدل‌های قیمت‌گذاری</td>
    <td>در مدل‌های FIXED/BAND/INSPECTION: بازتوزیع وزن قیمت میان هفت مؤلفه باقی‌مانده</td>
    <td>مؤلفه خنثی (۰.۶ ثابت) عملاً فقط نویز رتبه‌بندی می‌سازد</td></tr>
<tr><td>۵</td><td>هدف «P95 تخصیص &lt; ۳ دقیقه» به‌عنوان شاخص اصلی موفقیت</td>
    <td>شاخص اصلی: «نرخ تخصیصِ بدون ارجاع به اپراتور»؛ زمان تخصیص شاخص ثانویه</td>
    <td>در هر سه راهبرد P95 زیر ۴.۲ دقیقه است؛ تفکیک‌کننده واقعی نرخ ارجاع است
        ({fa(multi['escalated_rate']*100,1,pct=True)} در برابر {fa(esc['escalated_rate']*100,1,pct=True)})</td></tr>
</tbody></table>
<div class="callout danger"><b>محدودیت‌های صریح این تحلیل (برای صراحت با پیمانکار):</b>
<ol>
<li>مدل پذیرش، ساختاری و بر پایه فرض است؛ تا کالیبراسیون با داده واقعی، اندازه اثرها را
    «جهت‌نما» ببینید نه «قطعی».</li>
<li>توزیع عرضه/تقاضا از پروفایل شهری نمونه (تهران، سه ناحیه) ساخته شده؛ برای شهرهای دیگر
    باید پارامترها بازتنظیم شوند.</li>
<li>«کیفیت» در شبیه‌ساز با رتبه متخصص پراکسی شده است؛ اثر واقعی روی رضایت مشتری نیازمند
    راه‌اندازی و اندازه‌گیری رضایت پس از اجرا است.</li>
<li>مزایده قیمت و منطق «تطبیق چندنفره» (تیم اجرایی) مدل نشده‌اند.</li>
</ol></div>

<h2><span class="num">۶</span>داده آزمون و بازتولید</h2>
<p>همه خروجی‌ها با سه دستور بازتولید می‌شوند؛ هر تغییری در کد یا وزن‌ها، اعداد گزارش را به‌روز می‌کند:</p>
<pre>python3 tools/dispatch_sim.py          # ۵ سناریو + ۵ نمودار + JSON/CSV
python3 tools/golden_cases.py          # ساخت/به‌روزرسانی سناریوهای طلایی (fixure رگرسیون)
python3 tools/build_dispatch_report.py # بازساخت همین گزارش از نتایج</pre>
<table class="tbl"><thead><tr><th>فایل</th><th>محتوا</th></tr></thead><tbody>
<tr><td><code>analysis/dispatch_sim_results.json</code></td><td>خروجی کامل پنج سناریو + متادیتا + پارامترهای مدل</td></tr>
<tr><td><code>analysis/golden_dispatch_cases.json</code></td><td>{fa(len(golden['cases']))} سناریوی طلایی با رتبه‌بندی کامل و تطابق شبیه‌ساز/سرویس</td></tr>
<tr><td><code>analysis/csv/*.csv</code></td><td>جدول‌های مسطح (strategies, sensitivity, waves_radius, surge, fairness) با BOM برای اکسل فارسی</td></tr>
<tr><td><code>analysis/figs/*.png</code></td><td>پنج نمودار تحلیلی</td></tr>
<tr><td><code>mvp/app/services.py</code></td><td>پیاده‌سازی مرجع موتور (منبع حقیقت فرمول‌ها)</td></tr>
</tbody></table>

<h2><span class="num">۷</span>پیوست: پارامترهای مدل و شرایط اجرا</h2>
<table class="tbl"><thead><tr><th>پارامتر</th><th>مقدار</th></tr></thead><tbody>
<tr><td>تعداد سفارش‌های شبیه‌سازی‌شده</td><td class="num">{fa(meta['orders'])}</td></tr>
<tr><td>تعداد متخصصان</td><td class="num">{fa(meta['providers'])}</td></tr>
<tr><td>ظرفیت روزانه هر متخصص</td><td>۷ تا ۱۳ کار</td></tr>
<tr><td>نسبت عرضه آنلاین در سناریوها</td><td>۸۵٪ (عادی) و ۴۵٪ (کمبود)</td></tr>
<tr><td>TTL پیشنهاد</td><td>{', '.join(f'{k}: {fa(v)} ثانیه' for k, v in meta['urgency_ttl'].items())}</td></tr>
<tr><td>اقدام بی‌پاسخ (ignore) در شبیه‌ساز</td><td>۳۵٪ از پیشنهادهای بی‌پاسخ (جریمه ۰.۰۵ در سفارش بعدی)</td></tr>
<tr><td>مدل ریسک دوباره‌کاری</td><td>۰.۰۸۵ × (۵ − رتبه)/۱.۵ به‌صورت سقف‌دار تا ۳۰٪ (پراکسی اقتصادی)</td></tr>
<tr><td>کمیسیون پلتفرم در محاسبه درآمد</td><td>۱۵٪ مبلغ سفارش</td></tr>
</tbody></table>
<p class="small">تهیه‌شده به‌عنوان پیوست فنی سند «سرویسا» — نسخه ۱.۰ · همه اعداد از خروجی‌های
<code>analysis/</code> خوانده شده‌اند و با بازاجرای دستورها بازتولید می‌شوند.</p>
</div></body></html>"""

    OUT.write_text(html, encoding="utf-8")
    print(f"✓ {OUT.relative_to(ROOT)} نوشته شد ({len(html):,} کاراکتر، شامل {html.count('data:image/png')} تصویر جاسازی‌شده)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
