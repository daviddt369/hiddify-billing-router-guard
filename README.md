# Hiddify Addon Stack

> **Independent community project.**
> This is an independent community overlay addon stack for Hiddify Manager 12.0.0.
> It is not affiliated with, endorsed by, or officially supported by the Hiddify project.

---

## What this is

This project extends Hiddify Manager 12.0.0 with server-side routing control, upstream relay-node management, operational tooling, and optional billing and anti-sharing modules — installed as an overlay on top of a standard Hiddify Manager installation.

**Why overlay, not fork:**
This project was initially explored as a fork of Hiddify Manager 12.0.0. After Hiddify 12.3.0 was released with significant internal architecture changes, maintaining a full fork became impractical. The project was redesigned as an overlay installer: addon files are copied into the panel runtime at install time, the base panel remains unchanged and independently upgradeable, and rollback restores original files from a pre-install backup.

**Module optionality:**
All three addon modules are logically independent. You can use only the business/billing layer on a single-node installation without enabling routing or anti-share. The relay/cascade scenario is for operators who need upstream node management and server-side routing. Anti-share is an optional enforcement layer for shared-account detection.

---

## Compatibility

> ⚠️ **This release supports Hiddify Manager 12.0.0 only.**

Hiddify Manager 12.3.0 and newer are **not supported** by this release. The upstream project changed internal architecture and file layout between 12.0.0 and 12.3.0. Porting this addon stack to newer Hiddify versions requires a separate adaptation and validation phase.

**Do not install this release on Hiddify Manager 12.3.0+ unless you are intentionally working on a port.**

---

## Why this architecture

This addon stack was designed around a specific operational pattern: a **local or regional entry node** that handles user connections, combined with **server-side routing** that decides where each traffic flow goes — without requiring users to configure anything on their devices.

Three practical problems drove this design:

### Application compatibility with active VPN interfaces

Some applications and websites detect whether a VPN interface is active on the user's device. Behavior varies: some services work normally if the visible IP address belongs to the expected region, others restrict access regardless. In practical deployments, operators have observed that using a **local or regional entry node** with **server-side routing** can reduce false positives and improve compatibility in some environments. The user does not need to configure split-tunneling manually — routing decisions are made on the server.

This does not guarantee that every application will work. Results depend on the specific detection method used.

### Network traffic accounting

With some internet service providers and mobile carriers, international or cross-region traffic is metered separately from local/domestic traffic and may carry higher costs. Connecting users to a **local server** means their traffic is accounted as local. The local server routes only flows that require an external upstream through the relay node, keeping metered international traffic volume low.

This pattern allows operators to manage traffic costs without changing the user-facing connection profile.

### Server-side routing instead of client-side configuration

Distributing complex routing configurations to end users is fragile — clients may have outdated rule sets, misconfigure split-tunneling, or simply not know how to set it up. This stack moves routing decisions to the server: the operator configures once which traffic goes through the proxy, which goes direct, and which is dropped. Users connect with a single standard profile and receive the correct routing automatically.

---

## Components

| Component | Description |
|---|---|
| **Routing addon** | Server-side routing rules, upstream node management (VLESS, Trojan, WireGuard), relay-node support, health probe |
| **Anti-share addon** | IP-scoring detection of shared accounts, optional nftables enforcement |
| **Business addon** | Telegram bot, tariff plans, billing hooks, payment integration |

Each component has its own installer, smoke test script, manifest, and rollback script. They can be installed independently or together via the full-stack wrapper.

---

## What each module adds

### Routing and traffic control

Server-side routing rules managed from the panel admin UI.

- Select which domains, IP ranges, or CIDR blocks are routed, proxied, or blocked.
- Local/domestic traffic can stay direct; selected destinations are forwarded through an upstream relay node.
- Routing is applied to Xray and Sing-box outbound chains via `apply_configs.sh`.

**Rule sources** — routing rules are organized into sources. Each source is a list of domains or subnets with a single policy (`direct` / `upstream` / `block`). Sources can be added in three ways:

| Type | Description |
|---|---|
| **URL** | Remote list fetched automatically; supports on-demand re-import |
| **File** | Local file on the server (e.g. `/opt/hiddify-manager/routing-lists/mylist.txt`) |
| **Text** | Inline list entered directly in the admin UI |

Supported formats: plain domain list, CIDR subnet list, or mixed. Each line is one rule. Comments (`#`, `//`) are ignored. After adding or updating any source, click **Apply xray-router** in the UI.

### Relay-node and upstream management

One server can act as both the panel host and a routing relay:

- Add, edit, and remove upstream relay nodes from the admin UI (VLESS, Trojan, WireGuard).
- Switch routing between upstreams without touching config files.
- Upstream health status is visible in the admin panel (60-second probe writes status to DB and JSON file).

### Anti-sharing guard (optional)

- IP-scoring engine reads the Xray access log and counts unique source IPs per user UUID.
- Configurable threshold per user plan.
- Optional nftables enforcement — disabled by default, dry-run mode on first install.

### Telegram bot tooling

