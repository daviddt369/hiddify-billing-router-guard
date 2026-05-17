# Hiddify Addon Stack

> **Independent community project.** This project is an independent community addon stack for Hiddify Manager. It is not affiliated with, endorsed by, or officially supported by the Hiddify project unless explicitly stated otherwise.

An overlay addon suite for [Hiddify Manager](https://github.com/hiddify/HiddifyPanel) 12.0.0 that adds server-side routing control, upstream/relay-node management, operational tooling, and optional user management extensions — as an overlay installer on top of a standard Hiddify Manager installation, without maintaining a fork of the upstream Hiddify repository.

---

## What this project adds

### 1. Routing and traffic control

Server-side routing rules managed from the panel admin UI. Useful for operators whose users do not configure client-side routing and need sensible server-side defaults.

- Select which domains, IP ranges, or CIDR blocks are routed, proxied, or blocked directly from the admin panel.
- Local/domestic traffic can stay direct; selected destinations are forwarded through an upstream relay node.
- Routing is applied to Xray and Sing-box outbound chains automatically via `apply_configs.sh`.

**Rule sources** — routing rules are organized into sources. Each source is a list of domains or subnets with a single policy (direct / upstream / block). Sources can be added in three ways:

| Type | Description |
|---|---|
| **URL** | Remote list fetched automatically; supports re-import on demand or on schedule |
| **File** | Local file on the server (e.g. `/opt/hiddify-manager/routing-lists/mylist.txt`) |
| **Text** | Inline list entered directly in the admin UI |

Supported formats: plain domain list, CIDR subnet list, or a mix. Each line is one rule. Comments (`#`, `//`) are ignored. After adding or updating any source, click **Apply xray-router** in the UI to activate the changes.

### 2. Relay-node and upstream management

One server can act as both the panel host and a routing relay:

- Add, edit, and remove upstream relay nodes from the admin UI (VLESS, Trojan, WireGuard).
- Switch routing between upstreams without touching config files.
- Upstream health status is visible in the admin panel and checked by an automated probe.

### 3. Operational safety

- **Clean install script** — installs all addons in the correct order with preflight checks and smoke tests at each stage.
- **Upgrade script** — re-applies addon files on top of an upgraded panel without touching routing or antishare.
- **Smoke tests** — verify each addon after install or upgrade.
- **Rollback scripts** — per-addon and full-stack rollback from pre-install file backups.
- **Routing health probe** — 60-second systemd timer; writes upstream health status to the panel database and a status JSON file, visible in the Routing admin UI.

### 4. Anti-sharing guard

Optional account-sharing detection and enforcement, more relevant for commercial or community operators:

- IP-scoring engine reads the Xray access log and counts unique source IPs per user UUID.
- Configurable threshold per user plan.
- Optional nftables enforcement (disabled by default; dry-run mode on first install).

### 5. Telegram bot tooling

- **User self-service** — subscription status, setup instructions per platform (Android, iPhone, Windows), support contact.
- **Admin actions** — new registrations, plan requests, payment events, upstream failure alerts.
- Auto trial signup on phone number registration.
- Inline subscription link in status messages.

### 6. Billing / commercial layer (optional)

An optional commercial subscription layer for operators running paid or community VPN services:

- Tariff plans with configurable data limits and expiry.
- YooKassa payment integration.
- Automated expiry reminders via Celery.

This component is optional and last in priority. The routing, relay management, and operational tooling work independently of the billing layer.

---

## Why this architecture

This addon stack was designed around a specific operational pattern: a **local entry node** that handles user connections, combined with **server-side routing** that decides where each traffic flow goes — without requiring users to configure anything on their devices.

Three practical problems drove this design:

### Application compatibility with active VPN interfaces

Some applications and websites detect whether a VPN interface is active on the user's device. Behavior varies: some services work normally if the visible IP address belongs to the expected region, others restrict access regardless. In practice, operators have observed fewer compatibility issues when:

- the user connects to a **local or regional entry node** rather than a foreign upstream directly
- **server-side routing** keeps selected traffic flows local, so the visible exit IP for those flows remains regional
- the user does not need to configure split-tunneling manually on their device — routing decisions are made on the server

This does not guarantee compatibility with every application or service. Results depend on the specific detection method used.

### Network traffic accounting

With some internet service providers and mobile carriers, **international or cross-region traffic** is metered separately from local/domestic traffic and may carry significantly higher costs per gigabyte. Connecting users to a **local server** means their traffic is accounted as local traffic. The local server then routes only the flows that require an external upstream through the relay node, keeping the volume of metered international traffic low.

This pattern allows operators to manage traffic costs without changing the user-facing connection profile.

### Server-side routing instead of client-side configuration

Distributing complex routing configurations to end users is fragile — clients may have outdated rule sets, misconfigure split-tunneling, or simply not know how to set it up. This stack moves routing decisions to the server: the operator configures once which traffic goes through the proxy, which goes direct, and which is dropped. Users connect with a single standard profile and receive the correct routing automatically.

---

## Components

| Component | Description |
|---|---|
| **Routing addon** | Server-side routing rules, upstream management, relay-node support, health probe |
| **Anti-share addon** | IP-scoring detection, optional nftables enforcement |
| **Business addon** | Telegram bot, billing layer, tariff plans, payment integration |

---

## Requirements

| Requirement | Version / Note |
|---|---|
| Hiddify Manager | **12.0.0 exactly** |
| OS | Ubuntu 22.04 LTS or 24.04 LTS |
| User | root |
| RAM | 1 GB minimum; 2 GB swap recommended |
| Database | MariaDB running and accessible |
| Panel services | `hiddify-panel` and `hiddify-panel-background-tasks` must be active |

---

## Pre-install steps

### 0. Install Hiddify Manager 12.0.0

This addon stack requires **exactly version 12.0.0**. Other versions are not supported.

```bash
sudo apt update && sudo apt upgrade -y
bash <(curl https://raw.githubusercontent.com/hiddify/Hiddify-Manager/refs/tags/v12.0.0/common/download.sh) "v12.0.0"
```

Wait until the panel is fully up and accessible before continuing.

### 0a. Complete the initial panel setup

After installation, open the panel in a browser and complete the initial configuration wizard (admin account, domain, proxy settings). The addon installer requires the panel to be fully configured and its services to be active.

**Do not run the addon installer until the base panel is working correctly.**

### 1. Add swap (recommended for 1 GB RAM servers)

```bash
fallocate -l 2G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

### 2. Clone this repository

```bash
git clone https://github.com/daviddt369/hiddify-billing-router-guard.git
cd hiddify-billing-router-guard
```

---

## Clean install

```bash
sudo bash release/clean-install-full-stack.sh
```

Installs all three addons in order (business → routing → antishare) with preflight checks, DB migrations, smoke tests, and automatic rollback on failure. Expected duration: 10–15 minutes.

Full details: [INSTALL.md](INSTALL.md)

---

## Post-install manual steps

### Configure Telegram bot

1. Create a bot via [@BotFather](https://t.me/BotFather).
2. Admin UI → **Business → Telegram** → enter the bot token and save.
3. Send the activation command to your bot:

   ```
   /start admin_<ADMIN_UUID>
   ```

   The exact command is saved on the server:

   ```bash
   cat /opt/hiddify-manager/business-addon-secrets/telegram-owner-activation.txt
   ```

### Configure routing upstream and apply configuration (optional)

To enable traffic routing through an upstream relay node:

1. Admin UI → **Business → Routing** → add an upstream node (VLESS, Trojan, or WireGuard format).
2. Enable routing in the same section and save.
3. **Apply configuration** — required for routing to take effect in Xray/Sing-box:

   ```bash
   sudo bash /opt/hiddify-manager/apply_configs.sh
   ```

   Without this step the routing changes are saved in the DB but not active in the running proxy core.

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
# Full rollback (all addons, reverse order)
sudo bash release/rollback-all.sh

# Individual rollback
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

- **"Telegram bot token is not configured"** — logged on every panel start until the token is set in the admin UI.
- **"xray-router inactive" / "upstream not reachable"** — logged until at least one upstream node is configured under Business → Routing.

---

## Release status

**v1.0.0-rc1** — Release Candidate. Tested on a clean VM (Ubuntu 24.04 LTS + Hiddify Manager 12.0.0). Clean install completes in ~12 minutes with all smoke tests passing. Not recommended for production without your own review and testing.

---

## License

[MIT License](LICENSE) — Copyright (c) 2026 Alex Xles

---

## Disclaimer

This software is provided as-is, without warranty of any kind. Use at your own risk. The authors are not responsible for data loss, service interruption, or any other damage resulting from the use of this software.

This project is an independent community project and is not affiliated with, endorsed by, or officially supported by the Hiddify project.
