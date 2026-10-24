# STAND ONLY rehearsal of the package: install -> checks -> simulated client traffic -> relay -> router restart -> server kill -> uninstall -> no residue
P=/work/stage2/vkturn/pkg
echo "######## install"; BIN=$P/bin/wdtt-server EXPECT_SHA=$(cut -d' ' -f1 $P/bin/wdtt-server.sha256) XRAY_BIN=/work/stage2/bin/xray-26.3.27 ROUTER_SOCKS_PORT=20808 PUBLIC_PORT=47213 bash $P/install.sh 2>&1 | tail -12
echo; echo "######## listeners / firewall state"
ip netns exec vkturn ss -H -tulpn | awk '{print "ns:",$1,$5}'; ss -H -ulpn | grep socat | awk '{print "host:",$1,$4}'
echo "firewall udp/47213: $(iptables -C INPUT -p udp --dport 47213 -j ACCEPT 2>/dev/null && echo OPEN || echo closed)"
echo; echo "######## simulated client packets: source 10.66.77.7 (a wdtt client address) leaves the namespace"
ip netns exec vkturn ip addr add 10.66.77.7/32 dev lo 2>/dev/null
for i in 1 2; do ip netns exec vkturn curl -s -m 8 --interface 10.66.77.7 http://13.0.0.20:18080/ | head -c 130; echo; done
echo; echo "######## relay: public UDP port -> namespace (an echo replaces the server for a moment)"
systemctl stop vkturn-server; sleep 1
ip netns exec vkturn python3 -c "
import socket; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.bind(('10.99.0.2',56000)); s.settimeout(6)
d,a=s.recvfrom(100); s.sendto(b'echo:'+d,a)" &
sleep 1
python3 -c "
import socket; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.settimeout(4); s.sendto(b'ping',('127.0.0.1',47213)); print('relay answer:', s.recvfrom(100)[0])"
wait; systemctl start vkturn-server; sleep 4; echo "server again: $(systemctl is-active vkturn-server)"
echo; echo "######## router restart: the reserve entry must recover without a restart of ours"
systemctl restart xray-router 2>/dev/null || echo "(stand router unit name differs)"; sleep 4
ip netns exec vkturn curl -s -m 8 --interface 10.66.77.7 http://13.0.0.20:18080/ | head -c 130; echo
echo; echo "######## SIGTERM and SIGKILL of the server: systemd brings it back"
pkill -TERM -f "wdtt-server -listen"; sleep 9; echo "after SIGTERM: $(systemctl is-active vkturn-server)"
pkill -KILL -f "wdtt-server -listen"; sleep 9; echo "after SIGKILL: $(systemctl is-active vkturn-server)"
echo "wdtt0 present: $(ip netns exec vkturn ip -br link | grep -c wdtt0)"
echo; echo "######## uninstall"; bash $P/uninstall.sh 2>&1 | tail -3
echo "host: ns=$(ip netns list | wc -l) veth=$(ip -o link | grep -c vkt) units=$(ls /etc/systemd/system | grep -c vkturn) sockets=$(ss -H -ulpn | grep -c 47213)"
