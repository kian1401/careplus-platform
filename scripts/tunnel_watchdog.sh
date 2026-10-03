#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# نگهبان تونل عمومی: هر ۳۰ ثانیه سلامت آدرس عمومی را می‌سنجد و در صورت قطع،
# تونل تازه می‌سازد و فایل PUBLIC-URL.txt را به‌روز می‌کند.
# اجرا (داخل محیط کاری):  bash scripts/tunnel_watchdog.sh
# ---------------------------------------------------------------------------
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CF="${CF_BIN:-/tmp/cloudflared}"
URLFILE="$ROOT/PUBLIC-URL.txt"
LOGDIR="${TMPDIR:-/tmp}/cp_tunnel"
mkdir -p "$LOGDIR"
INTERVAL="${WATCH_INTERVAL:-30}"

now() { date -u +'%Y-%m-%d %H:%M:%SZ'; }

http_code() {  # http_code <base-url>
  curl -sS -o /dev/null -w '%{http_code}' --max-time 15 "${1%/}/health" 2>/dev/null || echo 000
}

current_url() { grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$URLFILE" 2>/dev/null | head -1; }

start_tunnel() {
  # تونل قبلی (اگر زنده باشد) بسته می‌شود
  if [ -f "$LOGDIR/cf.pid" ]; then kill "$(cat "$LOGDIR/cf.pid")" 2>/dev/null || true; sleep 2; fi
  pkill -f "cloudflared tunnel --url http://127.0.0.1:8000" 2>/dev/null || true
  : > "$LOGDIR/cf.out"
  nohup "$CF" tunnel --url http://127.0.0.1:8000 --no-autoupdate --protocol quic --retries 10 \
        >>"$LOGDIR/cf.out" 2>&1 &
  echo $! > "$LOGDIR/cf.pid"
  local u="" i
  for i in $(seq 1 45); do
    sleep 1
    u="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOGDIR/cf.out" | head -1)"
    [ -n "$u" ] && break
  done
  if [ -z "$u" ]; then echo "[$(now)] ✗ ساخت تونل ناموفق" >> "$LOGDIR/watchdog.log"; return 1; fi
  sleep 3
  local code; code="$(http_code "$u")"
  {
    echo "# آدرس عمومی جاری دموی «سرویسا / Care+»"
    echo "# آخرین به‌روزرسانی: $(now)"
    echo "# وضعیت سلامت: HTTP $code"
    echo "# نکته: این لینک موقت است؛ برای آدرس دائمی، استقرار روی Render را انجام دهید (DEPLOY.md)."
    echo "#"
    echo "# آدرس اصلی (Cloudflare Quick Tunnel):"
    echo "$u"
    if [ -n "${BACKUP_URL:-}" ]; then
      echo "# آدرس پشتیبان (localhost.run):"
      echo "$BACKUP_URL"
    fi
  } > "$URLFILE"
  echo "[$(now)] ✓ تونل تازه: $u (health=$code)" >> "$LOGDIR/watchdog.log"
  return 0
}

echo "[$(now)] نگهبان تونل آغاز شد (فاصله بررسی: ${INTERVAL}s)" >> "$LOGDIR/watchdog.log"
last_restart=0
while true; do
  url="$(current_url)"
  if [ -z "$url" ]; then
    start_tunnel
  else
    code="$(http_code "$url")"
    if [ "$code" != "200" ]; then
      echo "[$(now)] ⚠ آدرس $url با کد $code پاسخ داد ⇒ بازسازی تونل" >> "$LOGDIR/watchdog.log"
      start_tunnel
    else
      # تازه‌سازی زمان بررسی در فایل، بدون تغییر آدرس
      sed -i "s|^# آخرین بررسی: .*|# آخرین بررسی: $(now) (HTTP 200)|" "$URLFILE" 2>/dev/null || true
      grep -q '^# آخرین بررسی' "$URLFILE" || echo "# آخرین بررسی: $(now) (HTTP 200)" >> "$URLFILE"
    fi
  fi
  sleep "$INTERVAL"
done
