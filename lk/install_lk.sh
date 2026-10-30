#!/bin/bash
# Put the registration page of the trial access on the node (lk.<domain>) or take it off again. Checks first, an automatic way back on any failed check.
# usage: install_lk.sh <host> <bot username or -> <brand> <gate number>        install      (run from the lk/ directory of the repository, as root)
#        install_lk.sh --remove                                  remove (the files stay, the nginx block goes)
# Order on a node: (1) the A record of <host> points here; (2) NO_CERT_OK=1 CURL_OPTS=-k install_lk.sh ... (the block must exist first: it serves the ACME challenge); (3) cert_instance.sh <host> <main domain>; (4) install_lk.sh again, now with the real certificate.
# env:   CURL_OPTS="-k" (stand: self-signed certificates). The checks never create an account (the phone they send is invalid on purpose).
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
CONF=/opt/hiddify-manager/nginx/conf.d/zz-lk.conf
NGX_CONF=/opt/hiddify-manager/nginx/nginx.conf
ROOT=/opt/hiddify-manager/lk/www
CURL_OPTS="${CURL_OPTS:-}"
log() { echo "[lk $(date +%H:%M:%S)] $*"; }
reload() { systemctl reload hiddify-nginx; sleep 2; }

if [ "${1:-}" = "--remove" ]; then
    if [ -f "$CONF" ]; then
        mkdir -p /root/.claude-backups; cp -a "$CONF" "/root/.claude-backups/zz-lk.conf.removed-$(date +%Y%m%d-%H%M%S)"
        rm -f "$CONF"; nginx -t -c "$NGX_CONF" 2>&1 | tail -1; reload; log "removed"
    else log "not installed"; fi
    exit 0
fi

