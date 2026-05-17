# Hiddify Commercial Addon Stack

Коммерческий набор аддонов для [HiddifyPanel](https://github.com/hiddify/HiddifyPanel) версии 12.x.

Проект расширяет HiddifyPanel коммерческой логикой подписок, Telegram-ботом продаж, интеллектуальной маршрутизацией трафика и защитой от шаринга аккаунтов — без изменения исходного кода базовой панели.

---

## Компоненты

| Компонент | Описание |
|---|---|
| **Business addon** | Тарифные планы, пользовательские подписки, хуки биллинга |
| **Telegram bot** | Самообслуживание, автоматическая пробная выдача, инструкции по платформам, интеграция оплаты |
| **Routing addon** | Разделение локального/международного трафика через xray-router SOCKS5; управление внешними нодами (VLESS, Trojan) |
| **Routing health probe** | Проверка доступности upstream каждые 60 секунд; уведомление администратора в Telegram при сбое |
| **Anti-share addon** | Обнаружение шаринга аккаунтов по IP-скорингу; опциональная блокировка через nftables |

---

## Требования

| Требование | Версия / Примечание |
|---|---|
| HiddifyPanel | 12.0.0 |
| ОС | Ubuntu 22.04 LTS или 24.04 LTS |
| Пользователь | root |
| ОЗУ | Минимум 1 ГБ; рекомендуется 2 ГБ swap (см. ниже) |
| База данных | MariaDB должна быть запущена и доступна |
| Сервисы панели | `hiddify-panel` и `hiddify-panel-background-tasks` должны быть активны |

---

## Подготовка перед установкой

### 1. Добавить swap (рекомендуется)

Если на сервере 1 ГБ ОЗУ или меньше, добавьте 2 ГБ swap перед установкой:

```bash
fallocate -l 2G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

### 2. Клонировать репозиторий

```bash
git clone https://github.com/daviddt369/hiddify-billing-router-guard.git
cd hiddify-billing-router-guard
```

---

## Чистая установка (полный стек)

Установка всех трёх аддонов в правильном порядке (business → routing → antishare) одной командой:

```bash
sudo bash release/clean-install-full-stack.sh
```

Каждый этап выполняет preflight-проверки, копирует файлы, применяет миграции БД, перезапускает сервисы панели и проверяет результат с помощью smoke-тестов. При любой ошибке установщик автоматически откатывается.

---

## Ручные шаги после установки

### Настройка Telegram-бота

1. Получите токен бота у [@BotFather](https://t.me/BotFather).
2. Откройте интерфейс администратора HiddifyPanel и перейдите в раздел **Business → Telegram**.
3. Введите токен бота в поле **Токен Telegram-бота** и сохраните.
4. Отправьте вашему боту команду активации, которая показана на экране:

   ```
   /start admin_<UUID_АДМИНИСТРАТОРА>
   ```

   Точная команда также сохраняется в файл `/opt/hiddify-manager/business-addon-secrets/telegram-owner-activation.txt` на сервере.

### Исправление балансировщика proxy-stats (если установлен hiddify-cli)

Если на сервере установлен `hiddify-cli`, выполните после установки:

```bash
sudo bash release/service-tools/fix-hiddify-cli-balancer.sh
```

---

## Обновление

Для обновления business-слоя после обновления HiddifyPanel:

```bash
sudo bash release/upgrade-installer/upgrade-business-layer.sh
```

---

## Откат

Полный откат всех трёх аддонов в обратном порядке:

```bash
sudo bash release/rollback-all.sh
```

Откат по отдельности:

```bash
sudo bash release/antishare-installer/rollback-antishare.sh
sudo bash release/routing-installer/rollback-routing.sh
sudo bash release/business-installer/rollback-business.sh
```

---

## Smoke-тесты

Запускайте после любой установки или обновления:

```bash
sudo bash release/business-installer/smoke-business.sh
sudo bash release/routing-installer/smoke-routing.sh
sudo bash release/antishare-installer/smoke-antishare.sh
```

---

## Ожидаемые предупреждения (не ошибки)

- **"Telegram bot token is not configured"** — появляется в логах при первом запуске до настройки токена в панели администратора. Это штатное поведение.
- **"xray-router inactive"** / **"upstream not reachable"** — появляется до тех пор, пока не настроена хотя бы одна внешняя нода в разделе Business → Routing. Это штатное поведение.

---

## Статус релиза

**v1.0.0-rc1** — Release Candidate. Сборка прошла функциональное тестирование, но не прошла ревью на production-готовность. Не разворачивайте на production без собственного ревью и тестирования.

---

## Отказ от ответственности

Программное обеспечение предоставляется «как есть», без каких-либо гарантий. Используйте на свой страх и риск. Авторы не несут ответственности за потерю данных, перебои в работе сервисов или любой иной ущерб, возникший в результате использования данного программного обеспечения.
