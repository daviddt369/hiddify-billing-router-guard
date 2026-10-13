#!/usr/bin/env bash
# Local test-suite for build.py / resolve_links.py / update.sh (no network to the real node).
# usage: tests/test_all.sh <dir-with-real-HAPP.txt-and-INCY.txt> <dir-with-incy_rel.json>
set -uo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
REAL="${1:?dir with happ.txt incy.txt}"
REL="${2:?incy_rel.json}"
PY="${PYTHON:-python}"
W="$(mktemp -d)"
PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); echo "  PASS  $1"; }
bad()  { FAIL=$((FAIL+1)); echo "  FAIL  $1"; }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

echo "== 1. build with the real upstream deeplinks"
"$PY" "$HERE/build.py" --clients "$HERE/clients.json" --happ "$REAL/happ.txt" --incy "$REAL/incy.txt" --out "$W/index.html" >/dev/null
check "build exit 0"                         '[ -s "$W/index.html" ]'
HAPP="$(tr -d '\r\n' < "$REAL/happ.txt")"; INCY="$(tr -d '\r\n' < "$REAL/incy.txt")"
check "happ deeplink byte-identical in <a href>" 'grep -qF "href=\"$HAPP\"" "$W/index.html"'
check "incy deeplink byte-identical in <a href>" 'grep -qF "href=\"$INCY\"" "$W/index.html"'
check "no external <script src>/<link>/<img src=http>" '! grep -qE "<script[^>]+src=|<link |<img[^>]+src=.?http" "$W/index.html"'
check "CSP script hash matches inline script" '"$PY" - "$W/index.html" <<PYEOF
import sys,re,hashlib,base64,html
t=open(sys.argv[1],encoding="utf-8").read()
js=re.search(r"<script>(.*?)</script>",t,re.S).group(1)
h=base64.b64encode(hashlib.sha256(js.encode()).digest()).decode()
sys.exit(0 if ("sha256-"+h) in html.unescape(t) else 1)
PYEOF'
check "only https/happ/incy hrefs"           '"$PY" - "$W/index.html" <<PYEOF
import sys,re
t=open(sys.argv[1],encoding="utf-8").read()
bad=[h for h in re.findall(r"href=\"([^\"]*)\"",t) if not re.match(r"^(https://|happ://routing/onadd/|incy://routing/onadd/)",h)]
print(bad); sys.exit(1 if bad else 0)
PYEOF'
check "3 clients / 4 platforms rendered"     '[ "$(grep -o "class=\"card\" id=\"c-" "$W/index.html" | wc -l)" = 3 ] && [ "$(grep -o "class=\"chip\"" "$W/index.html" | wc -l)" = 4 ]'
check "no bad v2raytun/v2rayng/happ-plus"     '! grep -qiE "v2raytun|v2rayng|happ-proxy-utility-plus|id6746188973" "$W/index.html"'
check "section 3 has 3 same-style cards (Hiddify, Happ, INCY)" '[ "$(grep -o "class=\"card rt\" id=\"r-" "$W/index.html" | wc -l)" = 3 ] && grep -q "id=\"r-hiddify\"" "$W/index.html" && grep -q "Импорт не нужен" "$W/index.html"'
check "payment is a heading + big button with the pay URL" 'grep -q "<section class=\"pay\">" "$W/index.html" && grep -q "<h2>Не получается оплатить через бота?</h2>" "$W/index.html" && grep -q "class=\"btn big\" href=\"https://pay.example.com/\"" "$W/index.html"'
check "after-import steps for Happ and INCY (select profile, red !, reconnect)" '[ "$(grep -o "красный восклицательный знак" "$W/index.html" | wc -l)" = 2 ] && grep -q "Важно: после импорта выберите профиль в настройках" "$W/index.html" && [ "$(grep -o "включите переключатель" "$W/index.html" | wc -l)" = 2 ]'
check "region help: steps + both example addresses, iOS-only section" 'grep -q "class=\"region ios-only\"" "$W/index.html" && grep -q "100 Continental Dr" "$W/index.html" && grep -q "614 SW 11th Ave" "$W/index.html" && grep -q "appleid.apple.com" "$W/index.html" && grep -q "Соединённые Штаты" "$W/index.html"'
check "old small footer link is gone"           '! grep -q "<footer>" "$W/index.html"'

echo "== 2. build rejects bad deeplinks and leaves the output untouched"
cp "$W/index.html" "$W/index.before"
neg() { # name, happ-content
  printf '%s' "$2" > "$W/neg.txt"
  "$PY" "$HERE/build.py" --clients "$HERE/clients.json" --happ "$W/neg.txt" --incy "$REAL/incy.txt" --out "$W/index.html" >/dev/null 2>&1; rc=$?
  check "reject: $1 (rc=2, page unchanged)" '[ "$rc" = 2 ] && cmp -s "$W/index.html" "$W/index.before"'
}
neg "empty file" ""
neg "html error page" "<html><body>404 Not Found</body></html>"
neg "two lines" "$HAPP"$'\n'"$HAPP"
neg "wrong scheme (incy link in happ slot)" "$INCY"
neg "not base64" "happ://routing/onadd/!!!notbase64!!!"
neg "base64 but not json" "happ://routing/onadd/$(printf 'hello world' | base64 | tr -d '\n')"
neg "json without rules" "happ://routing/onadd/$(printf '{\"Name\":\"x\"}' | base64 | tr -d '\n')"

