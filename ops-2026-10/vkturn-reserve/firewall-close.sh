#!/bin/bash
# closes the port again: the live rule and, if it got saved, the line in /etc/iptables/rules.v4 (with a backup of that file)
set -u
PORT=${1:?port}
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }
while iptables -C INPUT -p udp --dport $PORT -j ACCEPT 2>/dev/null; do iptables -D INPUT -p udp --dport $PORT -j ACCEPT; done
if grep -q -- "--dport $PORT -j ACCEPT" /etc/iptables/rules.v4 2>/dev/null; then
  cp -p /etc/iptables/rules.v4 /etc/iptables/rules.v4.bak-vkturn-$(date +%Y%m%d-%H%M%S)
  sed -i "/-p udp -m udp --dport $PORT -j ACCEPT/d; /-A INPUT -p udp --dport $PORT -j ACCEPT/d" /etc/iptables/rules.v4
  echo "removed from rules.v4 (backup kept)"
fi
echo "closed udp/$PORT: $(iptables -S INPUT | grep -c -- "--dport $PORT") rules left"
