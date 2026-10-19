#!/bin/bash
# CP-07 · проверка ДО (только чтение). Запускать на .190 от root. Код возврата 0 = можно применять.
# Переменные (по умолчанию боевые): CONF, WANIF, PUB, CLIENT, WGIF.  CAPTURE=1 — дополнительно 6 секунд пассивного захвата (источник пакетов NAT).
set -u
CONF=${CONF:-/etc/wireguard/wg-rude.conf}
WANIF=${WANIF:-ens3}
PUB=${PUB:-203.0.113.20}
CLIENT=${CLIENT:-10.66.66.2/32}
WGIF=${WGIF:-wg-rude}
fail=0
ok()   { echo "  OK    $*"; }
bad()  { echo "  FAIL  $*"; fail=1; }
warn() { echo "  WARN  $*"; }

echo "1. файл $CONF"
n_masq_up=$(grep -c "^PostUp = iptables -t nat -C POSTROUTING -s $CLIENT -o $WANIF -j MASQUERADE" "$CONF")
n_masq_dn=$(grep -c "^PostDown = iptables -t nat -D POSTROUTING -s $CLIENT -o $WANIF -j MASQUERADE" "$CONF")
n_snat_up=$(grep -c "^PostUp = iptables -t nat -C POSTROUTING -s $CLIENT -o $WANIF -j SNAT --to-source $PUB" "$CONF")
if [ "$n_masq_up" = 1 ] && [ "$n_masq_dn" = 1 ]; then ok "в файле ровно по одной строке MASQUERADE в PostUp и PostDown (состояние «до»)"
elif [ "$n_snat_up" = 1 ]; then ok "файл уже содержит SNAT (состояние «после»)"
else bad "строки NAT в файле не такие, как ожидалось (MASQUERADE up=$n_masq_up down=$n_masq_dn, SNAT up=$n_snat_up)"; fi

echo "2. адреса $WANIF (порядок важен: MASQUERADE берёт ПЕРВЫЙ адрес списка, а не src из маршрута)"
ip -4 -o addr show dev "$WANIF" | awk '{print "  " NR ". " $4}'
first=$(ip -4 -o addr show dev "$WANIF" | awk 'NR==1{split($4,a,"/"); print a[1]}')
if ip -4 -o addr show dev "$WANIF" | grep -q " $PUB/"; then ok "публичный адрес $PUB есть на $WANIF"; else bad "адреса $PUB нет на $WANIF"; fi
if [ "$first" != "$PUB" ]; then warn "первый адрес = $first, а не $PUB: именно при таком порядке MASQUERADE выбирает $first (причина дефекта)"; else warn "первый адрес уже $PUB: MASQUERADE не должен был ломать обратный путь, причина может быть другой"; fi

echo "3. счётчики NAT и обратного правила FORWARD (если MASQUERADE: пакеты уходят, а обратных нет)"
iptables -t nat -L POSTROUTING -n -v --line-numbers 2>/dev/null | grep -E "MASQUERADE|SNAT" | grep "$WANIF" | grep "${CLIENT%/32}" | sed 's/^/  /'
iptables -L FORWARD -n -v -x 2>/dev/null | awk -v w="$WGIF" -v e="$WANIF" '($6==e && $7==w) || ($6==w && $7==e)' | sed 's/^/  FORWARD /'

echo "4. WireGuard"
if wg show "$WGIF" >/dev/null 2>&1; then
  hs=$(wg show "$WGIF" latest-handshakes | awk '{print $2}' | sort -n | tail -1); now=$(date +%s)
  if [ -n "$hs" ] && [ "$hs" -gt 0 ]; then ok "последнее рукопожатие $((now-hs)) с назад"; else warn "рукопожатия не было"; fi
  wg show "$WGIF" transfer | sed 's/^[^\t]*\t/  transfer rx=/; s/\t/ tx=/'
else bad "интерфейс $WGIF не поднят"; fi
if systemctl is-enabled "wg-quick@$WGIF" >/dev/null 2>&1; then ok "wg-quick@$WGIF включён при загрузке"; else warn "wg-quick@$WGIF не включён: после перезагрузки правило не вернётся само"; fi

if [ "${CAPTURE:-0}" = 1 ]; then
  echo "5. пассивный захват 6 с: с каким адресом пакеты клиента уходят в $WANIF (запись не делается, только счёт)"
  python3 - "$WANIF" <<'PY'
import collections, socket, struct, sys, time
s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3)); s.bind((sys.argv[1], 0)); s.settimeout(0.5)
c = collections.Counter(); t0 = time.time()
while time.time() - t0 < 6:
    try:
        b, meta = s.recvfrom(2048)
    except socket.timeout:
        continue
    if meta[2] != 4 or len(b) < 34 or b[12:14] != b"\x08\x00":      # только исходящие (PACKET_OUTGOING) IPv4
        continue
    if b[23] in (6, 17):
        src, dst = socket.inet_ntoa(b[26:30]), socket.inet_ntoa(b[30:34])
        if not dst.startswith(("10.", "127.", "172.16.")) and not src.startswith(("10.", "127.")):
            c[src] += 1
for src, n in c.most_common(6):
    print(f"  уходит с источником {src}: {n} пакетов")
PY
fi

echo
if [ $fail -eq 0 ]; then echo "ИТОГ: можно применять"; else echo "ИТОГ: НЕ применять, см. FAIL выше"; fi
exit $fail
