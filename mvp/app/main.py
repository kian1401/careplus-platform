# -*- coding: utf-8 -*-
"""
main.py — سرور MVP سرویسا (FastAPI).

اجرا:
    uvicorn app.main:app --reload --port 8000      (از پوشه mvp)
یا:
    python3 -m uvicorn app.main:app --port 8000
سپس:  http://localhost:8000/            ← کنسول دمو
        http://localhost:8000/docs       ← مستندات خودکار OpenAPI
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from .api import (ai, auth, catalog, demo, ops, orders, payments, providers, trust)
from .core import Base, SessionLocal, engine, settings
from .models import FeatureFlag, ServiceItem
from .seed import ensure_seed

HERE = os.path.dirname(os.path.abspath(__file__))


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        info = ensure_seed(db)
        db.commit()
        print(f"[seed] {info}")
    finally:
        db.close()
    yield


app = FastAPI(
    title="سرویسا MVP — API هسته (نسخه ۱.۱ با لایه هوشمندی)",
    description=(
        "اسکلت اجرایی پلتفرم خدمات آنلاین (مبنای سند جامع): هویت با OTP، کاتالوگ و قیمت‌گذاری پویا، "
        "ثبت سفارش با Idempotency، موتور تخصیص امتیازمحور با موج/تشدید، Escrow روی دفتر کل دوعاملی، "
        "چرخه اختلاف، امتیازدهی چندبعدی و Outbox رویداد."
    ),
    version="1.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # در محیط عملیاتی: فقط دامنه‌های مجاز
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (auth, catalog, orders, providers, payments, trust, ops, ai, demo):
    app.include_router(r, prefix="/v1")

app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")


@app.get("/", include_in_schema=False)
def console():
    return FileResponse(os.path.join(HERE, "static", "console.html"))


def _build_meta() -> dict:
    """شناسه بیلد برای رهگیری استقرار: نسخه، کامیت گیت (اگر موجود باشد)، زمان بیلد."""
    root = os.path.dirname(os.path.dirname(HERE))          # ریشه مخزن (…/achareh-plus)
    sha = os.getenv("CP_GIT_SHA") or os.getenv("RENDER_GIT_COMMIT", "")
    if not sha:
        try:
            head = os.path.join(root, ".git", "HEAD")
            with open(head, encoding="utf-8") as fh:
                ref = fh.read().strip()
            if ref.startswith("ref:"):
                with open(os.path.join(root, ".git", ref.split(" ", 1)[1].strip()), encoding="utf-8") as fh:
                    sha = fh.read().strip()
            else:
                sha = ref
        except Exception:
            sha = ""
    return {"app": settings.app_name, "version": app.version, "env": settings.env,
            "git_sha": (sha or "unknown")[:12],
            "ai_version": _ai_version(),
            "deployed_at": os.getenv("RENDER_DEPLOY_TIME", "") or _process_start_iso()}


_PROCESS_START = None


def _process_start_iso() -> str:
    global _PROCESS_START
    import datetime as _dt
    if _PROCESS_START is None:
        _PROCESS_START = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()
    return _PROCESS_START


def _ai_version() -> str:
    try:
        from .intelligence import AI_VERSION
        return AI_VERSION
    except Exception:
        return "unknown"


@app.get("/health", tags=["Meta"])
def health():
    """سلامت سرویس + شناسه بیلد (برای health check در Render و رهگیری نسخه مستقر)."""
    return {"status": "ok", "env": settings.env, "app": settings.app_name,
            "build": _build_meta(),
            "checks": {"process": "up"}}


@app.get("/ready", tags=["Meta"])
def ready():
    db = SessionLocal()
    try:
        services = db.execute(select(ServiceItem)).scalars().first()
        flags = db.execute(select(FeatureFlag)).scalars().first()
        from .models import Order
        orders = db.execute(select(Order)).scalars().first()
        return {"db": "ok", "seeded": bool(services and flags), "has_orders": bool(orders),
                "build": _build_meta(), "checks": {"process": "up", "db": "ok"}}
    finally:
        db.close()


@app.exception_handler(Exception)
async def unhandled(_request, exc: Exception):  # پاسخ ساخت‌یافته به‌جای صفحه خطای خام
    return JSONResponse(status_code=500, content={
        "error": {"code": "INTERNAL_ERROR", "message_fa": "خطای غیرمنتظره در سرور.", "details": str(exc)}})


if __name__ == "__main__":  # اجرای مستقیم: python3 -m app.main
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
