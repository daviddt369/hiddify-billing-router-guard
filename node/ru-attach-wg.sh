#!/usr/bin/env bash
# node/ru-attach-wg.sh - on the ENTRANCE node (the one with the panel): connect an exit node over WireGuard. The exit side is node/wg-exit.sh.
#
#   bash ru-attach-wg.sh keygen NAME
#       creates this node's key for the tunnel to the exit NAME (/etc/wireguard/wg-NAME_private.key, kept if it exists) and prints the PUBLIC key:
#       give it to the exit:   bash wg-exit.sh --apply --peer-pub <that key> ...
#   bash ru-attach-wg.sh attach NAME --server-pub KEY --endpoint IP:PORT [--tunnel-addr 10.66.70.2/32] [--slot N [--title TEXT --iso NL]] [--mtu 1280] [--priority 8]
#       takes the lines wg-exit.sh printed (SERVER_PUBLIC_KEY, ENDPOINT, PEER_TUNNEL_IP), adds the exit to the panel as an upstream (Routing -> Upstreams),
#       puts it into location slot N (a country of the "Countries / locations" page) and presses "Apply" (one router restart).
#       Before any change: a backup of the routing tables and of the router config in /root/attach-NAME-backup-<time>; a failure before Apply puts the database back.
#   bash ru-attach-wg.sh status NAME     handshake and traffic of the tunnel on this side (read only)
set -Eeuo pipefail

PY=/opt/hiddify-manager/.venv313/bin/python
CTL="$PY /opt/hiddify-manager/scripts/multiloc_ctl.py"
STAMP="$(date +%Y%m%d-%H%M%S)"
log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
sql() { mysql hiddifypanel -N -B -e "$1"; }

CMD="${1:-}"; NAME="${2:-}"
[ -n "$CMD" ] && [ -n "$NAME" ] || { sed -n 2,14p "$0"; exit 2; }
[[ "$NAME" =~ ^[a-z0-9][a-z0-9-]{0,24}$ ]] || { echo "NAME: lower-case letters, digits, '-', up to 25 characters" >&2; exit 2; }
shift 2
[ "$(id -u)" -eq 0 ] || { echo "root is required" >&2; exit 1; }
KEYFILE="/etc/wireguard/wg-${NAME}_private.key"
UPNAME="wg-${NAME}"

case "$CMD" in
keygen)
    command -v wg >/dev/null || { DEBIAN_FRONTEND=noninteractive apt-get update -qq; DEBIAN_FRONTEND=noninteractive apt-get install -y -qq wireguard-tools; }
    install -d -m 700 /etc/wireguard
    if [ ! -s "$KEYFILE" ]; then ( umask 077; wg genkey > "$KEYFILE" ); log "created $KEYFILE"; else log "$KEYFILE exists - kept"; fi
    chmod 600 "$KEYFILE"
    echo "PEER_PUBLIC_KEY=$(wg pubkey < "$KEYFILE")"
    ;;
status)
    # the tunnel lives inside the router core (xray: its own WireGuard on a TUN device), so `wg show` does not list it here
    row="$(sql "SELECT CONCAT('id=', id, ' enabled=', enabled, ' endpoint=', wg_endpoint, ' health=', COALESCE(last_status,''), ' checked=', COALESCE(last_checked_at,'')) FROM commercial_routing_upstream WHERE name='${UPNAME}'")"
    [ -n "$row" ] || { echo "no upstream ${UPNAME} in the panel" >&2; exit 1; }
    echo "panel:  $row"
    echo "router: $(systemctl is-active xray-router)"
    for i in $(ls /sys/class/net | grep -E '^wg[0-9]+$' || true); do
        echo "tunnel device $i: rx=$(cat /sys/class/net/$i/statistics/rx_bytes) tx=$(cat /sys/class/net/$i/statistics/tx_bytes) bytes"
    done
    echo "the handshake itself is seen on the exit:  bash wg-exit.sh --status --if <its interface>  (latest handshake, transfer)"
    ;;
