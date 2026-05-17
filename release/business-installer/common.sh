#!/usr/bin/env bash
set -Eeuo pipefail

readonly RELEASE_VERSION="business-final-20260507"
readonly RELEASE_TAG="business-final-20260507"
readonly RELEASE_COMMIT="3d251b9"
readonly SERVICE_PANEL="hiddify-panel"
readonly SERVICE_BG="hiddify-panel-background-tasks"
readonly PANEL_USER="hiddify-panel"
readonly INSTALL_ROOT="${INSTALL_ROOT:-/opt/hiddify-manager}"
readonly BACKUP_ROOT="${BACKUP_ROOT:-$INSTALL_ROOT/business-installer-backups}"
readonly MANIFEST_PATH="${MANIFEST_PATH:-$INSTALL_ROOT/business-addon.manifest}"
readonly TELEGRAM_ACTIVATION_DIR="${TELEGRAM_ACTIVATION_DIR:-$INSTALL_ROOT/business-addon-secrets}"
readonly TELEGRAM_ACTIVATION_FILE="${TELEGRAM_ACTIVATION_FILE:-$TELEGRAM_ACTIVATION_DIR/telegram-owner-activation.txt}"
readonly LOG_ERROR_RE='Traceback|ImportError|ModuleNotFoundError|AttributeError|TypeError|NameError|RuntimeError'
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly PAYLOAD_DIR="$SCRIPT_DIR/payload"

BACKUP_DIR=""
INSTALL_BLOCK=""
INSTALL_SUCCESS=0
SERVICES_RESTARTED=0

log() {
    echo "[$INSTALL_BLOCK] $*"
}

step() {
    echo
    echo "[$INSTALL_BLOCK][STEP] $*"
}

die() {
    echo "[$INSTALL_BLOCK][ERROR] $*" >&2
    exit 1
}

warn() {
    echo "[$INSTALL_BLOCK][WARN] $*" >&2
}

need_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "Missing command: $1"
}

require_root() {
    [[ "$(id -u)" -eq 0 ]] || die "Run as root."
}

assert_install_root() {
    [[ -d "$INSTALL_ROOT" ]] || die "Install root not found: $INSTALL_ROOT"
    [[ -d "$INSTALL_ROOT/hiddify-panel" ]] || die "Panel root not found: $INSTALL_ROOT/hiddify-panel"
}

assert_services_exist() {
    systemctl cat "$SERVICE_PANEL" >/dev/null 2>&1 || die "Missing systemd unit: $SERVICE_PANEL"
    systemctl cat "$SERVICE_BG" >/dev/null 2>&1 || die "Missing systemd unit: $SERVICE_BG"
}

get_owner_uuid() {
    mysql -N -B hiddifypanel -e "select uuid from admin_user order by id limit 1" 2>/dev/null | head -n 1 || true
}

detect_runtime_path() {
    mapfile -t found < <(find "$INSTALL_ROOT" -type d -path '*/site-packages/hiddifypanel' 2>/dev/null | sort)
    [[ "${#found[@]}" -gt 0 ]] || die "Cannot detect runtime path under $INSTALL_ROOT"
    printf '%s\n' "${found[0]}"
}

detect_venv_python() {
    local py="$INSTALL_ROOT/.venv313/bin/python"
    [[ -x "$py" ]] || die "Runtime python not found: $py"
    printf '%s\n' "$py"
}

detect_payload_file() {
    local rel="$1"
    local src="$PAYLOAD_DIR/$rel"
    [[ -f "$src" ]] || die "Payload file not found: $src"
    printf '%s\n' "$src"
}

begin_install() {
    local block="$1"
    INSTALL_BLOCK="$block"
    local stamp
    stamp="$(date +%F-%H%M%S)"
    BACKUP_DIR="$BACKUP_ROOT/${stamp}-${block}"
    mkdir -p "$BACKUP_DIR/files"
    : > "$BACKUP_DIR/created-files.txt"
    : > "$BACKUP_DIR/installed-files.txt"
    : > "$BACKUP_DIR/capabilities.txt"
    printf '%s\n' "$RELEASE_COMMIT" > "$BACKUP_DIR/release-commit.txt"
    printf '%s\n' "$RELEASE_VERSION" > "$BACKUP_DIR/release-version.txt"
    printf '%s\n' "$stamp" > "$BACKUP_DIR/timestamp.txt"
    INSTALL_SUCCESS=0
    SERVICES_RESTARTED=0
    trap 'on_install_error $?' ERR
    step "Backup directory: $BACKUP_DIR"
}

