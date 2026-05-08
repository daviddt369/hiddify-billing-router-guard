#!/usr/bin/env bash
set -Eeuo pipefail

readonly RELEASE_VERSION="antishare-1-20260508"
readonly RELEASE_TAG="antishare-1-20260508"
readonly RELEASE_COMMIT="HEAD"
readonly SERVICE_PANEL="hiddify-panel"
readonly SERVICE_BG="hiddify-panel-background-tasks"
readonly ANTISHARE_SERVICE="hiddify-anti-share"
readonly ANTISHARE_TIMER="hiddify-anti-share.timer"
readonly ANTISHARE_SERVICE_FILE="/etc/systemd/system/hiddify-anti-share.service"
readonly ANTISHARE_TIMER_FILE="/etc/systemd/system/hiddify-anti-share.timer"
readonly PANEL_USER="hiddify-panel"
readonly INSTALL_ROOT="${INSTALL_ROOT:-/opt/hiddify-manager}"
readonly BACKUP_ROOT="${BACKUP_ROOT:-$INSTALL_ROOT/antishare-installer-backups}"
readonly MANIFEST_PATH="${MANIFEST_PATH:-$INSTALL_ROOT/anti-share-addon.manifest}"
readonly BUSINESS_MANIFEST_PATH="${BUSINESS_MANIFEST_PATH:-$INSTALL_ROOT/business-addon.manifest}"
readonly ROUTING_MANIFEST_PATH="${ROUTING_MANIFEST_PATH:-$INSTALL_ROOT/routing-addon.manifest}"
readonly NFT_HELPER_PATH="${NFT_HELPER_PATH:-$INSTALL_ROOT/common/hiddify-antishare-nft.sh}"
readonly SUDOERS_FILE="/etc/sudoers.d/91-hiddify-panel-antishare"
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly PAYLOAD_DIR="$SCRIPT_DIR/payload"
readonly DB_MIGRATE_SCRIPT="$SCRIPT_DIR/scripts/commercial-antishare-db-migrate.sh"
readonly DB_NAME="${DB_NAME:-hiddifypanel}"

BACKUP_DIR=""
INSTALL_BLOCK=""
INSTALL_SUCCESS=0
SERVICES_RESTARTED=0

log()  { echo "[$INSTALL_BLOCK] $*"; }
step() { echo; echo "[$INSTALL_BLOCK][STEP] $*"; }
die() {
    echo "[$INSTALL_BLOCK][ERROR] $*" >&2
    if [[ -n "${BACKUP_DIR:-}" && "${INSTALL_SUCCESS:-0}" == "0" && -d "${BACKUP_DIR:-}" ]]; then
        on_install_error 1
    else
        exit 1
    fi
}
warn() { echo "[$INSTALL_BLOCK][WARN] $*" >&2; }

need_cmd() { command -v "$1" >/dev/null 2>&1 || die "Missing command: $1"; }
require_root() { [[ "$(id -u)" -eq 0 ]] || die "Run as root."; }

assert_install_root() {
    [[ -d "$INSTALL_ROOT" ]] || die "Install root not found: $INSTALL_ROOT"
    [[ -d "$INSTALL_ROOT/hiddify-panel" ]] || die "Panel root not found: $INSTALL_ROOT/hiddify-panel"
}

assert_services_exist() {
    systemctl cat "$SERVICE_PANEL" >/dev/null 2>&1 || die "Missing systemd unit: $SERVICE_PANEL"
    systemctl cat "$SERVICE_BG"    >/dev/null 2>&1 || die "Missing systemd unit: $SERVICE_BG"
}

assert_business_installed() {
    [[ -f "$BUSINESS_MANIFEST_PATH" ]] \
        || die "Business addon manifest not found: $BUSINESS_MANIFEST_PATH. Install business addon first."
}

# Routing is optional but if manifest exists we verify services are active.
assert_routing_safe() {
    if [[ -f "$ROUTING_MANIFEST_PATH" ]]; then
        log "Routing addon detected — verifying panel services are active before anti-share install"
        [[ "$(systemctl is-active "$SERVICE_PANEL")" == "active" ]] \
            || die "$SERVICE_PANEL must be active when routing is installed (routing may not survive panel failure)"
        [[ "$(systemctl is-active "$SERVICE_BG")" == "active" ]] \
            || die "$SERVICE_BG must be active when routing is installed"
        log "Routing addon present and panel services active — safe to continue"
    else
        log "Routing addon not detected — installing anti-share over business-only stack"
    fi
}

