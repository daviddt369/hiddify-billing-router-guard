#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common-antishare.sh"

main() {
    INSTALL_BLOCK="antishare-diagnostics"
    require_root

    local out_dir
    out_dir="$INSTALL_ROOT/antishare-diagnostics-$(date +%F-%H%M%S)"
    mkdir -p "$out_dir"
    step "Writing diagnostics to: $out_dir"

    # Service state
    systemctl status "$SERVICE_PANEL" "$SERVICE_BG" --no-pager \
        > "$out_dir/panel-status.txt" 2>&1 || true
    systemctl status "$ANTISHARE_SERVICE" "$ANTISHARE_TIMER" --no-pager \
        > "$out_dir/antishare-status.txt" 2>&1 || true
    systemctl is-enabled "$ANTISHARE_TIMER" \
        > "$out_dir/antishare-timer-enabled.txt" 2>&1 || true

    # Ports
    ss -lntp > "$out_dir/ss-lntp.txt" 2>&1 || true

    # Sudoers check
    {
        echo "=== sudoers file ==="
        cat "$SUDOERS_FILE" 2>/dev/null || echo "NOT PRESENT: $SUDOERS_FILE"
    } > "$out_dir/sudoers.txt"

    # nft helper
    {
        echo "=== nft helper ==="
        ls -la "$NFT_HELPER_PATH" 2>/dev/null || echo "NOT PRESENT: $NFT_HELPER_PATH"
    } > "$out_dir/nft-helper.txt"

    # DB anti-share tables
    {
        echo "=== anti_share_config ==="
        mysql "$DB_NAME" -e \
            "SELECT id, enabled, nft_enabled, nft_dry_run, telegram_enabled, \
                    window_seconds, learning_days, ban_seconds \
             FROM anti_share_config LIMIT 1;" \
            2>/dev/null || echo "table may not exist"

        echo ""
        echo "=== anti_share tables row counts ==="
        for tbl in anti_share_config anti_share_state anti_share_ip_profile \
                   anti_share_event anti_share_user_override; do
            cnt="$(mysql "$DB_NAME" -N -B -e "SELECT COUNT(*) FROM $tbl;" 2>/dev/null || echo 'N/A')"
            echo "$tbl: $cnt rows"
        done

        echo ""
        echo "=== anti_share_state distribution ==="
        mysql "$DB_NAME" -e \
            "SELECT state, COUNT(*) AS cnt FROM anti_share_state GROUP BY state;" \
            2>/dev/null || echo "table may not exist"

        echo ""
        echo "=== recent anti_share_event (last 20) ==="
        mysql "$DB_NAME" -e \
            "SELECT id, user_id, event_type, state_before, state_after, score_before, score_after, \
                    created_at \
             FROM anti_share_event ORDER BY id DESC LIMIT 20;" \
            2>/dev/null || echo "table may not exist"

        echo ""
        echo "=== commercial_antishare_installed flag ==="
        mysql "$DB_NAME" -e \
            "SELECT child_id, \`key\`, value FROM str_config \
             WHERE child_id=0 AND \`key\`='commercial_antishare_installed';" \
            2>/dev/null || echo "mysql error"

        echo ""
        echo "=== SHOW CREATE TABLE anti_share_state ==="
        mysql "$DB_NAME" -e "SHOW CREATE TABLE anti_share_state\G" \
            2>/dev/null || echo "table may not exist"
    } > "$out_dir/db-antishare.txt"

    # Manifest
    cp "$MANIFEST_PATH" "$out_dir/antishare-manifest.txt" 2>/dev/null \
        || echo "not present" > "$out_dir/antishare-manifest.txt"

    # Xray access log (info only)
    {
        echo "=== xray access log ==="
        ls -la /opt/hiddify-manager/log/system/xray.access.log 2>/dev/null \
            || echo "NOT PRESENT (no traffic yet)"
        echo ""
        echo "=== last 20 lines of xray access log ==="
        tail -20 /opt/hiddify-manager/log/system/xray.access.log 2>/dev/null \
            || echo "log not present"
    } > "$out_dir/xray-access-log.txt"

    # Journal
    journalctl -u "$ANTISHARE_SERVICE" -n 60 --no-pager \
        > "$out_dir/antishare-journal.txt" 2>&1 || true
    journalctl -u "$SERVICE_PANEL" -n 60 --no-pager \
        > "$out_dir/panel-journal.txt" 2>&1 || true

    step "Diagnostics written to: $out_dir"
    echo "collect-antishare-diagnostics OK: $out_dir"
}

main "$@"
