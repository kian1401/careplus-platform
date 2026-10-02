#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# انتشار خودکار روی GitHub (ساخت مخزن در صورت نبود + push شاخه main)
# کلید فقط از محیط یا فایل خوانده می‌شود و هرگز چاپ/ذخیره نمی‌گردد.
# استفاده:
#   export GITHUB_TOKEN=...        # یا: echo <token> > ~/.github_token
#   bash scripts/auto_push.sh careplus-platform ["توضیح مخزن"] [--once|--loop]
#   AUTO_EVERY=120 bash scripts/auto_push.sh careplus-platform      # حلقه انتشار
# ---------------------------------------------------------------------------
set -uo pipefail

REPO_NAME="${1:-careplus-platform}"
DESC="${2:-سرویسا / Care+ — پکیج کامل طراحی، داده، API و MVP}"
MODE="${3:---once}"
API="https://api.github.com"
LOG="$(dirname "$0")/push.log"

TOKEN="${GITHUB_TOKEN:-}"
if [ -z "$TOKEN" ] && [ -f "$HOME/.github_token" ]; then TOKEN="$(tr -d ' \n' < "$HOME/.github_token")"; fi
if [ -z "$TOKEN" ]; then echo "✗ توکن یافت نشد: GITHUB_TOKEN یا ~/.github_token"; exit 2; fi

cd "$(dirname "$0")/.." || exit 2
command -v git >/dev/null || { echo "✗ git نصب نیست"; exit 2; }
command -v curl >/dev/null || { echo "✗ curl نصب نیست"; exit 2; }

mask() { sed -E 's/(github_pat_[A-Za-z0-9_]{4})[A-Za-z0-9_]+/\1…/g; s/(ghp_[A-Za-z0-9]{4})[A-Za-z0-9]+/\1…/g'; }

whoami_login() {
  curl -sS -H "Authorization: Bearer $TOKEN" -H "Accept: application/vnd.github+json" \
       "$API/user" | python3 -c "import sys,json;print(json.load(sys.stdin).get('login',''))" 2>/dev/null
}

ensure_repo() {
  local login="$1"
  if curl -sS -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $TOKEN" \
        "$API/repos/$login/$REPO_NAME" | grep -q '^200$'; then
    echo "• مخزن $login/$REPO_NAME از قبل موجود است."
    return 0
  fi
  echo "• ساخت مخزن $login/$REPO_NAME …"
  local body resp
  body="$(python3 - "$REPO_NAME" "$DESC" <<'PY'
import json,sys
print(json.dumps({"name":sys.argv[1],"description":sys.argv[2],"private":False,"auto_init":False}))
PY
)"
  resp="$(curl -sS -X POST -H "Authorization: Bearer $TOKEN" -H "Accept: application/vnd.github+json" \
          "$API/user/repos" -d "$body")"
  if echo "$resp" | grep -q '"full_name"'; then echo "  ✓ ساخته شد."
  else echo "  ✗ خطا در ساخت مخزن: $(echo "$resp" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("message",""))' 2>/dev/null)"; return 1; fi
}

push_once() {
  local login="$1"
  git add -A
  if git diff --cached --quiet; then echo "• تغییری برای commit نیست."; else
    git -c user.name="Care+ Bot" -c user.email="bot@careplus.local" \
        commit -q -m "به‌روزرسانی خودکار: $(date -u +'%Y-%m-%d %H:%M UTC')" || true
    echo "• commit ساخته شد."
  fi
  if ! git remote get-url origin >/dev/null 2>&1; then
    git remote add origin "https://github.com/$login/$REPO_NAME.git"
  else
    git remote set-url origin "https://github.com/$login/$REPO_NAME.git"
  fi
  # push بدون ذخیره توکن: از askpass موقت استفاده می‌شود (در .git/config نوشته نمی‌شود)
  local askpass; askpass="$(mktemp)"
  printf '#!/bin/sh\necho "%s"\n' "$TOKEN" > "$askpass"; chmod 700 "$askpass"
  GIT_ASKPASS="$askpass" GIT_TERMINAL_PROMPT=0 \
    git push -q origin main 2>&1 | mask || { rm -f "$askpass"; echo "✗ push ناموفق بود."; return 1; }
  rm -f "$askpass"
  echo "✓ push انجام شد: https://github.com/$login/$REPO_NAME (شاخه main)"
}

LOGIN="$(whoami_login)"
[ -z "$LOGIN" ] && { echo "✗ توکن نامعتبر است."; exit 1; }
echo "• کاربر: $LOGIN"

{
  echo "=== $(date -u +'%Y-%m-%d %H:%M:%S UTC') — user=$LOGIN repo=$REPO_NAME mode=$MODE"
  ensure_repo "$LOGIN" && push_once "$LOGIN"
} 2>&1 | mask | tee -a "$LOG"

if [ "$MODE" = "--loop" ]; then
  EVERY="${AUTO_EVERY:-120}"
  echo "• حلقه انتشار فعال شد (هر ${EVERY} ثانیه). توقف: Ctrl+C"
  while true; do
    sleep "$EVERY"
    { echo "--- $(date -u +'%H:%M:%S UTC')"; push_once "$LOGIN"; } 2>&1 | mask | tee -a "$LOG"
  done
fi
