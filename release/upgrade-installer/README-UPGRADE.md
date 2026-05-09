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
- Users, UUIDs, subscription links, proxy paths, domains, routing rules, and anti-share config must be preserved
- Tariffs and subscriptions should be preserved when possible, but count drift is a warning, not a hard blocker
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
- Users, UUIDs, Telegram links, proxy paths, domains, routing, and anti-share preservation baseline
- Plans and subscriptions as soft-warning business drift
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

**Validated order (routing → antishare → business).** This exact sequence passed
full rehearsal on production clone with real data (16 users, 448 routing rules,
anti_share_config preserved). Do not change the order without a new rehearsal.

```bash
# Pre-upgrade (preflight + backup already done above)

# Stage B: Base stability (idempotent, panel stays running)
sudo bash ../service-tools/apply-base-stability.sh
sudo bash ../service-tools/stabilize-celery-beat.sh

# Stage C: Routing upgrade (idempotent — panel restarts internally)
sudo bash ../routing-installer/install-routing.sh

# Stage D: Antishare upgrade (panel restarts internally)
sudo bash ../antishare-installer/install-antishare.sh

# Stage E: Business layer upgrade (panel restarts internally)
sudo bash upgrade-business-layer.sh
```

Or use the wrapper (equivalent):

```bash
sudo bash upgrade-existing-stack.sh
sudo bash upgrade-business-layer.sh
```

Notes:
- `install-business.sh` must NOT be used on an existing production server
- `upgrade-business-layer.sh` is the business-layer upgrade entrypoint for an existing stack
- Each installer restarts panel services internally — no manual quiesce needed
- `apply_configs.sh` must NOT run automatically
- A quiesce/defer order (stop panel → all upgrades → start) is architecturally sound
  but has NOT been validated end-to-end — do not use it in production without a
  full rehearsal pass

### Local packaging validation before sync

Run these checks from the repo root before copying a release to clone or production-like hosts:

```bash
bash release/service-tools/check-line-endings.sh
bash -n release/upgrade-installer/*.sh
bash -n release/upgrade-installer/scripts/*.sh
bash -n release/service-tools/*.sh
git diff --check
```

Reason:
- shell scripts must be LF-only and BOM-free
- Linux shebang parsing breaks on UTF-8 BOM and CRLF

### Step 7 — Smoke

```bash
sudo bash smoke-upgrade.sh
sudo bash ../business-installer/smoke-business.sh --upgrade-existing-config
sudo bash ../routing-installer/smoke-routing.sh
sudo bash ../antishare-installer/smoke-antishare.sh --upgrade-existing-config
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
- UUID uniqueness changed or duplicates appeared
- Proxy paths changed
- Domains disappeared
- Telegram-linked users decreased
- Routing custom rules decreased
- anti_share_config nft_enabled was accidentally reset
- anti_share_config nft_dry_run or telegram_enabled changed unexpectedly

Warns but does not block if:
- Plans/subscriptions counts changed
- Tariff names/prices/status changed
- anti_share_state or anti_share_ip_profile changed

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
| Commercial plans | Best-effort | Count/name drift is warning, not blocker |
| Commercial subscriptions | Best-effort | Count/status drift is warning, not blocker |
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
- **Do** use `upgrade-business-layer.sh` for business-layer refresh on an existing stack
- `install-routing.sh` and `install-antishare.sh` are idempotent upgrade/install scripts for an existing stack
- **Do NOT** run `apply_configs.sh` automatically (may regenerate 00_log.json, disable access log)
- **Do NOT** reset anti_share_config (users may be under active bans)
- **Do NOT** change Telegram bot token or webhook during upgrade
- **Do NOT** change YooKassa or payment settings
- **Do NOT** change DNS or domain settings
- **Do NOT** manually enable/disable nft_enabled without explicit decision
- **Do NOT** `TRUNCATE`, destructive-`DELETE`, or recreate `commercial_plan` / `commercial_subscription`
- **Do NOT** clean the database (`DROP TABLE`, `DELETE FROM user`, etc.)
- **Do NOT** skip the backup step

---

## Known compatibility fixes included in this release

These fixes are already included in the current release:

- `IDENT_ME_TIMEOUT` compatible check
- `smoke-business.sh --upgrade-existing-config`
- `smoke-antishare.sh --upgrade-existing-config`
- `anti-share-access.conf` compatibility
- routing enum migration for existing stacks
- `check_port_9000`
- CRLF/shebang nft helper fix

Run `sudo bash preflight-upgrade-audit.sh` first to verify the target state before production execution.
