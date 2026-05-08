#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common-routing.sh"

ROUTING_IMPORTS=(
    hiddifypanel.hutils.commercial_routing
    hiddifypanel.hutils.proxy.router_core
    hiddifypanel.hutils.commercial_routing_source_parser
    hiddifypanel.models.commercial_routing_custom_rule
    hiddifypanel.models.commercial_routing_upstream
    hiddifypanel.models.commercial_routing_rule_source
)

main() {
    INSTALL_BLOCK="smoke-routing"
    require_root
    need_cmd mysql
    need_cmd curl
    need_cmd stat

    assert_install_root
    assert_services_exist

    local py
    py="$(detect_venv_python)"
    local log_since
    log_since="$(date '+%Y-%m-%d %H:%M:%S')"

    # --- Check 1: Main Hiddify panel services active ---
    step "Checking main Hiddify panel services"
    check_services_active
    check_port_9000

    # --- Check 2: Routing Python imports ---
    step "Checking routing imports"
    create_app_smoke
    import_routing_smoke "${ROUTING_IMPORTS[@]}"

    # --- Check 3: RoutingAdmin endpoint registered, business not broken, antishare not installed ---
    step "Checking routing endpoints"
    check_routing_endpoints

    # --- Check 4: DB tables exist ---
    step "Checking DB state"
    local table_exists
    table_exists="$(mysql "$DB_NAME" -N -B \
        -e "SHOW TABLES LIKE 'commercial_routing_custom_rule';" 2>/dev/null | head -n 1 || echo '')"
    [[ "$table_exists" == "commercial_routing_custom_rule" ]] \
        || die "DB table commercial_routing_custom_rule not found in $DB_NAME"
    echo "db-table-ok commercial_routing_custom_rule"

    local upstream_table_exists
    upstream_table_exists="$(mysql "$DB_NAME" -N -B \
        -e "SHOW TABLES LIKE 'commercial_routing_upstream';" 2>/dev/null | head -n 1 || echo '')"
    [[ "$upstream_table_exists" == "commercial_routing_upstream" ]] \
        || die "DB table commercial_routing_upstream not found in $DB_NAME"
    echo "db-table-ok commercial_routing_upstream"

    # Schema self-check for commercial_routing_upstream
    local upstream_schema
    upstream_schema="$(mysql "$DB_NAME" -e "SHOW CREATE TABLE commercial_routing_upstream\G" 2>/dev/null)"
    echo "$upstream_schema" | grep -q 'PRIMARY KEY' \
        || die "Upstream schema: PRIMARY KEY missing"
    echo "$upstream_schema" | grep -q 'ix_upstream_enabled' \
        || die "Upstream schema: KEY ix_upstream_enabled missing"
    echo "$upstream_schema" | grep -q 'ix_upstream_priority' \
        || die "Upstream schema: KEY ix_upstream_priority missing"
    echo "$upstream_schema" | grep -q 'uq_upstream_name' \
        || die "Upstream schema: UNIQUE KEY uq_upstream_name missing"
    echo "$upstream_schema" | grep -q 'last_status' \
        || die "Upstream schema: column last_status missing"
    echo "$upstream_schema" | grep -q 'last_error' \
        || die "Upstream schema: column last_error missing"
    echo "$upstream_schema" | grep -q 'last_checked_at' \
        || die "Upstream schema: column last_checked_at missing"
    echo "db-upstream-schema-ok"

    # Row count (informational — no records required on clean VM)
    local upstream_count
    upstream_count="$(mysql "$DB_NAME" -N -B \
        -e "SELECT COUNT(*) FROM commercial_routing_upstream;" 2>/dev/null | head -n 1 || echo '?')"
    echo "db-upstream-count=$upstream_count"

    # --- Check 5: commercial_routing_installed=1 ---
    local installed_val
    installed_val="$(mysql "$DB_NAME" -N -B \
        -e "SELECT value FROM bool_config WHERE child_id=0 AND \`key\`='commercial_routing_installed';" \
        2>/dev/null | head -n 1 || echo '')"
    [[ "$installed_val" == "1" ]] \
        || die "commercial_routing_installed != 1 in bool_config (got: '$installed_val')"
    echo "db-installed-flag-ok"

    # --- Check 6: xray-router.service unit exists and enabled ---
    step "Checking xray-router systemd unit"
    systemctl cat "$XRAY_ROUTER_SERVICE" >/dev/null 2>&1 \
        || die "xray-router.service unit not found"
    echo "xray-router-unit-ok"

    local enabled_state
    enabled_state="$(systemctl is-enabled "$XRAY_ROUTER_SERVICE" 2>/dev/null || echo disabled)"
    [[ "$enabled_state" == "enabled" ]] \
        || die "xray-router.service is not enabled (got: $enabled_state)"
    echo "xray-router-enabled-ok"

    # xray-router active state: soft warn if inactive (no upstream configured on clean VM),
    # hard fail if service crashed due to config error
    local xray_state
    if check_xray_router_unit_state; then
        echo "xray-router-active-ok"
    else
        local exit_code=$?
        if [[ $exit_code -eq 2 ]]; then
            die "xray-router.service failed or in restart loop — config error, check: journalctl -u xray-router"
        fi
        # exit_code 1 = inactive/dead (no upstream) — expected on clean VM, warn only
        warn "xray-router.service inactive (no upstream node configured — configure in routing admin UI)"
    fi

    # --- Check 7: sudoers installed and valid ---
    step "Checking sudoers rule"
    [[ -f "$SUDOERS_FILE" ]] \
        || die "Sudoers file not found: $SUDOERS_FILE"
    visudo -c -f "$SUDOERS_FILE" >/dev/null 2>&1 \
        || die "Sudoers file syntax invalid: $SUDOERS_FILE"
    echo "sudoers-ok"

    # --- Check 8: commander.py patched and valid ---
    # BusinessAdmin calls: sudo -n commander.py commercial-routing-apply (direct via shebang).
    # hiddify-panel does NOT read commander.py — sudo runs it as root.
    # All checks run as root; no sudo -u hiddify-panel.
    step "Checking commander.py routing patch"
    [[ -f "$COMMANDER_PATH" ]] \
        || die "commander.py not found: $COMMANDER_PATH"
    grep -q 'ROUTING_INSTALL_BEGIN' "$COMMANDER_PATH" \
        || die "commander.py routing patch not found (ROUTING_INSTALL_BEGIN marker missing)"
    "$py" -m py_compile "$COMMANDER_PATH" \
        || die "commander.py syntax invalid after routing patch"
    [[ -x "$COMMANDER_PATH" ]] \
        || die "commander.py missing execute bit (mode: $(stat -c '%a' "$COMMANDER_PATH"))"
    "$COMMANDER_PATH" --help | grep -q 'commercial-routing-apply' \
        || die "commercial-routing-apply not found in commander.py --help"
    "$COMMANDER_PATH" id >/dev/null \
        || die "commander.py id command failed (non-routing commands broken)"
    sudo -l -U "$PANEL_USER" | grep -q 'commander.py' \
        || die "hiddify-panel sudoers entry for commander.py not found"
    echo "commander-routing-patch-ok"

    # --- Check 9: Route accessibility ---
    step "Checking routing admin routes"
    admin_routing_route_smoke

    # --- Check 10 (Stage 2B): RoutingUpstreamAdmin patch checks ---
    step "Checking Stage 2B upstream admin patches"
    local runtime_path
    runtime_path="$(detect_runtime_path)"

    # __init__.py patch marker
    [[ -f "$runtime_path/panel/admin/__init__.py" ]] \
        || die "panel/admin/__init__.py not found"
    grep -q 'ROUTING_UPSTREAM_ADMIN_BEGIN' "$runtime_path/panel/admin/__init__.py" \
        || die "__init__.py missing ROUTING_UPSTREAM_ADMIN_BEGIN marker (Stage 2B patch not applied)"
    echo "upstream-admin-init-patch-ok"

    # RoutingUpstreamAdmin.py installed
    [[ -f "$runtime_path/panel/admin/RoutingUpstreamAdmin.py" ]] \
        || die "RoutingUpstreamAdmin.py not installed at $runtime_path/panel/admin/"
    "$py" -m py_compile "$runtime_path/panel/admin/RoutingUpstreamAdmin.py" \
        || die "RoutingUpstreamAdmin.py syntax invalid"
    echo "upstream-admin-file-ok"

    # Template installed
    [[ -f "$runtime_path/panel/admin/templates/routing-upstream.html" ]] \
        || die "routing-upstream.html template not found"
    echo "upstream-template-ok"

    # business-settings.html patches
    [[ -f "$runtime_path/templates/business-settings.html" ]] \
        || die "business-settings.html not found"
    grep -q 'ROUTING_UPSTREAM_UI_BEGIN' "$runtime_path/templates/business-settings.html" \
        || die "business-settings.html missing ROUTING_UPSTREAM_UI_BEGIN marker (Stage 2B patch not applied)"
    echo "upstream-ui-tmpl-patch-ok"
    grep -q 'ROUTING_LEGACY_UPSTREAM_HIDE_BEGIN' "$runtime_path/templates/business-settings.html" \
        || die "business-settings.html missing ROUTING_LEGACY_UPSTREAM_HIDE_BEGIN marker (legacy hide patch not applied)"
    # Verify legacy fields are inside display:none block (not visible)
    grep -q 'display:none' "$runtime_path/templates/business-settings.html" \
        || die "business-settings.html: display:none not found — legacy fields may be visible"
    # Verify custom rules section still present (accepts both old and new h4 text)
    grep -qE 'Пользовательские маршруты|Direct-правила' "$runtime_path/templates/business-settings.html" \
        || die "business-settings.html: custom rules section missing after patch"
    echo "legacy-upstream-hidden-ok"

    # Upstream routes registered via create_app
    create_app_smoke_with_upstream_routes
    echo "upstream-routes-registered-ok"

    # /upstreams/ route responds (302/200/401, not 500)
    local proxy_path
    proxy_path="$(_get_admin_proxy_path)"
    [[ -n "$proxy_path" ]] || die "Cannot determine admin proxy path"
    local upstreams_path="/$proxy_path/admin/routing-admin/upstreams/"
    _curl_check_route "http://127.0.0.1${upstreams_path}" \
        || _curl_check_route "https://127.0.0.1${upstreams_path}" \
        || die "Upstreams route smoke failed for $upstreams_path"

    # --- Check 11 (Stage 2C): multi-upstream apply and xray config validation ---
    step "Checking Stage 2C upstream apply and xray config"
    local upstream_count
    upstream_count="$(mysql "$DB_NAME" -N -B \
        -e "SELECT COUNT(*) FROM commercial_routing_upstream WHERE enabled=1 AND tunnel_type!='test_blackhole';" \
        2>/dev/null | head -n 1 || echo '0')"
    echo "real-enabled-upstreams=$upstream_count"

    if [[ "$upstream_count" -ge 1 ]]; then
        # Run commercial-routing-apply
        if sudo -n "$COMMANDER_PATH" commercial-routing-apply 2>/dev/null; then
            echo "commercial-routing-apply-ok"
        else
            warn "commercial-routing-apply failed — xray config may be missing upstream data"
        fi

        if [[ -f /etc/xray-router/config.json ]]; then
            # xray config test
            /usr/bin/xray run -test -config /etc/xray-router/config.json >/dev/null 2>&1 \
                && echo "xray-config-test-ok" \
                || die "xray run -test failed for /etc/xray-router/config.json"

            # Structural checks
            local cfg
            cfg="$(cat /etc/xray-router/config.json)"

            # Must have upstream-{id} outbounds
            echo "$cfg" | grep -q '"upstream-' \
                || die "config missing upstream-{id} outbounds"
            echo "upstream-outbounds-ok"

            # Must NOT have top-level balancers (must be inside routing)
            local top_balancers
            top_balancers="$(python3 -c "
import json, sys
d = json.load(open('/etc/xray-router/config.json'))
print('present' if 'balancers' in d else 'absent')
" 2>/dev/null)"
            [[ "$top_balancers" != "present" ]] \
                || die "top-level balancers detected — balancers must be inside routing block"
            echo "no-top-level-balancers-ok"

            if [[ "$upstream_count" -ge 2 ]]; then
                # balancers must be inside routing
                python3 -c "
import json, sys
d = json.load(open('/etc/xray-router/config.json'))
r = d.get('routing', {})
assert r.get('balancers'), 'routing.balancers missing'
# final rule must use balancerTag
rules = r.get('rules', [])
final = [ru for ru in rules if ru.get('balancerTag') == 'upstream-balancer']
assert final, 'no final rule with balancerTag upstream-balancer'
# no hardcoded to-de in outboundTag of final rules
bad = [ru for ru in rules if ru.get('outboundTag') == 'to-de' and ru.get('network') == 'tcp,udp' and not ru.get('domain') and not ru.get('ip')]
assert not bad, 'found final rule with outboundTag to-de (legacy path still used)'
print('balancer-structure-ok')
" 2>/dev/null || die "routing.balancers structure check failed"
                echo "balancer-config-validated-ok"

                # observatory must be present
                python3 -c "
import json, sys
d = json.load(open('/etc/xray-router/config.json'))
assert 'observatory' in d, 'observatory missing'
print('observatory-ok')
" 2>/dev/null || warn "observatory section missing (auto-failover may not work)"
            else
                # Single upstream: final rule must use outboundTag upstream-*
                python3 -c "
import json, sys
d = json.load(open('/etc/xray-router/config.json'))
rules = d.get('routing', {}).get('rules', [])
final = [r for r in rules if str(r.get('outboundTag','')).startswith('upstream-') and r.get('network')=='tcp,udp' and not r.get('domain') and not r.get('ip')]
assert final, 'no final rule with outboundTag upstream-*'
print('single-upstream-rule-ok')
" 2>/dev/null || die "single upstream routing rule check failed"
            fi
        else
            warn "/etc/xray-router/config.json not found — run commercial-routing-apply first"
        fi
    else
        warn "No real enabled upstreams — Stage 2C apply check skipped (add upstream nodes in UI)"
    fi

    # --- Check 12 (Stage 2D): routing activation in main Hiddify core ---
    step "Checking routing activation in main Hiddify core"

    local routing_enabled
    routing_enabled="$(mysql "$DB_NAME" -N -B \
        -e "SELECT value FROM bool_config WHERE child_id=0 AND \`key\`='commercial_routing_enable';" \
        2>/dev/null | head -n 1 || echo '0')"
    echo "commercial_routing_enable=$routing_enabled"

    local legacy_geosite drop_bittorrent
    legacy_geosite="$(mysql "$DB_NAME" -N -B \
        -e "SELECT value FROM bool_config WHERE child_id=0 AND \`key\`='commercial_legacy_geosite_to_router';" \
        2>/dev/null | head -n 1 || echo '?')"
    drop_bittorrent="$(mysql "$DB_NAME" -N -B \
        -e "SELECT value FROM bool_config WHERE child_id=0 AND \`key\`='commercial_drop_bittorrent';" \
        2>/dev/null | head -n 1 || echo '?')"
    echo "commercial_legacy_geosite_to_router=$legacy_geosite"
    echo "commercial_drop_bittorrent=$drop_bittorrent"

    if [[ "$routing_enabled" != "1" ]]; then
        warn "commercial_routing_enable=0: routing is installed but NOT active in main Hiddify core."
        warn "Main Hiddify Xray/Singbox will NOT send traffic to xray-router."
        warn "To activate: enable routing in /admin/routing-admin/ and run apply_configs.sh."
        warn "See README-ROUTING-INSTALL.md section 'Как включить routing'."
    else
        echo "routing-active-in-db=yes"

        # Check Xray main config has commercial-local-router outbound
        local xray_outbounds
        xray_outbounds="/opt/hiddify-manager/xray/configs/06_outbounds.json"
        if [[ -f "$xray_outbounds" ]]; then
            grep -q 'commercial-local-router' "$xray_outbounds" \
                && echo "xray-has-commercial-local-router-ok" \
                || die "xray 06_outbounds.json missing commercial-local-router — run apply_configs.sh"
        else
            warn "$xray_outbounds not found"
        fi

        # Check Xray routing sends to commercial-local-router
        local xray_routing
        xray_routing="/opt/hiddify-manager/xray/configs/03_routing.json"
        if [[ -f "$xray_routing" ]]; then
            grep -q 'commercial-local-router' "$xray_routing" \
                && echo "xray-routing-uses-commercial-local-router-ok" \
                || die "xray 03_routing.json does not route to commercial-local-router — run apply_configs.sh"
            # Check final fallback goes to router
            python3 -c "
import json, sys
d = json.load(open('$xray_routing'))
rules = d.get('routing',{}).get('rules',[])
fallback = [r for r in rules if r.get('port') == '0-65535' or (not r.get('domain') and not r.get('ip') and not r.get('protocol') and r.get('type') == 'field')]
ok = any(r.get('outboundTag') == 'commercial-local-router' for r in fallback)
print('xray-fallback-to-router: ok' if ok else 'xray-fallback-NOT-going-to-router')
sys.exit(0 if ok else 1)
" 2>/dev/null || warn "xray final fallback rule may not route to commercial-local-router"
        else
            warn "$xray_routing not found"
        fi

        # Check Singbox main config has commercial-local-router
        local singbox_outbounds
        singbox_outbounds="/opt/hiddify-manager/singbox/configs/06_outbounds.json"
        if [[ -f "$singbox_outbounds" ]]; then
            grep -q 'commercial-local-router' "$singbox_outbounds" \
                && echo "singbox-has-commercial-local-router-ok" \
                || die "singbox 06_outbounds.json missing commercial-local-router — run apply_configs.sh"
        else
            warn "$singbox_outbounds not found"
        fi

        # Check Singbox routing uses commercial-local-router as final
        local singbox_routing
        singbox_routing="/opt/hiddify-manager/singbox/configs/03_routing.json"
        if [[ -f "$singbox_routing" ]]; then
            python3 -c "
import json, sys
d = json.load(open('$singbox_routing'))
final = d.get('route',{}).get('final','')
ok = (final == 'commercial-local-router')
print('singbox-final-to-router: ok' if ok else f'singbox-final={final} (not commercial-local-router)')
sys.exit(0 if ok else 1)
" 2>/dev/null || warn "singbox route.final is not commercial-local-router — run apply_configs.sh"
        else
            warn "$singbox_routing not found"
        fi
    fi

    # --- Check 13 (Stage 2E): Rule source table, parser, and routes ---
    step "Checking Stage 2E rule sources"

    # DB table
    local rule_source_table
    rule_source_table="$(mysql "$DB_NAME" -N -B \
        -e "SHOW TABLES LIKE 'commercial_routing_rule_source';" 2>/dev/null | head -n 1 || echo '')"
    [[ "$rule_source_table" == "commercial_routing_rule_source" ]] \
        || die "DB table commercial_routing_rule_source not found"
    echo "db-table-ok commercial_routing_rule_source"

    # Schema self-check
    local rs_schema
    rs_schema="$(mysql "$DB_NAME" -e "SHOW CREATE TABLE commercial_routing_rule_source\G" 2>/dev/null)"
    echo "$rs_schema" | grep -q 'uq_rule_source_name' \
        || die "Rule source schema: UNIQUE KEY uq_rule_source_name missing"
    echo "$rs_schema" | grep -q 'ix_rule_source_enabled' \
        || die "Rule source schema: KEY ix_rule_source_enabled missing"
    echo "$rs_schema" | grep -q 'rules_count' \
        || die "Rule source schema: column rules_count missing"
    echo "db-rule-source-schema-ok"

    # Parser smoke: domain text
    local py
    py="$(detect_venv_python)"
    sudo -H -u "$PANEL_USER" env PYTHONUNBUFFERED=1 \
        bash -lc "cd '$INSTALL_ROOT/hiddify-panel' && '$py' -" <<'PY'
from hiddifypanel.hutils.commercial_routing_source_parser import parse_text

# Domain parser smoke
text = """
# comment
example.com
.sub.example.com
full:exact.ru
regexp:.*\\.ru$
"""
r = parse_text(text, "domain")
assert len(r.rules) == 4, f"Expected 4 domain rules, got {len(r.rules)}: {r.rules}"
assert r.rules[0].rule_type == "domain_suffix"
assert r.rules[0].normalized_value == "example.com"
assert r.rules[2].rule_type == "domain_exact"
assert r.rules[3].rule_type == "domain_regex"
print(f"parser-domain-ok rules={len(r.rules)} errors={len(r.errors)}")

# Subnet parser smoke
text2 = """
1.2.3.4
10.0.0.0/8
ip:5.6.7.8
cidr:192.168.0.0/16
"""
r2 = parse_text(text2, "subnet")
assert len(r2.rules) == 4, f"Expected 4 subnet rules, got {len(r2.rules)}: {r2.rules}"
assert r2.rules[0].rule_type == "ip"
assert r2.rules[1].rule_type == "cidr"
print(f"parser-subnet-ok rules={len(r2.rules)} errors={len(r2.errors)}")

# keyword: must produce error
r3 = parse_text("keyword:test", "domain")
assert len(r3.errors) == 1, f"Expected 1 error for keyword:, got {len(r3.errors)}"
assert "keyword" in r3.errors[0].lower()
print("parser-keyword-error-ok")

# Duplicate dedup smoke
r4 = parse_text("example.com\nexample.com\nexample.com", "domain")
assert len(r4.rules) == 1, f"Expected 1 unique rule, got {len(r4.rules)}"
assert r4.duplicates == 2
print("parser-dedup-ok")
PY
    echo "parser-domain-smoke-ok"
    echo "parser-subnet-smoke-ok"

    # Local file smoke (create test file, parse, clean up)
    local test_list_dir="/opt/hiddify-manager/routing-lists"
    mkdir -p "$test_list_dir"
    local test_file="$test_list_dir/smoke-test-domains.txt"
    cat > "$test_file" <<'EOF'
# smoke test file
smoke-test.ru
.sub.smoke-test.ru
full:exact.smoke-test.ru
EOF
    sudo -H -u "$PANEL_USER" env PYTHONUNBUFFERED=1 \
        bash -lc "cd '$INSTALL_ROOT/hiddify-panel' && '$py' -" <<PY
from hiddifypanel.hutils.commercial_routing_source_parser import read_local_file, parse_text

content = read_local_file("$test_file").decode("utf-8", errors="replace")
r = parse_text(content, "domain")
assert len(r.rules) == 3, f"Expected 3 rules from local file, got {len(r.rules)}: {r.rules}"
print(f"local-file-smoke-ok rules={len(r.rules)}")
PY
    rm -f "$test_file"
    echo "local-file-parser-smoke-ok"

    # RoutingRuleSourceAdmin routes registered
    create_app_smoke_with_rule_source_routes
    echo "rule-source-routes-registered-ok"

    # __init__.py patch marker for Stage 2E
    runtime_path="$(detect_runtime_path)"
    grep -q 'ROUTING_RULE_SOURCE_ADMIN_BEGIN' "$runtime_path/panel/admin/__init__.py" \
        || die "__init__.py missing ROUTING_RULE_SOURCE_ADMIN_BEGIN marker (Stage 2E patch not applied)"
    echo "rule-source-admin-init-patch-ok"

    # Template installed
    [[ -f "$runtime_path/panel/admin/templates/routing-rule-source.html" ]] \
        || die "routing-rule-source.html template not found"
    echo "rule-source-template-ok"

    # /rule-sources/ route responds (not 500)
    local proxy_path
    proxy_path="$(_get_admin_proxy_path)"
    [[ -n "$proxy_path" ]] || die "Cannot determine admin proxy path"
    local rs_path="/$proxy_path/admin/routing-admin/rule-sources/"
    _curl_check_route "http://127.0.0.1${rs_path}" \
        || _curl_check_route "https://127.0.0.1${rs_path}" \
        || die "Rule sources route smoke failed for $rs_path"

    echo "smoke-routing OK"
}

main "$@"
