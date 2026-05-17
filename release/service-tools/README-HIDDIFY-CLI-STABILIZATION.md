## Hiddify CLI Stabilization

These scripts are a separate service hygiene tool for `hiddify-cli`.

This directory also contains production-upgrade helpers:
- `apply-base-stability.sh`
- `check-line-endings.sh`
- `stabilize-celery-beat.sh`

They are not:
- part of the Business installer
- part of routing
- part of antishare

Known upstream blocker on clean Hiddify:
- bundled `hiddify-core v4.1.0`
- bundled `hiddify-sing-box v1.13.1`
- `unknown load balance strategy`
- restart loop / request storm from `hiddify-cli.service`

The panel and business addon work correctly without `hiddify-cli`.

---

## Production Upgrade Helpers

Used by the production upgrade runbook before addon upgrade:

```bash
cd /root/lab-work/release/service-tools
sudo bash apply-base-stability.sh
sudo bash stabilize-celery-beat.sh
```

What they do:
- `apply-base-stability.sh`
  protects `net.py` fail-safely if ident.me timeout handling is missing
- `check-line-endings.sh`
  fails if any tracked shell script has UTF-8 BOM or CRLF line endings
- `stabilize-celery-beat.sh`
  backs up and refreshes stale `celerybeat-schedule` files when needed

What they do not do:
- do not run `apply_configs.sh`
- do not perform destructive DB actions
- do not touch users, subscriptions, or plans

Local packaging check before syncing a release:

```bash
cd /root/lab-work/release/service-tools
bash check-line-endings.sh /path/to/repo
```

---

## Audit

```bash
cd /home/texas/lab-work/release/service-tools
sudo bash audit-hiddify-cli.sh
```

What it does:
- captures `systemctl status`
- captures `systemctl cat`
- captures `NRestarts`
- captures fresh `journalctl`
- checks restart growth over 60 seconds
- saves diagnostics under `/opt/hiddify-manager/hiddify-cli-audit/`

Expected on clean Hiddify:
- `restart_storm_detected=yes`
- `active_state=activating`
- `sub_state=auto-restart`

---

## Stabilize

```bash
cd /home/texas/lab-work/release/service-tools
sudo bash stabilize-hiddify-cli.sh
```

What it does:
- confirms restart storm before changing anything
- backs up current unit / override state
- applies a systemd override to slow the loop
- if the loop still persists, switches to explicit degraded mode
- prints `HIDDIFY_CLI_DEGRADED_EXPECTED` when degraded mode is intentional

It does not:
- claim that upstream `hiddify-core` is fixed
- touch Business / Telegram / YooKassa / Тарифы files

---

## Smoke

```bash
cd /home/texas/lab-work/release/service-tools
sudo bash smoke-hiddify-cli.sh
```

Expected outcomes:
- `hiddify-cli smoke OK` if service is stable
- `HIDDIFY_CLI_DEGRADED_EXPECTED` if stabilization intentionally disabled the service
- failure if restart loop is still present

---

## Rollback

```bash
cd /home/texas/lab-work/release/service-tools
sudo bash rollback-hiddify-cli-stabilization.sh
```

Optional:

```bash
sudo bash rollback-hiddify-cli-stabilization.sh --backup-dir /opt/hiddify-manager/hiddify-cli-stabilization-backups/<stamp>
```

---

## Place in the business install sequence

`hiddify-cli` re-enters the restart storm after business install restarts the panel services. This is expected. Stabilize again after business install:

```bash
cd /home/texas/lab-work/release/service-tools
sudo bash stabilize-hiddify-cli.sh
sudo bash smoke-hiddify-cli.sh
```

Then re-run `smoke-business.sh` to confirm the panel is still healthy.
