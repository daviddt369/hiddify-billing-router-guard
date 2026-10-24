#!/bin/bash
# CP-10 · AUTO director · проверка ПОСЛЕ. RU, от root. Код возврата 0 = всё как задумано.
# Переменные: CP10_SKIP_CANARY=1 (стенд), CP10_SERVICES, CP10_EXPECT (пары «ИМЯ=IP», по умолчанию боевые DE=203.0.113.20 NL=203.0.113.30)
set -u
. "$(cd "$(dirname "$0")" && pwd)/lib.sh"
EXPECT=${CP10_EXPECT:-"DE=203.0.113.20 NL=203.0.113.30"}

echo "1. файлы и службы"
[ "$(sha16 $LAYER)" = "$NEW_LAYER" ] && ok "слой = CP-10 ($NEW_LAYER)" || bad "файл слоя не тот"
[ "$(sha16 $MLOC)" = "$NEW_MLOC" ] && ok "multiloc.py = CP-10 ($NEW_MLOC)" || bad "multiloc.py не тот"
for u in ${CP10_SERVICES:-xray-router hiddify-panel hiddify-xray hiddify-haproxy hiddify-singbox auto-director}; do [ "$(systemctl is-active $u)" = active ] && ok "$u active" || bad "$u не active"; done

echo "2. живой конфиг router"
python3 /dev/stdin <<'PY' || fail=1
import json, os, sys
c = json.load(open('/etc/xray-router/config.json'))
bad = 0
bals = {b['tag']: b for b in c['routing']['balancers']}
def chk(cond, text):
    global bad
    print(('  OK    ' if cond else '  FAIL  ') + text)
    bad += 0 if cond else 1
d = bals.get('auto-director')
chk(bool(d) and d.get('fallbackTag') == 'block' and len(d['selector']) == 2, 'балансировщик auto-director: два члена, fallbackTag=block ' + (str(d['selector']) if d else ''))
for t in ('upstream-balancer',) + tuple(k for k in bals if k.startswith('balancer-s')):
    chk(bals[t].get('fallbackTag') == 'block', f'{t}: fallbackTag=block')
last = c['routing']['rules'][-1]
chk(last.get('balancerTag') == 'auto-director', 'общее правило «всё остальное» ведёт в auto-director')
chk(c.get('api', {}).get('services') == ['RoutingService'], 'API: только RoutingService')
api_in = [i for i in c['inbounds'] if i.get('tag') == 'api']
chk(bool(api_in) and str(api_in[0].get('listen', '')).startswith('/run/') and 'port' not in api_in[0], 'API слушает только unix-сокет')
loops = [o['tag'] for o in c['outbounds'] if o.get('protocol') == 'loopback']
chk(len(loops) == 2, f'loopback-выходов две штуки: {loops}')
chk(c['observatory'].get('probeInterval') == '10s', f"интервал проб router = {c['observatory'].get('probeInterval')}")
chk(all(t in c['observatory']['subjectSelector'] for t in loops), 'observatory проверяет loopback-выходы')
sys.exit(1 if bad else 0)
PY
[ -S $SOCK ] && [ "$(stat -c %a $SOCK)" = 600 ] && ok "сокет API $SOCK: права 600" || bad "сокет API нет или права не 600"
st=$(cd /opt/hiddify-manager/hiddify-panel && $CTL status 2>&1 | grep '^router:')
case "$st" in *"in sync"*) ok "$st" ;; *) bad "${st:-статус недоступен}" ;; esac

echo "3. контроллер"
python3 /dev/stdin <<'PY' || fail=1
import json, sys, time
s = json.load(open('/var/lib/auto-director/status.json'))
age = time.time() - s['ts']
print(f"  статус: страна={s['cur']} применено={s['applied']} замеры={s['measured_ms']} сглаженные={s['ewma_ms']} возраст={age:.0f} с причина={s['reason']!r}")
sys.exit(0 if age < 40 and s['applied'] and s['cur'] != 'NONE' else 1)
PY
[ $? -eq 0 ] && ok "контроллер жив, выбор применён в router" || bad "статус контроллера плохой"
o=$(/usr/bin/xray api bi --server=unix://$SOCK auto-director 2>&1 | tr '\n' ' ' | cut -c1-120)
echo "  router: $o"
case "$o" in *"Selecting Override:     1"*) ok "в router задано переопределение AUTO" ;; *) bad "в router нет переопределения AUTO" ;; esac

if [ "${CP10_SKIP_CANARY:-0}" = 1 ]; then echo "4. канарейка пропущена (CP10_SKIP_CANARY=1)"; else
echo "4. канарейка: страны и AUTO"
python3 - <<PY
import json
c = json.load(open('/etc/auto-director/config.json'))
open('/tmp/cp10-countries.txt', 'w').write('\n'.join(f"{x['name']} {x['entrance']}" for x in c['countries']))
PY
for line in $(cat /tmp/cp10-countries.txt | tr ' ' ':'); do
  name=${line%%:*}; port=${line##*:}
  want=$(echo " $EXPECT " | tr ' ' '\n' | grep "^$name=" | cut -d= -f2)
  ip=$(curl -s -m 12 --socks5-hostname 127.0.0.1:$port https://ipv4.icanhazip.com | tr -d '[:space:]')
  [ -n "$want" ] && [ "$ip" = "$want" ] && ok "$name (:$port) -> $ip" || bad "$name (:$port) -> '${ip:-нет ответа}' (ожидалось ${want:-?})"
done
ip=$(curl -s -m 12 --socks5-hostname 127.0.0.1:20808 https://ipv4.icanhazip.com | tr -d '[:space:]')
case " $EXPECT " in *"=$ip "*) ok "AUTO (:20808) -> $ip (одна из двух стран)" ;; *) bad "AUTO (:20808) -> '${ip:-нет ответа}'" ;; esac
echo "5. RU Direct не изменился"
for p in 20808 20811 20813; do
  for h in ya.ru vk.com mail.ru; do
    code=$(curl -s -o /dev/null -m 12 -w '%{http_code}' --socks5-hostname 127.0.0.1:$p https://$h/)
    case "$code" in 2*|3*) ok "RU Direct :$p $h -> $code" ;; *) bad "RU Direct :$p $h -> '${code:-нет ответа}'" ;; esac
  done
done
fi
echo "  ошибок router в журнале за 5 минут: $(journalctl -u xray-router --since '5 minutes ago' --no-pager 2>/dev/null | grep -ci 'failed to\|panic\|fatal')"
echo
if [ $fail -eq 0 ]; then echo "ИТОГ: CP-10 применён, проверки пройдены"; else echo "ИТОГ: есть FAIL, см. выше; при сомнении откат: rollback.sh"; fi
exit $fail
