#!/usr/bin/env bash
# Local test-suite for build.py / resolve_links.py / update.sh (no network to the real node).
# usage: tests/test_all.sh <dir-with-incy_rel.json>   (the first argument used to be a dir with the RoscomVPN deeplinks: not needed any more)
set -uo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
REL="${1:?path to incy_rel.json (a saved answer of the GitHub releases API of INCY)}"
PY="${PYTHON:-python}"
W="$(mktemp -d)"
PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); echo "  PASS  $1"; }
bad()  { FAIL=$((FAIL+1)); echo "  FAIL  $1"; }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

echo "== 1. build"
"$PY" "$HERE/build.py" --clients "$HERE/clients.json" --out "$W/index.html" >/dev/null
check "build exit 0"                         '[ -s "$W/index.html" ]'
check "no external <script src>/<link>/<img src=http>" '! grep -qE "<script[^>]+src=|<link |<img[^>]+src=.?http" "$W/index.html"'
check "CSP script hash matches inline script" '"$PY" - "$W/index.html" <<PYEOF
import sys,re,hashlib,base64,html
t=open(sys.argv[1],encoding="utf-8").read()
js=re.search(r"<script>(.*?)</script>",t,re.S).group(1)
h=base64.b64encode(hashlib.sha256(js.encode()).digest()).decode()
sys.exit(0 if ("sha256-"+h) in html.unescape(t) else 1)
PYEOF'
check "only https hrefs (no happ:// incy:// import links any more)"           '"$PY" - "$W/index.html" <<PYEOF
import sys,re
t=open(sys.argv[1],encoding="utf-8").read()
bad=[h for h in re.findall(r"href=\"([^\"]*)\"",t) if not re.match(r"^https://",h)]
print(bad); sys.exit(1 if bad else 0)
PYEOF'
check "3 clients / 4 platforms rendered"     '[ "$(grep -o "class=\"card\" id=\"c-" "$W/index.html" | wc -l)" = 3 ] && [ "$(grep -o "class=\"chip\"" "$W/index.html" | wc -l)" = 4 ]'
check "no links to v2raytun/v2rayng/happ-plus (the text names the apps, nothing links to them)" '! grep -qiE "href=\"[^\"]*(v2raytun|v2rayng|happ-proxy-utility-plus|id6746188973)" "$W/index.html"'
check "no routing step: no import section, no deeplink buttons, no auto-open" '! grep -q "Маршрутизация RoscomVPN" "$W/index.html" && ! grep -qE "routing/onadd|data-auto|class=\"card rt\"|Импорт не нужен|Важно: после импорта" "$W/index.html" && grep -q "Маршрутизация (российские сайты открываются напрямую) уже внутри подписки" "$W/index.html"'
check "unified instruction: INCY first on iPhone, Hiddify first elsewhere, the two links explained, other apps, Hiddify for heavy blocking" '"$PY" - "$W/index.html" <<PYEOF
import sys,re
t=open(sys.argv[1],encoding="utf-8").read()
o=re.search(r"data-order=\"([^\"]*)\" data-order-ios=\"([^\"]*)\"",t)
rec=dict(re.findall(r"data-rec=\"(\w+)\"><div class=\"pl\">[^<]*</div><p><b>([^<]*)</b>",t))
ok=bool(o) and o.group(1).split(",")[0]=="hiddify" and o.group(2).split(",")[0]=="incy"
ok=ok and rec.get("ios","").startswith("Рекомендуем INCY") and rec.get("other","").startswith("Рекомендуем Hiddify")
ok=ok and "вторая («для Happ и INCY»)" in t and "<h2>Другие приложения</h2>" in t and "V2RayTun" in t and "у него больше протоколов" in t and "Личный кабинет" in t
sys.exit(0 if ok else 1)
PYEOF'
check "payment: the bot first, the site form folded under \"Не получается оплатить в боте?\"" 'grep -q "<section class=\"pay\">" "$W/index.html" && grep -q "<h2>Оплата</h2>" "$W/index.html" && grep -q "Проще всего оплатить в Telegram-боте" "$W/index.html" && grep -q "<details class=\"paysite\"><summary>Не получается оплатить в боте?</summary>" "$W/index.html" && grep -q "class=\"btn big\" href=\"https://pay.example.com/\"" "$W/index.html"'
check "region help: steps + both example addresses, iOS-only section" 'grep -q "class=\"region ios-only\"" "$W/index.html" && grep -q "100 Continental Dr" "$W/index.html" && grep -q "614 SW 11th Ave" "$W/index.html" && grep -q "appleid.apple.com" "$W/index.html" && grep -q "Соединённые Штаты" "$W/index.html"'
check "old small footer link is gone"           '! grep -q "<footer>" "$W/index.html"'
check "guard marker id=\"copy-url\" is still on the page (deploy/cert-common.sh guard_check looks for it)" 'grep -q "id=\"copy-url\"" "$W/index.html"'
check "#region opens the region help on any platform (script + .force style)" 'grep -q "#region" "$W/index.html" && grep -q "\.js \.ios-only\.force{display:block}" "$W/index.html" && grep -q "id=\"region\"" "$W/index.html"'