HOST="$1"; BOT="$2"; BRAND="$3"; GATE="${4:?the gate number (the answer of the front page) is the 4th argument}"
[[ "$HOST" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}$ ]] || { log "bad host"; exit 2; }
FAILED=0
chk() { if eval "$2"; then log "  OK    $1"; else log "  FAIL  $1"; FAILED=1; fi; }
PATHC="$(mysql hiddifypanel -N -B -e "select value from str_config where \`key\`='proxy_path_client'")"
[[ "$PATHC" =~ ^[A-Za-z0-9]+$ ]] || { log "client path not found"; exit 2; }
SSL=/opt/hiddify-manager/ssl
if [ ! -s "$SSL/$HOST.crt" ]; then
    # the block has to be there BEFORE the certificate can be issued: Let's Encrypt asks for the challenge file through this block's webroot location (the default server of nginx has none)
    [ "${NO_CERT_OK:-}" = 1 ] || { log "no certificate for $HOST yet: run with NO_CERT_OK=1 CURL_OPTS=-k first, then cert_instance.sh, then this script again"; exit 3; }
    log "no certificate yet (NO_CERT_OK=1): the checks run against HAProxy's default certificate"
fi
UUID="$(mysql hiddifypanel -N -B -e "select uuid from user where enable=1 order by id limit 1")"
MAIN="$(mysql hiddifypanel -N -B -e "select domain from domain where domain not like '%*%' and domain not regexp '^[0-9.]+\$' and domain<>'$HOST' order by id limit 1")"
fetch() { curl -s -m 60 $CURL_OPTS --resolve "$1:443:127.0.0.1" "${@:2}"; }
nbody() { grep -v -E '\.time[:#]' "$1" | md5sum | cut -d' ' -f1; }

nginx -t -c "$NGX_CONF" >/dev/null 2>&1 || { log "baseline: nginx config is not valid, nothing changed"; exit 3; }
B0="$(fetch "$MAIN" -A 'Hiddify/2.0' -o /tmp/lk_b0.txt -w '%{http_code}' "https://$MAIN/$PATHC/$UUID/sub/")"; M0="$(nbody /tmp/lk_b0.txt)"
[ "$B0" = 200 ] || { log "baseline: the subscription on $MAIN answers $B0, nothing changed"; exit 3; }

mkdir -p /root/.claude-backups /opt/hiddify-manager/lk
[ -f "$CONF" ] && cp -a "$CONF" "/root/.claude-backups/zz-lk.conf.before-$(date +%Y%m%d-%H%M%S)"
install -d -m 755 -o root -g root "$ROOT"
python3 "$HERE/build.py" --outdir "$ROOT" --brand "$BRAND" --bot "$BOT" --gate "$GATE" >/dev/null || { log "page build failed"; exit 4; }
install -m 644 -o root -g root "$HERE"/assets/cw.webp "$HERE"/assets/ct.webp "$ROOT"/ || { log "cover pictures missing: put your own cw.webp (wide) and ct.webp (tall) into assets/"; exit 4; }
chmod 644 "$ROOT"/index.html "$ROOT"/app.html "$ROOT"/app.js
sed -e "s#@@HOST@@#$HOST#g" -e "s#@@PATH@@#$PATHC#g" -e "s#@@ROOT@@#$ROOT#g" -e "s#@@GATE@@#$GATE#g" "$HERE/zz-lk.conf.in" > "$CONF"
rollback() { log "ROLLBACK: removing the block"; rm -f "$CONF"; nginx -t -c "$NGX_CONF" 2>&1 | tail -1; reload; exit 5; }
nginx -t -c "$NGX_CONF" 2>&1 | tail -2 | grep -q "successful" || { nginx -t -c "$NGX_CONF" 2>&1 | tail -3; rollback; }
reload

H="$(fetch "$HOST" -D - -o /tmp/lk_page.html "https://$HOST/" | tr -d '\r' | tr 'A-Z' 'a-z')"
CODE_PAGE="$(fetch "$HOST" -o /dev/null -w '%{http_code}' "https://$HOST/")"
chk "the front answers 200 with the example and the content-security-policy" '[ "$CODE_PAGE" = 200 ] && grep -q "решите пример" /tmp/lk_page.html && grep -qi "content-security-policy" /tmp/lk_page.html'
chk "the front says nothing about the service (no vpn / subscription / trial / bot / brand words, in the text or in the source)" '! grep -qi -E "vpn|подписк|пробн|telegram|бот|$BRAND|hiddify|happ|incy|прокси" /tmp/lk_page.html'
CODE_A0="$(fetch "$HOST" -o /dev/null -w '%{http_code}' "https://$HOST/app.html")"; CODE_A1="$(fetch "$HOST" -o /tmp/lk_app.html -w '%{http_code}' "https://$HOST/app.html?k=$GATE")"; CODE_J1="$(fetch "$HOST" -o /dev/null -w '%{http_code}' "https://$HOST/app.js?k=$GATE")"; CODE_J0="$(fetch "$HOST" -o /dev/null -w '%{http_code}' "https://$HOST/app.js?k=1")"
chk "the working part is closed without the gate number (404) and open with it (200)" '[ "$CODE_A0" = 404 ] && [ "$CODE_J0" = 404 ] && [ "$CODE_A1" = 200 ] && [ "$CODE_J1" = 200 ] && grep -q "phone" /tmp/lk_app.html'
CT_IMG="$(fetch "$HOST" -o /dev/null -w '%{http_code} %{content_type}' "https://$HOST/cw.webp")"
chk "the cover pictures are served (image/webp)" '[[ "$CT_IMG" == "200 image/webp"* ]]'
chk "security headers" '[[ "$H" == *"x-frame-options: deny"* && "$H" == *"x-content-type-options: nosniff"* && "$H" == *"referrer-policy: no-referrer"* ]]'
chk "no panel path and no uuid in any of the files" '! grep -q "$PATHC" /tmp/lk_page.html /tmp/lk_app.html && ! grep -q "$UUID" /tmp/lk_page.html /tmp/lk_app.html && ! grep -q "$PATHC" "$ROOT/app.js"'
CODE_80="$(curl -s -m 30 --resolve "$HOST:80:127.0.0.1" -o /dev/null -w '%{http_code} %{redirect_url}' "http://$HOST/")"
chk "plain http is sent to https" '[[ "$CODE_80" == "301 https://$HOST/"* ]]'
CODE_G="$(fetch "$HOST" -o /dev/null -w '%{http_code}' "https://$HOST/api/trial")"
chk "GET /api/trial is refused (405)" '[ "$CODE_G" = 405 ]'
CODE_X="$(fetch "$HOST" -o /dev/null -w '%{http_code}' "https://$HOST/anything")"
chk "other paths give 404" '[ "$CODE_X" = 404 ]'
# a request the panel refuses by validation: proves the way to the panel's real endpoint WITHOUT creating an account
R="$(fetch "$HOST" -X POST -H 'Content-Type: application/json' -d '{"phone":"123"}' -A 'Mozilla/5.0 (iPhone)' -D /tmp/lk_ph.txt -w ' http=%{http_code}' "https://$HOST/api/trial")"
chk "POST /api/trial with a bad phone: the panel itself answers (JSON, status error, 400 validation or 429 limit)" '[[ "$R" == *"\"status\": \"error\""* && ( "$R" == *"http=400"* || "$R" == *"http=429"* ) ]] && grep -qi "content-type: application/json" /tmp/lk_ph.txt'
chk "the panel's CORS header is not passed on" '! grep -qi "access-control-allow-origin" /tmp/lk_ph.txt'
B1="$(fetch "$MAIN" -A 'Hiddify/2.0' -o /tmp/lk_b1.txt -w '%{http_code}' "https://$MAIN/$PATHC/$UUID/sub/")"
chk "the subscription on $MAIN is unchanged" '[ "$B1" = 200 ] && [ "$(nbody /tmp/lk_b1.txt)" = "$M0" ]'
chk "nginx is active" '[ "$(systemctl is-active hiddify-nginx)" = active ]'
if [ "$FAILED" != 0 ]; then rollback; fi
log "DONE: https://$HOST/  (remove: install_lk.sh --remove)"
