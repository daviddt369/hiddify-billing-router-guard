# Release Notes — v1.0.0-rc1

**Release date:** 2026-05-17
**Status:** Release Candidate — functional testing complete; production review recommended before deployment.

---

## Summary

v1.0.0-rc1 is the first public release candidate of the Hiddify Commercial Addon Stack. It ships all three addons (business, routing, antishare) as a unified, atomically installable and rollback-capable suite for HiddifyPanel 12.0.0.

---

## What's new

### Installer

- `release/clean-install-full-stack.sh` — installs all three addons sequentially (business → routing → antishare) with preflight checks and smoke tests at each stage.
- Port 9000 polling — the installer waits up to 120 seconds for the panel to bind port 9000 before proceeding, preventing race conditions on slow servers.
- Webhook auto-registration — after business addon install, the webhook is registered automatically if a bot token is already configured.
- Atomic rollback — each installer captures a file-level backup before touching any files; on error, files are restored in reverse order.

### Telegram bot

- Dynamic reply keyboard — buttons adapt based on whether the user has an active plan.
- Per-platform instructions — inline keyboard lets users select Android, iPhone, or Windows; instruction text is configurable from the admin UI.
- Auto trial signup — when a new user shares their phone number, a trial account is created automatically with configurable limits.
- Inline subscription link — subscription URL is sent inline in the status message for easy copy-paste into VPN clients.
- Expiry reminders — configurable day-before-expiry reminders sent automatically via Celery task.
- Admin notifications — admins receive Telegram notifications for new registrations, plan requests, and payment events.

### Database migrations

- `_v137` — adds platform-specific instruction config keys (`telegram_instruction_android`, `telegram_instruction_ios`, `telegram_instruction_windows`) to `str_config`.
- `_v138` — adds `ON DELETE CASCADE` to the `user_detail` foreign key, preventing orphaned rows on user deletion.

### Bug fixes

- `_v136` crash without routing addon — `BusinessAdmin.py` now gracefully handles the case where the routing addon is not installed; the routing section is hidden from the UI rather than causing a 500 error.
- Subscription link without request context — `_send_my_subscription` now builds subscription URLs using a test request context when called outside an HTTP request (e.g., from a Celery task), preventing `RuntimeError: Working outside of application context`.
- Webhook 500 error — removed UUID path segment from the tgbot URL to avoid triggering the auth middleware, which returned 500 on pre-flight requests.

### Security

- Webhook secret auto-created — the installer calls `openssl rand -hex 32` and writes the result to `/etc/hiddify-panel/panel-secrets.env` before registering the webhook.
- Fail-closed webhook validation — if the webhook secret is absent or empty, all POST requests to `/api/v2/tgbot/` are rejected with HTTP 403.
- Duplicate update deduplication — a 15-minute sliding window deduplication cache prevents Telegram from processing the same `update_id` twice.

---

## Known warnings (expected)

- **"Telegram bot token is not configured"** — logged on panel startup before the token is set in the admin UI. This is expected and not an error.
- **xray-router inactive / upstream unreachable** — logged by the routing health probe until at least one upstream node is configured. Expected.

---

## Upgrade notes

To upgrade the business layer after a HiddifyPanel base upgrade:

```bash
sudo bash release/upgrade-installer/upgrade-business-layer.sh
```

This script re-applies business addon files on top of the upgraded panel without touching routing or antishare. Run routing and antishare smoke tests afterward.

---

## Rollback

```bash
sudo bash release/rollback-all.sh
```

Or per-addon in reverse order:

```bash
sudo bash release/antishare-installer/rollback-antishare.sh
sudo bash release/routing-installer/rollback-routing.sh
sudo bash release/business-installer/rollback-business.sh
```

Each rollback script restores files from the most recent backup directory and optionally restores the database from the SQL dump captured at install time.
