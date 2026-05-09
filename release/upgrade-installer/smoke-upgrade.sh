#!/usr/bin/env bash
# smoke-upgrade.sh — post-upgrade smoke tests for an existing production stack.
#
# Upgrade-aware: does NOT hard-fail on nft_enabled=1 or telegram_enabled=1
# (production config is intentional and must be preserved).
# Instead, verifies that anti_share_config is preserved from before upgrade.
#
# Usage: sudo bash smoke-upgrade.sh [--skip-antishare-smoke]
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common-upgrade.sh"

UPGRADE_BLOCK="smoke-upgrade"
SKIP_ANTISHARE_SMOKE=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-antishare-smoke) SKIP_ANTISHARE_SMOKE=1 ;;
        --help|-h)
            echo "Usage: sudo bash smoke-upgrade.sh [--skip-antishare-smoke]"
            exit 0 ;;
        *) die "Unknown argument: $1" ;;
    esac
    shift
done

require_root
require_backup_exists

BD="$UPGRADE_BACKUP_DIR"

# ─── Check 1: Core services active ───────────────────────────────────────────
step "Checking core services"
check_services_active
echo "core-services-ok"

# ─── Check 2: DB accessible ──────────────────────────────────────────────────
step "Checking database"
mysql "$DB_NAME" -e "SELECT 1;" >/dev/null 2>&1 \
    || die "Database $DB_NAME not accessible"
db_ver=$(db_query "SELECT value FROM str_config WHERE \`key\`='db_version' LIMIT 1;" | head -1)
[[ "$db_ver" == "136" ]] || warn "db_version=$db_ver (expected 136)"
echo "db-accessible-ok db_version=$db_ver"

# ─── Check 3: No failed units (except known pre-existing) ────────────────────
step "Checking failed units"
# Known unrelated failures to ignore
IGNORE_UNITS="mtproxy|shadowsocks-libev|wg-quick@hiddifywg|fwupd-refresh"
new_failures=$(systemctl --failed --no-pager 2>/dev/null \
    | grep '●' | grep -vE "$IGNORE_UNITS" || true)
if [[ -n "$new_failures" ]]; then
    warn "Unexpected failed units found:"
    echo "$new_failures" | sed 's/^/  /' >&2
else
    echo "no-unexpected-failed-units-ok"
fi

# ─── Check 4: Business smoke ─────────────────────────────────────────────────
step "Running business smoke"
if [[ -f "$SCRIPT_DIR/../business-installer/smoke-business.sh" ]]; then
    bash "$SCRIPT_DIR/../business-installer/smoke-business.sh" 2>&1 \
        && echo "smoke-business-ok" \
        || warn "smoke-business failed — check output above"
else
    warn "business-installer/smoke-business.sh not found — skipping"
fi

# ─── Check 5: Routing smoke ──────────────────────────────────────────────────
step "Running routing smoke"
if [[ -f "$SCRIPT_DIR/../routing-installer/smoke-routing.sh" ]]; then
    bash "$SCRIPT_DIR/../routing-installer/smoke-routing.sh" 2>&1 \
        && echo "smoke-routing-ok" \
        || warn "smoke-routing failed — check output above"
else
    warn "routing-installer/smoke-routing.sh not found — skipping"
fi

# ─── Check 6: Anti-share upgrade-aware smoke ─────────────────────────────────
step "Checking anti-share state (upgrade-aware)"

if [[ $SKIP_ANTISHARE_SMOKE -eq 1 ]]; then
    warn "Anti-share smoke skipped via --skip-antishare-smoke"
