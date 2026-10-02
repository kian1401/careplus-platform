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

from .api import (auth, catalog, demo, ops, orders, payments, providers, trust)
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
    title="سرویسا MVP — API هسته",
    description=(
        "اسکلت اجرایی پلتفرم خدمات آنلاین (مبنای سند جامع): هویت با OTP، کاتالوگ و قیمت‌گذاری پویا، "
        "ثبت سفارش با Idempotency، موتور تخصیص امتیازمحور با موج/تشدید، Escrow روی دفتر کل دوعاملی، "
        "چرخه اختلاف، امتیازدهی چندبعدی و Outbox رویداد."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # در محیط عملیاتی: فقط دامنه‌های مجاز
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (auth, catalog, orders, providers, payments, trust, ops, demo):
    app.include_router(r, prefix="/v1")

app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")


@app.get("/", include_in_schema=False)
def console():
    return FileResponse(os.path.join(HERE, "static", "console.html"))


@app.get("/health", tags=["Meta"])
def health():
    return {"status": "ok", "env": settings.env, "app": settings.app_name}


@app.get("/ready", tags=["Meta"])
def ready():
    db = SessionLocal()
    try:
        services = db.execute(select(ServiceItem)).scalars().first()
        flags = db.execute(select(FeatureFlag)).scalars().first()
        return {"db": "ok", "seeded": bool(services and flags)}
    finally:
        db.close()


@app.exception_handler(Exception)
async def unhandled(_request, exc: Exception):  # پاسخ ساخت‌یافته به‌جای صفحه خطای خام
    return JSONResponse(status_code=500, content={
        "error": {"code": "INTERNAL_ERROR", "message_fa": "خطای غیرمنتظره در سرور.", "details": str(exc)}})


if __name__ == "__main__":  # اجرای مستقیم: python3 -m app.main
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
