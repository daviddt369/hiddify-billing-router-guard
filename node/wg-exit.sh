#!/usr/bin/env bash
# node/wg-exit.sh - WireGuard server on an EXIT node (the server whose IP the users should get), for one entrance node (RU).
# Run on the exit as root. The RU side is node/ru-attach-wg.sh.
#
#   bash wg-exit.sh --check  --peer-pub KEY      CHECK: shows what is found and what would be done. Changes nothing.
#   bash wg-exit.sh --apply  --peer-pub KEY      apply (repeatable; a file it replaces is copied first)
#   bash wg-exit.sh --status                     tunnel state (handshake, traffic) - read only
#   bash wg-exit.sh --remove                     take the interface and rules down (config and keys are kept in /root/wg-<if>-removed-...)
#
# Options (defaults suit the first exit; give every further exit its own interface, port and tunnel subnet):
#   --if NAME          interface name                     (wg-exit)
#   --port N           UDP port                           (51823)
#   --srv-addr A/M     address of this side in the tunnel (10.66.70.1/30)
#   --peer-tun A/32    address of the entrance node       (10.66.70.2/32)
#   --peer-pub KEY     PUBLIC key of the entrance node    (ru-attach-wg.sh prints it)
#   --mtu N  --keepalive N                                (1280, 25)
#
# What --apply does:
#   1. installs wireguard-tools (if missing), checks that the kernel can do WireGuard;
#   2. creates the server keys /etc/wireguard/<if>.key|.pub (if there are none);
#   3. writes /etc/wireguard/<if>.conf: one peer = the entrance node; the iptables rules live in PostUp/PostDown of that file and go up and down with the interface;
#   4. turns net.ipv4.ip_forward on (file /etc/sysctl.d/98-<if>.conf);
#   5. enables wg-quick@<if> at boot and prints the server public key and the endpoint for the entrance node.
# Rules of other software are not touched: only the rules for the UDP port and the NAT of the entrance node's tunnel address are added.
set -Eeuo pipefail

WG_IF="wg-exit"; WG_PORT="51823"; SRV_ADDR="10.66.70.1/30"; PEER_TUN="10.66.70.2/32"; PEER_PUB=""; MTU="1280"; KEEPALIVE="25"
MODE="check"
while [ $# -gt 0 ]; do
    case "$1" in
        --check) MODE="check" ;;
        --apply) MODE="apply" ;;
        --status) MODE="status" ;;
        --remove) MODE="remove" ;;
        --if) WG_IF="$2"; shift ;;
        --port) WG_PORT="$2"; shift ;;
        --srv-addr) SRV_ADDR="$2"; shift ;;
        --peer-tun) PEER_TUN="$2"; shift ;;
        --peer-pub) PEER_PUB="$2"; shift ;;
        --mtu) MTU="$2"; shift ;;
        --keepalive) KEEPALIVE="$2"; shift ;;
        -h|--help) sed -n 2,26p "$0"; exit 0 ;;
        *) echo "unknown argument: $1 (see --help)" >&2; exit 2 ;;
    esac
    shift
done
[[ "$WG_IF" =~ ^[A-Za-z0-9_-]{1,15}$ ]] || { echo "bad --if" >&2; exit 2; }
[[ "$WG_PORT" =~ ^[0-9]{2,5}$ ]] || { echo "bad --port" >&2; exit 2; }
[[ "$SRV_ADDR" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}/[0-9]{1,2}$ ]] || { echo "bad --srv-addr" >&2; exit 2; }
[[ "$PEER_TUN" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}/32$ ]] || { echo "bad --peer-tun" >&2; exit 2; }
if [ "$MODE" = "check" ] || [ "$MODE" = "apply" ]; then
    [[ "$PEER_PUB" =~ ^[A-Za-z0-9+/]{43}=$ ]] || { echo "--peer-pub is required: the public key of the entrance node (44 characters, base64)" >&2; exit 2; }
