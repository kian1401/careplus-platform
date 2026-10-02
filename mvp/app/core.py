# -*- coding: utf-8 -*-
"""
هسته مشترک MVP: تنظیمات، اتصال دیتابیس، امنیت توکن، ابزارها.

نکته: این پروژه «اسکلت اجرایی» سند جامع است؛ برای محیط عملیاتی باید:
  • رازها از KMS/Vault خوانده شوند (نه پیش‌فرض)،
  • دیتابیس PostgreSQL (با PostGIS) با Migration ابزار (Alembic) مدیریت شود،
  • توکن‌ها با JWT/RS256 و Passkey تکمیل شوند.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------------- settings
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CP_", env_file=".env", extra="ignore")

    app_name: str = "سرویسا MVP"
    env: str = "dev"                              # dev | staging | prod
    secret_key: str = os.getenv("CP_SECRET_KEY", "dev-secret-change-me")
    access_ttl_seconds: int = 15 * 60
    database_url: str = f"sqlite:///{os.path.join(BASE_DIR, 'var', 'mvp.sqlite')}"
    otp_ttl_seconds: int = 120
    otp_max_attempts: int = 3
    dev_otp_code: str = "11111"                   # در محیط واقعی: حذف شود
    platform_commission: float = 0.15             # پیش‌فرض کمیسیون (قابل override در provider)
    vat_rate: float = 0.10
    escrow_hold_hours: int = 48                   # پنجره اعتراض پس از اتمام کار
    approve_token: str = "dev-approve-token"       # کلید ساده برای عملیات اپراتور در دمو


settings = Settings()
os.makedirs(os.path.join(BASE_DIR, "var"), exist_ok=True)


# -------------------------------------------------------------------- database
class Base(DeclarativeBase):
    pass


engine_kwargs: dict[str, Any] = {"future": True, "echo": False}
if settings.database_url.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(settings.database_url, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _rec):  # فعال‌سازی کلیدهای خارجی در SQLite
    if settings.database_url.startswith("sqlite"):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


def get_db() -> Iterable[Session]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# --------------------------------------------------------------------- helpers
def now() -> datetime:
    return datetime.now(timezone.utc)


def aware(dt: datetime | None) -> datetime | None:
    """نرمال‌سازی زمان خوانده‌شده از دیتابیس: SQLite مقادیر را naïve برمی‌گرداند."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def new_id() -> str:
    """UUIDv7 مانند و مرتب بر پایه زمان (سازگار با ستون‌های CHAR(36))."""
    ms = int(time.time() * 1000)
    raw = uuid.uuid4().hex
    return f"{ms:012x}-{raw[0:4]}-7{raw[4:7]}-{raw[8:12]}-{raw[12:24]}"


def order_code(seq: int) -> str:
    return f"SR-{time.strftime('%y%m')}-{seq:05d}"


def money(value: int) -> int:
    return int(round(value))


def jdump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def jload(raw: str | None, default: Any = None) -> Any:
    if not raw:
        return default if default is not None else {}
    try:
        return json.loads(raw)
    except Exception:
        return default if default is not None else {}


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """فاصله مستقیم (km) — در نسخه عملیاتی با مسیریابی/شبکه معابر جایگزین می‌شود."""
    from math import asin, cos, radians, sin, sqrt

    r = 6371.0
    dlat, dlng = radians(lat2 - lat1), radians(lng2 - lng1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2
    return 2 * r * asin(sqrt(a))


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def geohash6(lat: float, lng: float) -> str:
    """geohash سبک برای گروه‌بندی منطقه‌ای (بدون وابستگی بیرونی)."""
    alphabet = "0123456789bcdefghjkmnpqrstuvwxyz"
    lat_r, lng_r = [-90.0, 90.0], [-180.0, 180.0]
    out, bit, ch, even = [], 0, 0, True
    while len(out) < 6:
        if even:
            mid = (lng_r[0] + lng_r[1]) / 2
            if lng > mid:
                ch = (ch << 1) | 1
                lng_r[0] = mid
            else:
                ch = ch << 1
                lng_r[1] = mid
        else:
            mid = (lat_r[0] + lat_r[1]) / 2
            if lat > mid:
                ch = (ch << 1) | 1
                lat_r[0] = mid
            else:
                ch = ch << 1
                lat_r[1] = mid
        even = not even
        bit += 1
        if bit == 5:
            out.append(alphabet[ch])
            bit, ch = 0, 0
    return "".join(out)


# ---------------------------------------------------------------------- tokens
def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(txt: str) -> bytes:
    return base64.urlsafe_b64decode(txt + "=" * (-len(txt) % 4))


def make_token(subject: str, role: str, ttl: int | None = None) -> str:
    payload = {
        "sub": subject,
        "role": role,
        "exp": int(time.time()) + (ttl or settings.access_ttl_seconds),
        "jti": secrets.token_hex(6),
    }
    body = _b64(jdump(payload).encode())
    sig = hmac.new(settings.secret_key.encode(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64(sig)}"


def read_token(token: str) -> dict | None:
    try:
        body, sig = token.split(".")
        expected = hmac.new(settings.secret_key.encode(), body.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(sig)):
            return None
        payload = json.loads(_unb64(body))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


def hash_secret(value: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", value.encode(), settings.secret_key.encode(), 60_000).hex()


def constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


def slot_range(minutes_from_now: int, duration_minutes: int = 120) -> tuple[datetime, datetime]:
    start = now() + timedelta(minutes=minutes_from_now)
    return start, start + timedelta(minutes=duration_minutes)
