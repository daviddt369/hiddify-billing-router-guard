#!/bin/bash
# CP-09 v2 · проверка ПОСЛЕ. RU, от root. Код возврата 0 = всё как задумано.
# usage: check-after.sh xray|singbox      (apply.sh передаёт BK=<каталог копии> для сравнения слушаемых портов)
set -u
. "$(cd "$(dirname "$0")" && pwd)/lib.sh"
setup_engine "${1:-}"

active=0
if [ "$(systemctl is-active "$UNIT" 2>/dev/null)" = active ]; then ok "$UNIT active"; active=1; else warn "$UNIT не active (в лаборатории нормально)"; fi
DNS_SERVER=$(dns_server)
t=$(mktemp -d)
if [ "$(sha16 "$J2")" = "$(sha16 "$T_AFTER")" ]; then ok "шаблон = новый"; else bad "шаблон не новый"; fi
if render "$T_AFTER" "$DNS_SERVER" "$t/expected.json" 2>/dev/null && same_json "$t/expected.json" "$GEN"; then ok "02_dns.json = результат нового шаблона"; else bad "02_dns.json не совпадает с результатом нового шаблона"; fi
if grep -q 'dns-query\|"https"' "$GEN" && ! grep -q '"localhost"' "$GEN"; then ok "02_dns.json: DoH, системного резолвера (localhost) и открытого UDP нет"; else bad "02_dns.json не в новом виде"; fi
rm -rf "$t"

if [ "$ENGINE" = xray ]; then
  t=$(mktemp -d); cp "$CONFDIR"/*.json "$t"/
  if "$BIN" run -test -confdir "$t" >/tmp/cp09-after.txt 2>&1; then ok "xray run -test -confdir: Configuration OK"; else bad "проверка Xray: $(tail -1 /tmp/cp09-after.txt)"; fi
  rm -rf "$t"
fi
if [ $active -eq 1 ]; then
  n=$(journalctl -u "$UNIT" --since "40 seconds ago" --no-pager 2>/dev/null | grep -ci 'failed to\|panic\|fatal')
  [ "${n:-0}" -le 3 ] && ok "ошибок в журнале $UNIT за последние 40 с: ${n:-0}" || bad "в журнале $UNIT много ошибок за 40 с: $n"
  if [ -n "${BK:-}" ] && [ -f "$BK/listen.before" ]; then
    ss -ltnH 2>/dev/null | awk '{print $4}' | sort > /tmp/cp09-listen.after
    miss=$(comm -23 "$BK/listen.before" /tmp/cp09-listen.after | wc -l)
    [ "$miss" -eq 0 ] && ok "все слушавшиеся до замены порты слушаются снова" || bad "после перезапуска не слушаются $miss порт(ов): $(comm -23 "$BK/listen.before" /tmp/cp09-listen.after | tr '\n' ' ')"
  fi
  # the engine's own sockets: no plain DNS from the process, DoH sessions appear as soon as users generate lookups
  pids=$(systemctl show -p MainPID --value "$UNIT" 2>/dev/null)      # only THIS unit's process (xray-router is also called "xray" and keeps its own, allowed, Yandex resolver socket)
  plain=0; doh=0
  for p in $pids; do
    plain=$((plain + $(ss -unp 2>/dev/null | grep "pid=$p," | grep -cE ':53 ')))
  done
  [ "$plain" -eq 0 ] && ok "у процесса $UNIT (pid ${pids:-?}) нет сокетов открытого DNS (UDP, порт 53)" || bad "у процесса $UNIT есть $plain сокет(ов) UDP/53"
  for i in $(seq 1 20); do
    doh=0; for p in $pids; do doh=$((doh + $(ss -tnp 2>/dev/null | grep "pid=$p," | grep -cE '(1\.1\.1\.1|1\.0\.0\.1|8\.8\.8\.8|8\.8\.4\.4|9\.9\.9\.9|149\.112\.112\.112):443'))); done
    [ "$doh" -gt 0 ] && break; sleep 1
  done
  [ "$doh" -gt 0 ] && ok "у процесса $UNIT открыто DoH-сессий (TCP/443 к резолверам): $doh" || warn "DoH-сессий пока нет (нет клиентского трафика с новыми именами? проверьте позже)"
fi

if [ "${CP09_SKIP_CAPTURE:-0}" = 1 ]; then echo "  (пассивный захват пропущен)"; else
  echo "  пассивный захват 15 с: открытый DNS (UDP/53) с этого узла к 1.1.1.1 / 1.0.0.1 / 8.8.8.8 / 8.8.4.4 / 9.9.9.9 / 149.112.112.112"
  if plain_dns_capture 15; then ok "открытого DNS к публичным резолверам нет"
  else
    if [ "${CP09_OTHER_ENGINE_PENDING:-1}" = 1 ]; then warn "открытый DNS ещё есть: ожидаемо, пока не исправлен второй движок (xray и singbox) либо это сторонний процесс — смотрите после второго шага"; else bad "открытый DNS всё ещё уходит с узла"; fi
  fi
fi
cat <<'TXT'
  Проверка со стороны клиента (вручную): открыть любой зарубежный и любой российский сайт — оба должны открываться; профили «Германия» и «Нидерланды» в Happ — тоже.
TXT
echo
if [ $fail -eq 0 ]; then echo "ИТОГ: CP-09 ($ENGINE) в порядке"; else echo "ИТОГ: есть FAIL, см. выше"; fi
exit $fail
