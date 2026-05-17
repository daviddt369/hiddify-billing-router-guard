# Руководство по эксплуатации

Это руководство описывает повседневные операционные задачи для Hiddify Commercial Addon Stack.

---

## Обзор сервисов

| Сервис | Назначение | Управляется |
|---|---|---|
| `hiddify-panel` | Основная панель (Flask/bjoern, порт 9000) | systemd |
| `hiddify-panel-background-tasks` | Celery-воркер (запланированные задачи, напоминания) | systemd |
| `xray-router` | Исходящий прокси routing addon (SOCKS5, порт 20808) | systemd |
| `hiddify-anti-share.timer` | Запуск движка anti-share скоринга | таймер systemd |
| `hiddify-routing-health.timer` | Проверка доступности upstream | таймер systemd |

---

## Проверка работоспособности

### Быстрый статус

```bash
systemctl is-active hiddify-panel hiddify-panel-background-tasks xray-router
systemctl is-active hiddify-anti-share.timer hiddify-routing-health.timer
ss -tlnp | grep -E ':9000|:20808'
```

### Smoke-тесты (полная проверка)

```bash
sudo bash release/business-installer/smoke-business.sh
sudo bash release/routing-installer/smoke-routing.sh
sudo bash release/antishare-installer/smoke-antishare.sh
```

---

## Логи

### Лог приложения панели

```bash
tail -f /opt/hiddify-manager/log/system/hiddify_panel.err.log
```

### Лог фоновых задач

```bash
tail -f /opt/hiddify-manager/log/system/hiddify_panel_background_tasks.err.log
```

### Системный журнал (все сервисы аддонов)

```bash
journalctl -u hiddify-panel -u hiddify-panel-background-tasks -u xray-router -f
```

### Таймер anti-share

```bash
journalctl -u hiddify-anti-share -n 50
```

### Routing health probe

```bash
journalctl -u hiddify-routing-health -n 20
cat /opt/hiddify-manager/routing-lists/probe-status.json
```

### Xray access log (используется anti-share)

```bash
tail -f /opt/hiddify-manager/log/system/xray.access.log
```

---

## Telegram-бот

### Проверка работы бота

```bash
# Проверить регистрацию webhook
curl -s "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/getWebhookInfo" | python3 -m json.tool
```

### Повторная регистрация webhook вручную

В интерфейсе администратора: **Business → Telegram**, измените любую настройку и сохраните. Это запустит повторную регистрацию webhook.

### Ротация webhook-секрета

1. Сгенерируйте новый секрет:

   ```bash
   openssl rand -hex 32
   ```

2. Обновите `/etc/hiddify-panel/panel-secrets.env`:

   ```
   HIDDIFY_TELEGRAM_WEBHOOK_SECRET=<новый_секрет>
   ```

3. Перерегистрируйте webhook из интерфейса администратора.

### Просмотр команды активации администратора

```bash
cat /opt/hiddify-manager/business-addon-secrets/telegram-owner-activation.txt
```

---

## Роутинг

### Просмотр статуса upstream

Интерфейс администратора → **Business → Routing → Upstreams**.

Или проверьте файл статуса probe:

```bash
cat /opt/hiddify-manager/routing-lists/probe-status.json
```

### Перезапуск xray-router после изменения конфигурации

```bash
systemctl restart xray-router
```

### Применение изменений конфигурации роутинга к основному Hiddify xray

```bash
sudo bash /opt/hiddify-manager/apply_configs.sh
```

---

## Anti-share

### Просмотр текущего скоринга

Интерфейс администратора → **Business → Anti-share**.

### Ручной запуск скоринга

```bash
journalctl -u hiddify-anti-share -f &
systemctl start hiddify-anti-share
```

### Включение принудительного применения nftables

После подтверждения корректной работы скоринга:

1. В интерфейсе администратора → Anti-share включите `nft_enabled`.
2. Отключайте `nft_dry_run` только после подтверждения отсутствия ложных срабатываний.

> **Предупреждение:** Включение nft enforcement блокирует IP на уровне файрвола. Тщательно протестируйте перед включением на production-сервере.

---

## База данных

### Проверка версии БД

```bash
mysql hiddifypanel -sN -e 'SELECT value FROM str_config WHERE `key`="db_version" AND child_id=0;'
```

### Проверка манифестов аддонов

```bash
ls /opt/hiddify-manager/*.manifest
cat /opt/hiddify-manager/business-addon.manifest
```

### Ручное резервное копирование БД

```bash
mysqldump hiddifypanel > /tmp/hiddifypanel-backup-$(date +%F).sql
```

---

## Резервное копирование и восстановление

### Расположение pre-install резервных копий

```bash
ls /opt/hiddify-manager/business-installer-backups/
ls /opt/hiddify-manager/routing-installer-backups/
ls /opt/hiddify-manager/antishare-installer-backups/
```

### Откат отдельного аддона

```bash
sudo bash release/antishare-installer/rollback-antishare.sh
sudo bash release/routing-installer/rollback-routing.sh
sudo bash release/business-installer/rollback-business.sh
```

### Полный откат

```bash
sudo bash release/rollback-all.sh
```

---

## Типичные проблемы

### Панель не отвечает на порту 9000

```bash
systemctl status hiddify-panel
tail -30 /opt/hiddify-manager/log/system/hiddify_panel.err.log
```

Типичные причины:
- MariaDB не запущена: `systemctl restart mariadb`
- Ошибка миграции БД: проверьте err.log на traceback
- Недостаток памяти: проверьте `free -h`, добавьте swap при необходимости

### Telegram-бот не отвечает

1. Убедитесь, что токен установлен в интерфейсе администратора → Business → Telegram.
2. Проверьте регистрацию webhook через `getWebhookInfo` (см. выше).
3. Проверьте логи панели на наличие `"Telegram bot token is not configured"`.

### xray-router не запускается

```bash
journalctl -u xray-router -n 30
```

Типичная причина: нет настроенных upstream-нод. Добавьте хотя бы одну upstream в интерфейсе администратора → Business → Routing.

### Таймер anti-share не срабатывает

```bash
systemctl status hiddify-anti-share.timer
systemctl list-timers hiddify-anti-share.timer
```