# TRAP: anti-share installer must not be run from inside a nested payload copy.
assert_canonical_payload() {
    [[ ! -d "$SCRIPT_DIR/antishare-installer/payload" ]] \
        || die "Nested antishare-installer payload detected. Packaging layout is broken."
    [[ -f "$DB_MIGRATE_SCRIPT" ]] \
        || die "DB migration script not found: $DB_MIGRATE_SCRIPT. Packaging layout is broken."
    [[ -f "$PAYLOAD_DIR/panel-overlay/hiddifypanel/antishare/runner.py" ]] \
        || die "Canonical payload missing antishare/runner.py. Packaging layout is broken."
    [[ -f "$PAYLOAD_DIR/panel-overlay/hiddifypanel/panel/admin/AntiShareAdmin.py" ]] \
        || die "Canonical payload missing AntiShareAdmin.py. Packaging layout is broken."
}

detect_runtime_path() {
    mapfile -t found < <(find "$INSTALL_ROOT" -type d -path '*/site-packages/hiddifypanel' 2>/dev/null | sort)
    [[ "${#found[@]}" -gt 0 ]] || die "Cannot detect hiddifypanel runtime path under $INSTALL_ROOT"
    printf '%s\n' "${found[0]}"
}

detect_venv_python() {
    local py="$INSTALL_ROOT/.venv313/bin/python"
    [[ -x "$py" ]] || die "Venv python not found: $py"
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
    trap - ERR
    if [[ -n "$BACKUP_DIR" && -d "$BACKUP_DIR" ]]; then
        echo "[$INSTALL_BLOCK][ERROR] install failed, rolling back backup $BACKUP_DIR" >&2
        rollback_backup_dir "$BACKUP_DIR" 0 "$SERVICES_RESTARTED" || true
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

record_installed_file() { printf '%s\n' "$1" >> "$BACKUP_DIR/installed-files.txt"; }

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

# Compile (syntax check only) — must NOT call create_app.
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

import_antishare_smoke() {
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
    sudo -H -u "$PANEL_USER" env PYTHONUNBUFFERED=1 \
        bash -lc "cd '$INSTALL_ROOT/hiddify-panel' && '$py' -c \"import hiddifypanel; app=hiddifypanel.create_app(); print('create_app OK', app.name)\""
}

check_services_active() {
    [[ "$(systemctl is-active "$SERVICE_PANEL")" == "active" ]] || die "$SERVICE_PANEL is not active"
    [[ "$(systemctl is-active "$SERVICE_BG")" == "active" ]] || die "$SERVICE_BG is not active"
}

check_port_9000() {
    ss -lntp | grep -qE '127\.0\.0\.1:9000|0\.0\.0\.0:9000|:::9000' || die "Port 9000 is not listening"
}

_get_admin_proxy_path() {
    local py
    py="$(detect_venv_python)"
    sudo -H -u "$PANEL_USER" env PYTHONUNBUFFERED=1 \
        bash -lc "cd '$INSTALL_ROOT/hiddify-panel' && '$py' -" <<'PY'
from hiddifypanel import create_app
from hiddifypanel.models import ConfigEnum, hconfig

app = create_app()
with app.app_context():
    p = hconfig(ConfigEnum.proxy_path_admin) or hconfig(ConfigEnum.proxy_path) or ''
    print(p)
PY
}

_curl_check_route() {
    local url="$1"
    local output code
    output="$(curl -k -i --max-time 10 "$url" 2>&1 || true)"
    code="$(sed -n 's/^HTTP\/[^ ]* \([0-9][0-9][0-9]\).*/\1/p' <<<"$output" | tail -n 1)"
    [[ "$code" == "500" ]] && die "Route returned HTTP 500: $url"
    case "$code" in
        200|302|400|401|403) echo "route-ok $url -> $code"; return 0 ;;
    esac
    return 1
}

admin_antishare_route_smoke() {
    local proxy_path
    proxy_path="$(_get_admin_proxy_path)"
    [[ -n "$proxy_path" ]] || die "Cannot determine admin proxy path"

    local admin_path="/$proxy_path/admin/"
    _curl_check_route "http://127.0.0.1${admin_path}" \
        || _curl_check_route "https://127.0.0.1${admin_path}" \
        || die "Admin route smoke failed for $admin_path"

    local antishare_path="/$proxy_path/admin/anti-share-admin/"
    _curl_check_route "http://127.0.0.1${antishare_path}" \
        || _curl_check_route "https://127.0.0.1${antishare_path}" \
        || die "Anti-share-admin route smoke failed for $antishare_path"
}

