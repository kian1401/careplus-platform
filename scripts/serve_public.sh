#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# راه‌اندازی سریع دموی عمومی: سرور محلی + تونل عمومی ⇒ چاپ آدرس
# استفاده:  bash scripts/serve_public.sh
# ---------------------------------------------------------------------------
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${PORT:-8000}"
cd "$ROOT/mvp" || exit 2

if ! curl -sS -o /dev/null --max-time 5 "http://127.0.0.1:$PORT/health"; then
  echo "• راه‌اندازی سرور روی پورت $PORT …"
  CP_ENV=demo CP_DATABASE_URL="sqlite:///./var/mvp.sqlite" \
    nohup python3 -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT" >/tmp/cp_server.log 2>&1 &
  sleep 6
fi
curl -sS --max-time 10 "http://127.0.0.1:$PORT/health" | head -c 120; echo

CF="${CF_BIN:-/tmp/cloudflared}"
if [ ! -x "$CF" ]; then
  echo "• دریافت cloudflared …"
  curl -sSL -o "$CF" --max-time 120 https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
  chmod +x "$CF"
fi
echo "• ساخت تونل عمومی …"
nohup bash "$ROOT/scripts/tunnel_watchdog.sh" >/dev/null 2>&1 &
for i in $(seq 1 40); do sleep 1; U="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$ROOT/PUBLIC-URL.txt" 2>/dev/null | head -1)"; [ -n "$U" ] && break; done
echo "✓ آدرس عمومی: ${U:-<در PUBLIC-URL.txt>}"
