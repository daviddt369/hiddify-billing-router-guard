#!/bin/bash
# CP-01 · ограничение доступа к секретам на RU-ноде (от root). Меняет ТОЛЬКО права/владельца трёх объектов; содержимое и службы не трогает.
# При любой ошибке или FAIL в проверке «после» права автоматически возвращаются.
# Переменные (по умолчанию боевые): BACKUP_DIR ROUTER_DIR DOCKER_ENV PANEL_USER FOREIGN_USERS PANEL_URL
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
BACKUP_DIR=${BACKUP_DIR:-/opt/hiddify-manager/hiddify-panel/backup}
ROUTER_DIR=${ROUTER_DIR:-/etc/xray-router}
DOCKER_ENV=${DOCKER_ENV:-/opt/hiddify-manager/docker.env}
PANEL_USER=${PANEL_USER:-hiddify-panel}
export BACKUP_DIR ROUTER_DIR DOCKER_ENV PANEL_USER
BK=/root/.claude-backups/cp01-$(date +%Y%m%d-%H%M%S)

echo "== 0. проверка до"
bash "$HERE/check-before.sh" || { echo "проверка «до» не пройдена: ничего не изменено"; exit 2; }

cur_b=$(stat -c '%a' "$BACKUP_DIR"); cur_r=$(stat -c '%a:%U:%G' "$ROUTER_DIR")
cur_d=600; [ -e "$DOCKER_ENV" ] && cur_d=$(stat -c '%a' "$DOCKER_ENV")
if [ "$cur_b" = 700 ] && [ "$cur_r" = "750:root:$PANEL_USER" ] && [ "$cur_d" = 600 ]; then
  echo "уже применено (700 / 750 root:$PANEL_USER / 600): ничего не делаю, запись прежнего состояния не перезаписывается"; exit 0
fi

echo "== 1. запись текущих прав в $BK"
install -d -m 700 "$BK"
stat -c '%a %U:%G %n' "$BACKUP_DIR" "$ROUTER_DIR" > "$BK/dirs.before"
[ -e "$DOCKER_ENV" ] && stat -c '%a %U:%G %n' "$DOCKER_ENV" > "$BK/file.before"
find "$BACKUP_DIR" -maxdepth 1 -type f -printf '%m %p\n' > "$BK/backup-files.modes.before"
sha256sum "$ROUTER_DIR/config.json" ${DOCKER_ENV:+$( [ -e "$DOCKER_ENV" ] && echo "$DOCKER_ENV")} > "$BK/content.sha256"
echo "$BK" > /run/cp01.last-backup
echo "  сохранено: $(wc -l < "$BK/backup-files.modes.before") файлов бэкапа, 2 каталога, docker.env"

revert() {
  echo "== ОТКАТ из $BK"
  while read -r mode own path; do chown "$own" "$path" && chmod "$mode" "$path"; done < "$BK/dirs.before"
  [ -f "$BK/file.before" ] && while read -r mode own path; do chown "$own" "$path" && chmod "$mode" "$path"; done < "$BK/file.before"
  while read -r mode path; do chmod "$mode" "$path"; done < "$BK/backup-files.modes.before"
  echo "  права возвращены"
}

echo "== 2. изменение прав"
set -e
trap 'echo "ошибка на шаге применения"; set +e; revert; exit 1' ERR
chmod 700 "$BACKUP_DIR"                                                      # владелец остаётся прежним (панель)
find "$BACKUP_DIR" -maxdepth 1 -type f -exec chmod 600 {} +                  # то же для самих файлов: защита «вторым рубежом»
chown root:"$PANEL_USER" "$ROUTER_DIR"; chmod 750 "$ROUTER_DIR"              # каталог, а не файл: переживает перезапись config.json при каждом Apply
[ -e "$DOCKER_ENV" ] && chmod 600 "$DOCKER_ENV"
trap - ERR
set +e

echo "== 3. проверка после"
SHA_BEFORE_FILE="$BK/content.sha256" bash "$HERE/check-after.sh"
if [ $? -ne 0 ]; then revert; echo "ИТОГ: проверка «после» не пройдена, права возвращены"; exit 1; fi
echo "Откат вручную: bash $HERE/rollback.sh $BK"
