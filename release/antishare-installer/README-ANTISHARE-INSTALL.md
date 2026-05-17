## Anti-Share Installer

Standalone installer for the Anti-Share addon. Installs after the Business addon.

## Install order / Dependencies

### Recommended order

```
clean Hiddify -> business -> routing -> antishare
```

### Supported orders

| Order | Status |
|---|---|
| clean Hiddify -> business -> antishare | OK |
| business -> routing -> antishare | OK (recommended full stack) |

### Not supported

| Order | Status | Reason |
|---|---|---|
| clean Hiddify -> antishare | FAIL | assert_business_installed() — business manifest missing |
| antishare -> business | FAIL | business-installer overwrites __init__.py |
| business -> antishare -> routing | NOT VALIDATED | smoke-routing hard-fails if AntiShareAdmin is already registered |
| routing -> antishare | FAIL | routing installer would overwrite shared files |

### Why antishare hard-depends on business

Anti-share requires business because:

1. `business-addon.manifest` — checked by `assert_business_installed()`, hard FAIL if missing
2. `hiddifypanel.panel.commercial.telegrambot` — business installs Telegram bot runtime used by antishare notifications
3. `hiddifypanel.panel.commercial.capabilities` — business installs `capabilities.py` checked during panel startup
4. `panel/admin/__init__.py` — business installs this file; anti-share is registered as an optional import inside it

### Anti-share and routing: no conflict

- `panel/admin/__init__.py` is NOT patched by antishare-installer (anti-share try/except already present in business layer)
- `admin-layout.html` is NOT patched by antishare-installer (sidebar already present in business layer)
- `business-settings.html` is NOT patched by antishare-installer

## Architecture

Anti-share monitors user connections and detects sharing.

```
every 2 minutes (hiddify-anti-share.timer):
  runner.py reads /opt/hiddify-manager/log/system/xray.access.log
    → maps UUID → [IP1, IP2, ...]  (sliding window, 120s default)
    → for each user with active state:
        score += bump_for_excess_ips
        score += traffic_multiplier_boost (if usage spike)
        score -= decay_on_clean_cycle
        state: normal → suspect → warned → blocked
    → on blocked: ban extra IPs via nftables (if nft_enabled=1)
    → on state transition: Telegram notification to USER (if telegram_enabled=1)
```

### Score thresholds (default)

| Score | State | Action |
|---|---|---|
| < 0.50 | normal | none |
| ≥ 0.50 | suspect | none (monitoring) |
| ≥ 0.75 | warned | Telegram warning to user |
| ≥ 1.00 | blocked | nft ban extra IPs + Telegram blocked to user |

Score recovery: `-0.25` per clean cycle (no excess IPs).

### Score bumps (default)

| Excess IPs | Bump |
|---|---|
| +1 over limit | +0.25 |
| +2 over limit | +0.50 |
| +3+ over limit | +1.00 |

Typical path to block: user with max_ips=1 connecting from 3 IPs → +1.00 score → instant block on next cycle.

### Safe defaults after install

By default, the runner is **active but takes no enforcement action**:

| Setting | Default | Meaning |
|---|---|---|
| `enabled` | 1 | Runner processes cycles |
| `nft_enabled` | **0** | No firewall bans |
| `nft_dry_run` | **1** | Dry-run mode (extra safety) |
| `telegram_enabled` | **0** | No Telegram notifications |

This means after install the system accumulates scores and transitions states, but does NOT ban IPs or send messages. Admin can observe behavior in UI before enabling enforcement.

## Terminology

In UI and Telegram messages: use **"устройства"** (devices), not "IP".
Internally the system counts by IP, but users understand "devices" better.

## Prerequisite

Business addon must be installed first:
```bash
ls /opt/hiddify-manager/business-addon.manifest
```

## Full runbook (clean VM, business+routing already installed)

