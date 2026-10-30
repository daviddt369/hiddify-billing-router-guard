#!/bin/bash
# The TLS certificate of the registration host (e.g. hd.example.com), kept by a SECOND instance of the routing landing's certificate service (the panel cannot do it:
# a domain added to the panel as "subscription only" would sort before the main domain and become the one the bot puts into links; a "direct" one adds servers to every subscription).
# Needs /opt/routing-links/cert-{common,issue,maintain}.sh (installed with the routing landing), the keep-marker patch of Hiddify's replace_variables.sh, and the A record of the host pointing here.
# usage: cert_instance.sh <host> <main domain of the subscription (guard host)>      install   (as root, on the node)
#        cert_instance.sh --remove <host>                                              take it off again (the certificate files stay in the stash)
set -uo pipefail
log() { echo "[lk-cert $(date +%H:%M:%S)] $*"; }
UNIT=lk-cert
STASH=/var/lib/lk-tls
NGX=/opt/hiddify-manager/nginx/conf.d/zz-lk.conf

if [ "${1:-}" = "--remove" ]; then
    H="${2:?host}"
    systemctl disable --now $UNIT.timer 2>/dev/null; rm -f /etc/systemd/system/$UNIT.service /etc/systemd/system/$UNIT.timer /opt/routing-links/keep-ssl/"$H"; systemctl daemon-reload
    log "removed (stash $STASH and ssl/$H.* stay; delete them by hand if the host is gone)"; exit 0
fi

HOST="$1"; GUARD="$2"
[[ "$HOST" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}$ ]] && [[ "$GUARD" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}$ ]] || { log "bad host arguments"; exit 2; }
for f in cert-common.sh cert-issue.sh cert-maintain.sh; do [ -x /opt/routing-links/$f ] || [ -f /opt/routing-links/$f ] || { log "missing /opt/routing-links/$f"; exit 3; }; done
grep -q "keep-ssl" /opt/hiddify-manager/common/replace_variables.sh || { log "the keep-marker patch of replace_variables.sh is not there: Hiddify would delete the certificate"; exit 3; }
MINE="$(curl -fsS -m 10 https://api.ipify.org 2>/dev/null || true)"; RES="$(dig +short -t a "$HOST" @1.1.1.1 | tail -1)"
[ -n "$RES" ] && { [ -z "$MINE" ] || [ "$RES" = "$MINE" ]; } || { log "A($HOST)=$RES, this server=$MINE: the record is not ready"; exit 3; }
log "DNS ok: $HOST -> $RES"

rollback() { log "ROLLBACK"; systemctl disable --now $UNIT.timer 2>/dev/null; rm -f /etc/systemd/system/$UNIT.service /etc/systemd/system/$UNIT.timer /opt/routing-links/keep-ssl/"$HOST"; systemctl daemon-reload; exit 5; }
mkdir -p /opt/routing-links/keep-ssl "$STASH"; : > /opt/routing-links/keep-ssl/"$HOST"; chmod 700 "$STASH"
cat > /etc/systemd/system/$UNIT.service <<EOF
[Unit]
Description=Keep the registration host TLS certificate installed and renewed
After=network-online.target

[Service]
Type=oneshot
ExecStart=/opt/routing-links/cert-maintain.sh
Environment=ROUTING_HOST=$HOST
Environment=ROUTING_TLS_STASH=$STASH
Environment=ROUTING_NGINX_CONF=$NGX
Environment=ROUTING_GUARD_HOST=$GUARD
PrivateTmp=true
TimeoutStartSec=300
EOF
cat > /etc/systemd/system/$UNIT.timer <<EOF
[Unit]
Description=Check the registration host TLS certificate every 5 minutes

[Timer]
OnBootSec=4min
OnUnitActiveSec=5min
AccuracySec=30s

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload

# issue once (Let's Encrypt over the shared webroot; the default server of nginx answers the challenge for any host), then install into ssl/ and reload HAProxy seamlessly
if ! ROUTING_HOST="$HOST" ROUTING_TLS_STASH="$STASH" ROUTING_NGINX_CONF="$NGX" ROUTING_GUARD_HOST="$GUARD" bash /opt/routing-links/cert-issue.sh; then log "issuing failed"; rollback; fi
systemctl enable --now $UNIT.timer >/dev/null 2>&1

FAILED=0
chk() { if eval "$2"; then log "  OK    $1"; else log "  FAIL  $1"; FAILED=1; fi; }
chk "certificate and key in ssl/" '[ -s /opt/hiddify-manager/ssl/$HOST.crt ] && [ -s /opt/hiddify-manager/ssl/$HOST.crt.key ]'
ISS="$(echo | openssl s_client -connect 127.0.0.1:443 -servername "$HOST" 2>/dev/null | openssl x509 -noout -issuer -enddate 2>/dev/null | tr '\n' ' ')"
chk "HAProxy serves it ($ISS)" '[[ "$ISS" == *"Let"*"Encrypt"* ]]'
chk "the timer is active" '[ "$(systemctl is-active $UNIT.timer)" = active ]'
chk "the keep-marker is in place" '[ -e /opt/routing-links/keep-ssl/$HOST ]'
chk "the routing landing's own service is untouched" '[ "$(systemctl is-failed routing-links-cert.service)" != failed ]'
if [ "$FAILED" != 0 ]; then log "checks failed: the certificate files stay, the timer and the marker are removed"; rollback; fi
log "DONE: $HOST has a certificate; now run lk/install_lk.sh"
