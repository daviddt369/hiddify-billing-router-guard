#!/bin/bash
# CP-09 v2 · проверка ДО (только чтение; на минуту запускает временные экземпляры движка на случайном loopback-порту). RU, от root.
# usage: check-before.sh xray|singbox      код возврата 0 = можно применять
set -u
. "$(cd "$(dirname "$0")" && pwd)/lib.sh"
setup_engine "${1:-}"

echo "1. шаблон $J2"
want=$(sha16 "$T_BEFORE"); have=$(sha16 "$J2"); after=$(sha16 "$T_AFTER")
if [ "$have" = "$want" ]; then ok "шаблон = ожидаемый исходный ($have)"
elif [ "$have" = "$after" ]; then ok "шаблон уже новый ($have): пакет для $ENGINE применён"
else bad "шаблон другой ($have, ожидалось $want): его кто-то менял; применять нельзя"; fi

echo "2. dns_server в панели и соответствие сгенерированного файла шаблону"
DNS_SERVER=$(dns_server)
echo "  dns_server = $DNS_SERVER"
case "$DNS_SERVER" in
  1.1.1.1|1.0.0.1|8.8.8.8|8.8.4.4|9.9.9.9|149.112.112.112) ok "адрес с DoH и IP-сертификатом: https://$DNS_SERVER/dns-query" ;;
  *[a-zA-Z]*) ok "имя узла DoH: https://$DNS_SERVER/dns-query" ;;
  *) if [ "${CP09_ALLOW_ANY_DNS:-0}" = 1 ]; then warn "нестандартный адрес разрешён флагом (лаборатория)"; else bad "dns_server=$DNS_SERVER: у этого адреса может не быть DoH с IP-сертификатом; задайте 1.1.1.1 / 8.8.8.8 или имя хоста"; fi ;;
esac
t=$(mktemp -d)
if [ "$have" = "$after" ]; then src="$T_AFTER"; else src="$T_BEFORE"; fi
if render "$src" "$DNS_SERVER" "$t/expected.json" 2>"$t/err"; then
  if same_json "$t/expected.json" "$GEN"; then ok "сгенерированный 02_dns.json = результат шаблона (ручных правок нет)"; else bad "сгенерированный 02_dns.json не равен результату шаблона: файл правили вручную или шаблон не применён"; fi
else bad "шаблон не отрисовывается: $(tail -1 "$t/err")"; fi
rm -rf "$t"

echo "3. текущая конфигурация проходит проверку движка (контроль)"
if [ "$ENGINE" = xray ]; then
  t=$(mktemp -d); cp "$CONFDIR"/*.json "$t"/ 2>/dev/null
  if "$BIN" run -test -confdir "$t" >/tmp/cp09-before.txt 2>&1; then ok "xray run -test -confdir: Configuration OK ($("$BIN" version | head -1 | cut -c1-30))"; else bad "текущая конфигурация не проходит проверку: $(tail -1 /tmp/cp09-before.txt)"; fi
  rm -rf "$t"
else
  echo "  у hiddify-core нет команды check: новый DNS проверяется временным экземпляром настоящего бинарника на шаге 5 (до замены файлов)"
  "$BIN" version 2>&1 | head -1 | cut -c1-80
fi
if [ "$(systemctl is-active "$UNIT" 2>/dev/null)" = active ]; then ok "$UNIT active"; else warn "$UNIT не active (в лаборатории нормально)"; fi

echo "4. DoH-серверы доступны с этого узла напрямую (без туннеля и без выходов)"
if [ "$ENGINE" = xray ]; then doh_reachability "$DNS_SERVER" 8.8.8.8 9.9.9.9; need=2; else doh_reachability "$DNS_SERVER"; need=1; fi
[ "${DOH_GOOD:-0}" -ge "$need" ] && ok "доступно резолверов: $DOH_GOOD (нужно не меньше $need)" || bad "доступно резолверов: ${DOH_GOOD:-0}, нужно не меньше $need: переход на DoH сломал бы разрешение имён"

echo "5. текущая утечка (информационно): открытый DNS с этого узла за 10 с"
if [ "${CP09_SKIP_CAPTURE:-0}" = 1 ]; then echo "  (захват пропущен)"; else plain_dns_capture 10 || echo "  (это и есть исправляемая проблема F-03; остановка не требуется)"; fi

echo "6. напоминание"
echo "  Пакет меняет ТОЛЬКО DNS первого хопа $ENGINE ($UNIT): $J2 и $GEN. AdGuard Home, xray-router, HAProxy, панель и БД не затрагиваются."
echo "  Перезапуск $UNIT оборвёт на 1–5 с клиентские соединения этого движка; клиенты переподключаются сами."
echo
if [ $fail -eq 0 ]; then echo "ИТОГ: можно применять"; else echo "ИТОГ: НЕ применять, см. FAIL выше"; fi
exit $fail