echo "== 2. build rejects a broken config and leaves the output untouched"
cp "$W/index.html" "$W/index.before"
"$PY" - "$HERE/clients.json" "$W/neg.json" <<PYEOF
import json,sys
c=json.load(open(sys.argv[1],encoding="utf-8")); c["order"]=["hiddify","happ"]
json.dump(c,open(sys.argv[2],"w",encoding="utf-8"),ensure_ascii=False)
PYEOF
"$PY" "$HERE/build.py" --clients "$W/neg.json" --out "$W/index.html" >/dev/null 2>&1; rc=$?
check "reject: an app missing from the order list (rc=3, page unchanged)" '[ "$rc" = 3 ] && cmp -s "$W/index.html" "$W/index.before"'
"$PY" - "$HERE/clients.json" "$W/neg.json" <<PYEOF
import json,sys
c=json.load(open(sys.argv[1],encoding="utf-8")); del c["recommend"]
json.dump(c,open(sys.argv[2],"w",encoding="utf-8"),ensure_ascii=False)
PYEOF
"$PY" "$HERE/build.py" --clients "$W/neg.json" --out "$W/index.html" >/dev/null 2>&1; rc=$?
check "reject: the recommendation texts missing (rc=3, page unchanged)" '[ "$rc" = 3 ] && cmp -s "$W/index.html" "$W/index.before"'

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
FX="$W/fx"; mkdir -p "$FX/ok"
YKF="$HERE/tests/fixtures/yookassa_construct_form.js"
[ -f "$YKF" ] && cp "$YKF" "$FX/ok/yk.js"; echo "// not the pinned script" > "$FX/ok/yk-other.js"
PORT=$((20000 + RANDOM % 20000))
"$PY" -m http.server "$PORT" --bind 127.0.0.1 --directory "$FX" >/dev/null 2>&1 &
SRV=$!
trap 'kill "$SRV" 2>/dev/null; taskkill //F //PID "$SRV" >/dev/null 2>&1; true' EXIT
for _ in 1 2 3 4 5 6 7 8 9 10; do curl -fsS -o /dev/null "http://127.0.0.1:$PORT/ok/yk-other.js" 2>/dev/null && break; sleep 0.5; done
APP="$W/app"; mkdir -p "$APP"; cp "$HERE/build.py" "$HERE/resolve_links.py" "$HERE/clients.json" "$APP/"
export ROUTING_APP_DIR="$APP" ROUTING_DATA_DIR="$APP" PYTHON="$PY" INCY_RELEASES_JSON="$REL" ROUTING_YK_SCRIPT_URL="http://127.0.0.1:$PORT/ok/yk.js"
run() { bash "$HERE/update.sh" >"$W/run.log" 2>&1; echo $?; }
rc=$(run ok);      check "first run publishes page (rc=0)"         '[ "$rc" = 0 ] && [ -s "$APP/www/index.html" ] && grep -q "page updated" "$W/run.log"'
cp "$APP/www/index.html" "$W/served.1"
rc=$(run ok);      check "second run is a no-op (rc=0, 'no changes')" '[ "$rc" = 0 ] && grep -q "no changes" "$W/run.log"'
rc=$(run);         check "third run is a no-op as well, nothing left half-written" '[ "$rc" = 0 ] && cmp -s "$APP/www/index.html" "$W/served.1"'
check "no temp dirs left behind"                 '[ -z "$(ls -A "$APP" | grep -E "^\.tmp\." || true)" ]'
check "the page has no import links and no RoscomVPN download is attempted" '! grep -q "routing/onadd" "$APP/www/index.html" && ! grep -qi "roscomvpn\|raw.githubusercontent" "$HERE/update.sh"'
APP3="$W/code"; DATA3="$W/data"; mkdir -p "$APP3" "$DATA3"; cp "$HERE/build.py" "$HERE/resolve_links.py" "$HERE/clients.json" "$HERE/update.sh" "$APP3/"
before="$(cd "$APP3" && ls -A | sort | tr '\n' ' ')"
ROUTING_APP_DIR="$APP3" ROUTING_DATA_DIR="$DATA3" bash "$APP3/update.sh" >"$W/run3.log" 2>&1; rc3=$?
check "split code/data dirs: page lands in DATA, code dir untouched" '[ "$rc3" = 0 ] && [ -s "$DATA3/www/index.html" ] && [ "$(cd "$APP3" && ls -A | sort | tr "\n" " ")" = "$before" ]'
if [ -f "$YKF" ]; then
echo "== 4b. the pay form follows the pinned YooKassa script"
check "update.sh: form page published with the pinned script (last-good kept)" 'grep -q "class=\"yoomoney-payment-form\"" "$APP/www/index.html" && cmp -s "$APP/last-good/yookassa_form.js" "$YKF"'
rcy=$(ROUTING_YK_SCRIPT_URL="http://127.0.0.1:$PORT/ok/yk-other.js" bash "$HERE/update.sh" >"$W/runy.log" 2>&1; echo $?)
check "a changed script upstream: last good copy is used, the form stays" '[ "$rcy" = 0 ] && grep -q "last good copy" "$W/runy.log" && grep -q "class=\"yoomoney-payment-form\"" "$APP/www/index.html"'
rm -f "$APP/last-good/yookassa_form.js"
rcy=$(ROUTING_YK_SCRIPT_URL="http://127.0.0.1:$PORT/ok/yk-other.js" bash "$HERE/update.sh" >"$W/runy.log" 2>&1; echo $?)
check "a changed script and no last good copy: page falls back to the pay link" '[ "$rcy" = 0 ] && ! grep -q "yoomoney-payment-form\"" "$APP/www/index.html" && grep -q "class=\"btn big\" href=\"https://" "$APP/www/index.html"'