fi
CONF="/etc/wireguard/${WG_IF}.conf"
KEY="/etc/wireguard/${WG_IF}.key"
PUB="/etc/wireguard/${WG_IF}.pub"
SYSCTL_FILE="/etc/sysctl.d/98-${WG_IF}.conf"
STAMP="$(date +%Y%m%d-%H%M%S)"

log()  { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
warn() { printf '[%s] WARNING: %s\n' "$(date +%H:%M:%S)" "$*" >&2; }
die()  { printf '[%s] ERROR: %s\n' "$(date +%H:%M:%S)" "$*" >&2; exit 1; }
trap 'die "failed at line $LINENO (command: $BASH_COMMAND). The script can be run again."' ERR

[ "$(id -u)" -eq 0 ] || die "root is required"
exec 9>"/run/wg-exit-${WG_IF}.lock"
flock -n 9 || die "another instance is running"

# has <regex>: reads all of stdin and searches it (no grep -q in a pipe: no SIGPIPE race with pipefail)
has()  { local text; text="$(cat)"; grep -Eq -- "$1" <<<"$text"; }
hasf() { local text; text="$(cat)"; grep -Fq -- "$1" <<<"$text"; }

backup() {
    if [ -e "$1" ]; then cp -a "$1" "$1.bak-${STAMP}"; log "copy: $1.bak-${STAMP}"; fi
    return 0
}

detect_net() {
    local line
    line="$(ip -4 route get 1.1.1.1 2>/dev/null | head -1 || true)"
    PUB_IF="$(printf '%s\n' "$line" | sed -nE 's/.* dev ([^ ]+).*/\1/p')"
    PUB_IP="$(printf '%s\n' "$line" | sed -nE 's/.* src ([0-9.]+).*/\1/p')"
    [ -n "$PUB_IF" ] || die "cannot find the outgoing interface (ip route get 1.1.1.1 said: $line)"
}

port_in_use() { ss -H -ulnp 2>/dev/null | awk '{print $4}' | has "[:.]${WG_PORT}\$"; }
if_exists()   { ip -o link show "${WG_IF}" >/dev/null 2>&1; }

rules_text() {   # the same set for PostUp (argument 1) and PostDown (argument 0)
    local add="$1" snat_target
    if [ -n "${PUB_IP:-}" ]; then snat_target="SNAT --to-source ${PUB_IP}"; else snat_target="MASQUERADE"; fi
    local -a specs=(
        "filter|INPUT|-p udp --dport ${WG_PORT} -j ACCEPT"
        "filter|FORWARD|-i ${WG_IF} -o ${PUB_IF} -j ACCEPT"
        "filter|FORWARD|-i ${PUB_IF} -o ${WG_IF} -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT"
        "nat|POSTROUTING|-s ${PEER_TUN} -o ${PUB_IF} -j ${snat_target}"
    )
    local s table chain rule tflag
    for s in "${specs[@]}"; do
        IFS='|' read -r table chain rule <<<"$s"
        tflag=""
        if [ "$table" = "nat" ]; then tflag="-t nat "; fi
        if [ "$add" = "1" ]; then
            echo "PostUp = iptables ${tflag}-C ${chain} ${rule} 2>/dev/null || iptables ${tflag}-I ${chain} 1 ${rule}"
        else
            echo "PostDown = iptables ${tflag}-D ${chain} ${rule} 2>/dev/null || true"
        fi
    done
}

render_conf() {
    local privkey="$1"
    cat <<EOF
# Managed by node/wg-exit.sh (${STAMP}). WireGuard server for the entrance node.
[Interface]
Address = ${SRV_ADDR}
ListenPort = ${WG_PORT}
PrivateKey = ${privkey}
MTU = ${MTU}
SaveConfig = false

$(rules_text 1)
$(rules_text 0)

[Peer]
# the entrance node (its router core runs its own WireGuard)
PublicKey = ${PEER_PUB}
AllowedIPs = ${PEER_TUN}
PersistentKeepalive = ${KEEPALIVE}
EOF
}

# ----------------------------------------------------------------------------------------------- status (read only)
if [ "$MODE" = "status" ]; then
    if_exists || die "no interface ${WG_IF} (was the script run with --apply?)"
    wg show "${WG_IF}" | sed -E 's/(private key: ).*/\1(hidden)/'
    echo
    iptables -t nat -S POSTROUTING 2>/dev/null | grep -F -- "${PEER_TUN}" || echo "no NAT rule for ${PEER_TUN}"
    exit 0
fi

# ----------------------------------------------------------------------------------------------- survey (read only)
detect_net
OS_NAME="$( . /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-unknown}" )"
log "server: $(hostname), ${OS_NAME}, kernel $(uname -r), virtualization: $(systemd-detect-virt 2>/dev/null || echo ?)"
log "outgoing interface: ${PUB_IF}, source address: ${PUB_IP:-?}"
if command -v wg >/dev/null 2>&1; then log "wg: $(command -v wg) ($(wg --version | awk '{print $2}'))"; else log "wg: not installed (--apply installs wireguard-tools)"; fi
if grep -q '^wireguard ' /proc/modules 2>/dev/null; then
    log "kernel module wireguard: loaded"
