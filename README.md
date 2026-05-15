# hiddify-commercial-stack

> Коммерческие аддоны для [HiddifyPanel](https://github.com/hiddify/HiddifyPanel) 12.x  
> Commercial add-on suite for [HiddifyPanel](https://github.com/hiddify/HiddifyPanel) 12.x

---

## Что это / What is this

Набор из трёх независимых аддонов, расширяющих функциональность HiddifyPanel без изменения его исходного кода. Каждый аддон устанавливается и откатывается атомарно.

A set of three independent add-ons extending HiddifyPanel without modifying its source code. Each add-on is installed and rolled back atomically.

---

## Аддоны / Add-ons

### 1. Business (`business-installer`)

**RU:** Коммерческая логика — тарифные планы, подписки, Telegram-бот для продаж.

**EN:** Commercial logic — tariff plans, subscriptions, Telegram bot for sales.

Возможности / Features:
- Тарифные планы с лимитами трафика и сроком действия / Tariff plans with traffic and time limits
- Подписки пользователей с автоматическим продлением / User subscriptions with auto-renewal
- Telegram-бот: активация, инструкции, приветственное сообщение / Telegram bot: activation, welcome & instruction messages
- Оплата через внешние провайдеры (интеграция) / External payment provider integration
- Страница бизнес-администратора в панели / Business admin page in panel

### 2. Routing (`routing-installer`)

**RU:** Маршрутизатор трафика — локальный трафик идёт напрямую, нелокальный через внешние ноды (xray-router SOCKS5 :20808).

**EN:** Traffic router — local traffic goes direct, non-local traffic routes through external upstream nodes via xray-router (SOCKS5 :20808).

Возможности / Features:
- Управление внешними нодами (VLESS, Trojan, WireGuard) / External upstream node management (VLESS, Trojan, WireGuard)
- Автоматический балансировщик с URLTest (leastPing) / Auto-balancer with URLTest (leastPing)
- Индикатор «★ Лучшая» для активной ноды / "★ Best" indicator for active node
- Пользовательские правила маршрутизации (домены, IP, CIDR) / Custom routing rules (domains, IP, CIDR)
- Источники правил — текстовые файлы, URL, sing-box SRS / Rule sources — text files, URLs, sing-box SRS
- GeoIP и суффиксы доменов для локального трафика / GeoIP and domain suffixes for local traffic
- Health probe каждые 60 сек / Health probe every 60s

### 3. Antishare (`antishare-installer`)

**RU:** Защита от шаринга аккаунтов — обнаружение по IP-скорингу с опциональной блокировкой через nftables.

**EN:** Anti account-sharing — IP scoring detection with optional nftables blocking.

Возможности / Features:
- Скоринг по количеству уникальных IP за окно / Scoring by unique IP count per window
- Настраиваемые пороги и политики / Configurable thresholds and policies
- Dry-run режим для безопасного тестирования / Dry-run mode for safe testing
- Telegram-уведомления о нарушениях / Telegram notifications for violations
- Блокировка через nftables (опционально) / nftables blocking (optional)

---

## Требования / Requirements

| | |
|---|---|
| **HiddifyPanel** | 12.x (tested on 12.0.0) |
| **OS** | Ubuntu 24.04 LTS |
| **Python** | 3.13 via uv venv (`/opt/hiddify-manager/.venv313`) |
| **DB** | MariaDB 10.11+ |
| **Cache** | Redis |

---

## Быстрый старт / Quick start

### Полная установка / Full stack install

```bash
# Клонировать / Clone
git clone https://github.com/YOUR_ORG/hiddify-commercial-stack.git
cd hiddify-commercial-stack

# Запустить / Run
sudo bash release/clean-install-full-stack.sh
```

Скрипт последовательно устанавливает все три аддона с проверками (smoke tests) на каждом этапе.  
The script installs all three add-ons sequentially with smoke tests at each stage.

### Установка по отдельности / Individual install

```bash
sudo bash release/business-installer/install-business.sh
sudo bash release/routing-installer/install-routing.sh
sudo bash release/antishare-installer/install-antishare.sh
```

### Откат / Rollback

```bash
sudo bash release/antishare-installer/rollback-antishare.sh
sudo bash release/routing-installer/rollback-routing.sh
sudo bash release/business-installer/rollback-business.sh
```

---

## Структура / Structure

```
release/
├── clean-install-full-stack.sh      # Полная установка всего стека
├── business-installer/
│   ├── install-business.sh          # Установка
│   ├── rollback-business.sh         # Откат
│   └── smoke-business.sh            # Проверка
├── routing-installer/
│   ├── install-routing.sh           # Установка
│   ├── rollback-routing.sh          # Откат
│   ├── smoke-routing.sh             # Проверка
│   └── scripts/
│       ├── commercial-routing-db-migrate.sh
│       └── install-routing-health-probe.sh
└── antishare-installer/
    ├── install-antishare.sh         # Установка
    ├── rollback-antishare.sh        # Откат
    └── smoke-antishare.sh           # Проверка
```

---

## Как работает установка / How installation works

1. **Preflight** — проверка окружения, сервисов, БД, отсутствия конфликтов
2. **Install** — копирование файлов, миграция БД, патчинг шаблонов
3. **Restart** — перезапуск панели для активации модулей
4. **Smoke** — автоматическая проверка всех endpoints, маршрутов, импортов
5. **Manifest** — запись манифеста для контроля версий и отката

При любой ошибке установщик автоматически откатывается к состоянию до установки.  
On any error, the installer automatically rolls back to the pre-install state.

---

## Безопасность / Security

- Никаких токенов, ключей или IP-адресов в репозитории / No tokens, keys, or IP addresses in the repository
- Telegram-токен хранится в `business-addon-secrets/` (в `.gitignore`) / Telegram token stored in `business-addon-secrets/` (`.gitignore`-d)
- Sudoers-правила с минимальными привилегиями / Minimal-privilege sudoers rules

---

## Версия / Version

`v1.0.0-rc1` — Release Candidate 1  
Протестировано на чистой Ubuntu 24.04 + HiddifyPanel 12.0.0 (db_version=113).  
Tested on clean Ubuntu 24.04 + HiddifyPanel 12.0.0 (db_version=113).