```bash
# 1. Sync and install
cd /root/hiddify-billing-router-guard/release/antishare-installer
sudo bash install-antishare.sh 2>&1 | tee /tmp/antishare-install.log

# 2. Smoke anti-share
sudo bash smoke-antishare.sh 2>&1 | tee /tmp/antishare-smoke.log

# 3. Regression checks (included in smoke-antishare.sh, but can run manually)
cd /root/hiddify-billing-router-guard/release/business-installer
sudo bash smoke-business.sh

cd /root/hiddify-billing-router-guard/release/routing-installer
sudo bash smoke-routing.sh
```

## Expected warnings after install (norm, not failures)

- `Telegram bot token is not configured` — normal (telegram_enabled=0)
- `Anti-share admin views disabled: optional module missing` — this warning disappears after install
- `hiddify-anti-share.timer not active` — may appear briefly after enable; starts on next tick

## Architecture after install (inactive enforcement)

```
After install (safe defaults):
  hiddify-anti-share.timer → active (every 2 min)
  hiddify-anti-share.service → runs runner.py each tick
  runner: enabled=1, nft_enabled=0, telegram_enabled=0
  Result: scores accumulate in DB, no bans, no notifications

After enabling enforcement:
  Set nft_enabled=1 and nft_dry_run=0 in /admin/anti-share-admin/
  Set telegram_enabled=1 if Telegram bot is configured
  Result: excess IPs get firewall-banned, user notified via Telegram
```

## Activating anti-share enforcement

### Step 1: Verify scoring is working

Wait 10-15 minutes after install, then check:
```bash
# Should show users with states (learning/normal/suspect)
sudo mysql hiddifypanel -e 'SELECT user_id, state, score, current_ip_count FROM anti_share_state LIMIT 10;'

# Check recent cycle events
sudo mysql hiddifypanel -e 'SELECT user_id, event_type, state_before, state_after, created_at FROM anti_share_event ORDER BY id DESC LIMIT 20;'
```

Note: scoring requires xray.access.log to exist (requires active user traffic).
On a fresh VM with no clients: `collect_recent_ips()` returns empty → runner runs but no state changes.

### Step 2: Enable in admin UI

```
https://<your-domain>/<proxy_path>/admin/anti-share-admin/
```

Enable Telegram notifications if bot is configured.
Enable nft enforcement only after confirming scoring is correct.

## Rollback

```bash
cd /root/hiddify-billing-router-guard/release/antishare-installer
sudo bash rollback-antishare.sh
```

With DB restore (DROPS anti_share_* data):
```bash
sudo bash rollback-antishare.sh --restore-db
```

## Diagnostics

```bash
cd /root/hiddify-billing-router-guard/release/antishare-installer
sudo bash collect-antishare-diagnostics.sh
```

## Notes

- Backups written to `/opt/hiddify-manager/antishare-installer-backups/`
- Manifest at `/opt/hiddify-manager/anti-share-addon.manifest`
- DB tables: `anti_share_config`, `anti_share_state`, `anti_share_ip_profile`, `anti_share_event`, `anti_share_user_override`
- nft helper: `/opt/hiddify-manager/common/hiddify-antishare-nft.sh`
- Rollback does NOT drop anti_share_* tables by default (user data preserved)
- DB restore only via `--restore-db` from dump

## Known limitations

- **Old IP counting system is separate**: `usage.html` shows "Too many Connected IPs" via `user_detail.connected_devices` — this is a base Hiddify mechanism, independent of anti-share scoring
- **Telegram notification goes to USER** (via user.telegram_id), not to admin
- **xray access log required** for real detection: if no traffic, log doesn't exist → no IPs detected
- **No traffic = no detected IPs** — scoring won't fire on users with no active connections
- **business → antishare → routing** install order is not validated; use routing first
- **SRS format** not supported in rule sources (routing Stage 2F scope, unrelated to antishare)

## Stage Anti-Share-1 status

- install-antishare.sh: skeleton ready
- smoke-antishare.sh: 15 checks
- DB migration: 5 tables + safe defaults
- nft backend: timeout=30 added
- systemd timer: every 2 minutes
- No patches to business-settings.html / admin-layout.html / __init__.py (already handled by business layer)