finish_install() {
    INSTALL_SUCCESS=1
    trap - ERR
    printf '%s\n' "$BACKUP_DIR" > "$BACKUP_ROOT/latest"
}

on_install_error() {
    local rc="$1"
    local restore_db=0
    trap - ERR
    if [[ -n "$BACKUP_DIR" && -d "$BACKUP_DIR" ]]; then
        [[ -f "$BACKUP_DIR/db-dump.sql" ]] && restore_db=1
        echo "[$INSTALL_BLOCK][ERROR] install failed, rolling back backup $BACKUP_DIR" >&2
        rollback_backup_dir "$BACKUP_DIR" "$restore_db" "$SERVICES_RESTARTED" || true
    fi
    exit "$rc"
}

backup_target() {
    local target="$1"
    local backup_path="$BACKUP_DIR/files/${target#/}"
    if [[ -e "$target" ]]; then
        mkdir -p "$(dirname "$backup_path")"
        cp -a "$target" "$backup_path"
    else
        printf '%s\n' "$target" >> "$BACKUP_DIR/created-files.txt"
    fi
}

record_installed_file() {
    local target="$1"
    printf '%s\n' "$target" >> "$BACKUP_DIR/installed-files.txt"
}

record_capability() {
    local capability="$1"
    printf '%s\n' "$capability" >> "$BACKUP_DIR/capabilities.txt"
}

install_payload_file() {
    local payload_rel="$1"
    local target="$2"
    local mode="${3:-0644}"
    local src
    src="$(detect_payload_file "$payload_rel")"
    backup_target "$target"
    mkdir -p "$(dirname "$target")"
    install -m "$mode" "$src" "$target"
    record_installed_file "$target"
}

compile_without_pyc() {
    local py
    py="$(detect_venv_python)"
    "$py" - "$@" <<'PY'
import pathlib
import sys

for arg in sys.argv[1:]:
    path = pathlib.Path(arg)
    source = path.read_text(encoding="utf-8")
    compile(source, str(path), "exec")
    print(f"compile-ok {path}")
PY
}

import_smoke() {
    local py
    py="$(detect_venv_python)"
    sudo -H -u "$PANEL_USER" env PYTHONUNBUFFERED=1 bash -lc "cd '$INSTALL_ROOT/hiddify-panel' && '$py' - \"\$@\"" bash "$@" <<'PY'
import importlib
import sys

mods = sys.argv[1:]
for mod in mods:
    importlib.import_module(mod)
    print(f"import-ok {mod}")
PY
}

create_app_smoke() {
    local py
    py="$(detect_venv_python)"
    sudo -H -u "$PANEL_USER" env PYTHONUNBUFFERED=1 bash -lc "cd '$INSTALL_ROOT/hiddify-panel' && '$py' -c \"import hiddifypanel; app=hiddifypanel.create_app(); print('create_app OK', app.name)\""
}

install_runtime_requirements() {
    local py req
    py="$(detect_venv_python)"
    req="$INSTALL_ROOT/scripts/commercial-runtime-requirements.txt"
    [[ -f "$req" ]] || die "Missing requirements file: $req"
    if ! "$py" -m pip --version >/dev/null 2>&1; then
        "$py" -m ensurepip --upgrade >/dev/null 2>&1 || die "Failed to bootstrap pip for $py"
    fi
    "$py" -m pip install -r "$req"
    sudo -H -u "$PANEL_USER" env PYTHONUNBUFFERED=1 bash -lc "cd '$INSTALL_ROOT/hiddify-panel' && '$py' -c \"import socks; print('PySocks import OK')\""
}

