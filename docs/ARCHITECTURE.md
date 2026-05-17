# Architecture

## Overview

The Hiddify Commercial Addon Stack is an overlay installation on top of Hiddify Manager. It does not fork or patch the base panel source; instead it copies additional Python modules, templates, and configuration files into the panel's runtime directory during installation. The base panel remains upgradeable independently, and rollback restores the original files from a pre-install backup.

---

## System layout

```
┌─────────────────────────────────────────────────────────────────┐
│  Ubuntu 22.04 / 24.04 LTS                                       │
│                                                                  │
│  ┌──────────────────────────────┐                               │
│  │  HiddifyPanel 12.0.0         │  ← base system (unchanged)    │
│  │  Flask/bjoern · port 9000    │                               │
│  │  MariaDB · Redis · Celery    │                               │
│  └──────────────┬───────────────┘                               │
│                 │ overlay (addon files installed on top)         │
│  ┌──────────────▼───────────────────────────────────────┐       │
│  │  Business addon                                       │       │
│  │  ├── commercial tariff plans + subscriptions          │       │
│  │  ├── payment provider integration (YooKassa)          │       │
│  │  ├── Telegram bot (user + admin)                      │       │
│  │  └── BusinessAdmin UI                                 │       │
│  │                                                       │       │
│  │  Routing addon                                        │       │
│  │  ├── xray-router service (SOCKS5 · port 20808)        │       │
│  │  ├── upstream node management (VLESS, Trojan)         │       │
│  │  ├── routing health probe (60-second timer)           │       │
│  │  └── RoutingAdmin + RuleSourceAdmin UI                │       │
│  │                                                       │       │
│  │  Anti-share addon                                     │       │
│  │  ├── IP-scoring engine (xray access log reader)       │       │
│  │  ├── nftables enforcement (optional)                  │       │
│  │  ├── hiddify-anti-share.timer (systemd)               │       │
│  │  └── AntiShareAdmin UI                                │       │
│  └───────────────────────────────────────────────────────┘       │
│                                                                  │
│  HAProxy (:80, :443) → nginx → bjoern (:9000)                   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Components

### Business addon

Installs into the panel runtime (`site-packages/hiddifypanel/`) and extends:

- **Models:** `Commercial`, `CommercialPlan`, `CommercialSubscription` — tariff plan and subscription data.
- **Admin UI:** `BusinessAdmin` — settings page for Telegram, YooKassa, tariff management.
- **Telegram bot:** `telegrambot/` package — user self-service (registration via phone number, trial issuance, subscription status), admin notifications, payment callbacks.
- **REST API v2:** `/api/v2/tgbot/` — webhook endpoint for Telegram updates. Validates requests using HMAC with a per-install secret.
- **DB migrations:** `init_db.py` — adds config keys and applies schema changes (`_v137`, `_v138`).

### Routing addon

Installs a separate `xray-router` systemd service alongside the panel:

- **xray-router.service** — runs a second Xray instance on a SOCKS5 port (default 20808), routing outbound traffic through configurable upstream nodes.
- **Upstream node management** — VLESS and Trojan upstream nodes stored in `commercial_routing_upstream` table; managed from `RoutingAdmin` UI.
- **Rule sources** — configurable domain/IP lists (`commercial_routing_rule_source`); rules stored in `commercial_routing_custom_rule` table.
- **Routing health probe** — `hiddify-routing-health.timer` runs every 60 seconds, checks upstream reachability via TCP connect, writes status to the panel DB and a JSON status file readable by the Routing admin UI.

### Anti-share addon

Runs as a background timer job:

- **hiddify-anti-share.timer** — fires every few minutes, calls `runner.py`.
- **Scoring engine** — reads the xray access log, counts unique source IPs per user UUID, compares against `max_ips` threshold.
- **nftables enforcement** — optionally adds `nft` rules to block flagged IPs. Disabled by default (dry-run mode on first install).
- **AntiShareAdmin UI** — configure thresholds, view events, manage per-user overrides.

---

## Relay-node scenario

A single server can act as both the main panel and a routing relay:

```
Users → HiddifyPanel (port 443)
                │
                └─► xray-router (port 20808, SOCKS5)
                           │
                           └─► Upstream relay node
                                    │
                                    └─► Internet
```

The routing addon's upstream management UI configures which outbound node to use. The health probe monitors upstream availability and writes the result to the panel database, making the current status visible in the Routing admin UI.

---

## Installer design

```
release/
├── clean-install-full-stack.sh     ← entry point for clean installs
├── rollback-all.sh                 ← combined rollback
├── business-installer/
│   ├── install-business.sh         ← installs business addon
│   ├── smoke-business.sh           ← verifies business addon
│   ├── rollback-business.sh        ← restores business files
│   └── payload/                    ← files to install
├── routing-installer/              ← same structure
├── antishare-installer/            ← same structure
└── upgrade-installer/
    └── upgrade-business-layer.sh   ← business-only upgrade
```

Each installer follows this sequence:
1. Pre-flight checks
2. Backup existing files (`backup_target` per file)
3. Install payload files
4. Run DB migrations
5. Restart services and poll port 9000 (up to 120 s)
6. Smoke test
7. On error: automatic rollback from backup

---

## Data flow — Telegram webhook

```
Telegram servers
      │  POST /api/v2/tgbot/
      │  X-Telegram-Bot-Api-Secret-Token: <secret>
      ▼
HAProxy (:443) → nginx → bjoern (:9000)
      │
      ▼
tgbot.py — _webhook_secret_is_valid()
      │  hmac.compare_digest(received, stored_secret)
      │  → 403 if mismatch or secret missing
      ▼
telegrambot handlers (Usage.py, admin.py)
```

---

## Security boundaries

| Boundary | Mechanism |
|---|---|
| Admin UI access | `login_required(roles={Role.super_admin})` on all business/routing/antishare admin views |
| Telegram webhook | HMAC validation with `hmac.compare_digest`; fail-closed (403 on missing secret) |
| Secrets storage | Bot token and payment token in panel DB; webhook secret in `/etc/hiddify-panel/panel-secrets.env` |
| Installer | `set -Eeuo pipefail`; per-file backup before overwrite; no `eval`, no shell injection |
