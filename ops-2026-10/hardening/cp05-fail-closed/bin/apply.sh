#!/bin/bash
# CP-05 · применение на RU-ноде (от root). Делает ТОЛЬКО замену одного файла штатным delta-deploy проекта
# и ОДНО применение слоя (один перезапуск xray-router). Всё остальное не трогает.
# usage: apply.sh [каталог пакета; по умолчанию /root/cp05-pkg]
set -euo pipefail
PKG="${1:-/root/cp05-pkg}"
TS=$(date +%Y%m%d-%H%M%S)
BK=/root/.claude-backups/cp05-$TS
PYV=/opt/hiddify-manager/.venv313/bin/python
CTL="$PYV /opt/hiddify-manager/scripts/multiloc_ctl.py"
[ -f "$PKG/MANIFEST.json" ] || { echo "нет $PKG/MANIFEST.json"; exit 2; }

echo "== 0. проверка до"
bash "$PKG/scripts/check-before.sh"

echo "== 1. сухой прогон delta-deploy (ничего не меняет)"
python3 "$PKG/multiloc_delta_deploy.py" --new "$PKG" | tee /tmp/cp05-dry.txt
if [ "$(grep -c '^   update ' /tmp/cp05-dry.txt)" != 1 ]; then
  echo "ожидался ровно один файл со статусом update (или файл уже обновлён): остановка"; exit 3
fi

install -d -m 700 /root/.claude-backups "$BK"
cp -p /etc/xray-router/config.json "$BK/router-config.before.json"        # копия рабочего конфига router (в ней ключи: каталог 700)
echo "== 2. delta-deploy --apply (бэкап в $BK; панель будет перезапущена один раз)"
python3 "$PKG/multiloc_delta_deploy.py" --new "$PKG" --apply --backup "$BK"

echo "== 3. применение слоя: один перезапуск xray-router (внутри patch_config: инварианты, xray -test, проверка входов, автооткат)"
cd /opt/hiddify-manager/hiddify-panel && $CTL apply

echo "== 4. проверка после"
if bash "$PKG/scripts/check-after.sh" "$BK"; then rc=0; else rc=$?; fi
echo "Откат (если нужен): bash $PKG/scripts/rollback.sh $BK"
exit $rc
