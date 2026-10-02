#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_pdf.py — تبدیل اسناد HTML فارسی (RTL) به PDF با Chromium/Playwright.

چرا این مسیر؟ در این محیط بدون دسترسی root، کتابخانه‌های بومی مثل wkhtmltopdf/weasyprint نصب‌شدنی
نیستند؛ در عوض Chromium بی‌سر (playwright install chromium) همراه با کتابخانه‌های استخراج‌شده در
`~/.cache/chromium-libs` استفاده می‌شود. فونت‌ها به‌صورت @font-face از `assets/fonts` درون HTML
جاسازی می‌شوند، بنابراین متن فارسی در PDF درست رندر و فونت در فایل embed می‌شود.

نمونه اجرا:
    python3 tools/build_pdf.py spec/index.html pdf/care-plus-spec.pdf \
        --title "سند جامع سرویسا" --toc-level 2
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
HOME = pathlib.Path.home()
CHROMIUM_LIBS = HOME / ".cache" / "chromium-libs"


def bootstrap_ld_path() -> None:
    """افزودن مسیر کتابخانه‌های استخراج‌شده Chromium به LD_LIBRARY_PATH (پیش از اجرای مرورگر)."""
    if CHROMIUM_LIBS.exists():
        cur = os.environ.get("LD_LIBRARY_PATH", "")
        if str(CHROMIUM_LIBS) not in cur.split(":"):
            os.environ["LD_LIBRARY_PATH"] = f"{CHROMIUM_LIBS}:{cur}" if cur else str(CHROMIUM_LIBS)


def ensure_fonts(html_path: pathlib.Path) -> str:
    """اگر HTML فونت جاسازی‌شده ندارد، بلوک @font-face با فونت‌های وزیرمتن را تزریق می‌کند."""
    css_fonts = ROOT / "assets" / "fonts"
    if not css_fonts.exists():
        return ""
    faces = []
    for weight, fname in ((400, "Vazirmatn-Regular.ttf"), (500, "Vazirmatn-Medium.ttf"),
                          (600, "Vazirmatn-SemiBold.ttf"), (700, "Vazirmatn-Bold.ttf")):
        f = css_fonts / fname
        if f.exists():
            faces.append(
                "@font-face{font-family:'Vazirmatn';font-style:normal;font-weight:%d;font-display:block;"
                "src:url('file://%s') format('truetype');}" % (weight, f))
    return "\n".join(faces)


def prepare_html(src: pathlib.Path, tmp_dir: pathlib.Path) -> pathlib.Path:
    html = src.read_text(encoding="utf-8")
    faces = ensure_fonts(src)
    if faces and "@font-face" not in html:
        html = html.replace("</style>", faces + "\n</style>", 1) if "</style>" in html else \
            f"<style>{faces}</style>" + html
    # فونت پیش‌فرض سراسری برای اطمینان از رندر فارسی
    override = ("<style>html,body{font-family:'Vazirmatn',Tahoma,sans-serif !important}"
                "table,th,td,pre,code,button{font-family:'Vazirmatn',Tahoma,monospace !important}"
                "@page{size:A4;margin:14mm 12mm 16mm 12mm}</style>")
    html = html.replace("</head>", override + "</head>", 1)
    out = tmp_dir / (src.stem + ".print.html")
    out.write_text(html, encoding="utf-8")
    return out


def build(src: str, dst: str, *, title: str = "", footer: bool = True, toc_level: int = 0,
          wait_ms: int = 1200) -> dict:
    from playwright.sync_api import sync_playwright

    bootstrap_ld_path()
    src_path = (ROOT / src).resolve() if not os.path.isabs(src) else pathlib.Path(src)
    dst_path = (ROOT / dst).resolve() if not os.path.isabs(dst) else pathlib.Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = ROOT / ".build_tmp"
    tmp_dir.mkdir(exist_ok=True)
    print_path = prepare_html(src_path, tmp_dir)

    header = ""
    if title:
        header = ("<div style=\"font-family:Vazirmatn,Tahoma;font-size:7pt;color:#7a8b99;"
                  "width:100%;padding:0 12mm;direction:rtl;text-align:right\">" + title + "</div>")
    foot = ""
    if footer:
        foot = ("<div style=\"font-family:Vazirmatn,Tahoma;font-size:7pt;color:#7a8b99;width:100%;"
                "padding:0 12mm;direction:rtl;display:flex;justify-content:space-between\">"
                "<span>" + (title or "") + "</span>"
                "<span>صفحه <span class=\"pageNumber\"></span> از <span class=\"totalPages\"></span></span></div>")

    t0 = time.time()
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox", "--font-render-hinting=none",
                                          "--disable-lcd-text", "--force-color-profile=srgb"])
        page = browser.new_page(viewport={"width": 1240, "height": 1754})
        page.goto(print_path.as_uri(), wait_until="load", timeout=120_000)
        page.wait_for_timeout(wait_ms)
        info = page.evaluate("""() => ({
            sections: document.querySelectorAll('section').length,
            tables: document.querySelectorAll('table').length,
            figures: document.querySelectorAll('svg').length,
            height: document.documentElement.scrollHeight,
            fonts: Array.from(document.fonts).filter(f => f.status === 'loaded').length
        })""")
        page.emulate_media(media="print")
        page.pdf(path=str(dst_path), format="A4", print_background=True,
                 display_header_footer=bool(header or foot),
                 header_template=header or "<span></span>", footer_template=foot or "<span></span>",
                 margin={"top": "16mm", "bottom": "16mm", "left": "12mm", "right": "12mm"},
                 prefer_css_page_size=False, outline=(toc_level > 0), tagged=True)
        browser.close()

    size = dst_path.stat().st_size
    print(f"✓ PDF ساخته شد: {dst_path.relative_to(ROOT)}  ({size:,} بایت، {time.time()-t0:.1f} ثانیه)")
    print(f"  بخش‌ها: {info['sections']} | جدول‌ها: {info['tables']} | شکل‌ها: {info['figures']} | "
          f"فونت‌های بارگذاری‌شده: {info['fonts']}")
    return {"pdf": str(dst_path), "bytes": size, **info}


def _cli() -> int:
    ap = argparse.ArgumentParser(description="ساخت PDF فارسی از HTML (Chromium/Playwright)")
    ap.add_argument("src", help="مسیر فایل HTML ورودی نسبت به ریشه پروژه")
    ap.add_argument("dst", help="مسیر PDF خروجی (پوشه pdf/ برای ماندگاری مناسب است)")
    ap.add_argument("--title", default="", help="عنوان در سرصفحه/پاصفحه")
    ap.add_argument("--no-footer", action="store_true")
    ap.add_argument("--toc", type=int, default=2, help="سطح ساخت فهرست (bookmarks)؛ ۰ = بدون")
    args = ap.parse_args()
    build(args.src, args.dst, title=args.title, footer=not args.no_footer, toc_level=args.toc)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