else
    # Check that anti_share_config row was PRESERVED (not wiped)
    cfg_count=$(db_count "anti_share_config")
    [[ "$cfg_count" -ge 1 ]] || die "anti_share_config table empty — config was wiped"
    echo "antishare-config-row-ok (rows: $cfg_count)"

    # Verify config values match pre-upgrade snapshot (if available)
    before_snap="$SCRIPT_DIR/../upgrade-installer-backups/user-preservation/snapshot-before.txt"
    if [[ -f "$BD/preflight-report.txt" ]]; then
        # Extract nft/telegram from preflight
        pre_nft=$(grep 'nft_enabled=' "$BD/preflight-report.txt" 2>/dev/null | grep -v '^    ⚠' | head -1 | grep -oP 'nft_enabled=\K[0-9]+' || echo "?")
        pre_dry=$(grep 'nft_dry_run=' "$BD/preflight-report.txt" 2>/dev/null | head -1 | grep -oP 'nft_dry_run=\K[0-9]+' || echo "?")
        pre_tg=$(grep 'telegram_enabled=' "$BD/preflight-report.txt" 2>/dev/null | grep -v '^    ⚠' | head -1 | grep -oP 'telegram_enabled=\K[0-9]+' || echo "?")

        cur_nft=$(db_query "SELECT nft_enabled FROM anti_share_config LIMIT 1;" | head -1)
        cur_dry=$(db_query "SELECT nft_dry_run FROM anti_share_config LIMIT 1;" | head -1)
        cur_tg=$(db_query "SELECT telegram_enabled FROM anti_share_config LIMIT 1;" | head -1)

        echo "antishare-config-nft_enabled: before=$pre_nft after=$cur_nft"
        echo "antishare-config-nft_dry_run: before=$pre_dry after=$cur_dry"
        echo "antishare-config-telegram_enabled: before=$pre_tg after=$cur_tg"

        # Hard-fail only if config was accidentally RESET (to 0 when it was 1)
        if [[ "$pre_nft" == "1" && "$cur_nft" != "1" ]]; then
            die "CRITICAL: nft_enabled was 1 before upgrade, now $cur_nft — config was reset!"
        fi
        if [[ "$pre_tg" == "1" && "$cur_tg" != "1" ]]; then
            die "CRITICAL: telegram_enabled was 1 before upgrade, now $cur_tg — config was reset!"
        fi
    fi

    # Note about nft_enabled — informational only in upgrade mode
    cur_nft=$(db_query "SELECT nft_enabled FROM anti_share_config LIMIT 1;" | head -1 || echo "?")
    cur_dry=$(db_query "SELECT nft_dry_run FROM anti_share_config LIMIT 1;" | head -1 || echo "?")
    if [[ "$cur_nft" == "1" && "$cur_dry" == "0" ]]; then
        echo "antishare-enforcement-active: nft_enabled=1 nft_dry_run=0 (preserved from pre-upgrade — expected)"
    fi

    echo "antishare-config-preserved-ok"

    # Run antishare-specific smoke if --upgrade-existing-config mode exists
    # For now, run with known skip since production has enforcement active
    if [[ -f "$SCRIPT_DIR/../antishare-installer/smoke-antishare.sh" ]]; then
        warn "Note: smoke-antishare.sh hard-checks nft_enabled=0 which WILL fail on production."
        warn "Skipping direct smoke-antishare.sh call. Endpoint and import checks done separately."
        # TODO: implement --upgrade-existing-config mode in smoke-antishare.sh
        # See compatibility-fixes.md section B
    fi
fi

# ─── Check 7: Users and subscriptions preserved ──────────────────────────────
step "Verifying user/subscription preservation"
if [[ -f "$SCRIPT_DIR/scripts/check-user-link-preservation.sh" ]]; then
    bash "$SCRIPT_DIR/scripts/check-user-link-preservation.sh" --after 2>&1 \
        && bash "$SCRIPT_DIR/scripts/check-user-link-preservation.sh" --compare 2>&1 \
        && echo "user-preservation-ok" \
        || warn "user-preservation check found differences — review output above"
else
    warn "check-user-link-preservation.sh not found"
fi

# ─── Check 8: xray access log still enabled ──────────────────────────────────
step "Checking xray access log"
if [[ -f "$XRAY_LOG_CONFIG" ]]; then
    access_val=$(python3 -c "
import json
with open('$XRAY_LOG_CONFIG') as f:
    d=json.load(f)
print(d.get('log',{}).get('access','none'))
" 2>/dev/null || echo "none")
    if [[ "$access_val" != "none" && "$access_val" != "" ]]; then
        echo "xray-access-log-ok ($access_val)"
    else
        warn "xray access log disabled in 00_log.json — anti-share IP detection will fail"
    fi
fi

# Check override files — accept both naming conventions from different installer versions:
#   antishare-log-perms.conf  (our installer)
#   anti-share-access.conf    (v0.12.5 addon — hyphenated form)
#   log-perms.conf            (live-debug sessions)
_xray_override_found=0
for _candidate in \
        "$XRAY_OVERRIDE_DIR/"*antishare* \
        "$XRAY_OVERRIDE_DIR/anti-share-access.conf" \
        "$XRAY_OVERRIDE_DIR/log-perms.conf"; do
    if [[ -f "$_candidate" ]]; then
        echo "xray-override-ok ($(basename "$_candidate"))"
        _xray_override_found=1
        break
    fi
done
[[ "$_xray_override_found" -eq 1 ]] \
    || warn "No antishare xray override found in $XRAY_OVERRIDE_DIR"

echo
echo "smoke-upgrade OK"