echo "== 3. resolve_links.py"
INCY_RELEASES_JSON="$REL" "$PY" "$HERE/resolve_links.py" "$W/dyn.json" >/dev/null
check "resolves 5 official INCY links, all under the official release path" '"$PY" - "$W/dyn.json" <<PYEOF
import json,sys
d=json.load(open(sys.argv[1]))
P="https://github.com/INCY-DEV/incy-platforms/releases/download/"
need={"incy_windows_setup","incy_windows_portable","incy_macos_arm64","incy_macos_intel","incy_android_apk"}
sys.exit(0 if set(d)==need and all(v.startswith(P) for v in d.values()) else 1)
PYEOF'
"$PY" - "$REL" "$W/evil.json" <<'PYEOF'
import json,sys
r=json.load(open(sys.argv[1]))
for rel in r:
    for a in rel["assets"]: a["browser_download_url"]="https://evil.example/"+a["name"]
json.dump(r,open(sys.argv[2],"w"))
PYEOF
cp "$W/dyn.json" "$W/dyn.keep"
INCY_RELEASES_JSON="$W/evil.json" "$PY" "$HERE/resolve_links.py" "$W/dyn.json" >/dev/null 2>&1; rc=$?
check "foreign download URLs are refused, old file kept" '[ "$rc" = 1 ] && cmp -s "$W/dyn.json" "$W/dyn.keep"'

echo "== 4. update.sh end-to-end against a local HTTP source"
FX="$W/fx"; mkdir -p "$FX/ok/HAPP" "$FX/ok/INCY" "$FX/garbage/HAPP" "$FX/garbage/INCY" "$FX/missing/HAPP"
cp "$REAL/happ.txt" "$FX/ok/HAPP/DEFAULT.DEEPLINK"; cp "$REAL/incy.txt" "$FX/ok/INCY/DEFAULT.DEEPLINK"
echo "<html>rate limited</html>" > "$FX/garbage/HAPP/DEFAULT.DEEPLINK"; cp "$REAL/incy.txt" "$FX/garbage/INCY/DEFAULT.DEEPLINK"
cp "$REAL/happ.txt" "$FX/missing/HAPP/DEFAULT.DEEPLINK"   # INCY file missing -> 404
PORT=$((20000 + RANDOM % 20000))
"$PY" -m http.server "$PORT" --bind 127.0.0.1 --directory "$FX" >/dev/null 2>&1 &
SRV=$!
trap 'kill "$SRV" 2>/dev/null; taskkill //F //PID "$SRV" >/dev/null 2>&1; true' EXIT
for _ in 1 2 3 4 5 6 7 8 9 10; do curl -fsS -o /dev/null "http://127.0.0.1:$PORT/ok/HAPP/DEFAULT.DEEPLINK" 2>/dev/null && break; sleep 0.5; done
APP="$W/app"; mkdir -p "$APP"; cp "$HERE/build.py" "$HERE/resolve_links.py" "$HERE/clients.json" "$APP/"
export ROUTING_APP_DIR="$APP" ROUTING_DATA_DIR="$APP" PYTHON="$PY" INCY_RELEASES_JSON="$REL"
run() { ROUTING_SRC_BASE="http://127.0.0.1:$PORT/$1" bash "$HERE/update.sh" >"$W/run.log" 2>&1; echo $?; }
rc=$(run ok);      check "first run publishes page (rc=0)"         '[ "$rc" = 0 ] && [ -s "$APP/www/index.html" ] && grep -q "page updated" "$W/run.log"'
cp "$APP/www/index.html" "$W/served.1"
rc=$(run ok);      check "second run is a no-op (rc=0, 'no changes')" '[ "$rc" = 0 ] && grep -q "no changes" "$W/run.log"'
rc=$(run garbage); check "garbage upstream -> rc=2, served page unchanged" '[ "$rc" = 2 ] && cmp -s "$APP/www/index.html" "$W/served.1"'
rc=$(run missing); check "download failure -> rc=1, served page unchanged" '[ "$rc" = 1 ] && cmp -s "$APP/www/index.html" "$W/served.1"'
check "no temp dirs left behind"                 '[ -z "$(ls -A "$APP" | grep -E "^\.tmp\." || true)" ]'
check "last-good copies match upstream"          'cmp -s "$APP/last-good/happ.deeplink" "$REAL/happ.txt"'
APP3="$W/code"; DATA3="$W/data"; mkdir -p "$APP3" "$DATA3"; cp "$HERE/build.py" "$HERE/resolve_links.py" "$HERE/clients.json" "$HERE/update.sh" "$APP3/"
before="$(cd "$APP3" && ls -A | sort | tr '\n' ' ')"
ROUTING_APP_DIR="$APP3" ROUTING_DATA_DIR="$DATA3" ROUTING_SRC_BASE="http://127.0.0.1:$PORT/ok" bash "$APP3/update.sh" >"$W/run3.log" 2>&1; rc3=$?
check "split code/data dirs: page lands in DATA, code dir untouched" '[ "$rc3" = 0 ] && [ -s "$DATA3/www/index.html" ] && [ "$(cd "$APP3" && ls -A | sort | tr "\n" " ")" = "$before" ]'
kill "$SRV" 2>/dev/null || true; taskkill //F //PID "$SRV" >/dev/null 2>&1 || true

echo; echo "RESULT: $PASS passed, $FAIL failed"
cp "$W/served.1" "${TEST_OUT_PAGE:-/dev/null}" 2>/dev/null || true
rm -rf "$W" 2>/dev/null || true
[ "$FAIL" = 0 ]
