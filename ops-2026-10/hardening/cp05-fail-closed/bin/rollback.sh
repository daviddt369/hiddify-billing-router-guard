#!/bin/bash
# CP-05 · ручной откат на RU-ноде (от root): возвращает прежний файл слоя и запись установщика,
# снимает fallbackTag="block" с upstream-balancer (прежний слой этого не умеет) и перерисовывает router прежним слоем.
# Для стран fallbackTag пропадает сам (прежний слой строит их заново). Router перезапускается один раз.
# Остальное содержимое рабочего конфига router НЕ трогается (откат безопасен и через несколько дней после применения).
# usage: rollback.sh <каталог бэкапа, который напечатал apply.sh, например /root/.claude-backups/cp05-20261017-120000>
set -uo pipefail
BK="${1:?укажите каталог бэкапа}"
REL=opt/hiddify-manager/.venv313/lib/python3.13/site-packages/hiddifypanel/hutils/multiloc_router.py
L=/$REL
RUN=$(ls -d /opt/hiddify-manager/multiloc-installer-backups/*/ | tail -1)
PYV=/opt/hiddify-manager/.venv313/bin/python
CTL="$PYV /opt/hiddify-manager/scripts/multiloc_ctl.py"
[ -f "$BK/$REL" ] && [ -f "$BK/STATE.json.before" ] || { echo "в $BK нет файла слоя или STATE.json.before"; exit 2; }

echo "== 1. файл слоя и запись установщика возвращаются из $BK"
cp -p "$L" "$BK/multiloc_router.py.cp05-new"                       # копия того, что откатываем
cp -p /etc/xray-router/config.json "$BK/router-config.before-rollback.json"
cp -p "$BK/$REL" "$L"
cp -p "$BK/STATE.json.before" "${RUN}STATE.json"
rm -f "$(dirname "$L")"/__pycache__/multiloc_router.*.pyc
systemctl restart hiddify-panel
for i in $(seq 1 60); do curl -s -o /dev/null -m 2 http://127.0.0.1:9000/ && break; sleep 1; done
echo "   файл слоя: $(sha256sum "$L" | cut -c1-16) (эталон до CP-05: 0ec2d27b2fe7315d)"

echo "== 2. fallbackTag снимается с upstream-balancer (атомарно, владелец и права сохраняются)"
python3 - <<'PY'
import json, os, tempfile
p = '/etc/xray-router/config.json'
c = json.load(open(p))
n = 0
for b in c['routing'].get('balancers', []):
    if b.get('tag') == 'upstream-balancer' and b.get('fallbackTag') == 'block':
        del b['fallbackTag']
        n += 1
if n:
    st = os.stat(p)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), prefix='.config.json.')
    with os.fdopen(fd, 'w') as f:
        json.dump(c, f, indent=2, ensure_ascii=False)
        f.write('\n')
    os.chmod(tmp, st.st_mode & 0o7777)
    os.chown(tmp, st.st_uid, st.st_gid)
    os.replace(tmp, p)
print('   upstream-balancer: fallbackTag снят' if n else '   upstream-balancer: fallbackTag уже отсутствует')
PY

echo "== 3. прежний слой перерисовывает router (внутри: инварианты, xray -test, проверка входов, автооткат)"
out=$(cd /opt/hiddify-manager/hiddify-panel && $CTL apply 2>&1); rc=$?
echo "$out" | tail -4
if [ $rc -ne 0 ] || echo "$out" | grep -q '"changed": false'; then
  echo "   слой не перезапустил router (rc=$rc): перезапускаю xray-router сам, чтобы в памяти не осталась прежняя конфигурация"
  systemctl restart xray-router
  for i in $(seq 1 30); do (exec 3<>/dev/tcp/127.0.0.1/20808) 2>/dev/null && break; sleep 1; done
fi

echo "== 4. итог"
python3 - <<'PY'
import json
c = json.load(open('/etc/xray-router/config.json'))
print('   балансировщики после отката:', [(b['tag'], b.get('fallbackTag')) for b in c['routing'].get('balancers', [])])
PY
for u in xray-router hiddify-panel; do echo "   $u: $(systemctl is-active $u)"; done
for p in 20808 20811 20813; do
  if (exec 3<>/dev/tcp/127.0.0.1/$p) 2>/dev/null; then echo "   вход $p слушает"; else echo "   ВХОД $p НЕ СЛУШАЕТ"; fi
done
