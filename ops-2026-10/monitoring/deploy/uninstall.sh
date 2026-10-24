#!/bin/bash
# CP-08 · удаление мониторинга vpn-watch с узла (от root). Остальное на сервере не затрагивается.
# usage: uninstall.sh [--purge]    --purge дополнительно удаляет /etc/vpn-watch (конфиг и telegram.env) и /var/lib/vpn-watch (состояние)
set -u
systemctl disable --now vpn-watch.timer vpn-watch-nodes.timer 2>/dev/null
systemctl stop vpn-watch.service vpn-watch-nodes.service 2>/dev/null
rm -f /etc/systemd/system/vpn-watch.service /etc/systemd/system/vpn-watch.timer /etc/systemd/system/vpn-watch-nodes.service /etc/systemd/system/vpn-watch-nodes.timer
systemctl daemon-reload
rm -rf /opt/vpn-watch
if [ "${1:-}" = "--purge" ]; then rm -rf /etc/vpn-watch /var/lib/vpn-watch; echo "удалено полностью (с конфигом и состоянием)"; else echo "удалены программа и таймеры; конфиг /etc/vpn-watch и состояние /var/lib/vpn-watch оставлены (--purge удалит)"; fi
systemctl list-timers 'vpn-watch*' --no-pager 2>/dev/null | head -3