check_services_active() {
    [[ "$(systemctl is-active "$SERVICE_PANEL")" == "active" ]] || die "$SERVICE_PANEL is not active"
    [[ "$(systemctl is-active "$SERVICE_BG")" == "active" ]] || die "$SERVICE_BG is not active"
}

check_port_9000() {
    local waited=0 interval=5 max=120
    while ! ss -lntp 2>/dev/null | grep -qE '127\.0\.0\.1:9000|0\.0\.0\.0:9000|:::9000'; do
        if [[ $waited -ge $max ]]; then
            die "9000 is not listening after ${max}s"
        fi
        sleep $interval
        waited=$((waited + interval))
        log "waiting for port 9000... ${waited}s"
    done
}

check_front_proxy_ports() {
    ss -lntp | grep -qE '0\.0\.0\.0:80|:::80' || die "80 is not listening"
    ss -lntp | grep -qE '0\.0\.0\.0:443|:::443' || die "443 is not listening"
}

check_no_proxy_env() {
    local env_dump
    env_dump="$(systemctl show -p Environment "$SERVICE_PANEL" "$SERVICE_BG")"
    if grep -qE 'HTTP_PROXY=|HTTPS_PROXY=' <<<"$env_dump"; then
        die "HTTP_PROXY/HTTPS_PROXY found in systemd Environment"
    fi
}

