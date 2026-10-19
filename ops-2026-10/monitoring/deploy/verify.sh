#!/bin/bash
# CP-08 · проверка ПОСЛЕ установки (только чтение). Запускать на узле от root.
echo "== таймеры"; systemctl list-timers 'vpn-watch*' --no-pager | head -6
echo "== последние запуски"; journalctl -u vpn-watch.service -u vpn-watch-nodes.service -n 6 --no-pager 2>/dev/null | cut -c1-170
echo "== результаты проверок"; python3 /opt/vpn-watch/vpn_watch.py status
echo "== файлы и права"; stat -c '%a %U:%G %n' /etc/vpn-watch /etc/vpn-watch/config.json /etc/vpn-watch/telegram.env /var/lib/vpn-watch 2>&1
echo "== очередь недоставленных сообщений"; python3 - <<'PY'
import json
try:
    s = json.load(open('/var/lib/vpn-watch/state.json')); print(len(s.get('pending', [])), "в очереди")
except Exception as e:
    print("состояния ещё нет:", type(e).__name__)
PY
