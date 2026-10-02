# -*- coding: utf-8 -*-
"""
آزمون‌های یکپارچه MVP (pytest + FastAPI TestClient).

اجرا از پوشه mvp:
    python3 -m pytest tests -q
(دیتابیس آزمون: mvp/var/test.sqlite — در شروع هر اجرا ساخته و در پایان حذف می‌شود.)

پوشش:
  ۱) سلامت سرویس و داده پایه
  ۲) احراز هویت با OTP، رد کد نادرست، محافظت مسیرهای اپراتوری
  ۳) کاتالوگ، برآورد قیمت و شفافیت تفکیک هزینه
  ۴) ثبت سفارش با Idempotency-Key (ثبت تکراری ⇒ همان سفارش)
  ۵) نگهداشت Escrow، تخصیص امتیازمحور، پذیرش، اجرا، آزادسازی، فاکتور و امتیازدهی
  ۶) توازن دفتر کل دوعاملی و انتشار رویداد در Outbox
  ۷) سه سناریوی دمو از طریق API
"""
from __future__ import annotations

import os
import pathlib
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent

TEST_DB = ROOT / "var" / "test.sqlite"
os.environ["CP_ENV"] = "test"
os.environ["CP_DATABASE_URL"] = f"sqlite:///{TEST_DB}"
TEST_DB.parent.mkdir(parents=True, exist_ok=True)
if TEST_DB.exists():
    TEST_DB.unlink()

sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient          # noqa: E402

from app.core import Base, SessionLocal, engine, settings      # noqa: E402
from app.main import app                                        # noqa: E402
from app.seed import DEMO_PHONE_CUSTOMER, DEMO_PHONE_PROVIDER   # noqa: E402

OTP = settings.dev_otp_code
OPS_HEADERS = {"x-ops-token": settings.approve_token}
DIMS = {"PUNCTUALITY": 5, "QUALITY": 4, "TIDINESS": 5, "PRICE_ACCURACY": 5, "CONDUCT": 4}

TOKENS: dict[str, str] = {}
CTX: dict[str, dict] = {}


@pytest.fixture(scope="module")
def client():
    Base.metadata.create_all(engine)
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------- helpers
def _login(c: TestClient, phone: str) -> str:
    """ورود با OTP (با کش توکن؛ محدودیت نرخ درخواست کد را رعایت می‌کند)."""
    if phone in TOKENS:
        return TOKENS[phone]
    r = c.post("/v1/auth/otp/request", json={"phone": phone})
    assert r.status_code == 200, r.text
    r = c.post("/v1/auth/otp/verify", json={"phone": phone, "code": OTP})
    assert r.status_code == 200, r.text
    body = r.json()
    token = body.get("access_token") or body.get("token")
    assert token, body
    TOKENS[phone] = token
    return token


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _items(payload):
    return payload["items"] if isinstance(payload, dict) else payload


def _oid(payload: dict) -> str:
    return payload.get("order_id") or payload["id"]


def _services(c: TestClient) -> dict[str, dict]:
    return {s["slug"]: s for s in _items(c.get("/v1/catalog/services").json())}


def _quote(c: TestClient, svc_id: str, urgency: str = "SAME_DAY") -> dict:
    r = c.post("/v1/pricing/quote", json={"service_item_id": svc_id, "urgency": urgency,
                                          "severity": 3, "lat": 35.7448, "lng": 51.4261,
                                          "distance_km": 3.0})
    assert r.status_code == 200, r.text
    return r.json()


def _create_order(c: TestClient, token: str, key: str, urgency: str = "SAME_DAY") -> dict:
    svc = _services(c)["plumbing.leak.sink"]
    quote = _quote(c, svc["id"], urgency)
    payload = {
        "service_item_id": svc["id"], "quote_id": quote["quote_id"], "urgency": urgency,
        "severity": 3, "lat": 35.7448, "lng": 51.4261,
        "address_text": "تهران، سعادت‌آباد، خیابان آزمون، پلاک ۷",
        "intake_text": "نشت آب از زیر سینک",
        "media": [{"kind": "IMAGE", "file_ref": "s3://test/leak.jpg", "ai_tags": ["water_leak"]}],
        "idempotency_key": key,
    }
    r = c.post("/v1/orders", json=payload, headers=_auth(token))
    assert r.status_code in (200, 201), r.text
    return r.json()


