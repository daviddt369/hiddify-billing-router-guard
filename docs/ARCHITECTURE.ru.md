# Архитектура

## Обзор

Hiddify Commercial Addon Stack — это overlay-установка поверх Hiddify Manager. Проект не форкает и не патчит базовый исходный код панели; вместо этого при установке дополнительные Python-модули, шаблоны и конфигурационные файлы копируются в runtime-директорию панели. Базовая панель остаётся обновляемой независимо, а откат восстанавливает оригинальные файлы из резервной копии, созданной перед установкой.

---

## Структура системы

```
┌─────────────────────────────────────────────────────────────────┐
│  Ubuntu 22.04 / 24.04 LTS                                       │
│                                                                  │
│  ┌──────────────────────────────┐                               │
│  │  HiddifyPanel 12.0.0         │  ← базовая система (без изм.) │
│  │  Flask/bjoern · порт 9000    │                               │
│  │  MariaDB · Redis · Celery    │                               │
│  └──────────────┬───────────────┘                               │
│                 │ overlay (файлы аддонов поверх)                 │
│  ┌──────────────▼───────────────────────────────────────┐       │
│  │  Business addon                                       │       │
│  │  ├── тарифные планы + подписки                        │       │
│  │  ├── интеграция платёжного провайдера (YooKassa)      │       │
│  │  ├── Telegram-бот (пользователи + администраторы)     │       │
│  │  └── UI BusinessAdmin                                 │       │
│  │                                                       │       │
│  │  Routing addon                                        │       │
│  │  ├── сервис xray-router (SOCKS5 · порт 20808)         │       │
│  │  ├── управление upstream-нодами (VLESS, Trojan, WireGuard)       │       │
│  │  ├── routing health probe (таймер 60 секунд)          │       │
│  │  └── UI RoutingAdmin + RuleSourceAdmin                │       │
│  │                                                       │       │
│  │  Anti-share addon                                     │       │
│  │  ├── движок IP-скоринга (читает xray access log)      │       │
│  │  ├── применение nftables (опционально)                │       │
│  │  ├── hiddify-anti-share.timer (systemd)               │       │
│  │  └── UI AntiShareAdmin                                │       │
│  └───────────────────────────────────────────────────────┘       │
│                                                                  │
│  HAProxy (:80, :443) → nginx → bjoern (:9000)                   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Компоненты

### Business addon

Устанавливается в runtime панели (`site-packages/hiddifypanel/`) и расширяет:

- **Модели:** `Commercial`, `CommercialPlan`, `CommercialSubscription` — данные тарифных планов и подписок.
- **UI администратора:** `BusinessAdmin` — страница настроек Telegram, YooKassa, управление тарифами.
- **Telegram-бот:** пакет `telegrambot/` — самообслуживание пользователей (регистрация по номеру телефона, выдача пробного доступа, статус подписки), уведомления администраторов, обработка платёжных колбэков.
- **REST API v2:** `/api/v2/tgbot/` — webhook-эндпоинт для Telegram-обновлений. Валидирует запросы через HMAC с уникальным секретом на каждую установку.
- **Миграции БД:** `init_db.py` — добавляет config-ключи и применяет изменения схемы (`_v137`, `_v138`).

### Routing addon

Устанавливает отдельный systemd-сервис `xray-router` рядом с панелью:

- **xray-router.service** — запускает второй экземпляр Xray на SOCKS5-порту (по умолчанию 20808), маршрутизируя исходящий трафик через настраиваемые upstream-ноды.
- **Управление upstream-нодами** — ноды VLESS, Trojan и WireGuard хранятся в таблице `commercial_routing_upstream`; управляются через UI `RoutingAdmin`.
- **Источники правил** — настраиваемые списки доменов/IP (`commercial_routing_rule_source`); правила хранятся в таблице `commercial_routing_custom_rule`.
- **Routing health probe** — `hiddify-routing-health.timer` запускается каждые 60 секунд, проверяет доступность upstream через TCP-connect, записывает статус в БД панели и JSON-файл, доступный через UI Routing admin.

### Anti-share addon

Работает как фоновое задание по таймеру:

- **hiddify-anti-share.timer** — срабатывает каждые несколько минут, вызывает `runner.py`.
- **Движок скоринга** — читает xray access log, считает уникальные IP источников на UUID пользователя, сравнивает с порогом `max_ips`.
- **Применение nftables** — опционально добавляет `nft`-правила для блокировки помеченных IP. По умолчанию отключено (режим dry-run при первой установке).
- **UI AntiShareAdmin** — настройка порогов, просмотр событий, управление исключениями для пользователей.

---

## Сценарий relay-ноды

Один сервер может одновременно выступать основной панелью и роутинговым relay:

```
Пользователи → HiddifyPanel (порт 443)
                     │
                     └─► xray-router (порт 20808, SOCKS5)
                                │
                                └─► Upstream relay-нода
                                          │
                                          └─► Интернет
```

UI управления upstream-нодами настраивает, через какую внешнюю ноду выходить. Health probe следит за доступностью и записывает результат в БД панели, делая текущий статус видимым в UI Routing admin.

---

## Архитектура установщика

```
release/
├── clean-install-full-stack.sh     ← точка входа для чистой установки
├── rollback-all.sh                 ← общий откат
├── business-installer/
│   ├── install-business.sh         ← устанавливает business addon
│   ├── smoke-business.sh           ← проверяет business addon
│   ├── rollback-business.sh        ← восстанавливает файлы business
│   └── payload/                    ← файлы для установки
├── routing-installer/              ← аналогичная структура
├── antishare-installer/            ← аналогичная структура
└── upgrade-installer/
    └── upgrade-business-layer.sh   ← обновление только business-слоя
```

Каждый установщик работает по следующей схеме:
1. Preflight-проверки
2. Резервное копирование существующих файлов (`backup_target` для каждого файла)
3. Установка файлов payload
4. Применение миграций БД
5. Перезапуск сервисов и polling порта 9000 (до 120 сек)
6. Smoke-тест
7. При ошибке: автоматический откат из резервной копии

---

## Поток данных — Telegram webhook

```
Серверы Telegram
      │  POST /api/v2/tgbot/
      │  X-Telegram-Bot-Api-Secret-Token: <секрет>
      ▼
HAProxy (:443) → nginx → bjoern (:9000)
      │
      ▼
tgbot.py — _webhook_secret_is_valid()
      │  hmac.compare_digest(полученный, сохранённый_секрет)
      │  → 403 при несовпадении или отсутствии секрета
      ▼
Обработчики telegrambot (Usage.py, admin.py)
```

---

## Границы безопасности

| Граница | Механизм |
|---|---|
| Доступ к UI администратора | `login_required(roles={Role.super_admin})` на всех представлениях business/routing/antishare |
| Telegram webhook | HMAC-валидация через `hmac.compare_digest`; fail-closed (403 при отсутствии секрета) |
| Хранение секретов | Токен бота и платёжный токен в БД панели; webhook-секрет в `/etc/hiddify-panel/panel-secrets.env` |
| Установщик | `set -Eeuo pipefail`; резервная копия каждого файла перед заменой; без `eval`, без shell-инъекций |
