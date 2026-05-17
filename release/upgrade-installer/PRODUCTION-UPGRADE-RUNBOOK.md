# Hiddify Production In-Place Upgrade Runbook

Validated target:
- Existing production Hiddify stack
- Business + old-style routing + anti-share already present
- Upgrade path validated on a production-like clone

Current decision:
- Strategy: `GO`
- Production execution: `Conditional GO`

The routing layer on production is expected to be in pre-upgrade state.
That is not a blocker if the same state was already migrated successfully on the clone rehearsal.

## Source Of Truth

Production source of truth is the explicit manual order:

- base stability
- quiesce panel/background
- business
- routing
- antishare
- start panel/background
- final checks

This explicit manual order takes precedence over helper wrappers.

## Hard Preservation Baseline

These are blocking invariants. If any of them regress after upgrade, treat it as failure:

- user count changed unexpectedly
- distinct UUID count changed
- duplicate UUID appeared
- `proxy_path` changed
- `proxy_path_admin` changed
- `proxy_path_client` changed
- domains disappeared
- Telegram-linked user count decreased
- `commercial_routing_custom_rule` count dropped unexpectedly
- `anti_share_config.nft_enabled` changed unexpectedly
- `anti_share_config.nft_dry_run` changed unexpectedly
- `anti_share_config.telegram_enabled` changed unexpectedly

## Soft Warnings Only

These must be reviewed, but do not block the in-place upgrade by themselves:

- `commercial_plan` count changed
- `commercial_subscription` count changed
- tariff names, prices, or status changed
- `anti_share_ip_profile` count changed
- `anti_share_event` count changed
- `anti_share_state` count changed

Reason:
- tariffs/subscriptions are not the primary preservation contract for this runbook
- they may drift on a live server after clone creation
- they may require manual post-upgrade adjustment without invalidating the upgrade itself

## Expected Pre-Upgrade Inputs

The following are normal for a pre-upgrade production server and are not blockers:

- no `routing-addon.manifest`
- no `commercial_routing_upstream`
- no `commercial_routing_rule_source`
- no `commercial_routing_installed`
- old-style routing config like `to-de`
- legacy `commercial_de_*` routing settings

The upgrade is expected to migrate this state into:

- `routing-addon.manifest`
- `commercial_routing_upstream`
- `commercial_routing_rule_source`
- `commercial_routing_installed=1`
- new router-core config with current routing logic

## Recommended Execution Order

This is the recommended dependency-layer order for production:

### A. Preflight and backup

1. `preflight-upgrade-audit.sh`
2. `backup-before-upgrade.sh`
3. `scripts/check-user-link-preservation.sh --before`

### B. Common/base stability

1. `service-tools/apply-base-stability.sh`
2. `service-tools/stabilize-celery-beat.sh`

### C. Maintenance quiesce

1. `systemctl stop hiddify-panel hiddify-panel-background-tasks`

Purpose:
- avoid Flask/SQLAlchemy metadata locks during `ALTER TABLE`
- avoid restarting panel against a partially upgraded schema/fileset
- keep `mariadb`, `hiddify-xray`, `hiddify-singbox`, `xray-router` running

### D. Business layer

1. `upgrade-installer/upgrade-business-layer.sh --defer-restart`

### E. Routing layer

1. `routing-installer/install-routing.sh --defer-restart`
2. `routing-installer/smoke-routing.sh`

### F. Antishare layer

1. `antishare-installer/install-antishare.sh --defer-restart`
2. `antishare-installer/smoke-antishare.sh --upgrade-existing-config`

### G. Bring panel back

1. `systemctl start hiddify-panel hiddify-panel-background-tasks`

### H. Final checks

1. `smoke-upgrade.sh`
2. `business-installer/smoke-business.sh --upgrade-existing-config`
3. `routing-installer/smoke-routing.sh`
4. `antishare-installer/smoke-antishare.sh --upgrade-existing-config`
5. `scripts/check-user-link-preservation.sh --after`
6. `scripts/check-user-link-preservation.sh --compare`
7. `scripts/compare-db-preservation.sh`

## Exact Manual Command Sequence

Run production manually in this exact order:

```bash
sudo bash preflight-upgrade-audit.sh
sudo bash backup-before-upgrade.sh
sudo bash scripts/check-user-link-preservation.sh --before

sudo bash ../service-tools/apply-base-stability.sh
sudo bash ../service-tools/stabilize-celery-beat.sh

sudo systemctl stop hiddify-panel hiddify-panel-background-tasks

sudo bash upgrade-business-layer.sh --defer-restart

sudo bash ../routing-installer/install-routing.sh --defer-restart
sudo bash ../antishare-installer/install-antishare.sh --defer-restart

sudo systemctl start hiddify-panel hiddify-panel-background-tasks

sudo bash smoke-upgrade.sh
sudo bash ../business-installer/smoke-business.sh --upgrade-existing-config
sudo bash ../routing-installer/smoke-routing.sh
sudo bash ../antishare-installer/smoke-antishare.sh --upgrade-existing-config

sudo bash scripts/check-user-link-preservation.sh --after
sudo bash scripts/check-user-link-preservation.sh --compare
sudo bash scripts/compare-db-preservation.sh
```

## Order Note

This runbook order differs from the first PASSED clone rehearsal order.

The first successful full rehearsal was executed in this order:
- routing
- antishare
- business

The final production order is now:
- base stability
- quiesce panel/background
- business
- routing
- antishare
- start panel/background
- final smoke and preservation

Business/routing/antishare are upgraded as files/schema first, while panel restart/readiness is deferred until after all schema-changing layers. This avoids mid-upgrade Hiddify domain/ACME/apply startup paths and reduces metadata-lock risk on production-like servers.

Since the first PASSED clone rehearsal used `routing -> antishare -> business` order, the final production order `base -> business -> routing -> antishare` must be revalidated on clone/snapshot before production execution.

## Preconditions Before Production Window

Required:

1. Provider snapshot or hypervisor snapshot
2. Full `hiddifypanel` DB dump
3. Runtime/config backup from `backup-before-upgrade.sh`
4. Final review of the exact upgrade runbook and fixed installer scripts

Recommended:

1. Fresh clone rehearsal from current production state
2. Final preflight audit immediately before the production window

## Monolithic Script Note

`upgrade-existing-stack.sh` is not the production source of truth.
Use the explicit manual order above for production.

If `upgrade-existing-stack.sh` is not independently confirmed to execute exactly:
- base stability
- quiesce panel/background
- business
- routing
- antishare
- start panel/background
- final checks

then do not use it in production.

## Post-Upgrade Acceptance

Production upgrade is accepted if:

- panel and background tasks are healthy
- router and anti-share services are healthy
- hard preservation baseline is intact
- smoke checks pass
- preservation compare passes with no hard failures

Allowed result:
- soft warnings on tariffs/subscriptions or anti-share telemetry

## Explicit Non-Goals

This runbook does not require:

- tariff counts to remain identical
- anti-share telemetry tables to remain identical
- old routing schema to remain unchanged

This runbook must not:

- reset user UUIDs
- break subscription links
- change proxy paths
- remove domains
- drop routing rules
- reset anti-share enforcement flags
- run `apply_configs.sh`
- run clean-install scripts on top of production
