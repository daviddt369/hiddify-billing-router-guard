#!/bin/bash
# CP-10 · AUTO director · применение на RU (от root). Порядок: проверка до -> копии -> delta-deploy двух файлов (панель перезапускается один раз) -> контроллер
# (файлы, не запущен) -> commercial_router_probe_interval=10s -> ОДНО применение слоя (один перезапуск xray-router) -> запуск контроллера -> проверка после.
# При любом сбое после начала изменений скрипт сам вызывает rollback.sh.
# usage: apply.sh
set -uo pipefail
. "$(cd "$(dirname "$0")" && pwd)/lib.sh"
TS=$(date +%Y%m%d-%H%M%S)
BK=/root/.claude-backups/cp10-$TS
install -d -m 700 /root/.claude-backups "$BK"
[ -f "$PKG/MANIFEST.json" ] || { echo "нет $PKG/MANIFEST.json"; exit 2; }

echo "== 0. проверка до"
bash "$HERE/check-before.sh" || { echo "проверка «до» не пройдена: ничего не изменено"; exit 2; }

echo "== 1. сухой прогон delta-deploy (ничего не меняет)"
python3 "$PKG/multiloc_delta_deploy.py" --new "$PKG" | tee "$BK/dry.txt"
n=$(grep -c '^   update ' "$BK/dry.txt")
if [ "$n" != 2 ]; then
  if [ "$(sha16 $LAYER)" = "$NEW_LAYER" ] && [ "$(sha16 $MLOC)" = "$NEW_MLOC" ]; then echo "файлы уже новые, пропускаю delta-deploy"; else echo "ожидалось два файла со статусом update: остановка"; exit 3; fi
fi

echo "== 2. копии до изменений -> $BK"
cp -p /etc/xray-router/config.json "$BK/router-config.before.json"
echo "$(hconfig_get commercial_router_probe_interval)" > "$BK/probe_interval.before"
[ -d $CONF_DIR ] && cp -a $CONF_DIR "$BK/auto-director.conf.before"
echo "   интервал проб router до: $(cat $BK/probe_interval.before)"

revert() { echo "== ОТКАТ ($1)"; bash "$HERE/rollback.sh" "$BK"; }

echo "== 3. delta-deploy двух файлов (панель перезапустится один раз)"
if [ "$n" = 2 ]; then python3 "$PKG/multiloc_delta_deploy.py" --new "$PKG" --apply --backup "$BK/delta" || { revert "delta-deploy"; exit 1; }; fi

echo "== 4. контроллер: файлы и конфигурация (служба пока не запущена)"
install -d -m 755 /opt/auto-director
install -m 755 "$PKG/controller/auto_director.py" /opt/auto-director/auto_director.py
install -d -m 700 $CONF_DIR
python3 "$HERE/make_config.py" $CONF_DIR/config.json.new "${CP10_PROBE_URL:-http://cp.cloudflare.com/generate_204}" || { rm -f $CONF_DIR/config.json.new; revert "конфигурация контроллера"; exit 1; }
chmod 600 $CONF_DIR/config.json.new && mv -f $CONF_DIR/config.json.new $CONF_DIR/config.json
install -m 644 "$PKG/controller/auto-director.service" /etc/systemd/system/auto-director.service
systemctl daemon-reload

echo "== 5. интервал проб router -> 10s (штатным сеттером панели)"
echo "   стало: $(hconfig_set commercial_router_probe_interval 10s)"

echo "== 6. применение слоя: один перезапуск xray-router (инварианты, xray -test, проверка входов, автооткат внутри)"
( cd /opt/hiddify-manager/hiddify-panel && $CTL apply ) || { revert "применение слоя"; exit 1; }

echo "== 7. запуск контроллера"
systemctl enable --now auto-director
for i in $(seq 1 60); do
  [ -f /var/lib/auto-director/status.json ] && python3 - <<'PY' && break
import json, sys, time
s = json.load(open('/var/lib/auto-director/status.json'))
sys.exit(0 if time.time() - s['ts'] < 30 and s.get('applied') and s.get('cur') in ([c['name'] for c in json.load(open('/etc/auto-director/config.json'))['countries']] + ['NONE']) else 1)
PY
  sleep 1
done

echo "== 8. проверка после"
if ! bash "$HERE/check-after.sh"; then revert "проверка после"; echo "ИТОГ: проверка «после» не пройдена, прежнее состояние возвращено"; exit 1; fi
echo "Откат вручную: bash $HERE/rollback.sh $BK"
