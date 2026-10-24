#!/bin/bash
# VK TURN reserve entry - installs ONLY new files (/opt/vkturn, /etc/vkturn, vkturn-*.service) and starts the isolated services.
# It changes no existing config, route, firewall rule or sysctl of the host, and it opens NO port (see firewall-open.sh, run by the owner when a test is wanted).
# usage (root):  BIN=/path/wdtt-server [XRAY_BIN=...] [ROUTER_SOCKS_PORT=20808] [PUBLIC_PORT=47213] [CLIENT_DNS=1.1.1.1] [EXPECT_SHA=<sha256 of BIN>] bash install.sh
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
: "${BIN:?path of the built wdtt-server}"; : "${XRAY_BIN:=/opt/hiddify-manager/xray/bin/xray}"; : "${ROUTER_SOCKS_PORT:=20808}"; : "${PUBLIC_PORT:=47213}"; : "${CLIENT_DNS:=1.1.1.1}"
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }
for t in socat ip systemctl; do command -v $t >/dev/null || { echo "missing: $t"; exit 1; }; done
[ -x "$XRAY_BIN" ] || { echo "xray binary not found: $XRAY_BIN"; exit 1; }
if [ -n "${EXPECT_SHA:-}" ]; then [ "$(sha256sum "$BIN" | cut -d' ' -f1)" = "$EXPECT_SHA" ] || { echo "wdtt-server checksum differs from the audited build"; exit 1; }; fi
ss -H -ulpn | awk '{print $4}' | grep -qE "[:.]${PUBLIC_PORT}$" && { echo "UDP ${PUBLIC_PORT} is already used"; exit 1; }
[ -e /opt/vkturn ] && { echo "/opt/vkturn exists: run uninstall.sh first"; exit 1; }
BK=/root/.claude-backups/vkturn-$(date +%Y%m%d-%H%M%S); install -d -m 700 $BK
snap() {
  echo "iptables:$(iptables-save 2>/dev/null | grep -v '^#' | sha256sum | cut -c1-16)"
  echo "nft:$(nft list ruleset 2>/dev/null | sha256sum | cut -c1-16)"
  echo "ip4rules:$(ip rule | sha256sum | cut -c1-16)"
  echo "sysctl:$(sysctl -n net.ipv4.ip_forward net.ipv4.tcp_congestion_control net.core.rmem_max net.core.wmem_max | tr '\n' ' ')"
  echo "units:$(systemctl list-units --type=service --all --no-legend | awk '{print $1}' | grep -v vkturn | sha256sum | cut -c1-16)"
  echo "listen:$(ss -H -tulpn | awk '{print $1,$5}' | grep -v ":${PUBLIC_PORT}$" | sort | sha256sum | cut -c1-16)"
  echo "routes:$(ip route | grep -v '10.99.0.0/30' | sha256sum | cut -c1-16)"
}
snap > $BK/host-state.before; iptables-save > $BK/iptables-save.before 2>/dev/null || true; ip route > $BK/routes.before
echo "backup of the state: $BK"
install -d -m 755 /opt/vkturn/bin /etc/vkturn; install -d -m 700 /etc/vkturn/wdtt
install -m 755 "$BIN" /opt/vkturn/bin/wdtt-server
for f in ns-up.sh ns-down.sh tun-up.sh; do install -m 755 $HERE/files/bin/$f /opt/vkturn/bin/$f; done
install -m 644 $HERE/files/etc/xray-ns.json /etc/vkturn/xray-ns.json
printf 'XRAY_BIN=%s\nROUTER_SOCKS_PORT=%s\nPUBLIC_PORT=%s\nCLIENT_DNS=%s\n' "$XRAY_BIN" "$ROUTER_SOCKS_PORT" "$PUBLIC_PORT" "$CLIENT_DNS" > /etc/vkturn/env
chmod 644 /etc/vkturn/env
[ -s /etc/vkturn/wdtt/owner.pw ] || { head -c 24 /dev/urandom | base64 | tr -d '/+=\n' | head -c 24 > /etc/vkturn/wdtt/owner.pw; chmod 600 /etc/vkturn/wdtt/owner.pw; }
"$XRAY_BIN" run -test -c /etc/vkturn/xray-ns.json >/dev/null 2>&1 || { echo "xray refuses the config (no TUN inbound in this build?)"; rm -rf /opt/vkturn /etc/vkturn; exit 1; }
for u in ns hostbridge xray nsbridge server relay; do sed "s#@XRAY_BIN@#$XRAY_BIN#g" $HERE/files/units/vkturn-$u.service > /etc/systemd/system/vkturn-$u.service; chmod 644 /etc/systemd/system/vkturn-$u.service; done      # systemd cannot take the executable from a variable
systemctl daemon-reload
for u in ns hostbridge xray nsbridge server relay; do
  systemctl enable --now vkturn-$u.service >/dev/null 2>&1 || { echo "FAILED to start vkturn-$u: rolling back"; bash "$HERE/uninstall.sh"; exit 2; }
done
sleep 4
for u in ns hostbridge xray nsbridge server relay; do echo "vkturn-$u: $(systemctl is-active vkturn-$u)"; done
snap > $BK/host-state.after
if diff -q $BK/host-state.before $BK/host-state.after >/dev/null; then
  echo "host state (iptables, nft, ip rules, sysctl, other services, listening sockets, routes): UNCHANGED"
else
  echo "HOST STATE DIFFERS:"; diff $BK/host-state.before $BK/host-state.after
fi
echo "public UDP port (closed until firewall-open.sh): ${PUBLIC_PORT}; owner password file: /etc/vkturn/wdtt/owner.pw (600, not printed)"
