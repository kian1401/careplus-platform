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
def _login(c: TestClient, phone: str, force: bool = False) -> str:
    """ورود با OTP (با کش توکن). توکن کش‌شده پیش از استفاده اعتبارسنجی می‌شود،
    چون `POST /v1/demo/reset` کاربران را بازمی‌سازد و توکن‌های قبلی بی‌اعتبار می‌شوند."""
    if phone in TOKENS and not force:
        me = c.get("/v1/auth/me", headers={"Authorization": f"Bearer {TOKENS[phone]}"})
        if me.status_code == 200:
            return TOKENS[phone]
        TOKENS.pop(phone, None)
    import time as _time
    for attempt in range(4):
        r = c.post("/v1/auth/otp/request", json={"phone": phone})
        if r.status_code == 429:                      # محدودیت نرخ ⇒ اندکی صبر و تلاش دوباره
            _time.sleep(1.5)
            continue
        break
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


# =============================================== ۷) لایه هوشمندی (AI Layer)
def test_ai_intake_standardizes_vague_request(client):
    r = client.post("/v1/ai/intake", json={
        "text": "آب از زیر سینک می‌آید و کابینت خیس شده، نشت قطره‌ای است",
        "media": [{"kind": "IMAGE", "ai_tags": ["water_leak", "under_sink"]}]})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["matched"] is True
    assert d["standard_code"].startswith("STD-")
    assert len(d["scope_standard"]) >= 4, "دامنه استاندارد باید عملیات اجباری داشته باشد"
    assert d["materials_estimate"] and d["materials_total"] > 0
    assert d["price_band"]["low"] < d["price_band"]["high"]
    assert 1 <= len(d["clarifying_questions"]) <= 3, "حداقل تصمیم: حداکثر ۳ پرسش تعیین‌کننده"
    assert all(q["impact_fa"] for q in d["clarifying_questions"]), "هر پرسش باید اثرش را بگوید"
    assert d["skills_required"] and d["equipment_required"]
    CTX["intake"] = d


def test_ai_intake_unknown_text_asks_instead_of_guessing(client):
    d = client.post("/v1/ai/intake", json={"text": "سلام، یک مشکل دارم"}).json()
    assert d["matched"] is False
    assert d["clarifying_questions"], "دسته نامشخص ⇒ پرسش راهنما، نه حدس"


def test_ai_match_preview_keeps_stage1_and_adds_features(client):
    ctx = _paid_order(client, "ai-match-1")
    oid = ctx["order_id"]
    client.post(f"/v1/orders/{oid}/submit-for-dispatch", headers=_auth(ctx["customer_token"]))
    d = client.get(f"/v1/ai/match/preview/{oid}?top=5", headers=OPS_HEADERS).json()
    assert d["items"], "رتبه‌بندی مرحله ۲ خالی است"
    assert abs(sum(d["weights"].values()) - 1.0) < 1e-9, "وزن‌های لایه ۲ باید نرمال باشند"
    for row in d["items"]:
        f = row["ai_features"]
        assert set(f) == {"semantic_fit", "no_show_risk", "price_fairness", "recent_quality"}
        assert all(0 <= v <= 1 for v in f.values()), f
        assert row["stage1_score"] <= 1.0 and 0 < row["ai_score"] <= 1.0
        assert row["ai_rank"] >= 1 and row["ai_explain_fa"]
    scores = [r["ai_score"] for r in d["items"]]
    assert scores == sorted(scores, reverse=True), "خروجی لایه ۲ باید نزولی مرتب باشد"
    assert "stage1_preserved_fa" in d
    CTX["ai_match"] = d


def test_ai_predictive_quality_forecast_and_interventions(client):
    ctx = _paid_order(client, "ai-quality-1")
    d = client.get(f"/v1/ai/quality/forecast/{ctx['order_id']}", headers=OPS_HEADERS).json()
    assert d["risk_band"] in ("LOW", "MEDIUM", "HIGH")
    assert 0 <= d["risk_score"] <= 1
    probs = d["probabilities"]
    assert set(probs) == {"rework", "dispute", "late_finish", "escalation_to_support"}
    assert all(0 <= v <= 1 for v in probs.values())
    assert 1 <= len(d["drivers"]) <= 4 and 1 <= len(d["interventions"]) <= 3
    for i in d["interventions"]:
        assert i["action_fa"] and i["expected_effect_fa"] and i["phase"] in ("MVP", "V1")
    assert d["model_card"]["features_count"] == 9
    assert "human_in_the_loop_fa" in d["model_card"]
    CTX["forecast"] = d


def test_ai_assistant_next_best_actions_per_status(client):
    token = _login(client, DEMO_PHONE_CUSTOMER)
    wallet = client.get("/v1/wallets/me", headers=_auth(token)).json()
    if wallet.get("balance", wallet.get("balance_cached", 0)) < 5_000_000:
        client.post("/v1/wallets/me/topup", json={"amount": 10_000_000}, headers=_auth(token))
    order = _create_order(client, token, key="ai-ux-1")
    oid = _oid(order)

    # وضعیت ۱: پرداخت‌نشده ⇒ اقدام اصلی باید پرداخت باشد
    d = client.get(f"/v1/ai/assistant/{oid}", headers=_auth(token)).json()
    codes = [a["code"] for a in d["next_best_actions"]]
    assert "pay" in codes, f"در وضعیت پرداخت‌نشده، اقدام اصلی باید پرداخت باشد: {codes}"
    assert len(d["next_best_actions"]) <= 3
    assert d["friction_kpi_fa"]["order_completion_target"].startswith("<")

    # وضعیت ۲: پس از پرداخت ⇒ اقدام‌ها باید عوض شوند (تعیین بازه/عکس/دامنه)
    paid = client.post(f"/v1/orders/{oid}/pay?method=WALLET", headers=_auth(token))
    assert paid.status_code == 200, paid.text
    d2 = client.get(f"/v1/ai/assistant/{oid}", headers=_auth(token)).json()
    codes2 = [a["code"] for a in d2["next_best_actions"]]
    assert codes2 != codes, "اقدام بعدی باید با تغییر وضعیت تغییر کند"
    assert "schedule" in codes2, codes2
    assert any("slots" in a for a in d2["next_best_actions"]), "پیشنهاد بازه زمانی باید ارائه شود"

    # وضعیت ۳: در صف تخصیص ⇒ رهگیری زنده و گسترش شعاع
    client.post(f"/v1/orders/{oid}/submit-for-dispatch", headers=_auth(token))
    d3 = client.get(f"/v1/ai/assistant/{oid}", headers=_auth(token)).json()
    codes3 = [a["code"] for a in d3["next_best_actions"]]
    assert "live" in codes3 or "track" in codes3, codes3


def test_ai_scorecards_and_governance(client):
    d = client.get("/v1/ai/scorecards").json()
    assert len(d["models"]) == 4
    assert d["governance_fa"]["fairness"] and d["governance_fa"]["rollback"]
    names = " ".join(m["name_fa"] for m in d["models"])
    for needle in ("استانداردسازی", "رتبه‌بندی", "کیفیت", "اقدام بعدی"):
        assert needle in names
    std = client.get("/v1/ai/standards").json()
    assert std["count"] >= 7
    one = client.get("/v1/ai/standards?slug=plumbing.leak.sink").json()
    assert one["scope"] and one["materials"]


def test_ai_ux_friction_report_requires_ops(client):
    assert client.get("/v1/ai/ux/friction-report").status_code == 401
    d = client.get("/v1/ai/ux/friction-report", headers=OPS_HEADERS).json()
    assert d["recommendations_fa"] and "media_absent_share" in d