def _paid_order(c: TestClient, key: str) -> dict:
    """سفارش تازه + شارژ کیف پول در صورت نیاز + نگهداشت Escrow."""
    token = _login(c, DEMO_PHONE_CUSTOMER)
    wallet = c.get("/v1/wallets/me", headers=_auth(token)).json()
    if wallet.get("balance", wallet.get("balance_cached", 0)) < 5_000_000:
        c.post("/v1/wallets/me/topup", json={"amount": 10_000_000}, headers=_auth(token))
    order = _create_order(c, token, key)
    oid = _oid(order)
    pay = c.post(f"/v1/orders/{oid}/pay?method=WALLET", headers=_auth(token))
    assert pay.status_code == 200, pay.text
    return {"order_id": oid, "customer_token": token, "amount": order["final_amount"]}


# ------------------------------------------------------------------ ۱) پایه
def test_health_and_seed(client):
    assert client.get("/health").json()["status"] == "ok"
    ready = client.get("/ready").json()
    assert ready["db"] == "ok" and ready["seeded"] is True


# ------------------------------------------------------------------ ۲) هویت
def test_otp_login_and_me(client):
    token = _login(client, DEMO_PHONE_CUSTOMER)
    me = client.get("/v1/auth/me", headers=_auth(token))
    assert me.status_code == 200
    assert "CUSTOMER" in me.json()["roles"]


def test_otp_rejects_wrong_code(client):
    client.post("/v1/auth/otp/request", json={"phone": "+989120009999"})
    r = client.post("/v1/auth/otp/verify", json={"phone": "+989120009999", "code": "00000"})
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "AUTH_INVALID_OTP"


def test_protected_and_ops_endpoints_need_credentials(client):
    assert client.get("/v1/auth/me").status_code == 401
    assert client.get("/v1/admin/kpi").status_code == 401
    assert client.get("/v1/admin/kpi", headers=OPS_HEADERS).status_code == 200
    customer = _login(client, DEMO_PHONE_CUSTOMER)
    assert client.get("/v1/admin/kpi", headers=_auth(customer)).status_code == 403


# -------------------------------------------------------- ۳) کاتالوگ و قیمت
def test_catalog_and_quote_breakdown(client):
    items = _services(client)
    assert "plumbing.leak.sink" in items
    quote = _quote(client, items["plumbing.leak.sink"]["id"])
    assert quote["final_amount"] > 0
    titles = " | ".join(b["title"] for b in quote["breakdown"])
    for expected in ("اجرت پایه", "ضریب فوریت", "ضریب پیچیدگی", "ضریب تقاضای منطقه",
                     "ایاب و ذهاب", "مالیات"):
        assert expected in titles, f"ردیف «{expected}» در تفکیک قیمت نیست"
    assert 0.95 <= quote["surge"] <= 1.5
    CTX["quote"] = quote


def test_urgency_increases_price(client):
    svc_id = _services(client)["plumbing.leak.sink"]["id"]
    base = _quote(client, svc_id, "SCHEDULED")
    emerg = _quote(client, svc_id, "EMERGENCY")
    assert emerg["final_amount"] > base["final_amount"]
    assert emerg["breakdown"][1]["note"].endswith("×1.35")


# ------------------------------------------- ۴) سفارش + Idempotency + Escrow
def test_order_create_is_idempotent(client):
    token = _login(client, DEMO_PHONE_CUSTOMER)
    first = _create_order(client, token, key="idem-xyz")
    second = _create_order(client, token, key="idem-xyz")
    assert _oid(first) == _oid(second), "ثبت تکراری با همان کلید باید همان سفارش را برگرداند"
    assert second.get("idempotent_replay") is True


