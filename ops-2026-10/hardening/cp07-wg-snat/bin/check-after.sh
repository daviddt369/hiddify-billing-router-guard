#!/bin/bash
# CP-07 · проверка ПОСЛЕ. Запускать на .190 от root. Ждёт до 150 с, пока появится обратный трафик (пробы observatory на RU идут раз в минуту).
# CP07_NO_WAIT=1 — без ожидания (для стенда без второй стороны).
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

echo "1. правило NAT"
if iptables -t nat -C POSTROUTING -s "$CLIENT" -o "$WANIF" -j SNAT --to-source "$PUB" 2>/dev/null; then ok "SNAT --to-source $PUB есть"; else bad "правила SNAT нет"; fi
if iptables -t nat -C POSTROUTING -s "$CLIENT" -o "$WANIF" -j MASQUERADE 2>/dev/null; then bad "старое правило MASQUERADE осталось"; else ok "MASQUERADE для $CLIENT нет"; fi
pos=$(iptables -t nat -L POSTROUTING -n --line-numbers | awk -v c="${CLIENT%/32}" '$0 ~ "SNAT" && $0 ~ c {print $1; exit}')
[ "$pos" = 1 ] && ok "SNAT стоит первым" || warn "SNAT на позиции ${pos:-?}: проверьте, что выше нет правил, которые перехватывают этот трафик"

echo "2. файл (переживёт перезапуск wg-quick и перезагрузку)"
if grep -q "^PostUp = iptables -t nat -C POSTROUTING -s $CLIENT -o $WANIF -j SNAT --to-source $PUB" "$CONF" && ! grep -q "MASQUERADE" "$CONF"; then ok "$CONF содержит SNAT и не содержит MASQUERADE"; else bad "$CONF не приведён к виду «после»"; fi

echo "3. обратный путь: счётчик правила FORWARD ens3 -> $WGIF (RELATED,ESTABLISHED) должен расти"
fwd() { iptables -L FORWARD -n -v -x 2>/dev/null | awk -v w="$WGIF" -v e="$WANIF" '$6==e && $7==w {print $1; exit}'; }
before=${CP07_FWD_BEFORE:-$(fwd)}; before=${before:-0}
if [ "${CP07_NO_WAIT:-0}" = 1 ]; then
  warn "ожидание пропущено (CP07_NO_WAIT=1); счётчик сейчас $before"
else
  got=0
  for i in $(seq 1 30); do
    now=$(fwd); now=${now:-0}
    if [ "$now" -gt "$before" ]; then got=1; break; fi
    sleep 5
  done
  if [ $got = 1 ]; then ok "обратные пакеты пошли: счётчик $before -> $now"; else bad "за 150 с обратных пакетов нет (счётчик $before); проверьте RU: ip -s link show wg0"; fi
fi

echo "4. WireGuard"
if wg show "$WGIF" >/dev/null 2>&1; then
  hs=$(wg show "$WGIF" latest-handshakes | awk '{print $2}' | sort -n | tail -1); now=$(date +%s)
  if [ -n "$hs" ] && [ "$hs" -gt 0 ]; then ok "последнее рукопожатие $((now-hs)) с назад"; else warn "рукопожатия не было"; fi
else bad "интерфейс $WGIF не поднят"; fi

cat <<'TXT'

Проверка со стороны RU-ноды (выполнить там; два безопасных чтения/запроса):
  ip -s link show wg0                                   # RX packets должен расти (до исправления стоял 0)
  curl -s -m 8 --interface wg0 https://1.1.1.1/cdn-cgi/trace | grep '^ip='   # ожидается ip=203.0.113.20
TXT
echo
if [ $fail -eq 0 ]; then echo "ИТОГ: CP-07 применён, проверки пройдены"; else echo "ИТОГ: есть FAIL, см. выше; при сомнении откат: rollback.sh"; fi
exit $fail
