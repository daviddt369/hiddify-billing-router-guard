#!/bin/bash
# CP-01 · ручной откат (RU, от root): возвращает прежние права и владельцев из записи apply.sh.
# usage: rollback.sh [каталог записи; по умолчанию последний из /run/cp01.last-backup]
set -eu
BK=${1:-$(cat /run/cp01.last-backup 2>/dev/null || true)}
[ -n "$BK" ] && [ -f "$BK/dirs.before" ] || { echo "нужен каталог записи с dirs.before (его печатает apply.sh)"; exit 2; }
while read -r mode own path; do chown "$own" "$path" && chmod "$mode" "$path" && echo "  $path -> $mode $own"; done < "$BK/dirs.before"
if [ -f "$BK/file.before" ]; then while read -r mode own path; do chown "$own" "$path" && chmod "$mode" "$path" && echo "  $path -> $mode $own"; done < "$BK/file.before"; fi
n=0; while read -r mode path; do chmod "$mode" "$path" && n=$((n+1)); done < "$BK/backup-files.modes.before"
echo "  файлов бэкапа возвращено: $n"
echo "готово: права как до CP-01"
