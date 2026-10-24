#!/bin/bash
# CP-05 · проверка ПОСЛЕ применения. Запускать на RU-ноде от root. Код возврата 0 = всё как задумано.
# usage: check-after.sh [каталог бэкапа от apply.sh — если есть router-config.before.json, конфиг сравнивается с ним]
set -u
BK="${1:-}"
P=/opt/hiddify-manager/.venv313/lib/python3.13/site-packages/hiddifypanel/hutils/multiloc_router.py
NEW=a0d08a6f2e64826b
PYV=/opt/hiddify-manager/.venv313/bin/python
fail=0
ok()  { echo "  OK    $*"; }
bad() { echo "  FAIL  $*"; fail=1; }

echo "1. файл слоя и службы"
if [ "$(sha256sum $P | cut -c1-16)" = "$NEW" ]; then ok "файл слоя = CP-05 ($NEW)"; else bad "файл слоя не тот"; fi
for u in ${CP05_SERVICES:-xray-router hiddify-panel hiddify-xray hiddify-haproxy hiddify-singbox}; do
  if [ "$(systemctl is-active $u)" = active ]; then ok "$u active"; else bad "$u не active"; fi
done
for p in 20808 20811 20813; do
  if (exec 3<>/dev/tcp/127.0.0.1/$p) 2>/dev/null; then ok "вход router $p слушает"; else bad "вход router $p не слушает"; fi
done

echo "2. живой конфиг router: все балансировщики закрываются при отказе"
BK="$BK" python3 - <<'PY' || fail=1
import json, os, sys
c = json.load(open('/etc/xray-router/config.json'))
bad = 0
for b in c['routing'].get('balancers', []):
    got = b.get('fallbackTag')
    st = 'OK  ' if got == 'block' else 'FAIL'
    if got != 'block':
        bad = 1
    print(f"  {st}  {b['tag']}: fallbackTag = {got!r} (нужно 'block'), узлов {len(b['selector'])}")
blk = [o for o in c['outbounds'] if o['tag'] == 'block']
good = bool(blk) and blk[0]['protocol'] == 'blackhole'
print('  ' + ('OK  ' if good else 'FAIL') + '  outbound block: blackhole')
if not good:
    bad = 1
# the only intended difference against the pre-change config: fallbackTag on the balancers
bk = os.environ.get('BK', '')
prev_path = os.path.join(bk, 'router-config.before.json') if bk else ''
if prev_path and os.path.exists(prev_path):
    prev = json.load(open(prev_path))
    for d in (prev, c):
        for b in d['routing'].get('balancers', []):
            b.pop('fallbackTag', None)
    same = json.dumps(prev, sort_keys=True) == json.dumps(c, sort_keys=True)
    print('  ' + ('OK  ' if same else 'FAIL') + '  конфиг router отличается от прежнего ТОЛЬКО значением fallbackTag у балансировщиков')
    if not same:
        bad = 1
        for k in sorted(set(prev) | set(c)):
            if json.dumps(prev.get(k), sort_keys=True) != json.dumps(c.get(k), sort_keys=True):
                print('        отличается раздел:', k)
else:
    print('  --    сравнение с прежним конфигом пропущено (нет router-config.before.json)')
sys.exit(1 if bad else 0)
PY

echo "3. панель согласована с router"
st=$(cd /opt/hiddify-manager/hiddify-panel && $PYV /opt/hiddify-manager/scripts/multiloc_ctl.py status 2>&1 | grep '^router:')
case "$st" in
  *"in sync"*) ok "$st" ;;
  *) bad "${st:-статус недоступен}" ;;
esac

if [ "${CP05_SKIP_CANARY:-0}" = 1 ]; then
  echo "4. канарейка пропущена (CP05_SKIP_CANARY=1: стенд без выхода в интернет)"
else
  echo "4. канарейка через входы router. Ожидаемые выходные IP: DE=203.0.113.20, NL=203.0.113.30"
  for pair in "20811 DE 203.0.113.20" "20813 NL 203.0.113.30" "20808 AUTO any"; do
    set -- $pair
    ip=$(curl -s -m 12 --socks5-hostname 127.0.0.1:$1 https://ipv4.icanhazip.com | tr -d '[:space:]')
    if [ "$3" = any ]; then
      case "$ip" in
        203.0.113.20|203.0.113.30) ok "$2 ($1) -> $ip" ;;
        *) bad "$2 ($1) -> '${ip:-нет ответа}'" ;;
      esac
    else
      if [ "$ip" = "$3" ]; then ok "$2 ($1) -> $ip"; else bad "$2 ($1) -> '${ip:-нет ответа}' (ожидалось $3)"; fi
    fi
  done
  echo "5. российский трафик по-прежнему идёт напрямую через каждый вход (ya.ru должен открываться)"
  for p in 20808 20811 20813; do
    code=$(curl -s -o /dev/null -m 12 -w '%{http_code}' --socks5-hostname 127.0.0.1:$p https://ya.ru/)
    case "$code" in
      2*|3*) ok "RU-direct через вход $p: ya.ru -> HTTP $code" ;;
      *) bad "RU-direct через вход $p: ya.ru -> '${code:-нет ответа}'" ;;
    esac
  done
fi
echo "  ошибок router в журнале за последние 5 минут: $(journalctl -u xray-router --since '5 minutes ago' --no-pager 2>/dev/null | grep -ci 'failed to\|panic\|fatal')"

echo
if [ $fail -eq 0 ]; then echo "ИТОГ: CP-05 применён, проверки пройдены"; else echo "ИТОГ: есть FAIL, см. выше; при сомнении откат: rollback.sh"; fi
exit $fail
