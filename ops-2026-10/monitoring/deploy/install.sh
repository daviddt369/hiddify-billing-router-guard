#!/bin/bash
# CP-08 · установка мониторинга vpn-watch на ОДНОМ узле (от root). Hiddify, Xray, HAProxy, файрвол не трогает.
# usage: install.sh ru|de|nl [--no-enable] [--telegram-from-panel]
#   ru — RU-нода (канарейки стран, проверка каждого узла, WG-туннели, службы, сертификаты, бэкапы)
#   de — выход .190,  nl — выход .238 (службы, дрейф публичных портов, свой выход, WG-пир, сертификаты, «выход изменился»)
#   --telegram-from-panel (только ru): взять токен бота, чат администратора и адрес прокси Telegram из настроек панели на этом же сервере
#       (тот же бот и чат, что уже используются панелью; значения читаются прямо в файл с правами 600 и нигде не печатаются)
# Файлы: /opt/vpn-watch/vpn_watch.py, /etc/vpn-watch/{config.json,telegram.env}, /var/lib/vpn-watch/ (состояние), юниты vpn-watch*.{service,timer}
set -euo pipefail
ROLE="${1:?укажите роль: ru | de | nl}"; shift || true
ENABLE=1; TGP=0
for a in "$@"; do
  case "$a" in
    --no-enable) ENABLE=0 ;;
    --telegram-from-panel) TGP=1 ;;
    *) echo "неизвестный параметр $a"; exit 2 ;;
  esac
done
case "$ROLE" in ru|de|nl) ;; *) echo "роль должна быть ru, de или nl"; exit 2;; esac
[ $TGP -eq 1 ] && [ "$ROLE" != ru ] && { echo "--telegram-from-panel имеет смысл только на ru"; exit 2; }
HERE=$(cd "$(dirname "$0")" && pwd)
SRC="$(cd "$HERE/.." && pwd)/src/vpn_watch.py"
[ -f "$SRC" ] || SRC="$HERE/vpn_watch.py"
[ -f "$SRC" ] || { echo "не найден vpn_watch.py"; exit 2; }
TS=$(date +%Y%m%d-%H%M%S)
BK=/root/.claude-backups/vpn-watch-$TS

command -v python3 >/dev/null && command -v curl >/dev/null && command -v openssl >/dev/null || { echo "нужны python3, curl, openssl"; exit 2; }
if [ "$ROLE" = ru ]; then command -v xray >/dev/null || { echo "на RU нужен xray (для временного клиента проверки узлов)"; exit 2; }; fi

echo "== 1. резервная копия прежних файлов (если были) → $BK"
install -d -m 700 /root/.claude-backups "$BK"
for f in /opt/vpn-watch/vpn_watch.py /etc/vpn-watch/config.json /etc/vpn-watch/telegram.env /etc/systemd/system/vpn-watch.service /etc/systemd/system/vpn-watch.timer /etc/systemd/system/vpn-watch-nodes.service /etc/systemd/system/vpn-watch-nodes.timer; do
  [ -f "$f" ] && cp -a --parents "$f" "$BK/" || true
done

echo "== 2. файлы"
install -d -m 755 /opt/vpn-watch
install -m 755 "$SRC" /opt/vpn-watch/vpn_watch.py
install -d -m 700 /etc/vpn-watch
if [ -f /etc/vpn-watch/config.json ]; then echo "  /etc/vpn-watch/config.json уже есть — НЕ перезаписываю (новый образец: $HERE/config.$ROLE.json)"
else install -m 600 "${VPN_WATCH_CONFIG_SRC:-$HERE/config.$ROLE.json}" /etc/vpn-watch/config.json; echo "  создан /etc/vpn-watch/config.json (роль $ROLE)"; fi
if [ $TGP -eq 1 ]; then
  q() { mysql -N -B hiddifypanel -e "$1" 2>/dev/null || true; }
  tok=$(q "select value from str_config where \`key\`='telegram_bot_token' and child_id=0")
  chat=$(q "select telegram_id from admin_user where mode='super_admin' and telegram_id is not null order by id limit 1")
  pen=$(q "select value from bool_config where \`key\`='telegram_api_proxy_enable' and child_id=0")
  purl=$(q "select value from str_config where \`key\`='telegram_api_proxy_url' and child_id=0")
  if [ -z "$tok" ] || [ -z "$chat" ]; then echo "  НЕ нашёл токен бота или чат администратора в настройках панели — заполните /etc/vpn-watch/telegram.env вручную"; TGP=0
  else
    ( umask 077
      { echo "# создано из настроек панели $(date +%F): тот же бот и чат, что у Telegram-бота панели (права 600, в репозиторий/журнал/чат не попадает)"
        echo "TG_TOKEN=$tok"; echo "TG_CHAT=$chat"
        if [ "$pen" = 1 ] && [ -n "$purl" ]; then echo "TG_PROXY=$purl"; fi
      } > /etc/vpn-watch/telegram.env.new )
    chmod 600 /etc/vpn-watch/telegram.env.new
    mv -f /etc/vpn-watch/telegram.env.new /etc/vpn-watch/telegram.env
    echo "  /etc/vpn-watch/telegram.env создан из настроек панели (бот, чат$( [ "$pen" = 1 ] && [ -n "$purl" ] && echo ', прокси Telegram'))"
  fi
  unset tok chat pen purl
fi
if [ $TGP -eq 0 ]; then
  if [ -f /etc/vpn-watch/telegram.env ]; then echo "  /etc/vpn-watch/telegram.env уже есть — не трогаю"
  else install -m 600 "$HERE/telegram.env.example" /etc/vpn-watch/telegram.env; echo "  создан ПУСТОЙ /etc/vpn-watch/telegram.env: впишите TG_TOKEN и TG_CHAT (права 600)"; fi
fi
install -m 644 "$HERE/vpn-watch.service" "$HERE/vpn-watch.timer" /etc/systemd/system/
[ "$ROLE" = ru ] && install -m 644 "$HERE/vpn-watch-nodes.service" "$HERE/vpn-watch-nodes.timer" /etc/systemd/system/
systemctl daemon-reload

echo "== 3. эталон публичных портов и отпечатков — ПРОВЕРЬТЕ СПИСОК ГЛАЗАМИ"
python3 /opt/vpn-watch/vpn_watch.py baseline

echo "== 4. пробный запуск (оповещения уйдут, только если заполнен telegram.env)"
python3 /opt/vpn-watch/vpn_watch.py run || true
if grep -q '^TG_TOKEN=.\+' /etc/vpn-watch/telegram.env && grep -q '^TG_CHAT=.\+' /etc/vpn-watch/telegram.env; then
  python3 /opt/vpn-watch/vpn_watch.py test-telegram || echo "  (тестовое сообщение не ушло: проверьте токен/чат/прокси и доступ к api.telegram.org)"
else echo "  telegram.env пуст: оповещения будут копиться в очереди (до 20 сообщений) и уйдут после заполнения"; fi

if [ $ENABLE -eq 1 ]; then
  echo "== 5. включение таймеров"
  systemctl enable --now vpn-watch.timer
  [ "$ROLE" = ru ] && systemctl enable --now vpn-watch-nodes.timer
  systemctl list-timers 'vpn-watch*' --no-pager | head -6
else echo "== 5. таймеры НЕ включены (--no-enable). Включить: systemctl enable --now vpn-watch.timer$( [ "$ROLE" = ru ] && echo ' vpn-watch-nodes.timer')"; fi
echo "Удаление: bash $HERE/uninstall.sh      Состояние: python3 /opt/vpn-watch/vpn_watch.py status"
