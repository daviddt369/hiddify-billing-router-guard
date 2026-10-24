#!/bin/bash
# removes everything the reserve entry installed (units, files, namespace, veth). Does NOT touch the firewall: run firewall-close.sh if the port was opened.
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }
PORT=$(grep -h PUBLIC_PORT /etc/vkturn/env 2>/dev/null | cut -d= -f2)
for u in relay server nsbridge xray hostbridge ns; do systemctl disable --now vkturn-$u.service >/dev/null 2>&1; done
rm -f /etc/systemd/system/vkturn-*.service; systemctl daemon-reload; systemctl reset-failed 'vkturn-*' 2>/dev/null
ip link del vkt-h 2>/dev/null; ip netns del vkturn 2>/dev/null
rm -rf /opt/vkturn /etc/vkturn /run/vkturn
echo "removed. left: units=$(ls /etc/systemd/system | grep -c vkturn) netns=$(ip netns list | grep -c vkturn) veth=$(ip -o link | grep -c vkt-h) dirs=$(ls -d /opt/vkturn /etc/vkturn 2>/dev/null | wc -l)"
[ -n "$PORT" ] && iptables -C INPUT -p udp --dport "$PORT" -j ACCEPT 2>/dev/null && echo "NOTE: the firewall rule for udp/$PORT is still open: run firewall-close.sh $PORT"
exit 0
