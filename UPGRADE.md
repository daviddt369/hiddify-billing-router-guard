# Upgrade Guide

This guide covers upgrading the Hiddify Commercial Addon Stack after a HiddifyPanel base upgrade.

> **Warning:** Always take a server snapshot or manual backup before upgrading.
> The upgrade scripts back up files automatically, but a full snapshot is the safest recovery option.

---

## When to use this guide

Use this guide when:
- You have upgraded HiddifyPanel to a new patch version and need to re-apply the addon layer.
- You have pulled a new version of this repository and want to deploy updated addon files.

Do **not** use the upgrade path to move between major HiddifyPanel versions. The addon stack is pinned to **HiddifyPanel 12.0.0**.

---

## Pre-upgrade checklist

1. Take a server snapshot (recommended).
2. Confirm services are healthy before starting:

   ```bash
   systemctl is-active hiddify-panel hiddify-panel-background-tasks
   ```

3. Pull the latest addon code:

   ```bash
   cd /root/hiddify-billing-router-guard
   git pull
   ```

4. Run the dry-run to see what will change without touching anything:

   ```bash
   sudo bash release/upgrade-installer/upgrade-business-layer.sh --dry-run
   ```

---

## Run the upgrade

```bash
sudo bash release/upgrade-installer/upgrade-business-layer.sh
```

This script:
- Backs up existing addon files before overwriting
- Copies updated business layer files into the panel runtime
- Re-applies routing and antishare patches to shared files (`admin/__init__.py`, `admin-layout.html`, `business-settings.html`)
- Runs the DB migration script (idempotent — safe to run on already-migrated databases)
- Restarts panel services
- Runs all smoke tests

---

## Post-upgrade smoke tests

Run after the upgrade to confirm everything is working:

```bash
sudo bash release/business-installer/smoke-business.sh
sudo bash release/routing-installer/smoke-routing.sh
sudo bash release/antishare-installer/smoke-antishare.sh
```

---

## Upgrade routing and antishare (if needed)

The `upgrade-business-layer.sh` script handles the business addon only. If routing or antishare files also changed, apply them in order:

```bash
# Re-run routing installer in upgrade mode
sudo bash release/routing-installer/install-routing.sh

# Re-run antishare installer in upgrade mode
sudo bash release/antishare-installer/install-antishare.sh
```

Both installers are idempotent and back up files before overwriting.

---

## Full stack upgrade (experimental)

A combined upgrade script is available for upgrading all three addons at once:

```bash
sudo bash release/upgrade-installer/upgrade-existing-stack.sh
```

Run the dry-run first and review the output carefully before proceeding.

---

## Rollback after a failed upgrade

If the upgrade fails, the script rolls back automatically. To roll back manually:

```bash
sudo bash release/rollback-all.sh
```

Or per-addon in reverse order:

```bash
sudo bash release/antishare-installer/rollback-antishare.sh
sudo bash release/routing-installer/rollback-routing.sh
sudo bash release/business-installer/rollback-business.sh
```

Each rollback script reads from the most recent backup directory created during the upgrade.

---

## Known warnings after upgrade

- **"Telegram bot token is not configured"** — expected on the first panel restart after upgrade if the token was not reconfigured. Check the admin UI.
- **Routing health probe warnings** — expected until the upstream node is confirmed reachable after the upgrade.