elif modinfo wireguard >/dev/null 2>&1; then
    log "kernel module wireguard: present, loads on start"
else
    warn "the kernel has no wireguard module (container / cut-down kernel?) - cannot go on"
    if [ "$MODE" = "apply" ]; then die "WireGuard is not available in this kernel"; fi
fi
if port_in_use; then warn "UDP ${WG_PORT} is already used: $(ss -H -ulnp | awk -v p=":${WG_PORT}\$" '$4 ~ p {print $4, $6}' | head -2)"; else log "UDP ${WG_PORT}: free"; fi
if if_exists; then log "interface ${WG_IF}: exists"; else log "interface ${WG_IF}: not there"; fi
if [ -e "$CONF" ]; then log "config ${CONF}: exists (it is copied before being replaced)"; else log "config ${CONF}: not there"; fi
log "ip_forward now: $(sysctl -n net.ipv4.ip_forward)"
log "ufw: $( (ufw status 2>/dev/null | head -1) || echo none )"
if command -v nft >/dev/null 2>&1; then
    log "nftables tables: $(nft list tables 2>/dev/null | tr '\n' ' ')"
    nft list ruleset 2>/dev/null | grep -Ei 'policy drop' | head -3 | sed 's/^/    policy drop in nft: /' || true
fi
log "iptables FORWARD: $(iptables -S FORWARD 2>/dev/null | head -1)"
log "rules that --apply adds (PostUp of the config):"
rules_text 1 | sed 's/^PostUp = /    /'

if [ "$MODE" = "check" ]; then
    log "config that would be written to ${CONF} (the server key is created by --apply):"
    render_conf "<created by --apply>" | sed 's/^/    | /'
    log "this was only a CHECK, nothing was changed. Apply: bash $0 --apply --if ${WG_IF} --port ${WG_PORT} --peer-pub ..."
    exit 0
fi

# ----------------------------------------------------------------------------------------------- remove
if [ "$MODE" = "remove" ]; then
    DEST="/root/wg-${WG_IF}-removed-${STAMP}"
    mkdir -m 700 "$DEST"
    systemctl disable --now "wg-quick@${WG_IF}" 2>/dev/null || true
    if if_exists; then wg-quick down "${WG_IF}" 2>/dev/null || true; fi
    for f in "$CONF" "$KEY" "$PUB" "$SYSCTL_FILE"; do
        if [ -e "$f" ]; then mv "$f" "$DEST/"; log "moved to $DEST: $f"; fi
    done
    log "done. ip_forward stays as it was until the reboot; the iptables rules left with the interface."
    exit 0
fi

# ----------------------------------------------------------------------------------------------- apply
if port_in_use && ! if_exists; then
    die "UDP ${WG_PORT} is used by another process - choose another --port and repeat"