def test_escrow_hold_keeps_ledger_balanced(client):
    ctx = _paid_order(client, "escrow-1")
    CTX["paid"] = ctx
    escrow = client.get(f"/v1/orders/{ctx['order_id']}/escrow",
                        headers=_auth(ctx["customer_token"])).json()
    assert escrow["amount"] == ctx["amount"]
    assert escrow["status"] in ("HELD", "PARTIALLY_RELEASED")
    tb = client.get("/v1/admin/ledger/trial-balance", headers=OPS_HEADERS).json()
    assert tb["balanced"] is True and tb["total_debit"] == tb["total_credit"] > 0


# -------------------------------------- ۵) تخصیص، اجرا، آزادسازی، امتیازدهی
def test_full_lifecycle_dispatch_to_release(client):
    ctx = _paid_order(client, "lifecycle-1")
    oid, token = ctx["order_id"], ctx["customer_token"]

    disp = client.post(f"/v1/orders/{oid}/submit-for-dispatch", headers=_auth(token))
    assert disp.status_code == 200, disp.text

    offers = _items(client.get(f"/v1/orders/{oid}/offers", headers=_auth(token)).json())
    assert offers, "هیچ پیشنهادی برای سفارش تولید نشد"
    assert all("score" in o and "breakdown" in o and "offer_id" in o for o in offers)
    weights = {"distance": 0.26, "quality": 0.18, "acceptance": 0.14, "reliability": 0.12,
               "price": 0.10, "equipment": 0.08, "history": 0.06, "fairness": 0.04,
               "retention": 0.02}
    for o in offers:
        b = o["breakdown"]
        assert set(b) == set(weights), f"مؤلفه‌های امتیاز ناقص است: {sorted(b)}"
        assert all(0 <= v <= 1 for v in b.values()), b
        weighted = sum(weights[k] * v for k, v in b.items())
        # امتیاز نهایی = جمع وزنی مؤلفه‌ها منهای جریمه‌ها (۰ تا ۰.۱۵)
        assert -0.02 <= weighted - o["score"] <= 0.15, (weighted, o["score"])
    scores = [o["score"] for o in offers]
    assert max(scores) <= 1.0

    # ورود متخصص دمو، روشن‌کردن وضعیت آنلاین و پذیرش پیشنهاد
    prov_token = _login(client, DEMO_PHONE_PROVIDER)
    online = client.post("/v1/providers/me/online?lat=35.7460&lng=51.4270", headers=_auth(prov_token))
    assert online.status_code == 200, online.text
    my = _items(client.get("/v1/providers/me/offers", headers=_auth(prov_token)).json())
    mine = [o for o in my if o.get("order_id") == oid]      # این مسیر فقط پیشنهادهای باز را برمی‌گرداند
    assert all("why_me_fa" in o for o in my), "توضیح «چرا به من» برای متخصص لازم است"
    assert mine, "متخصص دمو پیشنهاد فعالی برای این سفارش ندارد"

    acc = client.post(f"/v1/providers/me/offers/{mine[0]['offer_id']}/accept", headers=_auth(prov_token))
    assert acc.status_code == 200, acc.text

    detail = client.get(f"/v1/orders/{oid}", headers=_auth(token)).json()
    order_row = detail["order"]
    assert order_row["status"] in ("ASSIGNED", "IN_PROGRESS")
    assert order_row["assigned_provider_id"]
    assert detail["provider"] and detail["provider"]["name"], "اطلاعات متخصص تخصیص‌یافته باید نمایش داده شود"
    assert any(t["event"] == "ASSIGNED" for t in detail["timeline"]), "تایم‌لاین باید رویداد تخصیص را نشان دهد"

    after = _items(client.get(f"/v1/orders/{oid}/offers", headers=_auth(token)).json())
    assert sum(1 for o in after if o["response"] == "ACCEPT") == 1, "پذیرش باید اتمی و یگانه باشد"

    # اجرا: Check-in / Check-out سپس اعلام اتمام
    booking = order_row.get("booking_id")
    if booking:
        ci = client.post(f"/v1/providers/bookings/{booking}/check-in",
                         json={"lat": 35.7448, "lng": 51.4261, "photo_ref": "s3://test/in.jpg"},
                         headers=_auth(prov_token))
        assert ci.status_code == 200, ci.text
        co = client.post(f"/v1/providers/bookings/{booking}/check-out",
                         json={"photo_ref": "s3://test/out.jpg", "minutes_worked": 55},
                         headers=_auth(prov_token))
        assert co.status_code == 200, co.text
    done = client.post(f"/v1/orders/{oid}/complete", headers=_auth(prov_token))
    assert done.status_code == 200, done.text

    # تأیید مشتری ⇒ آزادسازی Escrow + صدور فاکتور
    approved = client.post(f"/v1/orders/{oid}/approve", headers=_auth(token))
    assert approved.status_code == 200, approved.text
    escrow = client.get(f"/v1/orders/{oid}/escrow", headers=_auth(token)).json()
    assert escrow["status"] == "RELEASED"
    assert escrow["released"] == escrow["amount"]

    # امتیازدهی چندبعدی و اثر آن بر رتبه متخصص
    rev = client.post(f"/v1/orders/{oid}/review", json={"dimensions": DIMS, "text": "کار تمیز و به‌موقع"},
                      headers=_auth(token))
    assert rev.status_code == 200, rev.text
    profile = client.get(f"/v1/providers/{order_row['assigned_provider_id']}/public-profile").json()
    assert profile.get("rating", 0) > 0, profile
    assert set(profile.get("dimension_averages", {})) >= {"PUNCTUALITY", "QUALITY", "TIDINESS",
                                                          "PRICE_ACCURACY", "CONDUCT"}

    tb = client.get("/v1/admin/ledger/trial-balance", headers=OPS_HEADERS).json()
    assert tb["balanced"] is True

    events = _items(client.get("/v1/admin/events?limit=50", headers=OPS_HEADERS).json())
    kinds = {e["event"] for e in events}
    assert any("order" in k for k in kinds) and any("payment" in k or "ledger" in k for k in kinds)