echo "== 5. build of the pay form"
"$PY" "$HERE/build.py" --clients "$HERE/clients.json" --yk-script "$YKF" --out "$W/pay.html" >/dev/null
check "form: YooKassa action, shop id, phone + customer + sum fields, three tariff buttons" '[ "$(grep -o "<form class=\"yoomoney-payment-form\"" "$W/pay.html" | wc -l)" = 1 ] && grep -q "action=\"https://yookassa.ru/integration/simplepay/payment\" method=\"post\"" "$W/pay.html" && grep -q "name=\"shopId\" type=\"hidden\" value=\"[0-9]*\"" "$W/pay.html" && [ "$(grep -o "class=\"plan\"" "$W/pay.html" | wc -l)" = 3 ] && grep -q "name=\"cps_phone\"" "$W/pay.html" && grep -q "name=\"customerNumber\"" "$W/pay.html" && grep -q "name=\"sum\"" "$W/pay.html"'
check "CSP: both inline scripts hashed, form-action = YooKassa only, no external script" '"$PY" - "$W/pay.html" <<PYEOF
import sys,re,hashlib,base64,html
t=open(sys.argv[1],encoding="utf-8").read()
csp=html.unescape(re.search(r"Content-Security-Policy\" content=\"([^\"]*)\"",t).group(1))
scripts=re.findall(r"<script>(.*?)</script>",t,re.S)
ok=len(scripts)==3 and all(("sha256-"+base64.b64encode(hashlib.sha256(s.encode()).digest()).decode()) in csp for s in scripts)
ok=ok and "form-action https://yookassa.ru" in csp and "<script src" not in t and "script-src \x27sha256" in csp
sys.exit(0 if ok else 1)
PYEOF'
check "CSP form-action also allows the redirect hosts, the page sends its origin to the pay endpoint" '"$PY" - "$W/pay.html" <<PYEOF
import sys,re,html
t=open(sys.argv[1],encoding="utf-8").read()
csp=html.unescape(re.search(r"Content-Security-Policy\" content=\"([^\"]*)\"",t).group(1))
fa=re.search(r"form-action ([^;]*)",csp).group(1).split()
sys.exit(0 if fa[0]=="https://yookassa.ru" and "https:" in fa and "<meta name=\"referrer\" content=\"strict-origin-when-cross-origin\">" in t else 1)
PYEOF'
check "the inline YooKassa script is byte-identical to the pinned file" '"$PY" - "$W/pay.html" "$YKF" <<PYEOF
import sys,re
t=open(sys.argv[1],encoding="utf-8").read()
s=re.findall(r"<script>(.*?)</script>",t,re.S)[2]
sys.exit(0 if s.encode("utf-8")==open(sys.argv[2],"rb").read() else 1)
PYEOF'
check "no-JS fallback link kept next to the form" 'grep -q "<div class=\"pay-nojs\"><a class=\"btn big\" href=\"https://" "$W/pay.html" && grep -q "\.js \.pay-nojs{display:none}" "$W/pay.html"'
check "the bot button leads to the named bot" 'grep -qE "href=\"https://t.me/[A-Za-z0-9_]{4,32}\" rel=\"noopener noreferrer\">Открыть бота" "$W/pay.html"'
python_cfg() { "$PY" - "$HERE/clients.json" "$W/bad.json" "$1" <<PYEOF
import json,sys
c=json.load(open(sys.argv[1],encoding="utf-8")); f=c["payment"]["form"]
exec(sys.argv[3])
json.dump(c,open(sys.argv[2],"w",encoding="utf-8"),ensure_ascii=False)
PYEOF
}
python_cfg 'c["payment"]["bot_username"]="-"'
"$PY" "$HERE/build.py" --clients "$W/bad.json" --yk-script "$YKF" --out "$W/pay4.html" >/dev/null 2>&1; rc=$?
check "no bot named (\"-\"): no bot button, the form is still there" '[ "$rc" = 0 ] && ! grep -q "href=\"https://t.me/" "$W/pay4.html" && grep -q "yoomoney-payment-form\"" "$W/pay4.html"'
python_cfg 'c["payment"]["bot_username"]="bad/name\"><script>"'
"$PY" "$HERE/build.py" --clients "$W/bad.json" --yk-script "$YKF" --out "$W/pay4.html" >/dev/null 2>&1; rc=$?
check "a bot name with markup is not turned into a link" '[ "$rc" = 0 ] && ! grep -q "t.me/" "$W/pay4.html"'
cp "$W/pay.html" "$W/pay.before"
python_cfg() { "$PY" - "$HERE/clients.json" "$W/bad.json" "$1" <<PYEOF
import json,sys
c=json.load(open(sys.argv[1],encoding="utf-8")); f=c["payment"]["form"]
exec(sys.argv[3])
json.dump(c,open(sys.argv[2],"w",encoding="utf-8"),ensure_ascii=False)
PYEOF
}
python_cfg 'f["shop_id"]="12ab\"><script>"'
"$PY" "$HERE/build.py" --clients "$W/bad.json" --yk-script "$YKF" --out "$W/pay.html" >/dev/null 2>&1; rc=$?
check "bad shop id: refused (rc=3), page unchanged" '[ "$rc" = 3 ] && cmp -s "$W/pay.html" "$W/pay.before"'
python_cfg 'f["shop_id"]="0"'
"$PY" "$HERE/build.py" --clients "$W/bad.json" --yk-script "$YKF" --out "$W/pay3.html" >/dev/null 2>&1; rc=$?
check "shop id 0 (no shop on this node): plain pay link, no form" '[ "$rc" = 0 ] && ! grep -q "yoomoney-payment-form\"" "$W/pay3.html" && grep -q "class=\"btn big\" href=\"https://" "$W/pay3.html"'
python_cfg 'f["plans"][0]["price"]=-5'
"$PY" "$HERE/build.py" --clients "$W/bad.json" --yk-script "$YKF" --out "$W/pay.html" >/dev/null 2>&1; rc=$?
check "negative price: refused (rc=3), page unchanged" '[ "$rc" = 3 ] && cmp -s "$W/pay.html" "$W/pay.before"'
"$PY" "$HERE/build.py" --clients "$HERE/clients.json" --yk-script "$YKF" --pay-action "javascript:alert(1)" --out "$W/pay.html" >/dev/null 2>&1; rc=$?
check "unsafe form action: refused (rc=3), page unchanged" '[ "$rc" = 3 ] && cmp -s "$W/pay.html" "$W/pay.before"'
python_cfg 'f["plans"][0]["name"]="<b>x</b>\" onfocus=\"alert(1)"'
"$PY" "$HERE/build.py" --clients "$W/bad.json" --yk-script "$YKF" --out "$W/pay2.html" >/dev/null 2>&1
check "product text is escaped (no injected markup)" '! grep -q "<b>x</b>" "$W/pay2.html" && ! grep -q "onfocus=\"alert" "$W/pay2.html"'
fi

kill "$SRV" 2>/dev/null || true; taskkill //F //PID "$SRV" >/dev/null 2>&1 || true

echo; echo "RESULT: $PASS passed, $FAIL failed"
cp "$W/served.1" "${TEST_OUT_PAGE:-/dev/null}" 2>/dev/null || true
rm -rf "$W" 2>/dev/null || true
[ "$FAIL" = 0 ]
