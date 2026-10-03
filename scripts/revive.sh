#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# احیای یک‌دستوری دموی «سرویسا / Care+» + ساخت آدرس عمومی تازه
#
#   bash scripts/revive.sh            # سایت استاتیک + اپ + تونل عمومی
#   PORT_APP=8000 PORT_SITE=8080 bash scripts/revive.sh
#
# این اسکریپت روی هر ماشینی (لپ‌تاپ شما، سرور، محیط ابری) کار می‌کند و در پایان
# آدرس‌های عمومی را چاپ و در PUBLIC-URL.txt و live.json می‌نویسد.
# پیش‌نیاز: python3، pip، و اتصال اینترنت. cloudflared در صورت نبود، خودکار دانلود می‌شود.
# ---------------------------------------------------------------------------
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT_APP="${PORT_APP:-8000}"
PORT_SITE="${PORT_SITE:-8080}"
CF="${CF_BIN:-/tmp/cloudflared}"
LOGDIR="${TMPDIR:-/tmp}/cp_revive"; mkdir -p "$LOGDIR"

log(){ echo "[$(date -u +'%H:%M:%S')] $*"; }

# ---------- ۱) وابستگی‌های اپ ----------
if ! python3 -c "import fastapi" 2>/dev/null; then
  log "نصب وابستگی‌های پایتون (یک‌بار)…"
  python3 -m pip install -q -r "$ROOT/mvp/requirements.txt" || { log "✗ نصب وابستگی ناموفق"; exit 2; }
fi

# ---------- ۲) اپ (کنسول دمو) ----------
if ! curl -sS -o /dev/null --max-time 4 "http://127.0.0.1:$PORT_APP/health"; then
  log "راه‌اندازی اپ روی پورت $PORT_APP …"
  mkdir -p "$ROOT/mvp/var"
  ( cd "$ROOT/mvp" && CP_ENV=demo CP_DATABASE_URL="sqlite:///./var/mvp.sqlite" \
      nohup python3 -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT_APP" >"$LOGDIR/app.log" 2>&1 & )
  sleep 7
fi
curl -sS "http://127.0.0.1:$PORT_APP/health" >/dev/null && log "✓ اپ فعال است" || { log "✗ اپ بالا نیامد — $LOGDIR/app.log"; exit 3; }

# ---------- ۳) سایت استاتیک ----------
if ! curl -sS -o /dev/null --max-time 4 "http://127.0.0.1:$PORT_SITE/index.html"; then
  log "راه‌اندازی سایت استاتیک روی پورت $PORT_SITE …"
  nohup python3 -m http.server "$PORT_SITE" --bind 0.0.0.0 --directory "$ROOT" >"$LOGDIR/site.log" 2>&1 &
  sleep 3
fi
curl -sS -o /dev/null "http://127.0.0.1:$PORT_SITE/index.html" && log "✓ سایت فعال است" || { log "✗ سایت بالا نیامد"; exit 4; }

# ---------- ۴) cloudflared ----------
if [ ! -x "$CF" ]; then
  log "دریافت cloudflared …"
  curl -sSL -o "$CF" --max-time 180 https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
    && chmod +x "$CF" || { log "✗ دریافت cloudflared ناموفق"; exit 5; }
fi

tunnel_url(){ # tunnel_url <port> <file>
  local port="$1" out="$2" url="" i
  : > "$out"
  nohup "$CF" tunnel --url "http://127.0.0.1:$port" --no-autoupdate --protocol quic >>"$out" 2>&1 &
  echo $! > "${out}.pid"
  for i in $(seq 1 45); do
    sleep 1
    url="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$out" | head -1)"
    [ -n "$url" ] && break
  done
  printf '%s' "$url"
}

log "ساخت تونل برای سایت …";  SITE_URL="$(tunnel_url "$PORT_SITE" "$LOGDIR/cf_site.out")"
log "ساخت تونل برای اپ …";    APP_URL="$(tunnel_url "$PORT_APP"  "$LOGDIR/cf_app.out")"
sleep 3
SITE_CODE="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 20 "${SITE_URL%/}/index.html" 2>/dev/null)"
APP_CODE="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 20 "${APP_URL%/}/health" 2>/dev/null)"

# ---------- ۵) ثبت آدرس‌ها ----------
python3 - "$SITE_URL" "$APP_URL" "$ROOT" <<'PY'
import json, pathlib, sys, datetime
site, app, root = sys.argv[1], sys.argv[2], pathlib.Path(sys.argv[3])
ts = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()
(root / "live.json").write_text(json.dumps(
    {"landing_url": site, "app_url": app, "updated_at": ts,
     "note_fa": "آدرس‌های زندهٔ موقت؛ با خاموش‌شدن این ماشین باطل می‌شوند."},
    ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
(root / "PUBLIC-URL.txt").write_text(
    "# آدرس‌های وب پروژه «سرویسا / Care+»\n"
    f"# آخرین به‌روزرسانی: {ts}\n#\n"
    "# ۱) صفحهٔ اول سایت (بستهٔ بازبین: سند، PDF، وایرفریم، داده، API، RFP):\n"
    f"{site}\n"
    "# ۲) کنسول دمو (اپ تعاملی):\n"
    f"{app}\n"
    "#\n"
    "# سایت دائمی (بدون نیاز به این ماشین):\n"
    "https://raw.githack.com/kian1401/careplus-platform/main/index.html\n",
    encoding="utf-8")
print("live.json و PUBLIC-URL.txt به‌روزرسانی شدند.")
PY

echo
echo "════════════════════════════════════════════════════════"
echo "  صفحهٔ اول سایت  : ${SITE_URL:-<ناموفق>}   (بررسی: HTTP ${SITE_CODE:-?})"
echo "  کنسول دمو       : ${APP_URL:-<ناموفق>}   (بررسی: HTTP ${APP_CODE:-?})"
echo "  سایت دائمی      : https://raw.githack.com/kian1401/careplus-platform/main/index.html"
echo "════════════════════════════════════════════════════════"
echo "برای توقف: kill \$(cat $LOGDIR/*.pid)  و بستن پروسه‌های uvicorn/http.server"
