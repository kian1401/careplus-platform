#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
دموی خط فرمان — اجرای کامل چرخه سفارش تا آزادسازی پول، بدون نیاز به HTTP.

نمونه:
    python3 demo.py                 # سناریوی موفق
    python3 demo.py reject_then_escalate
    python3 demo.py no_provider
    python3 demo.py accept --surge 1.4 --offline 4
    python3 demo.py --reset         # فقط بازنشانی داده دمو
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core import Base, SessionLocal, engine                      # noqa: E402
from app.scenario import run_scenario, snapshot                      # noqa: E402
from app.seed import ensure_seed, reset_database                     # noqa: E402


def nf(n: int | float | None) -> str:
    if n is None:
        return "—"
    return f"{int(n):,}"


def main() -> int:
    ap = argparse.ArgumentParser(description="دموی سرویسا MVP")
    ap.add_argument("tactic", nargs="?", default="accept",
                    choices=["accept", "reject_then_escalate", "no_provider"])
    ap.add_argument("--surge", type=float, default=None, help="ضریب تقاضای منطقه (0.95 تا 1.5)")
    ap.add_argument("--offline", type=int, default=0, help="تعداد متخصص که آفلاین می‌شوند")
    ap.add_argument("--reset", action="store_true", help="بازنشانی داده دمو پیش از اجرا")
    ap.add_argument("--json", action="store_true", help="خروجی JSON خام")
    args = ap.parse_args()

    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        if args.reset:
            reset_database(db)
            db.commit()
            print("داده دمو بازنشانی شد.")
        ensure_seed(db)
        db.commit()

        result = run_scenario(db, tactic=args.tactic, offline_providers=args.offline, surge=args.surge)
        db.commit()

        if args.json:
            import json
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
            return 0

        line = "─" * 78
        print(f"\n{line}\nسناریو: {args.tactic}   |   سفارش: {result['order_code']}   |   "
              f"متخصص تخصیص‌یافته: {result['provider_id'][:8]}…\n{line}")
        for s in result["steps"]:
            print(f"\n[{s['step']:>2}] {s['title_fa']}\n     {s['detail_fa']}")
        snap = result["snapshot"]
        L = snap["ledger"]
        print(f"\n{line}\nجمع سفارش‌ها: {snap['counts']['orders']}   |   "
              f"متخصص آنلاین: {snap['counts']['providers_online']}   |   "
              f"پیشنهادها: {snap['counts']['offers']}   |   قیدهای دفتر کل: {snap['counts']['ledger_entries']}")
        print(f"دفتر کل متوازن: {'بله ✓' if L['balanced'] else 'خیر ✗'}   |   "
              f"بدهکار: {nf(L['total_debit'])} ریال   |   بستانکار: {nf(L['total_credit'])} ریال")
        print("\nحساب‌های دفتر کل:")
        for code, v in sorted(L["accounts"].items()):
            print(f"   {code:<28} بدهکار {nf(v['DEBIT']):>16}   بستانکار {nf(v['CREDIT']):>16}")
        print(f"\n{line}\nبرای تجربه گرافیکی:  uvicorn app.main:app --port 8000  سپس http://localhost:8000/\n")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