check_antishare_endpoints() {
    local py
    py="$(detect_venv_python)"
    sudo -H -u "$PANEL_USER" env PYTHONUNBUFFERED=1 \
        bash -lc "cd '$INSTALL_ROOT/hiddify-panel' && '$py' -" <<'PY'
from hiddifypanel import create_app

app = create_app()
endpoints = {rule.endpoint for rule in app.url_map.iter_rules()}

assert 'admin.AntiShareAdmin:index' in endpoints, \
    'AntiShareAdmin:index not registered after anti-share install'
assert 'admin.BusinessAdmin:index' in endpoints, \
    'BusinessAdmin:index broken after anti-share install'

print('antishare-endpoints-ok')
PY
}

write_antishare_manifest() {
    local runtime_path="$1"
    cat > "$MANIFEST_PATH" <<EOF
release_version=$RELEASE_VERSION
release_tag=$RELEASE_TAG
git_commit=$RELEASE_COMMIT
timestamp=$(date '+%Y-%m-%d %H:%M:%S')
runtime_path=$runtime_path
backup_dir=$BACKUP_DIR
installed_files:
EOF
    while IFS= read -r f; do
        printf '  %s\n' "$f" >> "$MANIFEST_PATH"
    done < "$BACKUP_DIR/installed-files.txt"
    record_installed_file "$MANIFEST_PATH"
    log "Manifest written: $MANIFEST_PATH"
}

collect_antishare_checkpoint() {
    local status_dir="$1"
    mkdir -p "$status_dir"
    systemctl is-active "$SERVICE_PANEL"  > "$status_dir/panel-active.txt"  2>&1 || true
    systemctl is-active "$SERVICE_BG"     > "$status_dir/bg-active.txt"     2>&1 || true
    systemctl is-active "$ANTISHARE_SERVICE" > "$status_dir/antishare-service-active.txt" 2>&1 || true
    systemctl is-enabled "$ANTISHARE_TIMER"  > "$status_dir/antishare-timer-enabled.txt"  2>&1 || true
    mysql "$DB_NAME" -e "SELECT COUNT(*) AS table_count FROM information_schema.tables \
        WHERE table_schema='$DB_NAME' AND table_name LIKE 'anti_share_%';" \
        > "$status_dir/db-antishare-tables.txt" 2>/dev/null || true
    cat "$MANIFEST_PATH" > "$status_dir/manifest.txt" 2>/dev/null || true
    log "Post-install checkpoint written to $status_dir"
}

rollback_backup_dir() {
    local backup_dir="$1"
    local restore_db="${2:-0}"
    local services_restarted="${3:-0}"

    # Restore DB from dump if requested
    if [[ "$restore_db" == "1" && -f "$backup_dir/db-dump.sql" ]]; then
        log "Restoring database from $backup_dir/db-dump.sql"
        mysql "$DB_NAME" < "$backup_dir/db-dump.sql" \
            || warn "Database restore failed — manual intervention may be needed"
    fi

    # Restore backed-up files
    if [[ -d "$backup_dir/files" ]]; then
        find "$backup_dir/files" -type f | while IFS= read -r backed_up; do
            local orig_path="/${backed_up#${backup_dir}/files/}"
            mkdir -p "$(dirname "$orig_path")"
            cp -a "$backed_up" "$orig_path" \
                && log "Restored: $orig_path" \
                || warn "Failed to restore: $orig_path"
        done
    fi

    # Remove files that were newly created (didn't exist before)
    if [[ -f "$backup_dir/created-files.txt" ]]; then
        while IFS= read -r created_file; do
            [[ -z "$created_file" ]] && continue
            rm -f "$created_file" \
                && log "Removed created file: $created_file" \
                || warn "Failed to remove: $created_file"
        done < "$backup_dir/created-files.txt"
    fi

    # Restart panel services if they were restarted during install
    if [[ "$services_restarted" == "1" ]]; then
        systemctl restart "$SERVICE_PANEL" "$SERVICE_BG" 2>/dev/null || true
    fi
}
