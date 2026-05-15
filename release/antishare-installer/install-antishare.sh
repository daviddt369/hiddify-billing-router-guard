#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common-antishare.sh"

DEFER_RESTART=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --defer-restart|--no-restart) DEFER_RESTART=1 ;;
        --help|-h)
            cat <<'EOF'
Usage: sudo bash install-antishare.sh [--defer-restart|--no-restart]

  --defer-restart   Install files/schema only; skip panel restart and anti-share smoke
  --no-restart      Alias for --defer-restart
EOF
            exit 0
            ;;
        *) die "Unknown argument: $1" ;;
    esac
    shift
done

# Anti-share Python files to install into panel runtime
PANEL_PYTHON_FILES=(
    "panel-overlay/hiddifypanel/antishare/__init__.py"
    "panel-overlay/hiddifypanel/antishare/config.py"
    "panel-overlay/hiddifypanel/antishare/models.py"
    "panel-overlay/hiddifypanel/antishare/runner.py"
    "panel-overlay/hiddifypanel/antishare/scoring.py"
    "panel-overlay/hiddifypanel/antishare/telegram.py"
    "panel-overlay/hiddifypanel/antishare/nftables.py"
    "panel-overlay/hiddifypanel/antishare/traffic.py"
    "panel-overlay/hiddifypanel/panel/admin/AntiShareAdmin.py"
    "panel-overlay/hiddifypanel/templates/anti-share-settings.html"
)

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
    INSTALL_BLOCK="antishare"
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
    assert_routing_safe

    begin_install "antishare"

    local runtime_path
    runtime_path="$(detect_runtime_path)"
    step "Runtime path detected: $runtime_path"

    # --- Step 1: Install Python package files (no create_app — table not yet created) ---
    step "Installing anti-share Python files"
    for rel in "${PANEL_PYTHON_FILES[@]}"; do
        local dest_rel="${rel#panel-overlay/hiddifypanel/}"
        install_payload_file "$rel" "$runtime_path/$dest_rel" 0644
    done

    # --- Step 2: Compile Python files (syntax check only, no create_app) ---
    step "Compiling installed Python files (syntax check)"
    local py compiled_files=()
    py="$(detect_venv_python)"
    for rel in "${PANEL_PYTHON_FILES[@]}"; do
        [[ "$rel" == *.py ]] || continue
        local dest_rel="${rel#panel-overlay/hiddifypanel/}"
        compiled_files+=("$runtime_path/$dest_rel")
    done
    compile_without_pyc "${compiled_files[@]}"

    # --- Step 3: DB migration (before create_app or panel restart) ---
    step "Running anti-share DB migration"
    export BACKUP_DIR DB_NAME
    bash "$DB_MIGRATE_SCRIPT"

    # --- Step 3b: Enable xray access log ---
    # Anti-share collect_recent_ips() reads xray access log to detect active IPs.
    # Without this, anti-share sees 0 IPs and user online status never updates.
    step "Enabling xray access log (required by anti-share)"
    patch_xray_access_log
    # Patch the Jinja2 template so apply_configs.sh preserves the access log path.
    # Without this, every UI "Применить" (apply_configs.sh) regenerates the config
    # from the template and reverts "access" back to "none".
    step "Patching xray log template (survives apply_configs.sh)"
    patch_xray_access_log_template

    # --- Step 3c: Install xray log permissions systemd override ---
    # xray creates the log as root:root 600; hiddify-panel needs to read it.
    # Systemd override runs chmod 644 after each xray restart, surviving apply_configs.sh.
    step "Installing xray log permissions override (survives apply_configs.sh)"
    install_xray_log_permissions_override

    # --- Step 3d: Prepare xray access state file ---
    step "Preparing xray access state file"
    prepare_xray_access_state

    # --- Step 3e: Restart xray to apply log config ---
    step "Restarting xray to activate access log"
    restart_xray_for_access_log

    # --- Step 4: Install nft helper ---
    step "Installing nft helper"
    install_payload_file \
        "manager-overlay/common/hiddify-antishare-nft.sh" \
        "$NFT_HELPER_PATH" 0755

    # --- Step 5: Install sudoers rule ---
    # Pre-validate payload source BEFORE writing to /etc/sudoers.d/
    step "Installing sudoers rule for hiddify-antishare-nft.sh"
    local sudoers_src
    sudoers_src="$(detect_payload_file "manager-overlay/sudoers/91-hiddify-panel-antishare")"
    visudo -c -f "$sudoers_src" \
        || die "Payload sudoers file failed syntax check — not installing to /etc/sudoers.d/"
    install_payload_file \
        "manager-overlay/sudoers/91-hiddify-panel-antishare" \
        "$SUDOERS_FILE" 0440
    visudo -c -f "$SUDOERS_FILE" \
        || { rm -f "$SUDOERS_FILE"; die "Installed sudoers syntax check failed — file removed"; }

    # --- Step 6: Install systemd service and timer ---
    step "Installing hiddify-anti-share systemd service and timer"
    install_payload_file \
        "manager-overlay/systemd/hiddify-anti-share.service" \
        "$ANTISHARE_SERVICE_FILE" 0644
    install_payload_file \
        "manager-overlay/systemd/hiddify-anti-share.timer" \
        "$ANTISHARE_TIMER_FILE" 0644

    # --- Step 7: Reload systemd and enable timer ---
    step "Reloading systemd and enabling hiddify-anti-share.timer"
    systemctl daemon-reload
    systemctl enable "$ANTISHARE_TIMER"
    # Start timer — runner will fire after OnBootSec (5min) or OnUnitActiveSec (2min)
    systemctl start "$ANTISHARE_TIMER" || warn "hiddify-anti-share.timer start failed (non-fatal)"

    # --- Step 8: Restart panel services only when readiness is not deferred ---
    # anti-share module is auto-loaded by panel/admin/__init__.py when
    # antishare_enabled() returns True (which requires commercial_antishare_installed=1
    # in the DB hconfigs AND the manifest file on disk).
    # Root cause of menu not appearing: panel restarts with stale Redis get_hconfigs()
    # cache that predates the DB migration — antishare_enabled() returns False and the
    # closure captures False for the request lifetime. Flush Redis BEFORE restart so
    # the panel boots with a fresh hconfigs that includes commercial_antishare_installed=1.
    if [[ $DEFER_RESTART -eq 0 ]]; then
        # --- Step 8a: Write manifest BEFORE restart so fallback os.path.exists() also works ---
        step "Writing anti-share manifest (before panel restart)"
        write_antishare_manifest "$runtime_path"

        # --- Step 8b: Flush Redis hconfigs cache BEFORE panel restart ---
        # antishare_enabled() checks get_hconfigs() which is cached in Redis (ttl=500s).
        # Flushing now ensures the panel starts with the post-migration DB value,
        # not the pre-install cached value that lacked commercial_antishare_installed.
        step "Flushing Redis hconfigs cache before panel restart"
        flush_panel_hconfigs_cache

        step "Restarting panel services to activate anti-share module"
        systemctl restart "$SERVICE_PANEL" "$SERVICE_BG"
        SERVICES_RESTARTED=1

        sleep 30
        check_services_active
        check_port_9000

        # --- Step 9: Import smoke (safe now — tables exist, services up) ---
        step "Running anti-share import smoke"
        create_app_smoke
        import_antishare_smoke "${ANTISHARE_IMPORTS[@]}"

        # --- Step 10: Endpoint smoke ---
        step "Verifying anti-share admin endpoint"
        check_antishare_endpoints
    else
        log "Deferred restart mode: skipping panel restart, port 9000 check, and anti-share smoke"
        step "Writing anti-share manifest"
        write_antishare_manifest "$runtime_path"
    fi

    step "Collecting post-install checkpoint"
    collect_antishare_checkpoint "$BACKUP_DIR/status"

    finish_install

    if [[ $DEFER_RESTART -eq 1 ]]; then
        echo "Anti-share addon files upgraded"
        echo "Panel restart deferred by --defer-restart"
        echo "Run final smoke/readiness checks after all upgrade layers"
    else
        echo "Anti-share addon install OK"
        echo ""
        echo "IMPORTANT — safe defaults applied:"
        echo "  nft_enabled=0     : no firewall bans until explicitly enabled in UI"
        echo "  nft_dry_run=1     : dry-run mode for extra safety"
        echo "  telegram_enabled=0: no Telegram notifications until explicitly enabled"
        echo ""
        echo "To enable anti-share enforcement:"
        echo "  1. Add max_ips to user plans (or user.max_ips field)"
        echo "  2. Enable anti-share in /<proxy_path>/admin/anti-share-admin/"
        echo "  3. Enable nft_enabled after verifying scoring is working"
    fi
}

main "$@"
