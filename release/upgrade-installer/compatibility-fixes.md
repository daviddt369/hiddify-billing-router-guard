# Compatibility Fixes Required Before Production Upgrade

Discovered during production clone audit (2026-05-09).
These are changes needed in existing installer scripts before running upgrade on production.
Status: IDENTIFIED, NOT YET IMPLEMENTED.

---

## Fix A — service-tools/apply-base-stability.sh
### Issue
Production server uses `IDENT_ME_TIMEOUT = 2` constant in net.py (line 29).
Current `apply-base-stability.sh` only checks for `timeout=5` literal to detect
already-patched state. When not found, the script attempts to inject `,timeout=5`
into lines that already have `timeout=IDENT_ME_TIMEOUT` — this would produce invalid
syntax: `urlopen(f'...', timeout=IDENT_ME_TIMEOUT, timeout=5)`.

### Production symptom
net.py at `/opt/hiddify-manager/.venv313/.../hutils/network/net.py`:
```python
IDENT_ME_TIMEOUT = 2        # line 29
...urlopen(f'...ident.me/', timeout=IDENT_ME_TIMEOUT)...
```
apply-base-stability.sh would see "no timeout=5" and try to patch → corrupt file.

### Fix needed in apply-base-stability.sh
Change the already-patched detection from:
```bash
if grep -q "ident\.me.*timeout=5\|timeout=5.*ident\.me" "$NET_PY" 2>/dev/null; then
```
To:
```bash
if grep -q "ident\.me.*timeout=\|timeout=.*IDENT_ME" "$NET_PY" 2>/dev/null; then
```
Or more specifically: detect ANY existing timeout on ident.me urlopen calls.

### Risk
HIGH — current script would corrupt net.py on production servers using IDENT_ME_TIMEOUT.

---

## Fix B — antishare-installer/smoke-antishare.sh
### Issue
smoke-antishare.sh Check 8 hard-fails if `nft_enabled != 0` or `nft_dry_run != 1`.
Production server has `nft_enabled=1, nft_dry_run=0` (enforcement active, intentional).
Running smoke-antishare.sh on production would always fail Check 8.

### Fix needed in smoke-antishare.sh
Add `--upgrade-existing-config` flag that:
- Skips safe-defaults hard-check (nft_enabled, nft_dry_run, telegram_enabled)
- Instead, verifies that the config VALUES were preserved from before upgrade
- Reports current state as info, not die

Example flag addition:
```bash
UPGRADE_MODE=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --upgrade-existing-config) UPGRADE_MODE=1 ;;
    esac
done
```

In Check 8:
```bash
if [[ $UPGRADE_MODE -eq 0 ]]; then
    # clean install — enforce safe defaults
    [[ "$nft_enabled" == "0" ]] || die "SAFE-DEFAULT VIOLATION..."
    [[ "$nft_dry_run" == "1" ]] || die ...
else
    # upgrade — just report, don't enforce
    echo "upgrade-mode: nft_enabled=$nft_enabled nft_dry_run=$nft_dry_run telegram_enabled=$telegram_enabled (preserved)"
fi
```

### Risk
MEDIUM — smoke-antishare.sh unusable on production without this fix.

---

## Fix C — antishare-installer/install-antishare.sh + DB migration
### Issue
Need to confirm that existing `anti_share_config` row is NOT overwritten by upgrade.
Current DB migration Step 9 seeds with:
```sql
INSERT INTO anti_share_config (...)
SELECT ...
WHERE NOT EXISTS (SELECT 1 FROM anti_share_config LIMIT 1);
```
This correctly skips seeding if a row exists. ✓

However, install-antishare.sh should add explicit log message when config row preserved.

### Fix needed
Add to DB migration after seed step:
```bash
if [[ "$cfg_count" -ge 1 ]]; then
    # Verify nft/telegram settings were not reset
    nft_val=$(mysql ... "SELECT nft_enabled FROM anti_share_config LIMIT 1;")
    log "anti_share_config preserved (nft_enabled=$nft_val) — safe defaults NOT applied on upgrade"
fi
```

### Risk
LOW — current code already does the right thing. Fix is documentation/logging only.

---

## Fix D — antishare-installer/common-antishare.sh (xray override cleanup)
### Issue
`install_xray_log_permissions_override()` currently only removes `log-perms.conf`
as legacy file. Production server has `anti-share-access.conf` — a more sophisticated
override with proper file creation and permissions handling.

