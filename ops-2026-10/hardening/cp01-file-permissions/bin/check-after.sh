#!/bin/bash
# CP-01 · проверка ПОСЛЕ: пользователь панели сохранил доступ, посторонние потеряли, службы живы, конфиг router не изменился.
# Код возврата 0 = всё как задумано. Используется и самим apply.sh (при FAIL он откатывает изменения).
set -u
BACKUP_DIR=${BACKUP_DIR:-/opt/hiddify-manager/hiddify-panel/backup}
ROUTER_DIR=${ROUTER_DIR:-/etc/xray-router}
DOCKER_ENV=${DOCKER_ENV:-/opt/hiddify-manager/docker.env}
PANEL_USER=${PANEL_USER:-hiddify-panel}
FOREIGN_USERS=${FOREIGN_USERS:-nginx zabbix tgproxy avitobrowser hiddify-cli redis nobody}
PANEL_URL=${PANEL_URL:-http://127.0.0.1:9000/}
SHA_BEFORE_FILE=${SHA_BEFORE_FILE:-}
fail=0
ok()  { echo "  OK    $*"; }
bad() { echo "  FAIL  $*"; fail=1; }

echo "1. права"
for p in "$BACKUP_DIR" "$ROUTER_DIR" "$DOCKER_ENV"; do [ -e "$p" ] && echo "  $(stat -c '%a %U:%G' "$p")  $p"; done

echo "2. пользователь панели ($PANEL_USER) ДОЛЖЕН сохранить доступ"
if runuser -u "$PANEL_USER" -- head -c 1 "$ROUTER_DIR/config.json" >/dev/null 2>&1; then ok "читает $ROUTER_DIR/config.json"; else bad "не читает $ROUTER_DIR/config.json"; fi
if runuser -u "$PANEL_USER" -- ls "$BACKUP_DIR" >/dev/null 2>&1; then ok "видит каталог бэкапов"; else bad "не видит каталог бэкапов"; fi
t="$BACKUP_DIR/.cp01-write-test"
if runuser -u "$PANEL_USER" -- bash -c "echo x > '$t' && cat '$t' >/dev/null && rm -f '$t'" 2>/dev/null; then ok "создаёт и удаляет файл в каталоге бэкапов (как делает задача бэкапа)"; else bad "не может писать в каталог бэкапов"; rm -f "$t"; fi
newest=$(ls -1 "$BACKUP_DIR" 2>/dev/null | tail -1)
if [ -n "$newest" ] && runuser -u "$PANEL_USER" -- head -c 1 "$BACKUP_DIR/$newest" >/dev/null 2>&1; then ok "читает свежий бэкап ($newest)"; elif [ -z "$newest" ]; then echo "  (бэкапов нет)"; else bad "не читает $newest"; fi

echo "3. посторонние пользователи НЕ должны читать"
for u in $FOREIGN_USERS; do
  id "$u" >/dev/null 2>&1 || continue
  leak=""
  runuser -u "$u" -- test -r "$ROUTER_DIR/config.json" 2>/dev/null && leak="$leak config.json"
  runuser -u "$u" -- ls "$BACKUP_DIR" >/dev/null 2>&1 && leak="$leak backup-dir"
  [ -e "$DOCKER_ENV" ] && runuser -u "$u" -- test -r "$DOCKER_ENV" 2>/dev/null && leak="$leak docker.env"
  if [ -z "$leak" ]; then ok "$u: доступа нет"; else bad "$u: ещё читает:$leak"; fi
done

echo "4. службы и панель"
for u in hiddify-panel hiddify-panel-background-tasks xray-router hiddify-xray hiddify-haproxy; do
  systemctl cat "$u" >/dev/null 2>&1 || continue
  if [ "$(systemctl is-active "$u")" = active ]; then ok "$u active"; else bad "$u не active"; fi
done
code=$(curl -s -o /dev/null -m 8 -w '%{http_code}' "$PANEL_URL" 2>/dev/null)
case "$code" in 000|"") bad "панель не отвечает на $PANEL_URL";; *) ok "панель отвечает (HTTP $code)";; esac

if [ -n "$SHA_BEFORE_FILE" ] && [ -f "$SHA_BEFORE_FILE" ]; then
  echo "5. содержимое не менялось"
  if sha256sum -c --quiet "$SHA_BEFORE_FILE" 2>/dev/null; then ok "конфиг router и docker.env побайтово те же"; else bad "содержимое отличается от записанного до применения"; fi
fi
echo
if [ $fail -eq 0 ]; then echo "ИТОГ: CP-01 в порядке"; else echo "ИТОГ: есть FAIL, см. выше"; fi
exit $fail
