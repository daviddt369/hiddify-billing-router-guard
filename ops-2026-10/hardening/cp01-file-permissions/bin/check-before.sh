#!/bin/bash
# CP-01 · проверка ДО (только чтение, содержимое файлов не читается). Запускать на RU-ноде от root.
# Переменные (по умолчанию боевые): BACKUP_DIR ROUTER_DIR DOCKER_ENV PANEL_USER FOREIGN_USERS
set -u
BACKUP_DIR=${BACKUP_DIR:-/opt/hiddify-manager/hiddify-panel/backup}
ROUTER_DIR=${ROUTER_DIR:-/etc/xray-router}
DOCKER_ENV=${DOCKER_ENV:-/opt/hiddify-manager/docker.env}
PANEL_USER=${PANEL_USER:-hiddify-panel}
FOREIGN_USERS=${FOREIGN_USERS:-nginx zabbix tgproxy avitobrowser hiddify-cli redis nobody}
fail=0
ok()   { echo "  OK    $*"; }
bad()  { echo "  FAIL  $*"; fail=1; }
warn() { echo "  WARN  $*"; }

echo "1. объекты и текущие права"
for p in "$BACKUP_DIR" "$ROUTER_DIR" "$ROUTER_DIR/config.json" "$DOCKER_ENV"; do
  if [ -e "$p" ]; then echo "  $(stat -c '%a %U:%G' "$p")  $p"; else warn "нет $p (пропускается)"; fi
done
[ -d "$BACKUP_DIR" ] && echo "  файлов в каталоге бэкапов: $(find "$BACKUP_DIR" -maxdepth 1 -type f | wc -l), из них читаемых всеми: $(find "$BACKUP_DIR" -maxdepth 1 -type f -perm -004 | wc -l)"

echo "2. пользователь панели существует и состоит в нужной группе"
if id "$PANEL_USER" >/dev/null 2>&1; then ok "$(id "$PANEL_USER")"; else bad "нет пользователя $PANEL_USER"; fi
if [ -d "$BACKUP_DIR" ] && [ "$(stat -c '%U' "$BACKUP_DIR")" = "$PANEL_USER" ]; then ok "каталог бэкапов принадлежит $PANEL_USER (после chmod 700 он сохранит доступ)"; else bad "каталог бэкапов принадлежит не $PANEL_USER"; fi

echo "3. кто СЕЙЧАС может читать (проверка прав доступа от имени пользователя; содержимое не читается)"
for u in $PANEL_USER $FOREIGN_USERS; do
  id "$u" >/dev/null 2>&1 || continue
  r1=нет; r2=нет; r3=нет
  runuser -u "$u" -- test -r "$ROUTER_DIR/config.json" 2>/dev/null && r1=ДА
  runuser -u "$u" -- test -x "$BACKUP_DIR" 2>/dev/null && runuser -u "$u" -- test -r "$BACKUP_DIR" 2>/dev/null && r2=ДА
  [ -e "$DOCKER_ENV" ] && runuser -u "$u" -- test -r "$DOCKER_ENV" 2>/dev/null && r3=ДА
  printf "  %-14s конфиг router: %-3s  каталог бэкапов: %-3s  docker.env: %-3s\n" "$u" "$r1" "$r2" "$r3"
done

echo "4. службы"
for u in hiddify-panel hiddify-panel-background-tasks xray-router; do
  systemctl cat "$u" >/dev/null 2>&1 || continue
  if [ "$(systemctl is-active "$u")" = active ]; then ok "$u active"; else bad "$u не active"; fi
done
echo
if [ $fail -eq 0 ]; then echo "ИТОГ: можно применять"; else echo "ИТОГ: НЕ применять, см. FAIL выше"; fi
exit $fail