_curl_route_candidate() {
    local candidate="$1"
    local domain="${2:-}"
    local output code
    if [[ "$candidate" == https://127.0.0.1/* || "$candidate" == http://127.0.0.1/* ]]; then
        output="$(curl -k -i --max-time 10 "$candidate" 2>&1 || true)"
    elif [[ -n "$domain" && "$candidate" == https://"$domain"/* ]]; then
        output="$(curl -k -i --max-time 10 --resolve "${domain}:443:127.0.0.1" "$candidate" 2>&1 || true)"
    elif [[ -n "$domain" && "$candidate" == http://"$domain"/* ]]; then
        output="$(curl -k -i --max-time 10 --resolve "${domain}:80:127.0.0.1" "$candidate" 2>&1 || true)"
    else
        output="$(curl -k -i --max-time 10 "$candidate" 2>&1 || true)"
    fi
    code="$(sed -n 's/^HTTP\/[^ ]* \([0-9][0-9][0-9]\).*/\1/p' <<<"$output" | tail -n 1)"
    if [[ "$code" == "500" ]]; then
        die "Route returned HTTP 500 for $candidate"
    fi
    case "$code" in
        200|302|400|401|403)
            echo "route-ok $candidate -> $code"
            return 0
            ;;
    esac
    return 1
}

_normalize_admin_rule() {
    local rule="$1"
    local proxy_path="$2"
    rule="${rule//<proxy_path>/$proxy_path}"
    rule="$(sed -E 's#<int:[^>]+>#1#g; s#<[^>]+>##g' <<<"$rule")"
    printf '%s\n' "$rule"
}

_load_admin_runtime_metadata() {
    local py
    py="$(detect_venv_python)"
    sudo -H -u "$PANEL_USER" env PYTHONUNBUFFERED=1 \
        bash -lc "cd '$INSTALL_ROOT/hiddify-panel' && '$py' -" <<'PY'
from hiddifypanel import create_app
from hiddifypanel.models import ConfigEnum, Domain, hconfig

app = create_app()
with app.app_context():
    proxy_path = hconfig(ConfigEnum.proxy_path_admin) or hconfig(ConfigEnum.proxy_path)
    domain = None
    try:
        db_domain = Domain.query.filter(Domain.mode != None).first() or Domain.query.first()
        if db_domain:
            domain = db_domain.domain
    except Exception:
        domain = None
    print('proxy_path=' + (proxy_path or ''))
    print('domain=' + (domain or ''))
    known = (
        'admin.BusinessAdmin:index',
        'flask.plans.index_view',
        'admin.RoutingAdmin:index',
        'admin.AntiShareAdmin:index',
    )
    for endpoint in known:
        rule = next((item.rule for item in app.url_map.iter_rules() if item.endpoint == endpoint), "")
        print('rule=%s\t%s' % (endpoint, rule))
PY
}

# TRAP: hits="$( ... )" -- the closing )" on its own line is mandatory.
# Missing " causes bash to scan for a balancing " until EOF and fail with
# "unexpected EOF while looking for matching `"'" (bash 5.x, Ubuntu).
check_runtime_text_integrity() {
    local runtime_path py
    runtime_path="$(detect_runtime_path)"
    py="$(detect_venv_python)"
    local suspect_files=(
      "$runtime_path/templates/admin-layout.html"
      "$runtime_path/templates/business-settings.html"
      "$runtime_path/panel/admin/BusinessAdmin.py"
      "$runtime_path/panel/admin/PlanAdmin.py"
    )
    local hits
    hits="$(
      "$py" - "${suspect_files[@]}" <<'PY'
from pathlib import Path
import sys

patterns = ["\u0420\u045f", "\u0420\u2018", "\u0420\u045e", "\u0420\u045c", "\u00d0", "\u00d1"]
for name in sys.argv[1:]:
    text = Path(name).read_text(encoding="utf-8", errors="ignore")
    for pattern in patterns:
        if pattern in text:
            print(name)
            raise SystemExit(0)
raise SystemExit(0)
PY
    )"
    [[ -z "$hits" ]] || die "Mojibake markers found in runtime files"
}

admin_route_smoke() {
    local proxy_path="" domain="" rule_line endpoint rule business_rule="" plans_rule=""
    mapfile -t metadata < <(_load_admin_runtime_metadata)
    for rule_line in "${metadata[@]}"; do
        case "$rule_line" in
            proxy_path=*) proxy_path="${rule_line#proxy_path=}" ;;
            domain=*) domain="${rule_line#domain=}" ;;
            rule=*)
                endpoint="${rule_line#rule=}"
                endpoint="${endpoint%%$'\t'*}"
                rule="${rule_line#*$'\t'}"
                case "$endpoint" in
                    admin.BusinessAdmin:index) business_rule="$rule" ;;
                    flask.plans.index_view) plans_rule="$rule" ;;
                esac
                ;;
        esac
    done
    [[ -n "$proxy_path" ]] || die "Cannot determine admin proxy path for route smoke"

    local admin_path="/$proxy_path/admin/"
    _curl_route_candidate "http://127.0.0.1$admin_path" "$domain" \
        || _curl_route_candidate "https://127.0.0.1$admin_path" "$domain" \
        || { [[ -n "$domain" ]] && _curl_route_candidate "http://$domain$admin_path" "$domain"; } \
        || { [[ -n "$domain" ]] && _curl_route_candidate "https://$domain$admin_path" "$domain"; } \
        || die "Admin route smoke did not produce an acceptable HTTP response"

    if [[ -n "$business_rule" ]]; then
        local business_path
        business_path="$(_normalize_admin_rule "$business_rule" "$proxy_path")"
        _curl_route_candidate "http://127.0.0.1$business_path" "$domain" || die "Business route smoke failed"
    fi
    if [[ -n "$plans_rule" ]]; then
        local plans_path
        plans_path="$(_normalize_admin_rule "$plans_rule" "$proxy_path")"
        _curl_route_candidate "http://127.0.0.1$plans_path" "$domain" || die "Tariffs route smoke failed"
    fi
}

check_logs_since() {
    local since="$1"
    local errors=""
    errors="$(journalctl -u "$SERVICE_PANEL" --since "$since" --no-pager -o cat | grep -Ei "$LOG_ERROR_RE" || true)"
    [[ -z "$errors" ]] || die "Panel log errors found since $since"
    errors="$(journalctl -u "$SERVICE_BG" --since "$since" --no-pager -o cat | grep -Ei "$LOG_ERROR_RE" || true)"
    [[ -z "$errors" ]] || die "Background log errors found since $since"
}

restart_and_verify() {
    local since="$1"
    systemctl restart "$SERVICE_PANEL" "$SERVICE_BG"
    SERVICES_RESTARTED=1
    sleep 10
    check_services_active
    check_port_9000
    create_app_smoke
    check_no_proxy_env
    check_logs_since "$since"
}

