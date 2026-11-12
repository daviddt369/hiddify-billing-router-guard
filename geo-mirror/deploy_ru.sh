#!/bin/bash
# Put the geo-file mirror on the node (files, timer, nginx location on the lk host) or take it off again. Checks first, an automatic way back on any failed check.
# usage: deploy_ru.sh <lk host> <token> <owner/name of the GitHub repository> <profile.json>     install (run from the geo-mirror/ directory of the repository, as root)
#        deploy_ru.sh --remove                                                          remove (timer, location, include line; the downloaded files stay in /var/lib/cheb-geo)
# The token is the path part of the mirror URL: https://<lk host>/<token>/geoip.dat . Generate it once (for example `openssl rand -hex 12`) and keep it out of the repository.
# env:   CURL_OPTS="-k" (stand: self-signed certificates)
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
CONF=/opt/hiddify-manager/nginx/conf.d/zz-lk.conf
NGX_CONF=/opt/hiddify-manager/nginx/nginx.conf
EXTRA=/opt/hiddify-manager/nginx/conf.d/lk-extra
LOC=$EXTRA/cheb-geo.locations
INC='    include /opt/hiddify-manager/nginx/conf.d/lk-extra/*.locations;'
APP=/opt/cheb-geo
DATA=/var/lib/cheb-geo
CURL_OPTS="${CURL_OPTS:-}"
TS="$(date +%Y%m%d-%H%M%S)"
log() { echo "[geo-mirror $(date +%H:%M:%S)] $*"; }
reload() { systemctl reload hiddify-nginx; sleep 2; }

if [ "${1:-}" = "--remove" ]; then
    systemctl disable --now cheb-geo.timer >/dev/null 2>&1; rm -f /etc/systemd/system/cheb-geo.timer /etc/systemd/system/cheb-geo.service; systemctl daemon-reload
    mkdir -p /root/.claude-backups
    [ -f "$LOC" ] && { cp -a "$LOC" "/root/.claude-backups/cheb-geo.locations.removed-$TS"; rm -f "$LOC"; }
    if [ -f "$CONF" ]; then
        cp -a "$CONF" "/root/.claude-backups/zz-lk.conf.removed-geo-$TS"
        python3 - "$CONF" "$INC" <<'PY'
import sys
p, inc = sys.argv[1], sys.argv[2]
lines = open(p, encoding="utf-8").read().split("\n")
open(p, "w", encoding="utf-8").write("\n".join(x for x in lines if x != inc and x != "    # extra locations of this host (the geo-file mirror)"))
PY
    fi
    nginx -t -c "$NGX_CONF" 2>&1 | tail -1; reload; log "removed (the lk host block keeps working; the files stay in $DATA)"; exit 0
fi

HOST="$1"; TOKEN="$2"; REPO="$3"; PROFILE="$4"
[[ "$HOST" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}$ ]] || { log "bad host"; exit 2; }
[[ "$TOKEN" =~ ^[A-Za-z0-9]{12,64}$ ]] || { log "the token must be 12-64 letters/digits"; exit 2; }
[[ "$REPO" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]] || { log "bad repository (owner/name)"; exit 2; }
[ -s "$PROFILE" ] && python3 -c "import json,sys;json.load(open(sys.argv[1]))" "$PROFILE" || { log "profile.json is missing or not JSON"; exit 2; }
[ -f "$CONF" ] || { log "no $CONF: install the lk host first (lk/install_lk.sh)"; exit 3; }
[ -x /opt/hiddify-manager/xray/bin/xray ] || { log "no xray binary"; exit 3; }
FAILED=0
chk() { if eval "$2"; then log "  OK    $1"; else log "  FAIL  $1"; FAILED=1; fi; }
fetch() { curl -s -m 60 $CURL_OPTS --resolve "$HOST:443:127.0.0.1" "$@"; }
nbody() { grep -v -E '\.time[:#]' "$1" | md5sum | cut -d' ' -f1; }

