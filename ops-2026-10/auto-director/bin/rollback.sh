#!/bin/bash
# CP-10 · AUTO director · откат (RU, от root): останавливает контроллер (override снимается), возвращает файлы слоя и запись установщика, конфиг router
# (прежний слой не умеет убирать петли и API директора, поэтому конфиг берётся из копии), интервал проб и панель.
# usage: rollback.sh <каталог копии, который напечатал apply.sh>
set -uo pipefail
. "$(cd "$(dirname "$0")" && pwd)/lib.sh"
BK="${1:?укажите каталог копии, например /root/.claude-backups/cp10-20261017-120000}"
[ -f "$BK/router-config.before.json" ] || { echo "в $BK нет router-config.before.json"; exit 2; }
RUN=$(ls -d /opt/hiddify-manager/multiloc-installer-backups/*/ | tail -1)

echo "== 1. контроллер остановлен и отключён, override снят, конфигурация убрана"
systemctl disable --now auto-director 2>/dev/null
/usr/bin/xray api bo --server=unix://$SOCK -b auto-director -r >/dev/null 2>&1
if [ -f $CONF_DIR/config.json ]; then cp -a $CONF_DIR/config.json "$BK/auto-director.config.rolled-back"; rm -f $CONF_DIR/config.json; fi
rm -f /etc/systemd/system/auto-director.service; systemctl daemon-reload

echo "== 2. файлы слоя и запись установщика возвращаются"
if [ -d "$BK/delta" ] && [ -f "$BK/delta/STATE.json.before" ]; then
  cp -p "$LAYER" "$BK/multiloc_router.py.cp10-new"; cp -p "$MLOC" "$BK/multiloc.py.cp10-new"
  [ -f "$BK/delta/$REL_LAYER" ] && cp -p "$BK/delta/$REL_LAYER" "$LAYER"
  [ -f "$BK/delta/$REL_MLOC" ] && cp -p "$BK/delta/$REL_MLOC" "$MLOC"
  cp -p "$BK/delta/STATE.json.before" "${RUN}STATE.json"
  rm -f "$(dirname "$LAYER")"/__pycache__/multiloc_router.*.pyc "$(dirname "$LAYER")"/__pycache__/multiloc.*.pyc
else
  echo "   (delta-deploy ещё не выполнялся: файлы слоя не менялись)"
fi

echo "== 3. интервал проб router"
old=$(cat "$BK/probe_interval.before" 2>/dev/null)
[ -n "$old" ] && echo "   вернул: $(hconfig_set commercial_router_probe_interval "$old")"

echo "== 4. конфиг router из копии (до применения) и ОДИН перезапуск xray-router"
cp -p /etc/xray-router/config.json "$BK/router-config.before-rollback.json"
cp "$BK/router-config.before.json" /etc/xray-router/config.json.restore && chmod --reference=/etc/xray-router/config.json /etc/xray-router/config.json.restore && chown --reference=/etc/xray-router/config.json /etc/xray-router/config.json.restore && mv -f /etc/xray-router/config.json.restore /etc/xray-router/config.json
systemctl restart xray-router
wait_entrances && echo "   входы router слушают" || echo "   ВХОДЫ ROUTER НЕ СЛУШАЮТ: смотрите systemctl status xray-router"

echo "== 5. панель"
systemctl restart hiddify-panel
for i in $(seq 1 60); do curl -s -o /dev/null -m 2 http://127.0.0.1:9000/ && break; sleep 1; done
sleep 3

echo "== 6. итог"
python3 - <<'PY'
import json
c = json.load(open('/etc/xray-router/config.json'))
print('   балансировщики:', [(b['tag'], b.get('fallbackTag')) for b in c['routing']['balancers']], '| api:', 'api' in c, '| loopback-выходов:', len([o for o in c['outbounds'] if o.get('protocol') == 'loopback']))
PY
echo "   слой: $(sha16 $LAYER) (CP-05: $BASE_LAYER), multiloc.py: $(sha16 $MLOC) (боевой: $BASE_MLOC)"
( cd /opt/hiddify-manager/hiddify-panel && $CTL status 2>&1 | grep '^router:' )
for u in xray-router hiddify-panel; do echo "   $u: $(systemctl is-active $u)"; done
