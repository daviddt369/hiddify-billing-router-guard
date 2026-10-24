#!/bin/bash
# CP-07 · ручной откат на .190 (владелец, от root): возвращает wg-rude.conf из резервной копии и правило MASQUERADE «на лету».
# usage: rollback.sh [каталог резервной копии; по умолчанию последний из /run/cp07.last-backup]
set -euo pipefail
CONF=${CONF:-/etc/wireguard/wg-rude.conf}
WANIF=${WANIF:-ens3}
PUB=${PUB:-203.0.113.20}
CLIENT=${CLIENT:-10.66.66.2/32}
BK=${1:-$(cat /run/cp07.last-backup 2>/dev/null || true)}
[ -n "$BK" ] && [ -f "$BK/$(basename "$CONF")" ] || { echo "нужен каталог резервной копии с $(basename "$CONF")"; exit 2; }

grep -q "MASQUERADE" "$BK/$(basename "$CONF")" || { echo "в $BK/$(basename "$CONF") нет MASQUERADE: это не состояние до CP-07, откат остановлен"; exit 2; }
echo "== файл: восстановление из $BK"
cp -a "$BK/$(basename "$CONF")" "$CONF"
echo "== правило: сначала MASQUERADE, потом удаление SNAT"
iptables -t nat -C POSTROUTING -s "$CLIENT" -o "$WANIF" -j MASQUERADE 2>/dev/null \
  || iptables -t nat -I POSTROUTING 1 -s "$CLIENT" -o "$WANIF" -j MASQUERADE
while iptables -t nat -C POSTROUTING -s "$CLIENT" -o "$WANIF" -j SNAT --to-source "$PUB" 2>/dev/null; do
  iptables -t nat -D POSTROUTING -s "$CLIENT" -o "$WANIF" -j SNAT --to-source "$PUB"
done
iptables -t nat -L POSTROUTING -n -v --line-numbers | grep -E "SNAT|MASQUERADE" | grep "${CLIENT%/32}" | sed 's/^/  /'
echo "готово: состояние как до CP-07 (обратный путь снова не работает — так было до исправления)"
