#!/bin/bash
# health check of the reserve entry (root). Prints no secrets.
echo "== units"; for u in ns hostbridge xray nsbridge server relay; do printf "vkturn-%s: %s\n" $u "$(systemctl is-active vkturn-$u)"; done
echo "== namespace"; ip netns exec vkturn ip -br addr 2>&1 | sed -n 1,6p
echo "== listeners inside the namespace / public relay"
ip netns exec vkturn ss -H -tulpn 2>/dev/null | awk '{print "ns:",$1,$5}'
ss -H -ulpn | grep socat | awk '{print "host:",$1,$4}'
PORT=$(grep -h PUBLIC_PORT /etc/vkturn/env | cut -d= -f2)
echo "firewall: udp/$PORT $(iptables -C INPUT -p udp --dport $PORT -j ACCEPT 2>/dev/null && echo OPEN || echo closed)"
echo "== egress from the namespace (must be a FOREIGN exit, never the host's own address)"
echo "host address seen by the internet: $(curl -s -m 6 https://api.ipify.org)"
for i in 1 2; do echo "namespace exit: $(ip netns exec vkturn curl -s -m 10 https://api.ipify.org)"; done
echo "== DNS inside the namespace"
ip netns exec vkturn python3 - <<'PY'
import socket
q = b"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00" + b"\x07example\x03com\x00\x00\x01\x00\x01"
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(6)
try:
    s.sendto(q, ("8.8.8.8", 53)); d, _ = s.recvfrom(512); print("answered", len(d), "bytes, rcode", d[3] & 15)
except Exception as e:
    print("no answer:", e)
PY
echo "== resources"; systemctl show vkturn-server vkturn-xray -p MemoryCurrent --value 2>/dev/null | paste -sd' '
