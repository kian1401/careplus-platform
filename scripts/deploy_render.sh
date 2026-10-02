#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# ساخت/به‌روزرسانی سرویس دمو «سرویسا / Care+» روی Render با API
# کلید از RENDER_API_KEY یا ~/.render_api_key خوانده می‌شود و هرگز چاپ نمی‌گردد.
# استفاده:
#   export RENDER_API_KEY=...              # یا: echo <key> > ~/.render_api_key
#   bash scripts/deploy_render.sh careplus-platform [شاخه]
# ---------------------------------------------------------------------------
set -uo pipefail
API="https://api.render.com/v1"
KEY="${RENDER_API_KEY:-}"
[ -z "$KEY" ] && [ -f "$HOME/.render_api_key" ] && KEY="$(tr -d ' \n' < "$HOME/.render_api_key")"
[ -z "$KEY" ] && { echo "✗ کلید یافت نشد: RENDER_API_KEY یا ~/.render_api_key"; exit 2; }
command -v curl >/dev/null || { echo "✗ curl نصب نیست"; exit 2; }

REPO="${1:-careplus-platform}"; BRANCH="${2:-main}"
REPO_URL="https://github.com/${GITHUB_LOGIN:-kian1401}/${REPO}"

api() { # api METHOD PATH [BODY]
  local m="$1" p="$2" b="${3:-}"
  if [ -n "$b" ]; then
    curl -sS -X "$m" -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" "$API$p" -d "$b"
  else
    curl -sS -X "$m" -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" "$API$p"
  fi
}

echo "• خواندن مالک فضای کاری …"
OWNER="$(api GET '/owners?limit=1' | python3 -c 'import sys,json;print(json.load(sys.stdin)[0]["owner"]["id"])' 2>/dev/null)"
[ -z "$OWNER" ] && { echo "✗ کلید نامعتبر است یا مالک خوانده نشد."; exit 1; }
echo "  owner=$OWNER"

EXISTING="$(api GET '/services?limit=100' | python3 -c '
import sys,json
d=json.load(sys.stdin)
for it in d:
    s=it.get("service",{})
    if s.get("name")=="careplus-mvp": print(s["id"]); break
' 2>/dev/null)"

BODY="$(python3 - "$OWNER" "$REPO_URL" "$BRANCH" <<'PY'
import json,sys
owner, repo, branch = sys.argv[1], sys.argv[2], sys.argv[3]
print(json.dumps({
  "type": "web_service", "name": "careplus-mvp", "ownerId": owner,
  "repo": repo, "branch": branch, "rootDir": "mvp", "autoDeploy": "yes",
  "region": "frankfurt", "plan": "free", "runtime": "python",
  "healthCheckPath": "/health",
  "buildCommand": "pip install -r requirements.txt",
  "startCommand": "uvicorn app.main:app --host 0.0.0.0 --port $PORT",
  "envVars": [
    {"key": "CP_ENV", "value": "demo"},
    {"key": "CP_DATABASE_URL", "value": "sqlite:////tmp/careplus.sqlite"},
    {"key": "CP_ESCROW_HOLD_HOURS", "value": "48"},
    {"key": "CP_SECRET_KEY", "generateValue": True}
  ],
}, ensure_ascii=False))
PY
)"

if [ -n "$EXISTING" ]; then
  echo "• سرویس از قبل موجود است ⇒ به‌روزرسانی تنظیمات و راه‌اندازی استقرار …"
  api PATCH "/services/$EXISTING" "$BODY" >/dev/null
  DEP="$(api POST "/services/$EXISTING/deploys" '{"clearCache":"do_not_clear"}' | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d.get("id") or d.get("deploy",{}).get("id",""))' 2>/dev/null)"
else
  echo "• ساخت سرویس جدید …"
  RESP="$(api POST '/services' "$BODY")"
  SID="$(printf '%s' "$RESP" | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d.get("service",{}).get("id") or d.get("id",""))' 2>/dev/null)"
  if [ -z "$SID" ]; then echo "✗ ساخت سرویس ناموفق:"; printf '%s\n' "$RESP" | head -c 600; exit 1; fi
  EXISTING="$SID"; echo "  service_id=$SID"
  DEP="$(api POST "/services/$SID/deploys" '{"clearCache":"do_not_clear"}' | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d.get("id") or d.get("deploy",{}).get("id",""))' 2>/dev/null)"
fi

echo "• پیگیری استقرار (deploy_id=${DEP:-?}) …"
for i in $(seq 1 30); do
  sleep 10
  ST="$(api GET "/services/$EXISTING/deploys?limit=1" | python3 -c '
import sys,json
d=json.load(sys.stdin)
x=(d[0].get("deploy") if isinstance(d,list) and d else {}) or {}
print(x.get("status",""))' 2>/dev/null)"
  echo "  [$((i*10))s] وضعیت: ${ST:-?}"
  case "$ST" in
    live|build_failed|canceled|update_failed) break;;
  esac
done

URL="$(api GET "/services/$EXISTING" | python3 -c 'import sys,json;d=json.load(sys.stdin);s=d.get("service",d);print(s.get("serviceDetails",{}).get("url") or s.get("url") or "")' 2>/dev/null)"
echo "✓ پایان. آدرس سرویس: ${URL:-<در داشبورد Render ببینید>}"
echo "  بررسی سلامت: ${URL:-https://careplus-mvp.onrender.com}/health"
