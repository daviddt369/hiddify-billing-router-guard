#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/common.sh"

SMOKE_IMPORTS=(
  hiddifypanel.accesslog
  hiddifypanel.panel.user.user
  hiddifypanel.panel.commercial.telegrambot.runtime
  hiddifypanel.panel.commercial.telegrambot.secrets
  hiddifypanel.panel.commercial.telegrambot.Usage
  hiddifypanel.panel.commercial.restapi.v1.tgbot
  hiddifypanel.panel.commercial.restapi.v1.tgmsg
  hiddifypanel.panel.commercial.capabilities
  hiddifypanel.panel.admin
  hiddifypanel.panel.admin.BusinessAdmin
  hiddifypanel.panel.admin.PlanAdmin
)

main() {
    INSTALL_BLOCK="smoke-business"
    require_root
    assert_canonical_payload
    need_cmd stat
    assert_install_root
    assert_services_exist

    local runtime_path panel_root log_since py file_mode file_owner
    local upgrade_mode=0
    local args=()
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --upgrade-existing-config|--skip-owner-activation-file)
                upgrade_mode=1
                warn "Upgrade mode: owner activation file check downgraded to warning"
                ;;
            *)
                args+=("$1")
                ;;
        esac
        shift
    done
    set -- "${args[@]}"

    runtime_path="$(detect_runtime_path)"
    panel_root="$runtime_path"
    py="$(detect_venv_python)"
    log_since="$(date '+%Y-%m-%d %H:%M:%S')"

    step "Checking service state"
    check_services_active
    check_port_9000

    step "Checking Telegram admin activation command file"
    if [[ -f "$TELEGRAM_ACTIVATION_FILE" ]]; then
        file_mode="$(stat -c '%a' "$TELEGRAM_ACTIVATION_FILE")"
        [[ "$file_mode" == "600" ]] || die "Unexpected activation command file mode: $file_mode"
        file_owner="$(stat -c '%U:%G' "$TELEGRAM_ACTIVATION_FILE")"
        [[ "$file_owner" == "root:root" ]] || die "Unexpected activation command file owner: $file_owner"
    elif [[ "$upgrade_mode" -eq 1 ]]; then
        warn "Activation command file missing: $TELEGRAM_ACTIVATION_FILE (acceptable on upgraded stack if Telegram runtime is already functional)"
    else
        die "Missing activation command file: $TELEGRAM_ACTIVATION_FILE"
    fi

    step "Checking Flask app and business imports"
    create_app_smoke
    import_smoke "${SMOKE_IMPORTS[@]}"
    import_smoke hiddifypanel.panel.commercial.restapi.v2.telegram.tgbot

    step "Running business text and helper sanity checks"
    sudo -H -u "$PANEL_USER" env PYTHONUNBUFFERED=1 "$py" - <<'PY'
from hiddifypanel.panel.commercial.telegrambot.runtime import sanitize_telegram_html
import socks

result = sanitize_telegram_html("alpha<br>beta<br/>gamma<br />delta")
assert result == "alpha\nbeta\ngamma\ndelta", repr(result)
print("sanitize-ok")
print("PySocks import OK")
PY
    check_runtime_text_integrity

    step "Checking business routes"
    admin_route_smoke

    step "Checking business labels and endpoint registration"
    sudo -H -u "$PANEL_USER" env PYTHONUNBUFFERED=1 PANEL_ROOT="$panel_root" UPGRADE_MODE="$upgrade_mode" \
        bash -lc "cd '$INSTALL_ROOT/hiddify-panel' && '$py' -" <<'PY'
import os
from pathlib import Path
from hiddifypanel import create_app

app = create_app()
endpoints = {rule.endpoint for rule in app.url_map.iter_rules()}
upgrade_mode = os.environ.get('UPGRADE_MODE') == '1'
assert 'admin.BusinessAdmin:index' in endpoints, 'Business endpoint missing'
assert 'flask.plans.index_view' in endpoints, 'Plans endpoint missing'
if not upgrade_mode:
    assert 'admin.RoutingAdmin:index' not in endpoints, 'Routing endpoint must not be installed by business'
    assert 'admin.AntiShareAdmin:index' not in endpoints, 'Anti-share endpoint must not be installed by business'

runtime = Path(os.environ['PANEL_ROOT'])
admin_layout = (runtime / 'templates' / 'admin-layout.html').read_text(encoding='utf-8')
business_settings = (runtime / 'templates' / 'business-settings.html').read_text(encoding='utf-8')
plan_admin = (runtime / 'panel' / 'admin' / 'PlanAdmin.py').read_text(encoding='utf-8')
business_admin = (runtime / 'panel' / 'admin' / 'BusinessAdmin.py').read_text(encoding='utf-8')

for label in ['\u0411\u0438\u0437\u043d\u0435\u0441', 'Telegram', 'YooKassa']:
    assert label in admin_layout or label in business_admin, f'missing business label: {label}'
assert '\u0422\u0430\u0440\u0438\u0444' in plan_admin, 'missing tariffs label'

mojibake = ["\u0420\u045f", "\u0420\u2018", "\u0420\u045e", "\u0420\u045c", "\u00d0", "\u00d1"]
for marker in mojibake:
    assert marker not in admin_layout
    assert marker not in business_settings
    assert marker not in plan_admin
    assert marker not in business_admin

if upgrade_mode:
    print('business-endpoints-ok (upgrade/full-stack mode)')
else:
    print('business-endpoints-ok')
print('business-labels-ok')
PY
    check_no_proxy_env

    step "Checking logs for unexpected errors"
    sleep 15
    check_services_active
    check_port_9000
    check_logs_since_filtered "$log_since"

    echo "smoke-business OK"
}

main "$@"
