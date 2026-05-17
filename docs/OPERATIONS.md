# Operations Guide

This guide covers day-to-day operational tasks for the Hiddify Commercial Addon Stack.

---

## Service overview

| Service | Purpose | Managed by |
|---|---|---|
| `hiddify-panel` | Main panel (Flask/bjoern, port 9000) | systemd |
| `hiddify-panel-background-tasks` | Celery worker (scheduled tasks, reminders) | systemd |
| `xray-router` | Routing addon outbound proxy (SOCKS5, port 20808) | systemd |
| `hiddify-anti-share.timer` | Anti-share scoring runner | systemd timer |
| `hiddify-routing-health.timer` | Upstream health probe | systemd timer |

---

## Health checks

### Quick status

```bash
systemctl is-active hiddify-panel hiddify-panel-background-tasks xray-router
systemctl is-active hiddify-anti-share.timer hiddify-routing-health.timer
ss -tlnp | grep -E ':9000|:20808'
```

### Smoke tests (full verification)

```bash
sudo bash release/business-installer/smoke-business.sh
sudo bash release/routing-installer/smoke-routing.sh
sudo bash release/antishare-installer/smoke-antishare.sh
```

---

## Logs

### Panel application log

```bash
tail -f /opt/hiddify-manager/log/system/hiddify_panel.err.log
```

### Background tasks log

```bash
tail -f /opt/hiddify-manager/log/system/hiddify_panel_background_tasks.err.log
```

### Systemd journal (all addon services)

```bash
journalctl -u hiddify-panel -u hiddify-panel-background-tasks -u xray-router -f
```

### Anti-share timer

```bash
journalctl -u hiddify-anti-share -n 50
```

### Routing health probe

```bash
journalctl -u hiddify-routing-health -n 20
cat /opt/hiddify-manager/routing-lists/probe-status.json
```

### Xray access log (used by anti-share)

```bash
tail -f /opt/hiddify-manager/log/system/xray.access.log
```

---

## Telegram bot

### Verify bot is running

```bash
# Check webhook registration
curl -s "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/getWebhookInfo" | python3 -m json.tool
```

### Re-register webhook manually

In the admin UI: navigate to **Business → Telegram**, change any setting and save. This triggers webhook re-registration.

### Rotate webhook secret

1. Generate a new secret:

   ```bash
   openssl rand -hex 32
   ```

2. Update `/etc/hiddify-panel/panel-secrets.env`:

   ```
   HIDDIFY_TELEGRAM_WEBHOOK_SECRET=<new_secret>
   ```

3. Re-register the webhook from the admin UI.

### View admin activation command

```bash
cat /opt/hiddify-manager/business-addon-secrets/telegram-owner-activation.txt
```

---

## Routing

### View upstream status

Open admin UI → **Business → Routing → Upstreams**.

Or check the probe status file:

```bash
cat /opt/hiddify-manager/routing-lists/probe-status.json
```

### Restart xray-router after config change

```bash
systemctl restart xray-router
```

### Apply routing config changes to main Hiddify xray

```bash
sudo bash /opt/hiddify-manager/apply_configs.sh
```

---

## Anti-share

### View current scoring

Open admin UI → **Business → Anti-share**.

### Run scoring manually

```bash
journalctl -u hiddify-anti-share -f &
systemctl start hiddify-anti-share
```

### Enable nftables enforcement

After verifying scoring is working correctly:

1. In admin UI → Anti-share, enable `nft_enabled`.
2. Disable `nft_dry_run` only after confirming no false positives.

> **Warning:** Enabling nft enforcement blocks IPs at the firewall level. Test thoroughly before enabling on a production server.

---

## Database

### Check DB version

```bash
mysql hiddifypanel -sN -e 'SELECT value FROM str_config WHERE `key`="db_version" AND child_id=0;'
```

### Check addon manifests

```bash
ls /opt/hiddify-manager/*.manifest
cat /opt/hiddify-manager/business-addon.manifest
```

### Manual DB backup

```bash
mysqldump hiddifypanel > /tmp/hiddifypanel-backup-$(date +%F).sql
```

---

## Backup and restore

### Pre-install backups location

```bash
ls /opt/hiddify-manager/business-installer-backups/
ls /opt/hiddify-manager/routing-installer-backups/
ls /opt/hiddify-manager/antishare-installer-backups/
```

### Rollback individual addon

```bash
sudo bash release/antishare-installer/rollback-antishare.sh
sudo bash release/routing-installer/rollback-routing.sh
sudo bash release/business-installer/rollback-business.sh
```

### Full rollback

```bash
sudo bash release/rollback-all.sh
```

---

## Common issues

### Panel not responding on port 9000

```bash
systemctl status hiddify-panel
tail -30 /opt/hiddify-manager/log/system/hiddify_panel.err.log
```

Common causes:
- MariaDB not running: `systemctl restart mariadb`
- DB migration error: check err.log for traceback
- Out of memory: check `free -h`, add swap if needed

### Telegram bot not responding

1. Verify token is set in admin UI → Business → Telegram.
2. Check webhook is registered: `getWebhookInfo` (see above).
3. Check panel logs for `"Telegram bot token is not configured"`.

### xray-router not starting

```bash
journalctl -u xray-router -n 30
```

Common cause: no upstream nodes configured. Add at least one upstream in admin UI → Business → Routing.

### Anti-share timer not firing

```bash
systemctl status hiddify-anti-share.timer
systemctl list-timers hiddify-anti-share.timer
```