write_manifest_section() {
    local block="$1"
    local runtime_path="$2"
    local db_dump_path="${3:-}"
    local stamp
    stamp="$(date '+%Y-%m-%d %H:%M:%S')"
    {
        if [[ ! -f "$MANIFEST_PATH" ]]; then
            printf 'release_version=%s\n' "$RELEASE_VERSION"
            printf 'release_tag=%s\n' "$RELEASE_TAG"
            printf 'git_commit=%s\n' "$RELEASE_COMMIT"
            printf '\n'
        fi
        printf '[%s]\n' "$block"
        printf 'timestamp=%s\n' "$stamp"
        printf 'runtime_path=%s\n' "$runtime_path"
        printf 'backup_dir=%s\n' "$BACKUP_DIR"
        printf 'db_dump=%s\n' "$db_dump_path"
        printf 'capabilities=%s\n' "$(paste -sd, "$BACKUP_DIR/capabilities.txt" 2>/dev/null || true)"
        printf 'installed_files:\n'
        sed 's/^/  /' "$BACKUP_DIR/installed-files.txt"
        printf '\n'
    } >> "$MANIFEST_PATH"
}

collect_checkpoint_status() {
    local out_dir="$1"
    mkdir -p "$out_dir"
    systemctl status "$SERVICE_PANEL" "$SERVICE_BG" --no-pager > "$out_dir/systemctl-status.txt" 2>&1 || true
    ss -lntp > "$out_dir/ss-lntp.txt" 2>&1 || true
    create_app_smoke > "$out_dir/create-app.txt" 2>&1 || true
    tail -n 120 /opt/hiddify-manager/log/system/hiddify_panel.err.log > "$out_dir/hiddify-panel-err-tail.txt" 2>&1 || true
    journalctl -u "$SERVICE_PANEL" -u "$SERVICE_BG" -n 160 --no-pager > "$out_dir/journal-tail.txt" 2>&1 || true
}

filter_expected_log_noise() {
    sed '/Telegram bot token is not configured/d; /YooKassa credentials are empty/d'
}

check_logs_since_filtered() {
    local since="$1"
    local errors=""
    errors="$(journalctl -u "$SERVICE_PANEL" --since "$since" --no-pager -o cat | filter_expected_log_noise | grep -Ei "$LOG_ERROR_RE" || true)"
    [[ -z "$errors" ]] || die "Panel log errors found since $since"
    errors="$(journalctl -u "$SERVICE_BG" --since "$since" --no-pager -o cat | filter_expected_log_noise | grep -Ei "$LOG_ERROR_RE" || true)"
    [[ -z "$errors" ]] || die "Background log errors found since $since"
}

rollback_backup_dir() {
    local backup_dir="$1"
    local restore_db="${2:-0}"
    local restart_services="${3:-1}"
    local created_file target backup_path

    [[ -d "$backup_dir" ]] || die "Backup dir not found: $backup_dir"

    if [[ -f "$backup_dir/installed-files.txt" ]]; then
        tac "$backup_dir/installed-files.txt" | while IFS= read -r target; do
            [[ -n "$target" ]] || continue
            backup_path="$backup_dir/files/${target#/}"
            if [[ -e "$backup_path" ]]; then
                mkdir -p "$(dirname "$target")"
                cp -a "$backup_path" "$target"
            fi
        done
    fi

    if [[ -f "$backup_dir/created-files.txt" ]]; then
        tac "$backup_dir/created-files.txt" | while IFS= read -r created_file; do
            [[ -n "$created_file" ]] || continue
            rm -f "$created_file"
        done
    fi

    if [[ "$restore_db" == "1" && -f "$backup_dir/db-dump.sql" ]]; then
        mysql hiddifypanel < "$backup_dir/db-dump.sql"
    fi

    if [[ "$restart_services" == "1" ]]; then
        systemctl restart "$SERVICE_PANEL" "$SERVICE_BG"
        sleep 10
        check_services_active
        check_port_9000
    fi
}

