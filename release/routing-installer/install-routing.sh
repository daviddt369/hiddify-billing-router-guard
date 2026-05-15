#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common-routing.sh"

# Routing-exclusive Python files to install into panel runtime
PANEL_PYTHON_FILES=(
    "panel-overlay/hiddifypanel/hutils/commercial_routing.py"
    "panel-overlay/hiddifypanel/hutils/proxy/router_core.py"
    "panel-overlay/hiddifypanel/hutils/commercial_routing_source_parser.py"
    "panel-overlay/hiddifypanel/models/commercial_routing_custom_rule.py"
    "panel-overlay/hiddifypanel/models/commercial_routing_upstream.py"
    "panel-overlay/hiddifypanel/models/commercial_routing_rule_source.py"
    "panel-overlay/hiddifypanel/panel/admin/RoutingUpstreamAdmin.py"
    "panel-overlay/hiddifypanel/panel/admin/RoutingRuleSourceAdmin.py"
)

# Routing imports to smoke after install
ROUTING_IMPORTS=(
    hiddifypanel.hutils.commercial_routing
    hiddifypanel.hutils.proxy.router_core
    hiddifypanel.hutils.commercial_routing_source_parser
    hiddifypanel.models.commercial_routing_custom_rule
    hiddifypanel.models.commercial_routing_upstream
    hiddifypanel.models.commercial_routing_rule_source
    hiddifypanel.panel.admin.RoutingUpstreamAdmin
    hiddifypanel.panel.admin.RoutingRuleSourceAdmin
)

