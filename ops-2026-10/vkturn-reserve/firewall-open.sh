#!/bin/bash
# THE ONLY FIREWALL CHANGE of the reserve entry: accept UDP <port> on INPUT (the port the VK TURN relay sends to). The owner runs it, for the test window.
# usage: firewall-open.sh <port>   Not persisted by this script (the rule is gone after a reboot). Hiddify's next apply saves the live rules, so run firewall-close.sh after the test.
set -eu
PORT=${1:?port}
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }
BK=/root/.claude-backups/vkturn-firewall-$(date +%Y%m%d-%H%M%S); install -d -m 700 $BK; iptables-save > $BK/iptables-save.before
iptables -C INPUT -p udp --dport $PORT -j ACCEPT 2>/dev/null || iptables -I INPUT -p udp --dport $PORT -j ACCEPT
echo "opened udp/$PORT (backup of the rules: $BK)"; iptables -S INPUT | grep -- "--dport $PORT"