- **User self-service** — subscription status, setup instructions per platform (Android, iPhone, Windows).
- **Admin actions** — new registrations, plan requests, payment events.
- Auto trial signup on phone number registration.
- Inline subscription link in status messages.
- **Outgoing API proxy** — if the Telegram Bot API is not directly reachable from the server, outgoing API calls can be routed through a SOCKS5 or HTTP proxy. The built-in xray-router (port 20808) can serve as this proxy. Configured in admin UI → Business → Telegram → *Telegram API proxy*. Does not affect incoming webhooks.

### Billing / commercial layer (optional)

- Tariff plans with configurable data limits and expiry.
- YooKassa payment integration.
- Automated expiry reminders via Celery.

This module is optional. Routing, relay management, and operational tooling work independently of the billing layer.

---

## Requirements

| Requirement | Value |
|---|---|
| Hiddify Manager | **12.0.0 only** — see [Compatibility](#compatibility) |
| OS | Ubuntu 22.04 LTS or 24.04 LTS |
| User | `root` |
| RAM | 1 GB minimum; 2 GB swap recommended |
| Database | MariaDB running and accessible |
| Panel services | `hiddify-panel` and `hiddify-panel-background-tasks` active |

---

## Pre-install steps

### Step 0 — Install Hiddify Manager 12.0.0

```bash
sudo apt update && sudo apt upgrade -y
bash <(curl https://raw.githubusercontent.com/hiddify/Hiddify-Manager/refs/tags/v12.0.0/common/download.sh) "v12.0.0"
```

After installation, open the panel in a browser and **complete the initial setup wizard** (admin account, domain, proxy settings). Do not run the addon installer until the base panel is working correctly.

### Step 1 — Add swap (recommended for 1 GB RAM servers)

```bash
fallocate -l 2G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

### Step 2 — Clone this repository

```bash
git clone https://github.com/daviddt369/hiddify-billing-router-guard.git
cd hiddify-billing-router-guard
```

---

## Clean install (full stack)

```bash
sudo bash release/clean-install-full-stack.sh
```

Installs all three addons in order:

```text
business → routing → antishare
```

Each stage runs preflight checks, backs up existing files, copies addon files, runs DB migrations, restarts services, and verifies with smoke tests. Rolls back automatically on failure. Expected duration: 10–15 minutes.

Full details: [INSTALL.md](INSTALL.md)

---

## Post-install steps

### Configure Telegram bot

1. Create a bot via [@BotFather](https://t.me/BotFather).
2. Admin UI → **Business → Telegram** → enter the bot token and save.
3. Send the activation command to your bot:

   ```text
   /start admin_<ADMIN_UUID>
   ```

   The exact command is saved on the server:

   ```bash
   cat /opt/hiddify-manager/business-addon-secrets/telegram-owner-activation.txt
   ```

### Configure routing upstream (optional)

1. Admin UI → **Business → Routing** → add an upstream node (VLESS, Trojan, or WireGuard).
2. Enable routing and save.
3. Apply configuration to activate changes in the running proxy core:

   ```bash
   sudo bash /opt/hiddify-manager/apply_configs.sh
   ```

### Fix proxy-stats balancer (if hiddify-cli is installed)

```bash
sudo bash release/service-tools/fix-hiddify-cli-balancer.sh
```

---

## Upgrade

```bash
sudo bash release/upgrade-installer/upgrade-business-layer.sh
```

Full details: [UPGRADE.md](UPGRADE.md)

---

## Rollback

```bash
# Full rollback — all addons in reverse order
sudo bash release/rollback-all.sh
```

Per-addon rollback:

```bash
sudo bash release/antishare-installer/rollback-antishare.sh
sudo bash release/routing-installer/rollback-routing.sh
sudo bash release/business-installer/rollback-business.sh
```

---

## Smoke tests

```bash
sudo bash release/business-installer/smoke-business.sh
sudo bash release/routing-installer/smoke-routing.sh
sudo bash release/antishare-installer/smoke-antishare.sh
```

---

## Documentation

| Document | Description |
|---|---|
| [INSTALL.md](INSTALL.md) | Step-by-step installation guide |
| [UPGRADE.md](UPGRADE.md) | Upgrade and migration guide |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System architecture and component design |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Day-to-day operations, logs, health checks |
| [SECURITY.md](SECURITY.md) | Security policy and secret handling |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to contribute |

---

## Known warnings (expected, not errors)

- **"Telegram bot token is not configured"** — logged on every panel start until the token is set in the admin UI. Expected behavior.
- **"xray-router inactive" / "upstream not reachable"** — logged until at least one upstream node is configured under Business → Routing. Expected behavior.

---

## Release status

**v1.0.0** — Tested on a clean VM (Ubuntu 24.04 LTS + Hiddify Manager 12.0.0). Clean install completes in ~12 minutes with all smoke tests passing.

This release is pinned to Hiddify Manager 12.0.0. See [Compatibility](#compatibility).

---

## License

[MIT License](LICENSE) — Copyright (c) 2026 Alex Xles

---

## Disclaimer

This software is provided as-is, without warranty of any kind. Use at your own risk.

This project is an independent community project and is not affiliated with, endorsed by, or officially supported by the Hiddify project.
