#!/usr/bin/env bash
# rollback-all.sh — откат всех трёх аддонов к чистой HiddifyPanel 12.0.x
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

die()  { echo "[rollback-all][ERROR] $*" >&2; exit 1; }
step() { echo ""; echo "[rollback-all][STEP] $*"; }

[[ $EUID -eq 0 ]] || die "Запустите от root: sudo bash rollback-all.sh"

step "Откат antishare"
if [[ -f "$SCRIPT_DIR/antishare-installer/rollback-antishare.sh" ]] && \
   [[ -f /opt/hiddify-manager/anti-share-addon.manifest ]]; then
    bash "$SCRIPT_DIR/antishare-installer/rollback-antishare.sh" \
        || echo "[WARN] antishare rollback завершился с ошибкой (продолжаем)"
else
    echo "antishare не установлен, пропускаем"
fi

step "Откат routing"
if [[ -f "$SCRIPT_DIR/routing-installer/rollback-routing.sh" ]] && \
   [[ -f /opt/hiddify-manager/routing-addon.manifest ]]; then
    bash "$SCRIPT_DIR/routing-installer/rollback-routing.sh" \
        || echo "[WARN] routing rollback завершился с ошибкой (продолжаем)"
else
    echo "routing не установлен, пропускаем"
fi

step "Откат business"
if [[ -f "$SCRIPT_DIR/business-installer/rollback-business.sh" ]] && \
   [[ -f /opt/hiddify-manager/business-addon.manifest ]]; then
    bash "$SCRIPT_DIR/business-installer/rollback-business.sh" \
        || echo "[WARN] business rollback завершился с ошибкой (продолжаем)"
else
    echo "business не установлен, пропускаем"
fi

step "Перезапуск панели"
systemctl restart hiddify-panel hiddify-panel-background-tasks 2>/dev/null || true
sleep 10
systemctl is-active hiddify-panel && echo "hiddify-panel: active" || echo "[WARN] hiddify-panel не активен"

echo ""
echo "rollback-all завершён. Панель вернулась к чистому состоянию 12.0.x."
echo "Манифесты аддонов удалены — можно запускать clean-install-full-stack.sh заново."
