#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common-routing.sh"

main() {
    INSTALL_BLOCK="routing-diagnostics"
    require_root

    local out_dir
    out_dir="$INSTALL_ROOT/routing-diagnostics-$(date +%F-%H%M%S)"
    mkdir -p "$out_dir"
    step "Writing diagnostics to: $out_dir"

    # Service state
    systemctl status "$SERVICE_PANEL" "$SERVICE_BG" "$XRAY_ROUTER_SERVICE" --no-pager \
        > "$out_dir/systemctl-status.txt" 2>&1 || true
    systemctl is-enabled "$XRAY_ROUTER_SERVICE" \
        > "$out_dir/xray-router-enabled.txt" 2>&1 || true

    # Ports
    ss -lntp > "$out_dir/ss-lntp.txt" 2>&1 || true

    # xray-router config (if present)
    if [[ -f /etc/xray-router/config.json ]]; then
        cp /etc/xray-router/config.json "$out_dir/xray-router-config.json" || true
    else
        echo "not present" > "$out_dir/xray-router-config.json"
    fi

    # commander.py routing patch check
    {
        echo "=== ROUTING_INSTALL marker ==="
        grep -n 'ROUTING_INSTALL' "$COMMANDER_PATH" 2>/dev/null || echo "NOT PATCHED"
    } > "$out_dir/commander-check.txt"

    # Sudoers
    {
        echo "=== sudoers file ==="
        cat "$SUDOERS_FILE" 2>/dev/null || echo "NOT PRESENT: $SUDOERS_FILE"
    } > "$out_dir/sudoers.txt"

    # DB routing config
    {
        echo "=== bool_config routing keys ==="
        mysql "$DB_NAME" -e \
            "SELECT \`key\`, value FROM bool_config WHERE child_id=0 AND \`key\` LIKE 'commercial_%';" \
            2>/dev/null || echo "mysql error"
        echo ""
        echo "=== str_config routing keys ==="
        mysql "$DB_NAME" -e \
            "SELECT \`key\`, value FROM str_config WHERE child_id=0 AND \`key\` LIKE 'commercial_%';" \
            2>/dev/null || echo "mysql error"
        echo ""
        echo "=== commercial_routing_custom_rule count ==="
        mysql "$DB_NAME" -e \
            "SELECT COUNT(*) AS total, SUM(enabled) AS enabled FROM commercial_routing_custom_rule;" \
            2>/dev/null || echo "table may not exist"
        echo ""
        echo "=== SHOW CREATE TABLE commercial_routing_custom_rule ==="
        mysql "$DB_NAME" -e \
            "SHOW CREATE TABLE commercial_routing_custom_rule\G" \
            2>/dev/null || echo "table may not exist"
        echo ""
        echo "=== commercial_routing_upstream count ==="
        mysql "$DB_NAME" -e \
            "SELECT COUNT(*) AS total, SUM(enabled) AS enabled FROM commercial_routing_upstream;" \
            2>/dev/null || echo "table may not exist"
        echo ""
        echo "=== commercial_routing_upstream rows ==="
        mysql "$DB_NAME" -e \
            "SELECT id, name, label, enabled, priority, tunnel_type, last_status, last_checked_at FROM commercial_routing_upstream;" \
            2>/dev/null || echo "table may not exist"
        echo ""
        echo "=== SHOW CREATE TABLE commercial_routing_upstream ==="
        mysql "$DB_NAME" -e \
            "SHOW CREATE TABLE commercial_routing_upstream\G" \
            2>/dev/null || echo "table may not exist"
    } > "$out_dir/db-routing.txt"

    # Routing manifest
    cp "$MANIFEST_PATH" "$out_dir/routing-manifest.txt" 2>/dev/null \
        || echo "not present" > "$out_dir/routing-manifest.txt"

    # Journal
    journalctl -u "$SERVICE_PANEL" -u "$XRAY_ROUTER_SERVICE" -n 120 --no-pager \
        > "$out_dir/journal-tail.txt" 2>&1 || true

    # Installed templates check
    {
        for f in \
            "$INSTALL_ROOT/xray/configs/03_routing.json.j2" \
            "$INSTALL_ROOT/xray/configs/06_outbounds.json.j2" \
            "$INSTALL_ROOT/singbox/configs/03_routing.json.j2" \
            "$INSTALL_ROOT/singbox/configs/06_outbounds.json.j2"; do
            if [[ -f "$f" ]]; then
                echo "present: $f ($(wc -c < "$f") bytes)"
            else
                echo "MISSING: $f"
            fi
        done
    } > "$out_dir/templates-check.txt"

    step "Diagnostics written to: $out_dir"
    echo "collect-routing-diagnostics OK: $out_dir"
}

main "$@"
