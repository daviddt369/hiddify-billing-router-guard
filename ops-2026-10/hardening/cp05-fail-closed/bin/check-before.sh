#!/bin/bash
# CP-05 · проверка ДО применения (только чтение). Запускать на RU-ноде от root. Код возврата 0 = можно применять.
set -u
P=/opt/hiddify-manager/.venv313/lib/python3.13/site-packages/hiddifypanel/hutils/multiloc_router.py
BASE=0ec2d27b2fe7315d     # sha256 (первые 16 символов) файла, который сейчас на проде (v5.1, 2026-10-13 15:28)
NEW=a0d08a6f2e64826b      # sha256 (первые 16 символов) файла после CP-05 (страны + AUTO закрываются при отказе)
fail=0
ok()  { echo "  OK    $*"; }
bad() { echo "  FAIL  $*"; fail=1; }

echo "1. файл слоя маршрутизации"
cur=$(sha256sum "$P" | cut -c1-16)
if [ "$cur" = "$BASE" ]; then ok "multiloc_router.py = эталон v5.1 ($cur)"
elif [ "$cur" = "$NEW" ]; then ok "уже CP-05 ($cur)"
else bad "неизвестная версия $cur: применять нельзя (кто-то менял файл?)"; fi

echo "2. Xray"
v=$(/usr/bin/xray version 2>/dev/null | head -1); echo "  $v"
case "$v" in
  *26.7.28*5ca6f4b*) ok "версия та же, на которой проведены тесты" ;;
  *) bad "версия Xray другая: тесты CP-05 проводились на 26.7.28 (5ca6f4b)" ;;
esac

echo "3. службы"
for u in ${CP05_SERVICES:-xray-router hiddify-panel hiddify-xray hiddify-haproxy hiddify-singbox}; do
  if [ "$(systemctl is-active $u)" = active ]; then ok "$u active"; else bad "$u не active"; fi
done

echo "3b. router согласован с базой панели (иначе применение слоя подхватило бы чужие незавершённые изменения)"
st=$(cd /opt/hiddify-manager/hiddify-panel && /opt/hiddify-manager/.venv313/bin/python /opt/hiddify-manager/scripts/multiloc_ctl.py status 2>&1 | grep '^router:')
case "$st" in
  *"in sync"*) ok "$st" ;;
  *) if [ "$cur" = "$NEW" ]; then echo "  --    $st (файл слоя уже новый — это ожидаемо до применения слоя)"; else bad "${st:-статус недоступен}"; fi ;;
esac

echo "4. живой конфиг router"
python3 - <<'PY' || fail=1
import json, sys
c = json.load(open('/etc/xray-router/config.json'))
outs = [o['tag'] for o in c['outbounds']]
blk = [o for o in c['outbounds'] if o['tag'] == 'block']
print('  первый outbound файла:', outs[0], '(без fallbackTag Xray уводит сюда трафик, когда у балансировщика нет живых узлов)')
print('  outbound block:', ('есть, protocol=' + blk[0]['protocol']) if blk else 'ОТСУТСТВУЕТ')
for b in c['routing'].get('balancers', []):
    print('  балансировщик', b['tag'], 'узлов', len(b['selector']), 'fallbackTag =', b.get('fallbackTag'))
sys.exit(0 if blk and blk[0]['protocol'] == 'blackhole' else 1)
PY

echo "5. входы router"
for p in 20808 20811 20813; do
  if (exec 3<>/dev/tcp/127.0.0.1/$p) 2>/dev/null; then ok "порт $p слушает"; else bad "порт $p не слушает"; fi
done

echo "6. запись установщика (delta-deploy берёт последний завершённый запуск)"
python3 - <<'PY' || fail=1
import glob, hashlib, json
runs = sorted(glob.glob('/opt/hiddify-manager/multiloc-installer-backups/*/STATE.json'))
good = False
if runs:
    p = runs[-1]
    s = json.load(open(p))
    k = [k for k in s.get('installed_sha', {}) if k.endswith('hutils/multiloc_router.py')]
    live = hashlib.sha256(open('/opt/hiddify-manager/.venv313/lib/python3.13/site-packages/hiddifypanel/hutils/multiloc_router.py', 'rb').read()).hexdigest()
    same = bool(k) and s['installed_sha'][k[0]] == live
    print('  запуск', p.split('/')[-2], '| finished =', s.get('finished'), '| rolled_back =', s.get('rolled_back'), '| запись == живому файлу:', same)
    good = bool(s.get('finished')) and not s.get('rolled_back') and same
raise SystemExit(0 if good else 1)
PY

echo
if [ $fail -eq 0 ]; then echo "ИТОГ: можно применять"; else echo "ИТОГ: НЕ применять, см. FAIL выше"; fi
exit $fail
