#!/bin/bash
# CP-09 v2 · ручной откат (RU, от root): возвращает 02_dns.json.j2 и 02_dns.json движка из копии и один раз перезапускает его службу.
# usage: rollback.sh xray|singbox [каталог копии; по умолчанию тот, что запомнил apply.sh]
set -u
. "$(cd "$(dirname "$0")" && pwd)/lib.sh"
setup_engine "${1:-}"
BK=${2:-$(cat "$PTR" 2>/dev/null || true)}
[ -n "$BK" ] && [ -f "$BK/02_dns.json.j2" ] && [ -f "$BK/02_dns.json" ] || { echo "нужен каталог копии с 02_dns.json.j2 и 02_dns.json: rollback.sh $ENGINE <каталог>"; exit 2; }
cp -a "$J2" "$BK/02_dns.json.j2.cp09-new" 2>/dev/null; cp -a "$GEN" "$BK/02_dns.json.cp09-new" 2>/dev/null     # копия того, что откатываем
cp -a "$BK/02_dns.json.j2" "$J2"
cp -a "$BK/02_dns.json" "$GEN"
eval "$RESTART_CMD" || { echo "перезапуск не удался: systemctl status $UNIT"; exit 1; }
sleep 3
echo "готово: DNS первого хопа $ENGINE как до CP-09 ($(tr -d ' \n' < "$GEN" | cut -c1-90)…); $UNIT: $(systemctl is-active "$UNIT" 2>/dev/null)"