save_db_dump_if_needed() {
    local need_dump="${1:-0}"
    if [[ "$need_dump" != "1" ]]; then
        return 0
    fi
    mysqldump hiddifypanel > "$BACKUP_DIR/db-dump.sql"
    mysql hiddifypanel -e "SHOW TABLES; DESCRIBE str_config; DESCRIBE bool_config;" > "$BACKUP_DIR/schema-snapshot.txt"
}

assert_canonical_payload() {
    [[ ! -d "$SCRIPT_DIR/business-installer/payload" ]] \
        || die "Nested business-installer payload detected. Packaging layout is broken. Remove $SCRIPT_DIR/business-installer before running."
    local migration_sh="$PAYLOAD_DIR/manager-overlay/scripts/commercial-tariffs-db-migrate.sh"
    [[ -f "$migration_sh" ]] || die "Canonical migration script not found: $migration_sh"
    grep -q "commercial_plan missing, creating" "$migration_sh" \
        || die "Canonical migration script is stale: expected migration messages not found. Sync payload from repo."
}

ensure_panel_secrets_env() {
    local secrets_file="/etc/hiddify-panel/panel-secrets.env"
    mkdir -p /etc/hiddify-panel
    if [[ -f "$secrets_file" ]] && grep -q "HIDDIFY_TELEGRAM_WEBHOOK_SECRET=" "$secrets_file"; then
        log "webhook secret already present in $secrets_file"
        return 0
    fi
    local secret
    secret="$(openssl rand -hex 32)"
    # Preserve existing lines, just add/update the secret
    if [[ -f "$secrets_file" ]]; then
        grep -v "^HIDDIFY_TELEGRAM_WEBHOOK_SECRET=" "$secrets_file" > "${secrets_file}.tmp" || true
        mv "${secrets_file}.tmp" "$secrets_file"
    fi
    echo "HIDDIFY_TELEGRAM_WEBHOOK_SECRET=$secret" >> "$secrets_file"
    chmod 644 "$secrets_file"
    chown root:root "$secrets_file"
    log "webhook secret written to $secrets_file"
}

register_telegram_webhook() {
    local py runtime_path
    py="$(detect_venv_python)"
    runtime_path="$(detect_runtime_path)"
    log "Registering Telegram webhook..."
    sudo -H -u "$PANEL_USER" env PYTHONUNBUFFERED=1 \
        bash -lc "cd '$INSTALL_ROOT/hiddify-panel' && '$py' -" <<'PY' || warn "Telegram webhook registration failed (non-fatal)"
import os
os.environ.setdefault('HIDDIFY_CFG_PATH', '/opt/hiddify-manager/hiddify-panel/app.cfg')
from hiddifypanel import create_app
app = create_app()
with app.app_context():
    from hiddifypanel.panel.commercial.restapi.v2.telegram.tgbot import register_bot, telegram_bot_token
    token = telegram_bot_token()
    if not token:
        print("[webhook] bot token not configured — skipping")
    else:
        register_bot(set_hook=True)
        print("[webhook] registered OK")
PY
}

ensure_telegram_owner_activation_command() {
    local owner_uuid activation_command
    mkdir -p "$TELEGRAM_ACTIVATION_DIR"
    chmod 700 "$TELEGRAM_ACTIVATION_DIR"
    chown root:root "$TELEGRAM_ACTIVATION_DIR"

    if [[ -s "$TELEGRAM_ACTIVATION_FILE" ]]; then
        chmod 600 "$TELEGRAM_ACTIVATION_FILE"
        chown root:root "$TELEGRAM_ACTIVATION_FILE"
        echo "Telegram admin activation command saved to: $TELEGRAM_ACTIVATION_FILE"
        return 0
    fi

    owner_uuid="$(get_owner_uuid)"
    [[ -n "$owner_uuid" ]] || die "Cannot determine owner UUID for Telegram activation command"
    activation_command="/start admin_$owner_uuid"
    printf '%s\n' "$activation_command" > "$TELEGRAM_ACTIVATION_FILE"
    chmod 600 "$TELEGRAM_ACTIVATION_FILE"
    chown root:root "$TELEGRAM_ACTIVATION_FILE"
    echo "Telegram admin activation command saved to: $TELEGRAM_ACTIVATION_FILE"
}