attach)
    SERVER_PUB=""; ENDPOINT=""; ADDR="10.66.70.2/32"; MTU=1280; PRIORITY=8; SLOT=""; TITLE=""; ISO=""
    while [ $# -gt 0 ]; do
        case "$1" in
            --server-pub) SERVER_PUB="$2"; shift ;;
            --endpoint) ENDPOINT="$2"; shift ;;
            --tunnel-addr) ADDR="$2"; shift ;;
            --mtu) MTU="$2"; shift ;;
            --priority) PRIORITY="$2"; shift ;;
            --slot) SLOT="$2"; shift ;;
            --title) TITLE="$2"; shift ;;
            --iso) ISO="$2"; shift ;;
            *) echo "unknown argument: $1" >&2; exit 2 ;;
        esac
        shift
    done
    CREATED_ID=""; DB_TOUCHED=0; FINISHED=0
    BK="/root/attach-${NAME}-backup-${STAMP}"
    rollback_db() {
        [ "$DB_TOUCHED" = "1" ] || return 0
        echo "[$(date +%H:%M:%S)] rolling back the database changes of THIS run (the router was not changed); backup: $BK" >&2
        if [ -n "$CREATED_ID" ]; then
            $CTL unassign "$CREATED_ID" >/dev/null 2>&1 || true
            sql "DELETE FROM commercial_routing_upstream WHERE id=${CREATED_ID} AND name='${UPNAME}'" || true
        fi
        DB_TOUCHED=0
    }
    die() { echo "[$(date +%H:%M:%S)] ERROR: $*" >&2; if [ "$FINISHED" != "1" ]; then rollback_db || true; fi; exit 1; }
    trap 'rc=$?; trap - ERR; [ "$FINISHED" = "1" ] || rollback_db; exit $rc' ERR

    [[ "$SERVER_PUB" =~ ^[A-Za-z0-9+/]{43}=$ ]] || die "--server-pub: 44 characters of base64 ending in '=' (SERVER_PUBLIC_KEY of wg-exit.sh)"
    [[ "$ENDPOINT" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}:[0-9]{2,5}$ ]] || die "--endpoint must look like 203.0.113.5:51823"
    [[ "$ADDR" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}/32$ ]] || die "--tunnel-addr must be A.B.C.D/32"
    [ -s "$KEYFILE" ] || die "no $KEYFILE - run: bash $0 keygen ${NAME}"
    [ "$(stat -c %a "$KEYFILE")" = "600" ] || die "the key file must have mode 600"
    [ -z "$SLOT" ] || [[ "$SLOT" =~ ^[0-9]+$ ]] || die "--slot must be a number"
    FIRST_EXIT=0
    if [ -s /etc/xray-router/config.json ]; then
        systemctl is-active --quiet xray-router || die "xray-router is not active - fix that first"
    else
        FIRST_EXIT=1; log "no router config yet: this is the first exit of this node, the apply below builds the router config"
    fi
    if [ -s /var/lib/multiloc-router-apply/status.json ]; then
        state="$($PY -c 'import json;print(json.load(open("/var/lib/multiloc-router-apply/status.json")).get("state",""))')"
        case "$state" in running|starting|active) die "another router apply is running (state=$state)" ;; esac
        [ "$state" != "needs-confirm" ] || die "a built config waits for a decision (panel: Apply new config / Do not apply) - decide that first"
    fi
    $CTL status | grep -q '^problems: none' || die "multiloc_ctl status reports problems - sort them out first"
    log "checks passed; endpoint ${ENDPOINT}"

    mkdir -m 700 "$BK"
    if [ -s /etc/xray-router/config.json ]; then cp -a /etc/xray-router/config.json "$BK/xray-router.config.json"; fi
    mysqldump --single-transaction hiddifypanel commercial_routing_upstream commercial_routing_location commercial_routing_location_node commercial_routing_location_tester > "$BK/tables.sql"
    log "backup: $BK"

    KEYREF="file:${KEYFILE}"
    exists="$(sql "SELECT COUNT(*) FROM commercial_routing_upstream WHERE name='${UPNAME}'")"
    if [ "$exists" != "0" ]; then
        log "upstream ${UPNAME} exists - updating its fields"
        sql "UPDATE commercial_routing_upstream SET enabled=1, tunnel_type='wireguard', wg_endpoint='${ENDPOINT}', wg_public_key='${SERVER_PUB}', wg_private_key_ref='${KEYREF}', wg_addresses='${ADDR}', wg_mtu=${MTU}, updated_at=UTC_TIMESTAMP() WHERE name='${UPNAME}'"
    else
        DB_TOUCHED=1
        sql "INSERT INTO commercial_routing_upstream
             (name, label, enabled, priority, tunnel_type, wg_endpoint, wg_public_key, wg_private_key_ref, wg_addresses, wg_mtu, vless_uri, trojan_uri, last_status, created_at, updated_at)
             VALUES ('${UPNAME}', '${UPNAME}', 1, ${PRIORITY}, 'wireguard', '${ENDPOINT}', '${SERVER_PUB}', '${KEYREF}', '${ADDR}', ${MTU}, '', '', '', UTC_TIMESTAMP(), UTC_TIMESTAMP())"
        CREATED_ID="$(sql "SELECT id FROM commercial_routing_upstream WHERE name='${UPNAME}'")"
    fi
    NEW_ID="$(sql "SELECT id FROM commercial_routing_upstream WHERE name='${UPNAME}'")"
    [[ "$NEW_ID" =~ ^[0-9]+$ ]] || die "cannot read the id of the new upstream"
    # xray matches a balancer selector by tag PREFIX: the id must not be the beginning of another tag (upstream-1 would capture upstream-17) and vice versa
    clash="$(sql "SELECT GROUP_CONCAT(id) FROM commercial_routing_upstream WHERE id<>${NEW_ID} AND (CAST(id AS CHAR) LIKE '${NEW_ID}%' OR '${NEW_ID}' LIKE CONCAT(CAST(id AS CHAR), '%'))")"
    if [ -n "$clash" ] && [ "$clash" != "NULL" ]; then die "id ${NEW_ID} clashes by tag prefix with upstream(s) ${clash}: the balancer would capture the wrong outbounds. Delete this upstream in the panel and add the exits in another order"; fi
    log "upstream ${UPNAME}: id ${NEW_ID}"

    if [ -n "$SLOT" ]; then
        SETARGS=(); [ -z "$TITLE" ] || SETARGS+=(--title "$TITLE"); [ -z "$ISO" ] || SETARGS+=(--iso "$ISO")
        if [ ${#SETARGS[@]} -gt 0 ]; then $CTL set "$SLOT" "${SETARGS[@]}" --enabled 1; fi
        $CTL assign "$SLOT" "$NEW_ID"
    else
        log "no --slot: the upstream is in the AUTO pool only; give it a country in the panel (Routing -> Countries / locations) when you want one"
    fi

    log "applying (the same as the panel button): one router restart"
    # `if` on purpose: a plain failing command would fire the ERR trap (and roll the database back) even with `set +e`
    if $CTL apply --auto; then rc=0; else rc=$?; fi
    log "apply rc=${rc}"
    if [ "$rc" = "0" ] && [ "$FIRST_EXIT" = "1" ]; then
        # the very first config of a node is built the classic way (without the director layer); one more pass makes it the full pipeline result.
        # Nothing was edited in between, so the difference between the two is accepted.
        log "first exit of this node: converging the router config to the full pipeline once"
        if $CTL apply --full --accept-diff; then log "second pass done"; else log "WARNING: the second pass returned $? - the first config is working; press Apply in the panel to see what is pending"; fi
    fi
    if [ "$rc" = "4" ]; then
        FINISHED=1
        log "the config is built but differs from the working one by more than this change: it is held and waits for your decision (router untouched). See the panel -> Routing. Backup: $BK"
        exit 4
    fi
    if [ "$rc" != "0" ]; then rollback_db; FINISHED=1; die "apply finished with code ${rc}: the database is back as it was, the router was not changed (backup: $BK)"; fi
    FINISHED=1
    if ! systemctl is-active --quiet xray-router; then systemctl enable --now xray-router; fi
    systemctl is-active xray-router
    log "done. Check the exit side: bash wg-exit.sh --status --if <interface>  (handshake and traffic); here: bash $0 status ${NAME}"
    ;;
*)
    sed -n 2,14p "$0"; exit 2 ;;
esac