fi

if ! command -v wg >/dev/null 2>&1; then
    log "installing wireguard-tools"
    DEBIAN_FRONTEND=noninteractive apt-get update -qq || warn "apt-get update failed - trying the cache"
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq wireguard-tools iptables
fi
modprobe wireguard 2>/dev/null || true
command -v wg-quick >/dev/null 2>&1 || die "wg-quick not found after the install"

install -d -m 700 /etc/wireguard
if [ ! -s "$KEY" ]; then
    log "creating the server keys"
    ( umask 077; wg genkey > "$KEY"; wg pubkey < "$KEY" > "$PUB" )
else
    log "server keys exist - using them"
    if [ ! -s "$PUB" ]; then ( umask 077; wg pubkey < "$KEY" > "$PUB" ); fi
fi
chmod 600 "$KEY" "$PUB"

backup "$CONF"
NEW_CONF="$(mktemp "/etc/wireguard/.${WG_IF}.XXXXXX")"
chmod 600 "$NEW_CONF"
render_conf "$(cat "$KEY")" > "$NEW_CONF"
mv "$NEW_CONF" "$CONF"
log "written ${CONF}"

backup "$SYSCTL_FILE"
printf 'net.ipv4.ip_forward = 1\n' > "$SYSCTL_FILE"
sysctl -q -p "$SYSCTL_FILE"

if if_exists; then
    log "the interface is up - restarting it to apply the config"
    systemctl restart "wg-quick@${WG_IF}"
else
    systemctl enable --now "wg-quick@${WG_IF}"
fi
systemctl enable "wg-quick@${WG_IF}" >/dev/null 2>&1 || true
sleep 1

# ----------------------------------------------------------------------------------------------- self check
OK=1
chk() { if "$2" >/dev/null 2>&1; then log "  OK   $1"; else warn "  BAD  $1"; OK=0; fi; }
c_peer() { wg show "${WG_IF}" peers | hasf "${PEER_PUB}"; }
c_fwd()  { [ "$(sysctl -n net.ipv4.ip_forward)" = "1" ]; }
c_in()   { iptables -C INPUT -p udp --dport "${WG_PORT}" -j ACCEPT; }
c_out()  { iptables -C FORWARD -i "${WG_IF}" -o "${PUB_IF}" -j ACCEPT; }
c_back() { iptables -C FORWARD -i "${PUB_IF}" -o "${WG_IF}" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT; }
c_nat()  { iptables -t nat -S POSTROUTING | hasf "-s ${PEER_TUN} "; }
c_boot() { systemctl is-enabled "wg-quick@${WG_IF}"; }
log "self check:"
chk "interface ${WG_IF} is up"            if_exists
chk "UDP ${WG_PORT} is listening"         port_in_use
chk "the entrance node's peer is written" c_peer
chk "ip_forward = 1"                      c_fwd
chk "INPUT rule ${WG_PORT}/udp"           c_in
chk "FORWARD: tunnel -> outside"          c_out
chk "FORWARD: answers -> tunnel"          c_back
chk "NAT for ${PEER_TUN}"                 c_nat
chk "wg-quick@${WG_IF} starts at boot"    c_boot

echo
echo "================= GIVE THESE LINES TO THE ENTRANCE NODE (public, no secrets) ================="
echo "SERVER_PUBLIC_KEY=$(cat "$PUB")"
echo "ENDPOINT=${PUB_IP}:${WG_PORT}"
echo "SERVER_TUNNEL_IP=${SRV_ADDR%/*}   PEER_TUNNEL_IP=${PEER_TUN}   MTU=${MTU}"
echo "SELF_CHECK=$([ "$OK" = 1 ] && echo OK || echo FAILED)"
echo "==============================================================================================="
echo "If the hosting provider has a cloud firewall (in its panel), open UDP ${WG_PORT} there."
[ "$OK" = 1 ] || exit 1
