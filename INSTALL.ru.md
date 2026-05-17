# Руководство по установке

Это руководство описывает чистую установку Hiddify Commercial Addon Stack поверх HiddifyPanel 12.0.0.

---

## Требования

| Требование | Версия / Примечание |
|---|---|
| HiddifyPanel | **строго 12.0.0** |
| ОС | Ubuntu 22.04 LTS или 24.04 LTS |
| Пользователь | root |
| ОЗУ | Минимум 1 ГБ; настоятельно рекомендуется 2 ГБ swap |
| База данных | MariaDB должна быть запущена и доступна |
| Сервисы панели | `hiddify-panel` и `hiddify-panel-background-tasks` должны быть активны |

---

## Шаг 0 — Установка HiddifyPanel 12.0.0

Этот стек аддонов требует **строго версии 12.0.0**. Другие версии не поддерживаются.

```bash
sudo apt update && sudo apt upgrade -y
bash <(curl https://raw.githubusercontent.com/hiddify/Hiddify-Manager/refs/tags/v12.0.0/common/download.sh) "v12.0.0"
```

Дождитесь полного запуска панели перед продолжением.

**Завершите мастер начальной настройки панели** (аккаунт администратора, домен, настройки прокси) перед продолжением. Установщик аддонов требует, чтобы панель была полностью настроена, а сервисы `hiddify-panel` и `hiddify-panel-background-tasks` были активны.

---

## Шаг 1 — Добавить swap (рекомендуется)

На серверах с 1 ГБ ОЗУ нагрузка при установке может вызывать медленный перезапуск панели. Добавьте swap заранее:

```bash
fallocate -l 2G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

Проверка:

```bash
free -h
```

---

## Шаг 2 — Клонировать репозиторий

```bash
git clone https://github.com/daviddt369/hiddify-billing-router-guard.git
cd hiddify-billing-router-guard
```

---

## Шаг 3 — Запустить установщик

Установщик устанавливает все три аддона в правильном порядке (business → routing → antishare):

```bash
sudo bash release/clean-install-full-stack.sh
```

Ожидаемое время: 10–15 минут на типичном VPS.

Каждый этап:
1. Выполняет preflight-проверки (сервисы, подключение к БД, целостность payload)
2. Создаёт резервную копию существующих файлов перед заменой
3. Копирует файлы аддона в runtime панели
4. Применяет миграции БД
5. Перезапускает сервисы панели
6. Запускает smoke-тесты
7. Автоматически откатывается при любой ошибке

---

## Шаг 4 — Настройка Telegram-бота

После установки Telegram-бот установлен, но неактивен до указания токена.

1. Создайте бота через [@BotFather](https://t.me/BotFather) и скопируйте токен.
2. Откройте интерфейс администратора → **Business → Telegram**.
3. Введите токен бота и сохраните.
4. Активируйте доступ администратора — отправьте боту следующую команду:

   ```
   /start admin_<UUID_АДМИНИСТРАТОРА>
   ```

   Точная команда активации для вашего сервера сохранена в:

   ```
   /opt/hiddify-manager/business-addon-secrets/telegram-owner-activation.txt
   ```

---

## Шаг 5 — Настройка upstream-ноды для роутинга (опционально)

Если вы хотите включить маршрутизацию трафика через внешнюю relay-ноду:

1. Откройте интерфейс администратора → **Business → Routing**.
2. Добавьте upstream-ноду (формат VLESS, Trojan или WireGuard).
3. Включите роутинг в том же разделе и сохраните.
4. **Примените конфигурацию** — обязательный шаг для активации изменений в работающем ядре Xray/Sing-box:

   ```bash
   sudo bash /opt/hiddify-manager/apply_configs.sh
   ```

   Без этого шага настройки роутинга сохраняются в базе данных, но не активны в прокси-ядре.

---

## Шаг 6 — Исправление балансировщика proxy-stats (если установлен hiddify-cli)

Если на сервере установлен `hiddify-cli`:

```bash
sudo bash release/service-tools/fix-hiddify-cli-balancer.sh
```

---

## Проверка установки

Запустите smoke-тесты для подтверждения работоспособности:

```bash
sudo bash release/business-installer/smoke-business.sh
sudo bash release/routing-installer/smoke-routing.sh
sudo bash release/antishare-installer/smoke-antishare.sh
```

---

## Ожидаемые предупреждения (не ошибки)

- `"Telegram bot token is not configured"` — появляется в логах при каждом запуске панели до настройки токена в интерфейсе администратора.
- `"xray-router inactive"` — логируется routing health probe до тех пор, пока не настроена хотя бы одна upstream-нода.

---

## Откат

Если необходимо откатить установку:

```bash
sudo bash release/rollback-all.sh
```

Для обновления см. [UPGRADE.ru.md](UPGRADE.ru.md).
