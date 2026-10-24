# STAND ONLY: start the built qWDTT server inside an isolated netns and prove what it touches (host state before/after)
set -u
B=/work/stage2/vkturn/wdtt-server; C=/tmp/vkt-conf
snap() { echo "iptables:$(iptables-save 2>/dev/null | grep -v '^#' | sha256sum | cut -c1-12) nft:$(nft list ruleset 2>/dev/null | sha256sum | cut -c1-12) links:$(ip -o link | awk -F': ' '{print $2}' | sort | tr '\n' ',' | sha256sum | cut -c1-12) cc:$(sysctl -n net.ipv4.tcp_congestion_control) rmem:$(sysctl -n net.core.rmem_max) fwd:$(sysctl -n net.ipv4.ip_forward) routes:$(ip route | sha256sum | cut -c1-12)"; }
cleanup() { ip netns pids vkts 2>/dev/null | xargs -r kill 2>/dev/null; sleep 1; ip netns del vkts 2>/dev/null; rm -rf $C; }
cleanup; mkdir -p $C; chmod 700 $C; head -c 18 /dev/urandom | base64 > $C/pw; chmod 600 $C/pw
ip netns add vkts; ip netns exec vkts ip link set lo up
echo "BEFORE host: $(snap)"
ip netns exec vkts $B -listen 127.0.0.1:56000 -listen-direct 127.0.0.1:56002 -wg-port 56001 -config-dir $C -password-file $C/pw -dns 1.1.1.1 >/tmp/vkt-server.log 2>&1 &
sleep 6
echo "AFTER  host: $(snap)"
echo "-- server log (first lines):"; sed -E 's/[0-9]{4}\/[0-9]{2}\/[0-9]{2} [0-9:.]+ //' /tmp/vkt-server.log | head -25
echo "-- inside the namespace:"; ip netns exec vkts ip -br addr; ip netns exec vkts ss -H -tulpn | awk '{print $1,$5,$7}' | sed -E 's/users:\(\("([^"]+)".*/\1/'
echo "-- ns firewall:"; ip netns exec vkts iptables-save 2>/dev/null | grep -E "WDTT|MASQ|TCPMSS" | head -6; ip netns exec vkts nft list tables 2>/dev/null
echo "-- config dir files:"; ls $C
cleanup; echo "AFTER cleanup host: $(snap)"; ip netns list | wc -l
