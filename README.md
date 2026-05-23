# Hiddify Addon Stack

[🇷🇺 Русский](#русский) | [🇬🇧 English](#english)

---

<a name="русский"></a>
# 🇷🇺 Русский

> **Независимый community-проект.**
> Не связан с официальным проектом Hiddify и не поддерживается им.

Overlay-аддон поверх Hiddify Manager 12.0.0: серверная маршрутизация, управление upstream-нодами, антишейринг, биллинг и Telegram-бот.

---

## Быстрый старт

```bash
# 1. Установить Hiddify Manager 12.0.0
sudo apt update && sudo apt upgrade -y
bash <(curl https://raw.githubusercontent.com/hiddify/Hiddify-Manager/refs/tags/v12.0.0/common/download.sh) "v12.0.0"

# 1.5 Пройти первую настройку панели hiddify задать домен и т.д
Дождитесь полного запуска панели перед продолжением.

Завершите мастер начальной настройки панели (аккаунт администратора, домен, настройки прокси) перед продолжением. Установщик аддонов требует, чтобы панель была полностью настроена, а сервисы hiddify-panel и hiddify-panel-background-tasks были активны.

# 2. Добавить swap (для серверов с 1 ГБ RAM)
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab

# 3. Клонировать репозиторий
git clone https://github.com/daviddt369/hiddify-billing-router-guard.git
cd hiddify-billing-router-guard

# 4. Установить полный стек
sudo bash release/clean-install-full-stack.sh
```

Установка занимает 10–15 минут. Каждый этап делает бэкап, запускает smoke-тесты и откатывается автоматически при ошибке.

📖 Подробная инструкция: [INSTALL.ru.md](INSTALL.ru.md)

---

## Совместимость

> ⚠️ **Поддерживается только Hiddify Manager 12.0.0.**

Версии 12.3.0 и новее **не поддерживаются** — upstream изменил внутреннюю архитектуру между 12.0.0 и 12.3.0.

---

## Что входит

| Модуль | Описание |
|--------|----------|
| **Routing** | Серверные правила маршрутизации, управление upstream-нодами (VLESS, Trojan, WireGuard), relay-нода, health probe |
| **Anti-share** | IP-скоринг для обнаружения шаринга аккаунтов, опциональный nftables |
| **Business** | Telegram-бот, тарифные планы, биллинг, интеграция с ЮKassa |

Модули независимы — можно поставить только нужные.

---

## Серверная маршрутизация

Правила маршрутизации управляются через панель администратора без редактирования конфигов.

- Российский трафик остаётся на RU-ноде, остальной уходит на upstream.
- Правила применяются к Xray и Sing-box.
- Источники правил: URL, файл на сервере, текст напрямую в UI.

### Готовый список российских доменов

Российские сервисы, использующие не-.ru TLD (Яндекс, VK, Сбер, Ozon, WB, банки, стриминг, и др.) — 215+ доменов, данные из реальных перехватов сетей.

**Репозиторий:** [ru-not-ru-domain](https://github.com/daviddt369/ru-not-ru-domain)

**Прямая ссылка для импорта в панели:**
```
https://raw.githubusercontent.com/daviddt369/ru-not-ru-domain/main/domains.txt
```

Добавляется через: Admin UI → Routing → Rule Sources → Source type: `external_url` → Policy: `direct_ru`.

---

## После установки

### Настроить Telegram-бот

1. Создать бота через [@BotFather](https://t.me/BotFather).
2. Admin UI → **Business → Telegram** → вставить токен и сохранить.
3. Отправить боту команду активации:

   ```bash
   cat /opt/hiddify-manager/business-addon-secrets/telegram-owner-activation.txt
   ```

### Добавить upstream-ноду (опционально)

1. Admin UI → **Business → Routing** → добавить ноду (VLESS, Trojan или WireGuard).
2. Включить маршрутизацию и сохранить.
3. Применить конфигурацию:

   ```bash
   sudo bash /opt/hiddify-manager/apply_configs.sh
   ```

---

## Откат

```bash
# Полный откат всех модулей
sudo bash release/rollback-all.sh
```

---

## Документация

| Документ | Описание |
|----------|----------|
| [INSTALL.ru.md](INSTALL.ru.md) | Пошаговая инструкция по установке |
| [UPGRADE.ru.md](UPGRADE.ru.md) | Обновление |
| [docs/ARCHITECTURE.ru.md](docs/ARCHITECTURE.ru.md) | Архитектура системы |
| [docs/OPERATIONS.ru.md](docs/OPERATIONS.ru.md) | Эксплуатация, логи, health checks |

---

## Известные предупреждения (норма, не ошибки)

- **"Telegram bot token is not configured"** — выводится при каждом старте панели до тех пор, пока токен не задан в UI.
- **"xray-router inactive" / "upstream not reachable"** — до тех пор, пока не добавлена хотя бы одна upstream-нода.

---

## Статус релиза

**v1.0.0** — Протестировано на чистой VM (Ubuntu 24.04 LTS + Hiddify Manager 12.0.0). Полная установка завершается за ~12 минут, все smoke-тесты проходят.

---

<a name="english"></a>
# 🇬🇧 English

> **Independent community project.**
> Not affiliated with or officially supported by the Hiddify project.

Overlay addon stack on top of Hiddify Manager 12.0.0: server-side routing, upstream relay-node management, anti-sharing, billing, and Telegram bot.

---

## Quick start

```bash
# 1. Install Hiddify Manager 12.0.0
sudo apt update && sudo apt upgrade -y
bash <(curl https://raw.githubusercontent.com/hiddify/Hiddify-Manager/refs/tags/v12.0.0/common/download.sh) "v12.0.0"

# 2. Add swap (recommended for 1 GB RAM servers)
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab

# 3. Clone repository
git clone https://github.com/daviddt369/hiddify-billing-router-guard.git
cd hiddify-billing-router-guard

# 4. Install full stack
sudo bash release/clean-install-full-stack.sh
```

Install takes 10–15 minutes. Each stage backs up existing files, runs smoke tests, and rolls back automatically on failure.

📖 Full guide: [INSTALL.md](INSTALL.md)

---

## Compatibility

> ⚠️ **Supports Hiddify Manager 12.0.0 only.**

Hiddify Manager 12.3.0 and newer are **not supported**. The upstream project changed internal architecture between 12.0.0 and 12.3.0.

---

## Components

| Component | Description |
|-----------|-------------|
| **Routing** | Server-side routing rules, upstream node management (VLESS, Trojan, WireGuard), relay-node support, health probe |
| **Anti-share** | IP-scoring detection of shared accounts, optional nftables enforcement |
| **Business** | Telegram bot, tariff plans, billing hooks, YooKassa payment integration |

All modules are independent — install only what you need.

---

## Server-side routing

Routing rules managed from the panel admin UI — no config file editing required.

- Russian/domestic traffic stays on the local node; other traffic goes to upstream.
- Rules applied to Xray and Sing-box outbound chains.
- Rule sources: URL, local file, or inline text in the UI.

### Ready-made Russian domain list

Russian services using non-.ru TLDs (Yandex, VK, Sber, Ozon, WB, banks, streaming, etc.) — 215+ domains from real network captures.

**Repository:** [ru-not-ru-domain](https://github.com/daviddt369/ru-not-ru-domain)

**Direct import URL:**
```
https://raw.githubusercontent.com/daviddt369/ru-not-ru-domain/main/domains.txt
```

Add via: Admin UI → Routing → Rule Sources → Source type: `external_url` → Policy: `direct_ru`.

---

## Why this architecture

Three practical problems drove the design:

**Application compatibility** — Some apps detect an active VPN interface and behave differently. Using a local entry node with server-side routing reduces false positives. Users don't configure split-tunneling — the server decides.

**Traffic accounting** — Some ISPs meter international traffic separately. Connecting users to a local server keeps their traffic accounted as local; only flows requiring an external upstream go through the relay node.

**Server-side routing vs client-side config** — Distributing routing configs to end users is fragile. This stack moves routing decisions to the server: configure once, all users get correct routing automatically.

---

## Post-install

### Configure Telegram bot

1. Create a bot via [@BotFather](https://t.me/BotFather).
2. Admin UI → **Business → Telegram** → enter token and save.
3. Send activation command to your bot:

   ```bash
   cat /opt/hiddify-manager/business-addon-secrets/telegram-owner-activation.txt
   ```

### Configure routing upstream (optional)

1. Admin UI → **Business → Routing** → add upstream node (VLESS, Trojan, or WireGuard).
2. Enable routing and save.
3. Apply config:

   ```bash
   sudo bash /opt/hiddify-manager/apply_configs.sh
   ```

---

## Rollback

```bash
sudo bash release/rollback-all.sh
```

---

## Documentation

| Document | Description |
|----------|-------------|
| [INSTALL.md](INSTALL.md) | Step-by-step installation guide |
| [UPGRADE.md](UPGRADE.md) | Upgrade guide |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System architecture |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Operations, logs, health checks |
| [SECURITY.md](SECURITY.md) | Security policy |

---

## Known warnings (expected, not errors)

- **"Telegram bot token is not configured"** — logged on every panel start until token is set in UI.
- **"xray-router inactive" / "upstream not reachable"** — until at least one upstream node is configured.

---

## Release status

**v1.0.0** — Tested on a clean VM (Ubuntu 24.04 LTS + Hiddify Manager 12.0.0). Clean install completes in ~12 minutes with all smoke tests passing.

---

## License

[MIT License](LICENSE) — Copyright (c) 2026 Alex Xles

---

## Disclaimer

This software is provided as-is, without warranty of any kind. Use at your own risk.
This project is not affiliated with, endorsed by, or officially supported by the Hiddify project.