main() {
    INSTALL_BLOCK="routing"
    require_root
    need_cmd find
    need_cmd mysql
    need_cmd mysqldump
    need_cmd systemctl
    need_cmd curl

    assert_canonical_payload
    assert_install_root
    assert_services_exist
    assert_business_installed
    assert_xray_binary
    assert_apply_configs

    begin_install "routing"

    local runtime_path log_since
    runtime_path="$(detect_runtime_path)"
    log_since="$(date '+%Y-%m-%d %H:%M:%S')"
    step "Runtime path detected: $runtime_path"

    # --- Step 1: Install Python panel files (NO create_app — table not yet created) ---
    step "Installing routing Python panel files"
    for rel in "${PANEL_PYTHON_FILES[@]}"; do
        local dest_rel="${rel#panel-overlay/hiddifypanel/}"
        install_payload_file "$rel" "$runtime_path/$dest_rel" 0644
    done

    # --- Step 2: Compile Python files (syntax check only, no create_app) ---
    step "Compiling installed Python files (syntax check)"
    local py
    py="$(detect_venv_python)"
    local compiled_files=()
    for rel in "${PANEL_PYTHON_FILES[@]}"; do
        local dest_rel="${rel#panel-overlay/hiddifypanel/}"
        compiled_files+=("$runtime_path/$dest_rel")
    done
    compile_without_pyc "${compiled_files[@]}"

    # --- Step 3: DB migration (must happen before create_app or panel restart) ---
    step "Running routing DB migration"
    export BACKUP_DIR DB_NAME
    bash "$DB_MIGRATE_SCRIPT"

    # --- Step 3b: Install routing admin template ---
    step "Installing routing upstream admin template"
    local tmpl_dir="$runtime_path/panel/admin/templates"
    mkdir -p "$tmpl_dir"
    install_payload_file \
        "panel-overlay/hiddifypanel/panel/admin/templates/routing-upstream.html" \
        "$tmpl_dir/routing-upstream.html" 0644

    # --- Step 3c: Patch panel/admin/__init__.py for RoutingUpstreamAdmin ---
    step "Patching panel/admin/__init__.py for RoutingUpstreamAdmin"
    patch_routing_upstream_admin "$runtime_path/panel/admin/__init__.py"

    # --- Step 3d: Patch business-settings.html with upstream link ---
    step "Patching business-settings.html with upstream management link"
    patch_business_settings_upstream_link "$runtime_path/templates/business-settings.html"

    # --- Step 3e: Hide legacy single-upstream fields in business-settings.html ---
    step "Hiding legacy single-upstream fields in business-settings.html"
    patch_business_settings_hide_legacy_upstream "$runtime_path/templates/business-settings.html"

    # --- Step 3f: Install rule source admin template ---
    step "Installing routing rule source admin template"
    install_payload_file \
        "panel-overlay/hiddifypanel/panel/admin/templates/routing-rule-source.html" \
        "$tmpl_dir/routing-rule-source.html" 0644

    # --- Step 3g: Patch panel/admin/__init__.py for RoutingRuleSourceAdmin ---
    step "Patching panel/admin/__init__.py for RoutingRuleSourceAdmin"
    patch_routing_rule_source_admin "$runtime_path/panel/admin/__init__.py"

    # --- Step 3h: Patch business-settings.html with rule-sources link ---
    step "Patching business-settings.html with rule sources link"
    patch_business_settings_rule_source_link "$runtime_path/templates/business-settings.html"

    # --- Step 3i: Replace 3 alert banners with compact info card ---
    step "Replacing verbose routing alerts with compact info card"
    patch_business_settings_compact_info "$runtime_path/templates/business-settings.html"

    # --- Step 3j: Update direct-rules section labels and descriptions ---
    step "Updating direct-rules section labels"
    patch_business_settings_direct_labels "$runtime_path/templates/business-settings.html"

    # --- Step 3k: Add routing sidebar links to admin-layout.html ---
    step "Patching admin-layout.html with routing sidebar links"
    patch_admin_layout_routing_sidebar "$runtime_path/templates/admin-layout.html"

    # --- Step 4: Install Jinja2 template files ---
    step "Installing routing Jinja2 templates"
    install_payload_file \
        "manager-overlay/xray/configs/03_routing.json.j2" \
        "$INSTALL_ROOT/xray/configs/03_routing.json.j2" 0644
    install_payload_file \
        "manager-overlay/xray/configs/06_outbounds.json.j2" \
        "$INSTALL_ROOT/xray/configs/06_outbounds.json.j2" 0644
    install_payload_file \
        "manager-overlay/singbox/configs/03_routing.json.j2" \
        "$INSTALL_ROOT/singbox/configs/03_routing.json.j2" 0644
    install_payload_file \
        "manager-overlay/singbox/configs/06_outbounds.json.j2" \
        "$INSTALL_ROOT/singbox/configs/06_outbounds.json.j2" 0644

    # --- Step 5: Install xray-router systemd service file ---
    step "Installing xray-router.service"
    install_payload_file \
        "manager-overlay/systemd/xray-router.service" \
        "$XRAY_ROUTER_SERVICE_FILE" 0644

    # --- Step 6: Install sudoers rule ---
    # Pre-validate payload source BEFORE writing to /etc/sudoers.d/ to avoid
    # leaving a broken file in sudoers.d on syntax error.
    step "Installing sudoers rule for commercial-routing-apply"
    local sudoers_src
    sudoers_src="$(detect_payload_file "manager-overlay/sudoers/90-hiddify-panel-routing")"
    visudo -c -f "$sudoers_src" \
        || die "Payload sudoers file failed syntax check — not installing to /etc/sudoers.d/"
    install_payload_file \
        "manager-overlay/sudoers/90-hiddify-panel-routing" \
        "$SUDOERS_FILE" 0440
    visudo -c -f "$SUDOERS_FILE" \
        || { rm -f "$SUDOERS_FILE"; die "Installed sudoers syntax check failed — file removed"; }

    # --- Step 7: Patch commander.py with routing command ---
    step "Patching commander.py with commercial-routing-apply command"
    patch_commander_routing

    # Commander smoke.
    # BusinessAdmin calls: sudo -n commander.py commercial-routing-apply (direct via shebang).
    # hiddify-panel does NOT read commander.py directly — sudo runs it as root.
    # Checks run as root (current user); no sudo -u hiddify-panel needed.
    "$py" -m py_compile "$COMMANDER_PATH" \
        || die "commander.py syntax check failed after patch"
    [[ -x "$COMMANDER_PATH" ]] \
        || die "commander.py missing execute bit after patch"
    "$COMMANDER_PATH" --help | grep -q 'commercial-routing-apply' \
        || die "commercial-routing-apply not found in commander.py --help after patch"
    "$COMMANDER_PATH" id >/dev/null \
        || die "commander.py id command failed after patch (non-routing commands broken)"
    sudo -l -U "$PANEL_USER" | grep -q 'commander.py' \
        || die "hiddify-panel sudoers entry for commander.py not found"

    # --- Step 8: systemctl reload + enable xray-router ---
    step "Reloading systemd and enabling xray-router"
    systemctl daemon-reload
    systemctl enable "$XRAY_ROUTER_SERVICE"

    # --- Step 9: Restart panel services to activate routing module ---
    # apply_configs.sh is NOT run here: hiddifypanel all-configs makes external
    # network calls (port 443 to upstream servers) that hang in offline/restricted
    # environments. The Jinja2 templates (xray/singbox routing configs) are already
    # installed and will be applied on the next scheduled apply_configs.sh run.
    # For smoke purposes, a panel restart is sufficient to register RoutingAdmin.
    step "Restarting panel services to activate routing module"
    systemctl restart "$SERVICE_PANEL" "$SERVICE_BG"
    SERVICES_RESTARTED=1

    sleep 10
    check_services_active
    check_port_9000

    # --- Step 10: Import smoke (NOW safe — table exists, services up) ---
    step "Running routing import smoke"
    create_app_smoke
    # Retry once after a brief pause — panel may still be writing __pycache__ on first attempt.
    import_routing_smoke "${ROUTING_IMPORTS[@]}" \
        || { warn "Import smoke attempt 1 failed — retrying after 5s"; sleep 5; import_routing_smoke "${ROUTING_IMPORTS[@]}"; }

    # --- Step 10b: Install routing health probe (systemd timer) ---
    step "Installing routing upstream health probe"
    bash "$SCRIPT_DIR/scripts/install-routing-health-probe.sh" \
        || warn "Health probe install failed — continuing without it (non-fatal)"

    # --- Step 11: Write manifest and collect checkpoint ---
    step "Writing routing manifest"
    write_routing_manifest "$runtime_path"

    # Final panel restart — ensures panel loads routing modules from warm __pycache__
    # (first restart after install may have a brief import race; this one is clean).
    step "Final panel restart to ensure clean module load"
    systemctl restart "$SERVICE_PANEL" "$SERVICE_BG"
    sleep 10
    check_services_active
    check_port_9000

    step "Collecting post-install checkpoint"
    collect_routing_checkpoint "$BACKUP_DIR/status"

    finish_install
    echo "Routing addon install OK"
}

main "$@"