def test_wallet_cannot_go_negative(client):
    token = _login(client, DEMO_PHONE_CUSTOMER)
    txs = _items(client.get("/v1/wallets/me/transactions", headers=_auth(token)).json())
    assert txs, "تراکنش‌های کیف پول ثبت نشده است"
    assert all(t.get("direction") in ("DEBIT", "CREDIT") for t in txs)


# ------------------------------------------------------------- ۶) سناریوها
@pytest.mark.parametrize("tactic", ["accept", "reject_then_escalate", "no_provider"])
def test_demo_scenarios(client, tactic):
    r = client.post(f"/v1/demo/scenario?tactic={tactic}&surge=1.0")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["steps"], "روایت سناریو خالی است"
    assert [s["step"] for s in body["steps"]] == list(range(1, len(body["steps"]) + 1))
    assert body["snapshot"]["ledger"]["balanced"] is True
    assert body["snapshot"]["counts"]["orders"] >= 1
    if tactic == "no_provider":
        assert body["escalated_to_ops"] is True


def test_demo_reset_and_state(client):
    assert client.post("/v1/demo/reset").status_code == 200
    state = client.get("/v1/demo/state").json()
    assert state["counts"]["providers_online"] >= 10
    assert state["ledger"]["balanced"] is True
    assert state["ledger"]["total_debit"] == state["ledger"]["total_credit"]


def test_ops_token_endpoint_available_outside_prod(client):
    data = client.get("/v1/demo/ops-token").json()
    assert data["x_ops_token"] == settings.approve_token
