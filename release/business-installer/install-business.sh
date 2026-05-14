#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFEST_PATH="${MANIFEST_PATH:-/opt/hiddify-manager/business-addon.manifest}"
source "$SCRIPT_DIR/common.sh"

BASE_IMPORTS=(
  hiddifypanel.accesslog
  hiddifypanel.panel.user.user
  hiddifypanel.panel.commercial.telegrambot.runtime
  hiddifypanel.panel.commercial.telegrambot.secrets
  hiddifypanel.panel.commercial.telegrambot.Usage
  hiddifypanel.panel.commercial.restapi.v1.tgbot
  hiddifypanel.panel.commercial.restapi.v1.tgmsg
  hiddifypanel.panel.commercial.restapi.v2.telegram.tgbot
  hiddifypanel.panel.commercial.capabilities
  hiddifypanel.panel.admin
)

main() {
    INSTALL_BLOCK="business"
    require_root
    assert_canonical_payload
    need_cmd find
    need_cmd sudo
    need_cmd ss
    need_cmd systemctl
    need_cmd mysql
    need_cmd mysqldump
    assert_install_root
    assert_services_exist
    begin_install "business"

    local runtime_path panel_root log_since
    runtime_path="$(detect_runtime_path)"
    panel_root="$runtime_path"
    log_since="$(date '+%Y-%m-%d %H:%M:%S')"

    step "Runtime path detected: $runtime_path"
    step "Installing business support scripts"
    install_payload_file "manager-overlay/scripts/commercial-runtime-requirements.txt" "$INSTALL_ROOT/scripts/commercial-runtime-requirements.txt" 0644
    install_payload_file "manager-overlay/scripts/commercial-tariffs-db-migrate.sh" "$INSTALL_ROOT/scripts/commercial-tariffs-db-migrate.sh" 0755

    step "Installing business runtime files"
    install_payload_file "panel-overlay/hiddifypanel/accesslog.py" "$panel_root/accesslog.py" 0644
    install_payload_file "panel-overlay/hiddifypanel/access.py" "$panel_root/access.py" 0644
    install_payload_file "panel-overlay/hiddifypanel/commercial_logic.py" "$panel_root/commercial_logic.py" 0644
    install_payload_file "panel-overlay/hiddifypanel/hutils/flask.py" "$panel_root/hutils/flask.py" 0644
    install_payload_file "panel-overlay/hiddifypanel/hutils/proxy/singbox.py" "$panel_root/hutils/proxy/singbox.py" 0644
    install_payload_file "panel-overlay/hiddifypanel/models/__init__.py" "$panel_root/models/__init__.py" 0644
    install_payload_file "panel-overlay/hiddifypanel/models/commercial.py" "$panel_root/models/commercial.py" 0644
    install_payload_file "panel-overlay/hiddifypanel/models/user.py" "$panel_root/models/user.py" 0644
    install_payload_file "panel-overlay/hiddifypanel/panel/init_db.py" "$panel_root/panel/init_db.py" 0644
    install_payload_file "panel-overlay/hiddifypanel/panel/hiddify.py" "$panel_root/panel/hiddify.py" 0644
    install_payload_file "panel-overlay/hiddifypanel/panel/user/user.py" "$panel_root/panel/user/user.py" 0644

    step "Installing business API and Telegram runtime files"
    install_payload_file "panel-overlay/hiddifypanel/panel/commercial/restapi/v1/tgbot.py" "$panel_root/panel/commercial/restapi/v1/tgbot.py" 0644
    install_payload_file "panel-overlay/hiddifypanel/panel/commercial/restapi/v1/tgmsg.py" "$panel_root/panel/commercial/restapi/v1/tgmsg.py" 0644
    install_payload_file "panel-overlay/hiddifypanel/panel/commercial/telegrambot/runtime.py" "$panel_root/panel/commercial/telegrambot/runtime.py" 0644
    install_payload_file "panel-overlay/hiddifypanel/panel/commercial/telegrambot/secrets.py" "$panel_root/panel/commercial/telegrambot/secrets.py" 0644
    install_payload_file "panel-overlay/hiddifypanel/panel/commercial/capabilities.py" "$panel_root/panel/commercial/capabilities.py" 0644
    install_payload_file "panel-overlay/hiddifypanel/panel/commercial/restapi/v2/telegram/__init__.py" "$panel_root/panel/commercial/restapi/v2/telegram/__init__.py" 0644
    install_payload_file "panel-overlay/hiddifypanel/panel/commercial/restapi/v2/telegram/tgbot.py" "$panel_root/panel/commercial/restapi/v2/telegram/tgbot.py" 0644
    install_payload_file "panel-overlay/hiddifypanel/panel/commercial/telegrambot/Usage.py" "$panel_root/panel/commercial/telegrambot/Usage.py" 0644
    install_payload_file "panel-overlay/hiddifypanel/models/config_enum.py" "$panel_root/models/config_enum.py" 0644

    step "Installing business admin and template files"
    install_payload_file "panel-overlay/hiddifypanel/panel/admin/__init__.py" "$panel_root/panel/admin/__init__.py" 0644
    install_payload_file "panel-overlay/hiddifypanel/panel/custom_widgets.py" "$panel_root/panel/custom_widgets.py" 0644
    install_payload_file "panel-overlay/hiddifypanel/panel/admin/BusinessAdmin.py" "$panel_root/panel/admin/BusinessAdmin.py" 0644
    install_payload_file "panel-overlay/hiddifypanel/panel/admin/PlanAdmin.py" "$panel_root/panel/admin/PlanAdmin.py" 0644
    install_payload_file "panel-overlay/hiddifypanel/templates/admin-layout.html" "$panel_root/templates/admin-layout.html" 0644
    install_payload_file "panel-overlay/hiddifypanel/templates/macros.html" "$panel_root/templates/macros.html" 0644
    install_payload_file "panel-overlay/hiddifypanel/templates/business-settings.html" "$panel_root/templates/business-settings.html" 0644

    record_capability "business-telegram"
    record_capability "business-yookassa"
    record_capability "business-tariffs"

    step "Compiling installed Python files"
    compile_without_pyc \
      "$panel_root/accesslog.py" \
      "$panel_root/access.py" \
      "$panel_root/commercial_logic.py" \
      "$panel_root/hutils/flask.py" \
      "$panel_root/hutils/proxy/singbox.py" \
      "$panel_root/models/__init__.py" \
      "$panel_root/models/commercial.py" \
      "$panel_root/models/config_enum.py" \
      "$panel_root/models/user.py" \
      "$panel_root/panel/init_db.py" \
      "$panel_root/panel/hiddify.py" \
      "$panel_root/panel/user/user.py" \
      "$panel_root/panel/custom_widgets.py" \
      "$panel_root/panel/commercial/restapi/v1/tgbot.py" \
      "$panel_root/panel/commercial/restapi/v1/tgmsg.py" \
      "$panel_root/panel/commercial/telegrambot/runtime.py" \
      "$panel_root/panel/commercial/telegrambot/secrets.py" \
      "$panel_root/panel/admin/__init__.py" \
      "$panel_root/panel/commercial/capabilities.py" \
      "$panel_root/panel/admin/BusinessAdmin.py" \
      "$panel_root/panel/admin/PlanAdmin.py" \
      "$panel_root/panel/commercial/restapi/v2/telegram/__init__.py" \
      "$panel_root/panel/commercial/restapi/v2/telegram/tgbot.py" \
      "$panel_root/panel/commercial/telegrambot/Usage.py"

    step "Running idempotent tariffs DB migration with DB dump backup"
    BACKUP_DIR="$BACKUP_DIR" "$INSTALL_ROOT/scripts/commercial-tariffs-db-migrate.sh"

    step "Ensuring Telegram owner activation command file"
    ensure_telegram_owner_activation_command

    step "Running business import smoke"
    import_smoke "${BASE_IMPORTS[@]}"

    step "Installing runtime Python requirements"
    install_runtime_requirements

    write_manifest_section "business" "$runtime_path" "$BACKUP_DIR/db-dump.sql"

    step "Restarting panel services and verifying health"
    restart_and_verify "$log_since"

    step "Collecting post-install checkpoint status"
    collect_checkpoint_status "$BACKUP_DIR/status"
    finish_install
    echo "Business addon install OK"
}

main "$@"