PATHC="$(mysql hiddifypanel -N -B -e "select value from str_config where \`key\`='proxy_path_client'")"
UUID="$(mysql hiddifypanel -N -B -e "select uuid from user where enable=1 order by id limit 1")"
MAIN="$(mysql hiddifypanel -N -B -e "select domain from domain where domain not like '%*%' and domain not regexp '^[0-9.]+\$' and domain<>'$HOST' order by id limit 1")"
nginx -t -c "$NGX_CONF" >/dev/null 2>&1 || { log "baseline: nginx config is not valid, nothing changed"; exit 3; }
B0="$(curl -s -m 60 $CURL_OPTS --resolve "$MAIN:443:127.0.0.1" -A 'Hiddify/2.0' -o /tmp/gm_b0.txt -w '%{http_code}' "https://$MAIN/$PATHC/$UUID/sub/")"; M0="$(nbody /tmp/gm_b0.txt)"
[ "$B0" = 200 ] || { log "baseline: the subscription on $MAIN answers $B0, nothing changed"; exit 3; }

mkdir -p /root/.claude-backups "$APP" "$DATA/www" "$DATA/last-good" "$EXTRA"
cp -a "$CONF" "/root/.claude-backups/zz-lk.conf.before-geo-$TS"
HAD_LOC=0; [ -f "$LOC" ] && { HAD_LOC=1; cp -a "$LOC" "/root/.claude-backups/cheb-geo.locations.before-geo-$TS"; }
HAD_TIMER=0; [ -f /etc/systemd/system/cheb-geo.timer ] && HAD_TIMER=1
install -m 755 -o root -g root "$HERE/cheb_geo_update.py" "$APP/cheb_geo_update.py"
install -m 644 -o root -g root "$PROFILE" "$DATA/profile.json"
chmod 755 "$DATA" "$DATA/www"
sed -e "s#@@TOKEN@@#$TOKEN#g" -e "s#@@WWW@@#$DATA/www#g" "$HERE/cheb-geo.locations.in" > "$LOC"; chmod 644 "$LOC"
rollback() {
    log "ROLLBACK: nginx (and the timer) back to the state before"
    if [ "$HAD_TIMER" = 0 ]; then systemctl disable --now cheb-geo.timer >/dev/null 2>&1; rm -f /etc/systemd/system/cheb-geo.timer /etc/systemd/system/cheb-geo.service; systemctl daemon-reload; fi
    cp -a "/root/.claude-backups/zz-lk.conf.before-geo-$TS" "$CONF"
    if [ "$HAD_LOC" = 1 ]; then cp -a "/root/.claude-backups/cheb-geo.locations.before-geo-$TS" "$LOC"; else rm -f "$LOC"; fi
    nginx -t -c "$NGX_CONF" 2>&1 | tail -1; reload; exit 5
}
if ! grep -qF "$INC" "$CONF"; then
    python3 - "$CONF" "$INC" <<'PY' || { log "could not place the include line"; rollback; }
import sys
p, inc = sys.argv[1], sys.argv[2]
t = open(p, encoding="utf-8").read()
marker = "    location / {\n        return 404;"
if marker not in t:
    sys.exit(1)
open(p, "w", encoding="utf-8").write(t.replace(marker, "    # extra locations of this host (the geo-file mirror)\n" + inc + "\n\n" + marker, 1))
PY
fi
nginx -t -c "$NGX_CONF" 2>&1 | tail -2 | grep -q "successful" || { nginx -t -c "$NGX_CONF" 2>&1 | tail -3; rollback; }
reload

log "first download + check (the real xray loads the profile with the files)"
python3 "$APP/cheb_geo_update.py" --repo "$REPO" --profile "$DATA/profile.json" --force || true
[ -s "$DATA/www/geoip.dat" ] && [ -s "$DATA/www/geosite.dat" ] || { log "no files were published (see above)"; rollback; }

cat > /etc/systemd/system/cheb-geo.service <<EOF
[Unit]
Description=Geo-file mirror: take the latest release, check it, publish it
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 $APP/cheb_geo_update.py --repo $REPO --profile $DATA/profile.json
EOF
cat > /etc/systemd/system/cheb-geo.timer <<'EOF'
[Unit]
Description=Geo-file mirror, four checks a day (it downloads only when the release changed)

[Timer]
OnCalendar=*-*-* 00,06,12,18:30:00
AccuracySec=5min
RandomizedDelaySec=10min
Persistent=false

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload; systemctl enable --now cheb-geo.timer >/dev/null 2>&1

for n in geoip geosite; do
    C="$(fetch -o /tmp/gm_$n.dat -w '%{http_code} %{content_type}' "https://$HOST/$TOKEN/$n.dat")"
    chk "$n.dat is served (200, octet-stream), the bytes equal the published file" '[[ "$C" == "200 application/octet-stream"* ]] && cmp -s /tmp/gm_$n.dat "$DATA/www/$n.dat"'
done
chk "a wrong token and a missing token give 404" '[ "$(fetch -o /dev/null -w "%{http_code}" "https://$HOST/${TOKEN}x/geoip.dat")" = 404 ] && [ "$(fetch -o /dev/null -w "%{http_code}" "https://$HOST/geoip.dat")" = 404 ]'
chk "plain http gives 404 (no files over http)" '[ "$(curl -s -m 30 --resolve "$HOST:80:127.0.0.1" -o /dev/null -w "%{http_code}" "http://$HOST/$TOKEN/geoip.dat")" = 404 ]'
chk "the host still answers its front page (200) and keeps the working part closed without the gate" '[ "$(fetch -o /dev/null -w "%{http_code}" "https://$HOST/")" = 200 ] && [ "$(fetch -o /dev/null -w "%{http_code}" "https://$HOST/app.html")" = 404 ]'
chk "the other mirror paths of the host are not served (a sibling name and a directory listing are 404)" '[ "$(fetch -o /dev/null -w "%{http_code}" "https://$HOST/$TOKEN/")" = 404 ] && [ "$(fetch -o /dev/null -w "%{http_code}" "https://$HOST/$TOKEN/state.json")" = 404 ]'
B1="$(curl -s -m 60 $CURL_OPTS --resolve "$MAIN:443:127.0.0.1" -A 'Hiddify/2.0' -o /tmp/gm_b1.txt -w '%{http_code}' "https://$MAIN/$PATHC/$UUID/sub/")"
chk "the subscription is unchanged (same body)" '[ "$B1" = 200 ] && [ "$(nbody /tmp/gm_b1.txt)" = "$M0" ]'
chk "panel and nginx are active, the timer is waiting" '[ "$(systemctl is-active hiddify-panel)" = active ] && [ "$(systemctl is-active hiddify-nginx)" = active ] && systemctl is-enabled cheb-geo.timer >/dev/null'
rm -f /tmp/gm_*.dat /tmp/gm_*.txt
if [ "$FAILED" != 0 ]; then rollback; fi
log "DONE. Mirror URLs: https://$HOST/<token>/geoip.dat and /geosite.dat . Timer: cheb-geo.timer. Remove: deploy_ru.sh --remove. Back to the state before: cp /root/.claude-backups/zz-lk.conf.before-geo-$TS $CONF (and delete $LOC)"