Current cleanup code:
```bash
local legacy_file="$XRAY_OVERRIDE_DIR/log-perms.conf"
if [[ -f "$legacy_file" ...]]; then
    if grep -q 'chmod 644.*xray.access.log' "$legacy_file" ...
```

This does NOT handle `anti-share-access.conf` from the v0.12.5 addon.

Production `anti-share-access.conf` is BETTER than our `antishare-log-perms.conf`:
- Creates log file if missing (install -o root -g hiddify-panel -m 0640)
- Creates state.json if missing
- Uses 0640 permissions (group-readable, more secure than 644)

### Fix needed
Options:
1. Also check for `anti-share-access.conf` as a known legacy file that can coexist
2. Recognize it as compatible and skip writing our simpler version
3. Adopt the production version's approach (better permissions model)

Recommended: Check if ANY existing override already handles xray.access.log permissions.
If found, skip creating `antishare-log-perms.conf` and just log a warning:
```bash
local existing_override
existing_override=$(grep -rl 'xray.access.log' "$XRAY_OVERRIDE_DIR"/ 2>/dev/null | head -1 || true)
if [[ -n "$existing_override" && "$existing_override" != "$XRAY_LOG_OVERRIDE_FILE" ]]; then
    warn "Existing xray log override found at $existing_override — skipping creation of $XRAY_LOG_OVERRIDE_FILE"
    return 0
fi
```

### Risk
LOW — two overrides coexist harmlessly. But our simpler `chmod 644` could
theoretically downgrade security from `0640` to `0644` on production.

---

## Fix E — routing-installer/scripts/commercial-routing-db-migrate.sh
### Issue
Verify that the routing DB migration is safe when:
1. `commercial_routing_custom_rule` already exists with DIFFERENT schema
   (production uses HASH for UNIQUE KEY, no source_id column)
2. `commercial_routing_upstream` and `commercial_routing_rule_source` don't exist
3. Existing custom rules must NOT be lost when source_id column is added

### Current migration behavior (Steps 3, 7, 10, 13):
- Step 3: `CREATE TABLE IF NOT EXISTS commercial_routing_custom_rule` — skipped if exists ✓
- Step 7: `CREATE TABLE IF NOT EXISTS commercial_routing_upstream` — creates new ✓
- Step 10: `CREATE TABLE IF NOT EXISTS commercial_routing_rule_source` — creates new ✓
- Step 13: ADD COLUMN source_id IF NOT EXISTS — idempotent via INFORMATION_SCHEMA check ✓
- Step 9: Upstream seed from legacy — only if `upstream_count == 0` ✓

### Risk areas
- Step 13 ALTER TABLE adds source_id FK to commercial_routing_rule_source.
  On production, commercial_routing_rule_source doesn't exist YET at this point
  (created in Step 10, which runs before Step 13).
  So FK should resolve. Safe ✓

- UNIQUE KEY schema difference on custom_rule table:
  Production has: `UNIQUE KEY (rule_type, normalized_value) USING HASH`
  Release expects: `UNIQUE KEY (rule_type, normalized_value(255))`
  ALTER TABLE in Step 13 does not touch the UNIQUE KEY — only adds source_id. ✓

- Existing custom rules: ALTER TABLE ADD COLUMN is non-destructive. ✓

### Recommendation
Migration is SAFE as-is for production schema. Add explicit pre-check:
```bash
log "Checking existing commercial_routing_custom_rule schema"
existing_rows=$(mysql ... "SELECT COUNT(*) FROM commercial_routing_custom_rule;")
log "Existing custom rules: $existing_rows (will be preserved)"
```

### Risk
LOW — migration is correct. Logging improvement only needed.

---

## Implementation priority

| Fix | Risk if not applied | Priority |
|-----|---------------------|----------|
| A (apply-base-stability) | HIGH — corrupts net.py | Before running upgrade |
| B (smoke safe-defaults) | MEDIUM — smoke unusable on production | Before running smoke |
| C (antishare DB migration logging) | LOW — code correct, logging only | Nice to have |
| D (xray override coexistence) | LOW — harmless coexistence | Before production |
| E (routing migration pre-check) | LOW — migration correct | Nice to have |
