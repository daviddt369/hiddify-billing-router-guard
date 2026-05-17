# Installation Guide

This guide covers a clean installation of the Hiddify Commercial Addon Stack on top of HiddifyPanel 12.0.0.

---

## Requirements

| Requirement | Version / Note |
|---|---|
| HiddifyPanel | **12.0.0 exactly** |
| OS | Ubuntu 22.04 LTS or 24.04 LTS |
| User | root |
| RAM | 1 GB minimum; 2 GB swap strongly recommended |
| Database | MariaDB running and accessible |
| Panel services | `hiddify-panel` and `hiddify-panel-background-tasks` must be active |

---

## Step 0 — Install HiddifyPanel 12.0.0

This addon stack requires **exactly version 12.0.0**. Other versions are not supported.

```bash
sudo apt update && sudo apt upgrade -y
bash <(curl https://raw.githubusercontent.com/hiddify/Hiddify-Manager/refs/tags/v12.0.0/common/download.sh) "v12.0.0"
```

Wait until the panel is fully up and accessible before continuing.

**Complete the initial panel setup wizard** (admin account, domain, proxy settings) before proceeding. The addon installer requires the panel to be fully configured and both `hiddify-panel` and `hiddify-panel-background-tasks` services to be active.

---

## Step 1 — Add swap (recommended)

On servers with 1 GB RAM or less, memory pressure during installation can cause the panel to restart slowly. Add a swap file first:

```bash
fallocate -l 2G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

Verify:

```bash
free -h
```

---

## Step 2 — Clone the repository

```bash
git clone https://github.com/daviddt369/hiddify-billing-router-guard.git
cd hiddify-billing-router-guard
```

---

## Step 3 — Run the installer

The installer runs all three addons in the correct order (business → routing → antishare):

```bash
sudo bash release/clean-install-full-stack.sh
```

Expected duration: 10–15 minutes on a typical VPS.

Each stage:
1. Runs preflight checks (services, DB connectivity, payload integrity)
2. Backs up existing files before overwriting
3. Copies addon files into the panel runtime
4. Runs database migrations
5. Restarts panel services
6. Runs smoke tests
7. Rolls back automatically if any step fails

---

## Step 4 — Configure Telegram bot

After installation, the Telegram bot is installed but inactive until a token is set.

1. Create a bot via [@BotFather](https://t.me/BotFather) and copy the token.
2. Open the admin interface → **Business → Telegram**.
3. Enter the bot token and save.
4. Activate admin access: send the following command to your bot:

   ```
   /start admin_<ADMIN_UUID>
   ```

   The exact activation command for your server is saved to:

   ```
   /opt/hiddify-manager/business-addon-secrets/telegram-owner-activation.txt
   ```

---

## Step 5 — Configure routing upstream (optional)

If you want to enable traffic routing through an upstream relay node:

1. Open admin interface → **Business → Routing**.
2. Add an upstream node (VLESS, Trojan, or WireGuard format).
3. Enable routing in the same section and save.
4. **Apply configuration** — required for changes to take effect in the running Xray/Sing-box core:

   ```bash
   sudo bash /opt/hiddify-manager/apply_configs.sh
   ```

   Without this step the routing settings are saved in the database but not active in the proxy core.

---

## Step 6 — Fix proxy-stats balancer (if hiddify-cli is installed)

If `hiddify-cli` is installed on the server:

```bash
sudo bash release/service-tools/fix-hiddify-cli-balancer.sh
```

---

## Verify installation

Run smoke tests to confirm all addons are healthy:

```bash
sudo bash release/business-installer/smoke-business.sh
sudo bash release/routing-installer/smoke-routing.sh
sudo bash release/antishare-installer/smoke-antishare.sh
```

---

## Known warnings (expected output, not errors)

- `"Telegram bot token is not configured"` — logged on every panel start until the token is set in the admin UI.
- `"xray-router inactive"` — logged by the routing health probe until at least one upstream node is configured.

---

## Rollback

If you need to revert the installation:

```bash
sudo bash release/rollback-all.sh
```

See [UPGRADE.md](UPGRADE.md) for the upgrade path.
