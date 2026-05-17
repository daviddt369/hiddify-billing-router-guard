# Hiddify Commercial Addon Stack

Commercial addon suite for [HiddifyPanel](https://github.com/hiddify/HiddifyPanel) 12.x.

This project extends HiddifyPanel with commercial subscription management, a Telegram sales bot, intelligent traffic routing, and anti account-sharing protection — all installed without modifying the base panel source code.

---

## Components

| Component | Description |
|---|---|
| **Business addon** | Commercial tariff plans, user subscriptions, billing hooks |
| **Telegram bot** | Self-service subscription management, auto trial signup, per-platform instructions, payment integration |
| **Routing addon** | Local/international traffic split via xray-router SOCKS5; upstream node management (VLESS, Trojan) |
| **Routing health probe** | 60-second keepalive probe; alerts admin via Telegram on upstream failure |
| **Anti-share addon** | IP-scoring detection of shared accounts; optional nftables enforcement |

---

## Requirements

| Requirement | Version / Note |
|---|---|
| HiddifyPanel | 12.0.0 |
| OS | Ubuntu 22.04 LTS or 24.04 LTS |
| User | root |
| RAM | 1 GB minimum; 2 GB swap recommended (see below) |
| Database | MariaDB running and accessible |
| Panel services | `hiddify-panel` and `hiddify-panel-background-tasks` must be active |

---

## Pre-install steps

### 0. Install HiddifyPanel 12.0.0

This addon stack requires **exactly version 12.0.0**. Install it first:

```bash
sudo apt update && sudo apt upgrade -y
bash <(curl https://raw.githubusercontent.com/hiddify/Hiddify-Manager/refs/tags/v12.0.0/common/download.sh) "v12.0.0"
```

Wait until the panel is fully up and accessible before proceeding.

### 1. Add swap (recommended)

If your server has 1 GB RAM or less, add a 2 GB swap file before installing:

```bash
fallocate -l 2G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

### 2. Clone the repository

```bash
git clone https://github.com/daviddt369/hiddify-billing-router-guard.git
cd hiddify-billing-router-guard
```

---

## Clean install (full stack)

Run all three addons in the correct order (business → routing → antishare) with a single command:

```bash
sudo bash release/clean-install-full-stack.sh
```

Each stage runs preflight checks, copies files, runs database migrations, restarts panel services, and verifies the result with smoke tests. If any stage fails, the installer rolls back automatically.

---

## Post-install manual steps

### Configure Telegram bot

1. Obtain a bot token from [@BotFather](https://t.me/BotFather).
2. Open the HiddifyPanel admin interface and navigate to **Business → Telegram**.
3. Enter the bot token in the **Telegram Bot Token** field and save.
4. Send the activation command shown on the screen to your bot:

   ```
   /start admin_<ADMIN_UUID>
   ```

   Replace `<ADMIN_UUID>` with the UUID of the super-admin account. The exact command is also saved to `/opt/hiddify-manager/business-addon-secrets/telegram-owner-activation.txt` on the server.

### Fix proxy-stats balancer (if hiddify-cli is installed)

If `hiddify-cli` is installed on the server, run the balancer fix after installation:

```bash
sudo bash release/service-tools/fix-hiddify-cli-balancer.sh
```

---

## Upgrade

To upgrade the business layer after a HiddifyPanel upgrade:

```bash
sudo bash release/upgrade-installer/upgrade-business-layer.sh
```

---

## Rollback

To revert all three addons in reverse order:

```bash
sudo bash release/rollback-all.sh
```

To revert individual addons:

```bash
sudo bash release/antishare-installer/rollback-antishare.sh
sudo bash release/routing-installer/rollback-routing.sh
sudo bash release/business-installer/rollback-business.sh
```

---

## Smoke tests

Run after any install or upgrade to verify the stack is healthy:

```bash
sudo bash release/business-installer/smoke-business.sh
sudo bash release/routing-installer/smoke-routing.sh
sudo bash release/antishare-installer/smoke-antishare.sh
```

---

## Known warnings (expected, not errors)

- **"Telegram bot token is not configured"** — appears in logs on first start before you configure the token in the admin UI. Expected behavior.
- **"xray-router inactive"** / **"upstream not reachable"** — appears until at least one upstream node is configured under Business → Routing. Expected behavior.

---

## Release status

**v1.0.0-rc1** — Release Candidate. This build has been functionally tested but has not undergone a production hardening review. Do not deploy to production without your own review and testing.

---

## Disclaimer

This software is provided as-is, without warranty of any kind. Use at your own risk. The authors are not responsible for data loss, service interruption, or any other damage resulting from use of this software.
