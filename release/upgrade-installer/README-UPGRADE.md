# Hiddify Production Upgrade Installer

Upgrade an existing production Hiddify stack to the current validated release.

**This is NOT a clean install.** For new servers, use the installers in:
- `release/business-installer/`
- `release/routing-installer/`
- `release/antishare-installer/`

---

## When to use this

Use `upgrade-installer/` when:
- The server already has business + routing + antishare installed (any version)
- Users, subscription links, and tariffs must be preserved
- anti_share_config (nft_enabled, telegram_enabled) must NOT be reset
- You cannot or should not do a clean reinstall

---

## Recommended production upgrade workflow

### Step 1 — Clone production

Clone your production server to a local VM before touching production.
Never run the upgrade rehearsal directly on production.

### Step 2 — Snapshot clone

Take a hypervisor snapshot of the clean clone before running anything.
This allows instant rollback if anything goes wrong.

### Step 3 — Preflight audit

```bash
sudo bash preflight-upgrade-audit.sh [--output /tmp/preflight.txt]
```

Reviews:
- Service states and failed units
- Addon manifests and versions
- DB tables and db_version
- Users, subscriptions, plans counts
- Anti-share config (nft_enabled, telegram state)
- Runtime file checksums
- Net.py timeout status
- Celery beat health

### Step 4 — Backup

```bash
sudo bash backup-before-upgrade.sh
```

Creates a timestamped backup in `/opt/hiddify-manager/upgrade-installer-backups/`.

Backs up:
- Full DB dump (`mysqldump`)
- Runtime Python files (routing, antishare, business admin)
- HTML templates
- Systemd unit files and overrides
- Sudoers entries
- Addon manifests
- commander.py
- xray/singbox config templates
- Table row counts for comparison

### Step 5 — Before-snapshot

```bash
sudo bash scripts/check-user-link-preservation.sh --before
```

Records critical user/subscription data for before/after comparison.

### Step 6 — Upgrade

```bash
sudo bash upgrade-existing-stack.sh [--dry-run]
```

Run `--dry-run` first to preview what will be done.

The upgrade:
- Runs service-tools base stability
- Runs routing installer (idempotent: adds Stage 2D/2E, fixes FK, advances db_version)
- Runs antishare installer (replaces addon files, preserves anti_share_config)
- Does NOT reset anti_share_config
- Does NOT run apply_configs.sh

### Step 7 — Smoke

```bash
sudo bash smoke-upgrade.sh
```

Upgrade-aware smoke tests:
- Panel and background tasks active
- DB accessible, db_version=136
- Business, routing, antishare endpoints working
- anti_share_config preserved (nft_enabled, telegram_enabled unchanged)
- User/subscription counts unchanged
- No unexpected failed units

### Step 8 — After-snapshot and compare

```bash
sudo bash scripts/check-user-link-preservation.sh --after
sudo bash scripts/check-user-link-preservation.sh --compare
sudo bash scripts/compare-db-preservation.sh
```

Hard-fails if:
- Users were lost
- Subscription UUIDs decreased
- Plans/subscriptions were removed
- anti_share_config nft_enabled was accidentally reset

### Step 9 — Decision

If clone upgrade passes all checks → prepare production runbook.

If anything fails → rollback the clone and investigate.

---

## Rollback

```bash
# File-only rollback (default):
sudo bash rollback-upgrade.sh

# Full rollback including DB (DESTRUCTIVE — overwrites all current data):
CONFIRM_RESTORE_DB=YES sudo bash rollback-upgrade.sh --restore-db
```

---

## What is preserved during upgrade

| Data | Preserved | Notes |
|------|-----------|-------|
| Users (user table) | YES | Not touched |
| Subscription UUIDs | YES | Not touched |
| Subscription links | YES | proxy_path unchanged |
| telegram_id | YES | Not touched |
| max_ips / device limits | YES | Not touched |
| Commercial plans | YES | Not touched |
| Commercial subscriptions | YES | Not touched |
| YooKassa / payment config | YES | str_config preserved |
| Telegram bot token | YES | str_config preserved |
| Admin Telegram config | YES | str_config preserved |
| commercial_de_* (upstream) | YES | ON DUPLICATE KEY UPDATE preserves |
| routing custom_rules | YES | Table preserved, rows kept |
| anti_share_config | YES | Not overwritten if row exists |
| nft_enabled / nft_dry_run | YES | Preserved from pre-upgrade |
| telegram_enabled | YES | Preserved from pre-upgrade |
| anti_share_state | YES | Table preserved, scores kept |

## What changes during upgrade

| Component | Change |
|-----------|--------|
| Routing Python files | Updated to current release |
| commercial_routing_upstream table | Created if missing |
| commercial_routing_rule_source table | Created if missing |
| commercial_routing_custom_rule.source_id | Column added if missing |
| Antishare Python files | Updated to current release |
| Routing manifest | Created if missing |
| db_version | Advanced 134→136 (only if needed) |
| Celery beat schedule | Refreshed if stale |

---

## What NOT to do

- **Do NOT** run `install-business.sh` over an existing production stack
- **Do NOT** run `apply_configs.sh` automatically (may regenerate 00_log.json, disable access log)
- **Do NOT** reset anti_share_config (users may be under active bans)
- **Do NOT** change Telegram bot token or webhook during upgrade
- **Do NOT** change YooKassa or payment settings
- **Do NOT** change DNS or domain settings
- **Do NOT** manually enable/disable nft_enabled without explicit decision
- **Do NOT** clean the database (`DROP TABLE`, `DELETE FROM user`, etc.)
- **Do NOT** skip the backup step

---

## Known compatibility issues (see compatibility-fixes.md)

Before running upgrade on a production server, apply these fixes to the release scripts:

| Fix | Script | Issue |
|-----|--------|-------|
| A | service-tools/apply-base-stability.sh | Must recognize `IDENT_ME_TIMEOUT` as already-compatible |
| B | antishare-installer/smoke-antishare.sh | Add `--upgrade-existing-config` mode |
| C | antishare-installer/install-antishare.sh | Confirm anti_share_config not overwritten |
| D | antishare-installer/common-antishare.sh | Accept `anti-share-access.conf` as compatible |
| E | routing-installer/scripts/commercial-routing-db-migrate.sh | Verify idempotent upstream seed |

Run `sudo bash preflight-upgrade-audit.sh` first to see which fixes apply to your server.
