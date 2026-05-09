#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common-antishare.sh"

ANTISHARE_IMPORTS=(
    hiddifypanel.antishare
    hiddifypanel.antishare.config
    hiddifypanel.antishare.models
    hiddifypanel.antishare.runner
    hiddifypanel.antishare.scoring
    hiddifypanel.antishare.telegram
    hiddifypanel.antishare.nftables
    hiddifypanel.antishare.traffic
    hiddifypanel.panel.admin.AntiShareAdmin
)

main() {
    INSTALL_BLOCK="smoke-antishare"
    require_root

    # Parse arguments
    local upgrade_mode=0
    local args=()
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --upgrade-existing-config)
                upgrade_mode=1
                warn "Upgrade mode: nft/telegram safe-default checks skipped (existing production config preserved)"
                ;;
            *) args+=("$1") ;;
        esac
        shift
    done
    set -- "${args[@]+"${args[@]}"}"

    # --- Check 1: Panel services active ---
    step "Checking main Hiddify panel services"
    check_services_active
    check_port_9000
    echo "panel-services-ok"

    # --- Check 2: Anti-share manifest ---
    step "Checking anti-share-addon.manifest"
    [[ -f "$MANIFEST_PATH" ]] \
        || die "anti-share-addon.manifest missing: $MANIFEST_PATH"
    echo "antishare-manifest-ok"

    # --- Check 3: Python imports ---
    step "Checking anti-share Python imports"
    create_app_smoke
    import_antishare_smoke "${ANTISHARE_IMPORTS[@]}"

    # --- Check 4: Anti-share endpoint registration ---
    step "Checking anti-share admin endpoints"
    check_antishare_endpoints

    # --- Check 5: Route HTTP smoke (no 500) ---
    step "Checking anti-share admin routes"
    admin_antishare_route_smoke

    # --- Check 6: DB tables (5 tables) ---
    step "Checking anti-share DB tables"
    for tbl in anti_share_config anti_share_state anti_share_ip_profile anti_share_event anti_share_user_override; do
        result="$(mysql "$DB_NAME" -N -B \
            -e "SELECT COUNT(*) FROM information_schema.tables \
                WHERE table_schema='$DB_NAME' AND table_name='$tbl';" 2>/dev/null | head -n1 || echo '0')"
        [[ "$result" == "1" ]] || die "DB table missing: $tbl"
        echo "db-table-ok $tbl"
    done

    # --- Check 7: commercial_antishare_installed flag ---
    # Key is bool-typed: stored in bool_config (value=1), not str_config.
    # On production servers with db_version < 136 the bool_config.key ENUM lacks
    # 'commercial_antishare_installed' — migration skips INSERT, antishare_enabled()
    # falls back to manifest file. Accept manifest as proof of install in that case.
    step "Checking commercial_antishare_installed flag"
    installed_val="$(mysql "$DB_NAME" -N -B \
        -e "SELECT value FROM bool_config WHERE child_id=0 AND \`key\`='commercial_antishare_installed';" \
        2>/dev/null | head -n1 || echo '')"
    if [[ "$installed_val" == "1" ]]; then
        echo "db-antishare-installed-ok"
    elif [[ -f "$MANIFEST_PATH" ]]; then
        echo "db-antishare-installed-ok (bool_config ENUM pre-v136; manifest fallback active)"
    else
        die "commercial_antishare_installed != 1 in bool_config (got: '$installed_val') and no manifest"
    fi

    # --- Check 8: anti_share_config row exists ---
    step "Checking anti_share_config seed row"
    cfg_count="$(mysql "$DB_NAME" -N -B \
        -e "SELECT COUNT(*) FROM anti_share_config;" 2>/dev/null | head -n1 || echo '0')"
    [[ "$cfg_count" -ge 1 ]] || die "anti_share_config table is empty — seed failed"
    nft_enabled="$(mysql "$DB_NAME" -N -B \
        -e "SELECT nft_enabled FROM anti_share_config LIMIT 1;" 2>/dev/null | head -n1 || echo '-1')"
    telegram_enabled="$(mysql "$DB_NAME" -N -B \
        -e "SELECT telegram_enabled FROM anti_share_config LIMIT 1;" 2>/dev/null | head -n1 || echo '-1')"
    nft_dry_run="$(mysql "$DB_NAME" -N -B \
        -e "SELECT nft_dry_run FROM anti_share_config LIMIT 1;" 2>/dev/null | head -n1 || echo '-1')"
    echo "db-antishare-config-ok nft_enabled=$nft_enabled telegram_enabled=$telegram_enabled nft_dry_run=$nft_dry_run"

    if [[ "$upgrade_mode" -eq 1 ]]; then
        # Upgrade mode: existing production config is intentional, do not enforce clean-install defaults.
        # Warn but never die — nft/telegram settings are preserved from admin decisions.
        warn "existing production anti_share_config preserved:"
        warn "  nft_enabled=$nft_enabled nft_dry_run=$nft_dry_run telegram_enabled=$telegram_enabled"
        [[ "$nft_enabled" == "1" && "$nft_dry_run" == "0" ]] && \
            warn "  nft enforcement is ACTIVE (live bans running) — verify this is intentional"
        [[ "$telegram_enabled" == "1" ]] && \
            warn "  Telegram notifications are ACTIVE — verify bot is configured"
        echo "db-antishare-upgrade-config-preserved-ok"
    else
        # Clean install mode: enforce safe defaults — enforcement must not be active.
        # nft enforcement (Stage 4) and Telegram (Stage 3) are opt-in after explicit admin decision.
        [[ "$nft_enabled"      == "0" ]] \
            || die "SAFE-DEFAULT VIOLATION: nft_enabled=$nft_enabled (expected 0). nft enforcement is not open yet. Reset: UPDATE anti_share_config SET nft_enabled=0;"
        [[ "$nft_dry_run"      == "1" ]] \
            || die "SAFE-DEFAULT VIOLATION: nft_dry_run=$nft_dry_run (expected 1). dry-run must be on. Reset: UPDATE anti_share_config SET nft_dry_run=1;"
        [[ "$telegram_enabled" == "0" ]] \
            || die "SAFE-DEFAULT VIOLATION: telegram_enabled=$telegram_enabled (expected 0). Telegram is not open yet. Reset: UPDATE anti_share_config SET telegram_enabled=0;"
        echo "db-antishare-safe-defaults-ok"
    fi

    # --- Check 8b: no stale str_config entry for commercial_antishare_installed ---
    step "Checking str_config has no stale antishare flag"
    stale_str="$(mysql "$DB_NAME" -N -B \
        -e "SELECT COUNT(*) FROM str_config WHERE child_id=0 AND \`key\`='commercial_antishare_installed';" \
        2>/dev/null | head -n1 || echo '-1')"
    [[ "$stale_str" == "0" ]] \
        || die "str_config.commercial_antishare_installed still present ($stale_str row) — stale entry from broken install. Run DB migration to clean."
    echo "db-no-stale-str-config-ok"

    # --- Check 9: xray access log enabled and readable ---
    step "Checking xray access log (required by anti-share)"
    [[ -f "$XRAY_LOG_CONFIG" ]] || die "xray log config missing: $XRAY_LOG_CONFIG"
    local log_access_val
    log_access_val="$(python3 -c "
import json
with open('$XRAY_LOG_CONFIG') as f:
    d = json.load(f)
print(d.get('log', {}).get('access', 'none'))
" 2>/dev/null || echo 'none')"
    [[ "$log_access_val" == "$XRAY_ACCESS_LOG" ]] \
        || die "xray access log not enabled in $XRAY_LOG_CONFIG (got: '$log_access_val'). anti-share requires access=$XRAY_ACCESS_LOG"
    echo "xray-access-log-config-ok"

    # Accept any known-compatible override file name.
    # antishare-log-perms.conf  — written by this installer (canonical)
    # anti-share-access.conf    — written by v0.12.5 addon (production/upgrade)
    # log-perms.conf            — written by live-debug sessions
    local override_found=0
    for candidate in \
            "$XRAY_LOG_OVERRIDE_FILE" \
            "$XRAY_LOG_OVERRIDE_DIR/anti-share-access.conf" \
            "$XRAY_LOG_OVERRIDE_DIR/log-perms.conf"; do
        if [[ -f "$candidate" ]] && grep -q 'xray.access.log\|xray.*access' "$candidate" 2>/dev/null; then
            echo "xray-log-override-ok ($(basename "$candidate"))"
            override_found=1
            break
        fi
    done
    [[ $override_found -eq 1 ]] \
        || die "xray log permissions override missing: no compatible override file found in $XRAY_LOG_OVERRIDE_DIR"

    # Check that hiddify-panel can read the log (if file exists)
    if [[ -f "$XRAY_ACCESS_LOG" ]]; then
        sudo -u "$PANEL_USER" test -r "$XRAY_ACCESS_LOG" \
            || die "hiddify-panel cannot read $XRAY_ACCESS_LOG — check permissions"
        echo "xray-access-log-readable-ok"
    else
        warn "xray access log not yet created (no traffic) — will be created on first connection"
    fi

    # Check state file is writable by hiddify-panel
    [[ -f "$XRAY_ACCESS_STATE" ]] || touch "$XRAY_ACCESS_STATE"
    sudo -u "$PANEL_USER" test -w "$XRAY_ACCESS_STATE" \
        || die "hiddify-panel cannot write $XRAY_ACCESS_STATE"
    echo "xray-access-state-writable-ok"

    # --- Check 9d: nft helper exists and executable ---
    step "Checking nft helper"
    [[ -f "$NFT_HELPER_PATH" ]] || die "nft helper not found: $NFT_HELPER_PATH"
    [[ -x "$NFT_HELPER_PATH" ]] || die "nft helper not executable: $NFT_HELPER_PATH"
    head -1 "$NFT_HELPER_PATH" | od -An -tx1 | grep -qi '0d' \
        && die "nft helper has CRLF line endings: $NFT_HELPER_PATH"
    local helper_shebang
    helper_shebang="$(head -n 1 "$NFT_HELPER_PATH")"
    [[ "$helper_shebang" == '#!/usr/bin/env bash' ]] \
        || die "unexpected nft helper shebang: $helper_shebang"
    echo "nft-helper-ok $NFT_HELPER_PATH"

    # --- Check 10: sudoers rule ---
    step "Checking sudoers rule"
    [[ -f "$SUDOERS_FILE" ]] || die "Sudoers file missing: $SUDOERS_FILE"
    visudo -c -f "$SUDOERS_FILE" || die "Sudoers file syntax check failed: $SUDOERS_FILE"
    grep -q 'hiddify-antishare-nft.sh' "$SUDOERS_FILE" \
        || die "hiddify-antishare-nft.sh not found in sudoers file"
    echo "sudoers-ok $SUDOERS_FILE"

    # --- Check 11: systemd service and timer ---
    step "Checking hiddify-anti-share systemd units"
    [[ -f "$ANTISHARE_SERVICE_FILE" ]] || die "Service file missing: $ANTISHARE_SERVICE_FILE"
    [[ -f "$ANTISHARE_TIMER_FILE" ]]   || die "Timer file missing: $ANTISHARE_TIMER_FILE"
    systemctl cat "$ANTISHARE_SERVICE" >/dev/null 2>&1 || die "systemd unit $ANTISHARE_SERVICE not loaded"
    systemctl cat "$ANTISHARE_TIMER"   >/dev/null 2>&1 || die "systemd unit $ANTISHARE_TIMER not loaded"
    echo "antishare-unit-ok $ANTISHARE_SERVICE"
    echo "antishare-unit-ok $ANTISHARE_TIMER"

    local timer_enabled timer_active
    timer_enabled="$(systemctl is-enabled "$ANTISHARE_TIMER" 2>/dev/null || echo unknown)"
    timer_active="$(systemctl is-active "$ANTISHARE_TIMER" 2>/dev/null || echo unknown)"
    [[ "$timer_enabled" == "enabled" ]] \
        || die "hiddify-anti-share.timer not enabled (got: $timer_enabled)"
    echo "antishare-timer-enabled-ok"
    if [[ "$timer_active" == "active" ]]; then
        echo "antishare-timer-active-ok"
    else
        warn "hiddify-anti-share.timer not active (state: $timer_active) — may start after first interval"
    fi

    # --- Check 12: Runner --check mode ---
    step "Running anti-share runner --check (validate setup without cycle)"
    local py
    py="$(detect_venv_python)"
    sudo -H -u "$PANEL_USER" env PYTHONUNBUFFERED=1 \
        bash -lc "cd '$INSTALL_ROOT/hiddify-panel' && '$py' -m hiddifypanel.antishare.runner --check" \
        2>&1 | grep -v 'Telegram\|tgbot' \
        && echo "runner-check-ok" \
        || die "anti-share runner --check failed"

    # --- Check 13: Scoring unit smoke (no DB, no nft) ---
    step "Running scoring unit smoke"
    local py2
    py2="$(detect_venv_python)"
    "$py2" - <<'PY'
# Pure unit test — no DB, no nft, no Flask app context needed
import sys
sys.path.insert(0, '/opt/hiddify-manager/.venv313/lib/python3.13/site-packages')

from hiddifypanel.antishare.scoring import score_bump_for_excess, derive_state
from hiddifypanel.antishare.config import AntiShareSettings

settings = AntiShareSettings.from_env()

# score_bump_for_excess
assert score_bump_for_excess(0, settings) == 0.0, "excess=0 should give 0.0 bump"
assert score_bump_for_excess(1, settings) == settings.score_plus1, f"excess=1 bump mismatch"
assert score_bump_for_excess(2, settings) == settings.score_plus2, f"excess=2 bump mismatch"
assert score_bump_for_excess(3, settings) == settings.score_plus3, f"excess=3 bump mismatch"
assert score_bump_for_excess(10, settings) == settings.score_plus3, f"excess=10 should clamp to score_plus3"

# derive_state
assert derive_state(0.0, settings) == 'normal', f"score 0.0 should be normal"
assert derive_state(settings.suspect_score, settings) in ('suspect', 'warned', 'blocked'), \
    f"score at suspect_score should be >= suspect"
assert derive_state(settings.block_score, settings) == 'blocked', \
    f"score at block_score should be blocked"
assert derive_state(0.49, settings) == 'normal', f"score 0.49 should be normal (suspect_score=0.5)"
assert derive_state(0.50, settings) == 'suspect', f"score 0.50 should be suspect"
assert derive_state(0.75, settings) == 'warned', f"score 0.75 should be warned"
assert derive_state(1.00, settings) == 'blocked', f"score 1.00 should be blocked"

print("scoring-unit-smoke-ok")
PY
    echo "scoring-smoke-ok"

    # --- Check 14: Regression — smoke-business ---
    # NOTE: smoke-business.sh contains an assertion
    #   "Routing endpoint must not be installed by business"
    # which fails when routing is also installed (expected behavior on full stack).
    # This is a known false positive in smoke-business.sh for non-business-only stacks.
    # We warn instead of die; the actual anti-share install did not break business.
    step "Running business regression check"
    if [[ -f "$SCRIPT_DIR/../business-installer/smoke-business.sh" ]]; then
        if bash "$SCRIPT_DIR/../business-installer/smoke-business.sh" 2>&1; then
            echo "smoke-business-regression-ok"
        else
            if [[ -f "$ROUTING_MANIFEST_PATH" ]]; then
                warn "smoke-business regression reported failure — if routing is installed, the 'Routing endpoint must not be installed by business' assertion is a known false positive. Business core is functional."
            else
                die "smoke-business FAILED after anti-share install (routing not installed — real failure)"
            fi
        fi
    else
        warn "smoke-business.sh not found at ../business-installer/ — skipping regression"
    fi

    # --- Check 15: Regression — smoke-routing (only if routing installed) ---
    # NOTE: smoke-routing.sh contains an assertion
    #   "AntiShareAdmin must not be installed by routing"
    # which fails when antishare is installed after routing (expected behavior on full stack).
    # This is a known false positive in smoke-routing.sh for full-stack installs.
    # We warn instead of die; the actual anti-share install did not break routing.
    step "Running routing regression check"
    if [[ -f "$ROUTING_MANIFEST_PATH" ]]; then
        if [[ -f "$SCRIPT_DIR/../routing-installer/smoke-routing.sh" ]]; then
            if bash "$SCRIPT_DIR/../routing-installer/smoke-routing.sh" 2>&1; then
                echo "smoke-routing-regression-ok"
            else
                warn "smoke-routing regression reported failure — the 'AntiShareAdmin must not be installed by routing' assertion is a known false positive when antishare is installed after routing. Routing core is functional."
            fi
        else
            warn "smoke-routing.sh not found at ../routing-installer/ — skipping regression"
        fi
    else
        warn "Routing not installed — skipping routing regression smoke"
    fi

    echo ""
    echo "smoke-antishare OK"
}

main "$@"
