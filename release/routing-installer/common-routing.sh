#!/usr/bin/env bash
set -Eeuo pipefail

readonly RELEASE_VERSION="routing-1-20260508"
readonly RELEASE_TAG="routing-1-20260508"
readonly RELEASE_COMMIT="bfe6851"
readonly SERVICE_PANEL="hiddify-panel"
readonly SERVICE_BG="hiddify-panel-background-tasks"
readonly XRAY_ROUTER_SERVICE="xray-router"
readonly PANEL_USER="hiddify-panel"
readonly INSTALL_ROOT="${INSTALL_ROOT:-/opt/hiddify-manager}"
readonly BACKUP_ROOT="${BACKUP_ROOT:-$INSTALL_ROOT/routing-installer-backups}"
readonly MANIFEST_PATH="${MANIFEST_PATH:-$INSTALL_ROOT/routing-addon.manifest}"
readonly BUSINESS_MANIFEST_PATH="${BUSINESS_MANIFEST_PATH:-$INSTALL_ROOT/business-addon.manifest}"
readonly COMMANDER_PATH="${COMMANDER_PATH:-$INSTALL_ROOT/common/commander.py}"
readonly XRAY_ROUTER_SERVICE_FILE="/etc/systemd/system/xray-router.service"
readonly SUDOERS_FILE="/etc/sudoers.d/90-hiddify-panel-routing"
readonly APPLY_CONFIGS="${APPLY_CONFIGS:-$INSTALL_ROOT/apply_configs.sh}"
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly PAYLOAD_DIR="$SCRIPT_DIR/payload"
readonly DB_MIGRATE_SCRIPT="$SCRIPT_DIR/scripts/commercial-routing-db-migrate.sh"
readonly DB_NAME="${DB_NAME:-hiddifypanel}"

BACKUP_DIR=""
INSTALL_BLOCK=""
INSTALL_SUCCESS=0
SERVICES_RESTARTED=0

log() { echo "[$INSTALL_BLOCK] $*"; }
step() { echo; echo "[$INSTALL_BLOCK][STEP] $*"; }
die() {
    echo "[$INSTALL_BLOCK][ERROR] $*" >&2
    # Trigger rollback if we are inside an active install (begin_install called, not yet finished)
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
    systemctl cat "$SERVICE_BG" >/dev/null 2>&1 || die "Missing systemd unit: $SERVICE_BG"
}

assert_business_installed() {
    [[ -f "$BUSINESS_MANIFEST_PATH" ]] \
        || die "Business addon manifest not found: $BUSINESS_MANIFEST_PATH. Install business addon first."
}

assert_xray_binary() {
    [[ -x "/usr/bin/xray" ]] || die "Xray binary not found or not executable: /usr/bin/xray"
}

assert_apply_configs() {
    [[ -f "$APPLY_CONFIGS" ]] || die "apply_configs.sh not found: $APPLY_CONFIGS"
    [[ -x "$APPLY_CONFIGS" ]] || die "apply_configs.sh not executable: $APPLY_CONFIGS"
}

# Redis availability check.
# Does NOT try to restart redis-server.service: if redis is already listening on :6379
# but the systemd unit shows "failed" (e.g., socket was bound before systemd tracked it),
# a restart would fail with "port already in use" and break things.
assert_redis_available() {
    if ss -lntp 2>/dev/null | grep -q ':6379'; then
        # Port :6379 is listening — Redis is up. redis-cli ping is informational only:
        # Hiddify configures Redis with requirepass, so redis-cli without a password
        # returns "NOAUTH Authentication required", not "PONG". That is expected.
        if command -v redis-cli >/dev/null 2>&1; then
            local ping_out
            ping_out="$(redis-cli ping 2>/dev/null || true)"
            if [[ "$ping_out" == "PONG" ]]; then
                log "Redis available on :6379 (ping: PONG)"
            else
                log "Redis available on :6379 (ping: ${ping_out:-no response} — auth likely required, OK)"
            fi
        else
            log "Redis available on :6379"
        fi
        return 0
    fi
    # Redis not listening at all — collect diagnostic context and fail
    {
        echo "=== redis-server status ==="
        systemctl status redis-server --no-pager -l 2>/dev/null || true
        echo "=== redis-server journal ==="
        journalctl -xeu redis-server --no-pager -n 60 2>/dev/null || true
        echo "=== ss :6379 ==="
        ss -lntp 2>/dev/null | grep ':6379' || echo "nothing on :6379"
    } >&2
    die "Redis not available: nothing listening on :6379"
}

assert_mariadb_available() {
    mysql -u root -e "SELECT 1;" >/dev/null 2>&1 \
        || mysqladmin ping >/dev/null 2>&1 \
        || die "MariaDB/MySQL not accessible from root"
    log "MariaDB accessible"
}

# Preflight checks before running apply_configs.sh.
preflight_apply_configs() {
    step "Preflight: checking services before apply_configs.sh"
    assert_mariadb_available
    assert_redis_available
    check_services_active
    [[ -x "$COMMANDER_PATH" ]] \
        || die "commander.py not executable before apply — something went wrong in patch step"
    log "Preflight passed"
}

_collect_apply_timeout_diagnostics() {
    echo "=== Process list (apply/hiddify) ==="
    ps -eo pid,ppid,etime,stat,cmd 2>/dev/null \
        | grep -E 'all-configs|apply_config|install\.sh|hiddifypanel' | head -20 || true
    echo "=== Last 120 lines of install log ==="
    tail -120 "$INSTALL_ROOT/log/system/0-install.log" 2>/dev/null || true
    echo "=== hiddify-panel journal ==="
    journalctl -u "$SERVICE_PANEL" -n 80 --no-pager -o cat 2>/dev/null || true
    echo "=== Service status ==="
    systemctl status mariadb redis-server "$SERVICE_PANEL" --no-pager -l 2>/dev/null || true
    echo "=== Redis ping ==="
    redis-cli ping 2>/dev/null || echo "redis-cli failed"
    echo "=== Ports :6379 :9000 :3306 ==="
    ss -lntp 2>/dev/null | grep -E ':6379|:9000|:3306' || true
}

run_apply_configs_with_timeout() {
    local timeout_secs=600
    need_cmd timeout
    log "Running apply_configs.sh (timeout ${timeout_secs}s)"
    if ! timeout --foreground "$timeout_secs" "$APPLY_CONFIGS" --no-gui; then
        local rc=$?
        if [[ $rc -eq 124 ]]; then
            _collect_apply_timeout_diagnostics >&2
            die "apply_configs.sh timed out after ${timeout_secs}s — see diagnostics above"
        else
            die "apply_configs.sh failed with exit code $rc"
        fi
    fi
}

# TRAP: routing installer must not be run from inside a nested routing-installer/ copy.
# Also verifies the DB migration script is present (packaging check).
assert_canonical_payload() {
    [[ ! -d "$SCRIPT_DIR/routing-installer/payload" ]] \
        || die "Nested routing-installer payload detected. Packaging layout is broken."
    [[ -f "$DB_MIGRATE_SCRIPT" ]] \
        || die "DB migration script not found: $DB_MIGRATE_SCRIPT. Packaging layout is broken."
    [[ -f "$PAYLOAD_DIR/panel-overlay/hiddifypanel/hutils/commercial_routing.py" ]] \
        || die "Canonical payload missing commercial_routing.py. Packaging layout is broken."
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

# TRAP: compile_without_pyc — must NOT call create_app or import panel modules.
# Only syntax-checks files. Safe to run before DB migration.
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

import_routing_smoke() {
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

create_app_smoke_with_upstream_routes() {
    local py
    py="$(detect_venv_python)"
    sudo -H -u "$PANEL_USER" env PYTHONUNBUFFERED=1 \
        bash -lc "cd '$INSTALL_ROOT/hiddify-panel' && '$py' -" <<'PY'
from hiddifypanel import create_app

app = create_app()
endpoints = {rule.endpoint for rule in app.url_map.iter_rules()}
rules_by_endpoint = {rule.endpoint: rule.rule for rule in app.url_map.iter_rules()}

required = [
    'admin.RoutingAdmin:upstream_list',
    'admin.RoutingAdmin:upstream_add',
    'admin.RoutingAdmin:upstream_edit',
    'admin.RoutingAdmin:upstream_delete',
    'admin.RoutingAdmin:upstream_toggle',
    'admin.RoutingAdmin:upstream_move_up',
    'admin.RoutingAdmin:upstream_move_down',
]

missing = [ep for ep in required if ep not in endpoints]
if missing:
    raise AssertionError("Missing upstream endpoints: " + ", ".join(missing))

for ep in required:
    print(f"endpoint-ok {ep} -> {rules_by_endpoint.get(ep, '?')}")
PY
}

check_routing_endpoints() {
    local py antishare_manifest
    py="$(detect_venv_python)"
    antishare_manifest="${ANTISHARE_MANIFEST_PATH:-$INSTALL_ROOT/anti-share-addon.manifest}"

    # If antishare is installed, AntiShareAdmin will be registered — this is expected
    # on a full-stack install (routing + antishare) and is NOT a routing bug.
    if [[ -f "$antishare_manifest" ]]; then
        sudo -H -u "$PANEL_USER" env PYTHONUNBUFFERED=1 \
            bash -lc "cd '$INSTALL_ROOT/hiddify-panel' && '$py' -" <<'PY'
from hiddifypanel import create_app

app = create_app()
endpoints = {rule.endpoint for rule in app.url_map.iter_rules()}
assert 'admin.RoutingAdmin:index' in endpoints, 'RoutingAdmin endpoint missing after routing install'
assert 'admin.BusinessAdmin:index' in endpoints, 'BusinessAdmin endpoint broken after routing install'
# AntiShareAdmin presence is expected (antishare installed) — not asserting absence
print('routing-endpoints-ok (full-stack: antishare also present)')
PY
    else
        sudo -H -u "$PANEL_USER" env PYTHONUNBUFFERED=1 \
            bash -lc "cd '$INSTALL_ROOT/hiddify-panel' && '$py' -" <<'PY'
from hiddifypanel import create_app

app = create_app()
endpoints = {rule.endpoint for rule in app.url_map.iter_rules()}
assert 'admin.RoutingAdmin:index' in endpoints, 'RoutingAdmin endpoint missing after routing install'
assert 'admin.BusinessAdmin:index' in endpoints, 'BusinessAdmin endpoint broken after routing install'
assert 'admin.AntiShareAdmin:index' not in endpoints, 'AntiShareAdmin must not be installed by routing'
print('routing-endpoints-ok')
PY
    fi
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

admin_routing_route_smoke() {
    local proxy_path
    proxy_path="$(_get_admin_proxy_path)"
    [[ -n "$proxy_path" ]] || die "Cannot determine admin proxy path"

    local admin_path="/$proxy_path/admin/"
    _curl_check_route "http://127.0.0.1${admin_path}" \
        || _curl_check_route "https://127.0.0.1${admin_path}" \
        || die "Admin route smoke failed for $admin_path"

    local routing_path="/$proxy_path/admin/routing-admin/"
    _curl_check_route "http://127.0.0.1${routing_path}" \
        || _curl_check_route "https://127.0.0.1${routing_path}" \
        || die "Routing-admin route smoke failed for $routing_path"
}

check_services_active() {
    [[ "$(systemctl is-active "$SERVICE_PANEL")" == "active" ]] || die "$SERVICE_PANEL is not active"
    [[ "$(systemctl is-active "$SERVICE_BG")" == "active" ]] || die "$SERVICE_BG is not active"
}

check_port_9000() {
    # Poll for port 9000 with backoff — production servers may take >30s to start
    # (more DB data, longer init_db/create_all cycle).
    local max_wait=120 interval=5 elapsed=0
    while [[ $elapsed -lt $max_wait ]]; do
        if ss -lntp 2>/dev/null | grep -qE '127\.0\.0\.1:9000|0\.0\.0\.0:9000|:::9000|\*:9000'; then
            log "Port 9000 ready after ${elapsed}s"
            return 0
        fi
        sleep "$interval"
        elapsed=$(( elapsed + interval ))
        log "Waiting for port 9000... ${elapsed}s/${max_wait}s"
    done
    die "Port 9000 not listening after ${max_wait}s — panel failed to start"
}

# Returns 0 if active, 1 if inactive (no upstream config), 2 if failed (config error)
check_xray_router_unit_state() {
    local active sub
    active="$(systemctl show "$XRAY_ROUTER_SERVICE" --property=ActiveState --value 2>/dev/null || echo unknown)"
    sub="$(systemctl show "$XRAY_ROUTER_SERVICE" --property=SubState --value 2>/dev/null || echo unknown)"

    case "$active/$sub" in
        active/running)   echo "xray-router-active"; return 0 ;;
        inactive/dead)    warn "xray-router inactive/dead (no upstream node configured — expected on clean VM)"; return 1 ;;
        failed/*)         return 2 ;;
        activating/auto-restart) return 2 ;;
        *)                warn "xray-router state: $active/$sub"; return 1 ;;
    esac
}

patch_commander_routing() {
    [[ -f "$COMMANDER_PATH" ]] || die "commander.py not found: $COMMANDER_PATH"

    # Idempotent: skip if already patched
    if grep -q 'ROUTING_INSTALL_BEGIN' "$COMMANDER_PATH"; then
        log "commander.py already patched, skipping"
        return 0
    fi

    # Validate expected structure
    grep -q 'if __name__' "$COMMANDER_PATH" \
        || die "commander.py structure unexpected: 'if __name__' block not found"

    # Backup before patching
    backup_target "$COMMANDER_PATH"

    local py
    py="$(detect_venv_python)"

    # Write patch content to temp file
    local patch_tmp
    patch_tmp="$(mktemp)"
    cat > "$patch_tmp" <<'PATCH'

# ROUTING_INSTALL_BEGIN --- do not remove this line
@cli.command('commercial-routing-apply')
def commercial_routing_apply():
    from hiddifypanel import create_app

    service_path = '/etc/systemd/system/xray-router.service'
    service_body = """[Unit]
Description=Local Xray router-core for commercial routing
After=network.target nss-lookup.target

[Service]
User=root
ExecStart=/usr/bin/xray run -config /etc/xray-router/config.json
Restart=always
RestartSec=3
LimitNOFILE=infinity

[Install]
WantedBy=multi-user.target
"""

    os.makedirs('/etc/xray-router', exist_ok=True)
    if not os.path.exists(service_path):
        with open(service_path, 'w', encoding='utf-8') as fh:
            fh.write(service_body)
        subprocess.run(['systemctl', 'daemon-reload'], check=True)
        subprocess.run(['systemctl', 'enable', 'xray-router'], check=False)

    os.environ.setdefault('HIDDIFY_CFG_PATH', '/opt/hiddify-manager/hiddify-panel/app.cfg')
    os.environ.setdefault('HIDDIFY_CONFIG_PATH', '/opt/hiddify-manager/')
    os.chdir('/opt/hiddify-manager/hiddify-panel')

    app = create_app(app_mode='cli')
    with app.app_context():
        from hiddifypanel.hutils.commercial_routing import apply_router_core_config
        result = apply_router_core_config()
    subprocess.run(['systemctl', 'enable', 'xray-router'], check=False)
    print(
        f"Applied {result.target_path} and restarted {result.service_name} custom_rules={result.custom_rules_total}"
    )
# ROUTING_INSTALL_END --- do not remove this line
PATCH

    # Use Python to insert patch before "if __name__" line (more reliable than sed)
    local patched_tmp
    patched_tmp="$(mktemp)"
    "$py" - "$COMMANDER_PATH" "$patch_tmp" "$patched_tmp" <<'PY'
import sys

orig_path, patch_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]

with open(orig_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

with open(patch_path, 'r', encoding='utf-8') as f:
    patch_content = f.read()

insert_idx = None
for i, line in enumerate(lines):
    if line.startswith('if __name__'):
        insert_idx = i
        break

if insert_idx is None:
    print("ERROR: if __name__ not found in commander.py", file=sys.stderr)
    sys.exit(1)

new_lines = lines[:insert_idx] + [patch_content + '\n'] + lines[insert_idx:]

with open(out_path, 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print(f"Patched: inserted routing command before line {insert_idx + 1}")
PY

    rm -f "$patch_tmp"

    # Validate patched file
    "$py" -m py_compile "$patched_tmp" || {
        rm -f "$patched_tmp"
        die "commander.py patch produced invalid Python syntax"
    }

    # Write patched content INTO the existing file (cat > keeps the inode, preserving
    # owner/group/ACLs). Do NOT use mv or install: both replace the inode and may drop
    # permissions (install respects the source mode, which for mktemp is 0600).
    cat "$patched_tmp" > "$COMMANDER_PATH"
    rm -f "$patched_tmp"

    # commander.py is called directly by BusinessAdmin via:
    #   sudo -n /opt/hiddify-manager/common/commander.py commercial-routing-apply
    # The shebang requires +x. Ensure it is set regardless of the original mode.
    chmod a+x "$COMMANDER_PATH"

    # Post-patch sanity
    [[ -x "$COMMANDER_PATH" ]] \
        || die "commander.py still not executable after chmod a+x"

    printf 'true\n' > "$BACKUP_DIR/commander-patched.flag"
    log "commander.py patched successfully (mode $(stat -c '%a' "$COMMANDER_PATH"))"
}

rollback_commander_routing() {
    if [[ -f "$BACKUP_DIR/commander-patched.flag" && -f "$COMMANDER_PATH" ]]; then
        sed -i '/# ROUTING_INSTALL_BEGIN/,/# ROUTING_INSTALL_END/d' "$COMMANDER_PATH"
        log "commander.py routing patch removed"
    fi
}

# Patch panel/admin/__init__.py to import RoutingAdmin from RoutingUpstreamAdmin.
# Uses guard markers; idempotent. Fails hard if insertion point not found.
patch_routing_upstream_admin() {
    local init_py="$1"

    [[ -f "$init_py" ]] || die "panel/admin/__init__.py not found: $init_py"

    if grep -q 'ROUTING_UPSTREAM_ADMIN_BEGIN' "$init_py"; then
        log "__init__.py already patched for RoutingUpstreamAdmin, skipping"
        return 0
    fi

    grep -q 'from .AntiShareAdmin import AntiShareAdmin' "$init_py" \
        || die "__init__.py structure unexpected: 'from .AntiShareAdmin import AntiShareAdmin' not found — not patching"

    backup_target "$init_py"

    local py patched_tmp
    py="$(detect_venv_python)"
    patched_tmp="$(mktemp)"

    "$py" - "$init_py" "$patched_tmp" <<'PY'
import sys
orig_path, out_path = sys.argv[1], sys.argv[2]

patch_block = (
    "    # ROUTING_UPSTREAM_ADMIN_BEGIN --- do not remove this line\n"
    "    if routing_enabled and RoutingAdmin is not None:\n"
    "        try:\n"
    "            from .RoutingUpstreamAdmin import RoutingAdmin\n"
    "        except Exception:\n"
    "            logger.exception(\"RoutingUpstreamAdmin unavailable; falling back to base RoutingAdmin\")\n"
    "    # ROUTING_UPSTREAM_ADMIN_END --- do not remove this line\n"
)

with open(orig_path, 'r', encoding='utf-8') as f:
    content = f.read()

needle = '    try:\n        from .AntiShareAdmin import AntiShareAdmin'
if needle not in content:
    print("ERROR: insertion point not found in __init__.py", file=sys.stderr)
    sys.exit(1)

idx = content.index(needle)
new_content = content[:idx] + patch_block + '\n' + content[idx:]

with open(out_path, 'w', encoding='utf-8') as f:
    f.write(new_content)

print("Patched __init__.py: inserted ROUTING_UPSTREAM_ADMIN block")
PY

    "$py" -m py_compile "$patched_tmp" || {
        rm -f "$patched_tmp"
        die "__init__.py patch produced invalid Python syntax"
    }

    cat "$patched_tmp" > "$init_py"
    rm -f "$patched_tmp"

    record_installed_file "$init_py"
    printf '%s\n' "$init_py" > "$BACKUP_DIR/upstream-admin-init-path.txt"
    printf 'true\n' > "$BACKUP_DIR/upstream-admin-init-patched.flag"
    log "__init__.py patched for RoutingUpstreamAdmin"
}

# Patch business-settings.html: add upstream button after "Politika router-core" h4.
# Uses HTML comment guard markers; idempotent.
patch_business_settings_upstream_link() {
    local tmpl="$1"

    [[ -f "$tmpl" ]] || die "business-settings.html not found: $tmpl"

    if grep -q 'ROUTING_UPSTREAM_UI_BEGIN' "$tmpl"; then
        log "business-settings.html already patched with upstream link, skipping"
        return 0
    fi

    # Insertion point: after <h4>Politika router-core</h4>
    grep -q 'Политика router-core' "$tmpl" \
        || die "business-settings.html: 'Политика router-core' heading not found"

    backup_target "$tmpl"

    local py patched_tmp
    py="$(detect_venv_python)"
    patched_tmp="$(mktemp)"

    "$py" - "$tmpl" "$patched_tmp" <<'PY'
import sys
orig_path, out_path = sys.argv[1], sys.argv[2]

patch_block = (
    "            <!-- ROUTING_UPSTREAM_UI_BEGIN -->\n"
    "            <div class=\"mb-3\">\n"
    "              <a href=\"{{ routing_section_url.split('?')[0].rstrip('/') }}/upstreams/\""
    " class=\"btn btn-primary\">&#9881; Внешние ноды (upstream)</a>\n"
    "              <small class=\"form-text text-muted d-block mt-1\">"
    "VLESS / Trojan / WireGuard. Принимает нелокальный трафик и направляет наружу."
    " Поддерживается несколько нод с авто-failover и балансировкой leastPing.</small>\n"
    "            </div>\n"
    "            <!-- ROUTING_UPSTREAM_UI_END -->\n"
)

with open(orig_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

insert_after = None
for i, line in enumerate(lines):
    if '<h4>' in line and 'router-core' in line and 'Политика' in line:
        insert_after = i
        break

if insert_after is None:
    print("ERROR: 'Политика router-core' h4 not found", file=sys.stderr)
    sys.exit(1)

# Insert AFTER the h4 line
new_lines = lines[:insert_after + 1] + [patch_block] + lines[insert_after + 1:]

with open(out_path, 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print(f"Patched: inserted ROUTING_UPSTREAM_UI block after line {insert_after + 1}")
PY

    cat "$patched_tmp" > "$tmpl"
    rm -f "$patched_tmp"

    record_installed_file "$tmpl"
    printf '%s\n' "$tmpl" > "$BACKUP_DIR/upstream-ui-tmpl-path.txt"
    printf 'true\n' > "$BACKUP_DIR/upstream-ui-tmpl-patched.flag"
    log "business-settings.html patched with upstream link (after Politika router-core)"
}

# Hide legacy single-upstream fields (commercial_de_*) in business-settings.html.
# Wraps the block in display:none with guard markers; idempotent.
patch_business_settings_hide_legacy_upstream() {
    local tmpl="$1"

    [[ -f "$tmpl" ]] || die "business-settings.html not found: $tmpl"

    if grep -q 'ROUTING_LEGACY_UPSTREAM_HIDE_BEGIN' "$tmpl"; then
        log "business-settings.html already patched to hide legacy upstream fields, skipping"
        return 0
    fi

    if ! grep -q 'commercial_de_tunnel_type' "$tmpl"; then
        log "business-settings.html: commercial_de_* fields already absent — legacy hide patch not needed"
        return 0
    fi
    grep -q 'commercial_de_trojan_uri' "$tmpl" \
        || die "business-settings.html: commercial_de_trojan_uri field not found — cannot patch"

    backup_target "$tmpl"

    local py patched_tmp
    py="$(detect_venv_python)"
    patched_tmp="$(mktemp)"

    "$py" - "$tmpl" "$patched_tmp" <<'PY'
import sys
orig_path, out_path = sys.argv[1], sys.argv[2]

HIDE_BEGIN = (
    "            <!-- ROUTING_LEGACY_UPSTREAM_HIDE_BEGIN -->\n"
    "            <div class=\"alert alert-info py-2 small\">\n"
    "              &#9432; Внешние ноды настраиваются через"
    " <a href=\"{{ routing_section_url.split('?')[0].rstrip('/') }}/upstreams/\">"
    "Управление upstream-нодами</a>."
    " Legacy-поля single-upstream скрыты, но данные сохранены для совместимости.\n"
    "            </div>\n"
    "            <div style=\"display:none\" aria-hidden=\"true\">\n"
)
HIDE_END = (
    "            </div><!-- end legacy-upstream hidden -->\n"
    "            <!-- ROUTING_LEGACY_UPSTREAM_HIDE_END -->\n"
)

with open(orig_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

start_idx = None
end_idx = None
for i, line in enumerate(lines):
    if 'commercial_de_tunnel_type' in line and start_idx is None:
        start_idx = i
    if 'commercial_de_trojan_uri' in line:
        end_idx = i

if start_idx is None or end_idx is None:
    print("ERROR: could not find legacy upstream field block", file=sys.stderr)
    sys.exit(1)

new_lines = (
    lines[:start_idx]
    + [HIDE_BEGIN]
    + lines[start_idx:end_idx + 1]
    + [HIDE_END]
    + lines[end_idx + 1:]
)

with open(out_path, 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print(f"Patched business-settings.html: hid legacy upstream block lines {start_idx+1}-{end_idx+1}")
PY

    cat "$patched_tmp" > "$tmpl"
    rm -f "$patched_tmp"

    record_installed_file "$tmpl"
    printf '%s\n' "$tmpl" > "$BACKUP_DIR/legacy-upstream-hide-tmpl-path.txt"
    printf 'true\n' > "$BACKUP_DIR/legacy-upstream-hide-patched.flag"
    log "business-settings.html: legacy single-upstream fields hidden"
}

# Patch panel/admin/__init__.py to import RoutingAdmin from RoutingRuleSourceAdmin.
# Stage 2E: upgrades the ROUTING_UPSTREAM_ADMIN block to use RoutingRuleSourceAdmin
# which extends RoutingUpstreamAdmin and adds /rule-sources/* routes.
# Uses a nested guard marker; idempotent.
patch_routing_rule_source_admin() {
    local init_py="$1"

    [[ -f "$init_py" ]] || die "panel/admin/__init__.py not found: $init_py"

    if grep -q 'ROUTING_RULE_SOURCE_ADMIN_BEGIN' "$init_py"; then
        log "__init__.py already patched for RoutingRuleSourceAdmin, skipping"
        return 0
    fi

    # The Stage 2B ROUTING_UPSTREAM_ADMIN block must already be present
    grep -q 'ROUTING_UPSTREAM_ADMIN_BEGIN' "$init_py" \
        || die "__init__.py missing ROUTING_UPSTREAM_ADMIN_BEGIN — Stage 2B patch not applied"
    grep -q 'from .RoutingUpstreamAdmin import RoutingAdmin' "$init_py" \
        || die "__init__.py: RoutingUpstreamAdmin import line not found — cannot upgrade"

    backup_target "$init_py"

    local py patched_tmp
    py="$(detect_venv_python)"
    patched_tmp="$(mktemp)"

    "$py" - "$init_py" "$patched_tmp" <<'PY'
import sys
orig_path, out_path = sys.argv[1], sys.argv[2]

with open(orig_path, 'r', encoding='utf-8') as f:
    content = f.read()

old_line = '            from .RoutingUpstreamAdmin import RoutingAdmin\n'
new_block = (
    '            # ROUTING_RULE_SOURCE_ADMIN_BEGIN --- do not remove this line\n'
    '            try:\n'
    '                from .RoutingRuleSourceAdmin import RoutingAdmin\n'
    '            except Exception:\n'
    '                logger.exception("RoutingRuleSourceAdmin unavailable; falling back to RoutingUpstreamAdmin")\n'
    '                from .RoutingUpstreamAdmin import RoutingAdmin\n'
    '            # ROUTING_RULE_SOURCE_ADMIN_END --- do not remove this line\n'
)

if old_line not in content:
    print("ERROR: RoutingUpstreamAdmin import line not found", file=sys.stderr)
    sys.exit(1)

new_content = content.replace(old_line, new_block, 1)

with open(out_path, 'w', encoding='utf-8') as f:
    f.write(new_content)

print("Patched __init__.py: upgraded to RoutingRuleSourceAdmin")
PY

    "$py" -m py_compile "$patched_tmp" || {
        rm -f "$patched_tmp"
        die "__init__.py rule-source patch produced invalid Python syntax"
    }

    cat "$patched_tmp" > "$init_py"
    rm -f "$patched_tmp"

    record_installed_file "$init_py"
    printf '%s\n' "$init_py" > "$BACKUP_DIR/rule-source-admin-init-path.txt"
    printf 'true\n' > "$BACKUP_DIR/rule-source-admin-init-patched.flag"
    log "__init__.py patched for RoutingRuleSourceAdmin"
}

# Patch business-settings.html: add rule source button after "Polzovatelskiye marshruty" h4.
# Uses HTML comment guard markers; idempotent.
patch_business_settings_rule_source_link() {
    local tmpl="$1"

    [[ -f "$tmpl" ]] || die "business-settings.html not found: $tmpl"

    if grep -q 'ROUTING_RULE_SOURCE_UI_BEGIN' "$tmpl"; then
        log "business-settings.html already patched with rule source link, skipping"
        return 0
    fi

    # Insertion point: after "Пользовательские маршруты" h4
    grep -q 'Пользовательские маршруты' "$tmpl" \
        || die "business-settings.html: 'Пользовательские маршруты' heading not found"

    backup_target "$tmpl"

    local py patched_tmp
    py="$(detect_venv_python)"
    patched_tmp="$(mktemp)"

    "$py" - "$tmpl" "$patched_tmp" <<'PY'
import sys
orig_path, out_path = sys.argv[1], sys.argv[2]

patch_block = (
    "            <!-- ROUTING_RULE_SOURCE_UI_BEGIN -->\n"
    "            <div class=\"mb-3\">\n"
    "              <a href=\"{{ routing_section_url.split('?')[0].rstrip('/') }}/rule-sources/\""
    " class=\"btn btn-primary\">&#128462; Источники правил маршрутизации</a>\n"
    "              <small class=\"form-text text-muted d-block mt-1\">"
    "Импорт списков из URL или файла с выбором политики: direct (выход напрямую),"
    " upstream (через внешнюю ноду) или block."
    " После импорта нажмите <strong>&#9654; Применить xray-router</strong>.</small>\n"
    "            </div>\n"
    "            <!-- ROUTING_RULE_SOURCE_UI_END -->\n"
)

with open(orig_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

insert_after = None
for i, line in enumerate(lines):
    if '<h4>' in line and 'Пользовательские маршруты' in line:
        insert_after = i
        break

if insert_after is None:
    print("ERROR: 'Пользовательские маршруты' h4 not found", file=sys.stderr)
    sys.exit(1)

# Insert AFTER the h4 line
new_lines = lines[:insert_after + 1] + [patch_block] + lines[insert_after + 1:]

with open(out_path, 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print(f"Patched: inserted ROUTING_RULE_SOURCE_UI block after line {insert_after + 1}")
PY

    cat "$patched_tmp" > "$tmpl"
    rm -f "$patched_tmp"

    record_installed_file "$tmpl"
    printf '%s\n' "$tmpl" > "$BACKUP_DIR/rule-source-ui-tmpl-path.txt"
    printf 'true\n' > "$BACKUP_DIR/rule-source-ui-tmpl-patched.flag"
    log "business-settings.html patched with rule source link (after Polzovatelskiye marshruty)"
}

# Replace 3 verbose alert banners in routing section with compact info card.
# Idempotent: skips if card already present.
patch_business_settings_compact_info() {
    local tmpl="$1"

    [[ -f "$tmpl" ]] || die "business-settings.html not found: $tmpl"

    if grep -q 'Как работает маршрутизация' "$tmpl"; then
        log "business-settings.html already has compact info card, skipping"
        return 0
    fi

    grep -q 'Внешняя нода = upstream' "$tmpl" \
        || die "business-settings.html: 3 alert banners not found — nothing to replace"

    backup_target "$tmpl"

    local py patched_tmp
    py="$(detect_venv_python)"
    patched_tmp="$(mktemp)"

    "$py" - "$tmpl" "$patched_tmp" <<'PY'
import sys
orig_path, out_path = sys.argv[1], sys.argv[2]

card = (
    "            <div class=\"card border-0 bg-light mb-3\">\n"
    "              <div class=\"card-body py-2 px-3 small text-muted\">\n"
    "                <strong>Как работает маршрутизация:</strong>\n"
    "                <ul class=\"mb-0 mt-1 ps-3\">\n"
    "                  <li>RU-трафик (домены, geoip:ru, ваши списки) выходит <strong>напрямую</strong> через сервер.</li>\n"
    "                  <li>Остальной трафик уходит на <strong>upstream-ноду</strong> (может быть любая страна).</li>\n"
    "                  <li>Включите <em>Применять к Xray</em> и <em>к sing-box</em> для полного split-routing.</li>\n"
    "                  <li>Блокировка UDP/443 переключает клиентов на TCP/443 - маршрутизация становится предсказуемее.</li>\n"
    "                </ul>\n"
    "              </div>\n"
    "            </div>\n"
)

with open(orig_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find start/end of 3-alert block
start_idx = end_idx = None
for i, line in enumerate(lines):
    if start_idx is None and 'alert' in line and 'Внешняя нода = upstream' in line:
        start_idx = i
    if start_idx is not None and 'alert' in line and 'UDP/443' in line:
        end_idx = i
        break

if start_idx is None or end_idx is None:
    print("ERROR: could not find 3-alert block", file=sys.stderr)
    sys.exit(1)

new_lines = lines[:start_idx] + [card] + lines[end_idx + 1:]

with open(out_path, 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print(f"Replaced 3 alerts (lines {start_idx+1}-{end_idx+1}) with compact card")
PY

    cat "$patched_tmp" > "$tmpl"
    rm -f "$patched_tmp"

    record_installed_file "$tmpl"
    printf '%s\n' "$tmpl" > "$BACKUP_DIR/compact-info-tmpl-path.txt"
    printf 'true\n' > "$BACKUP_DIR/compact-info-patched.flag"
    log "business-settings.html: 3 alerts replaced with compact info card"
}

# Update section labels/descriptions for direct-rules section.
# Idempotent: skips if "Direct-правила" already present.
patch_business_settings_direct_labels() {
    local tmpl="$1"

    [[ -f "$tmpl" ]] || die "business-settings.html not found: $tmpl"

    if grep -q 'Direct-правила' "$tmpl"; then
        log "business-settings.html already has Direct-pravila labels, skipping"
        return 0
    fi

    grep -q 'Пользовательские маршруты' "$tmpl" \
        || die "business-settings.html: custom routes section not found"

    backup_target "$tmpl"

    local py patched_tmp
    py="$(detect_venv_python)"
    patched_tmp="$(mktemp)"

    "$py" - "$tmpl" "$patched_tmp" <<'PY'
import sys
orig_path, out_path = sys.argv[1], sys.argv[2]

with open(orig_path, 'r', encoding='utf-8') as f:
    content = f.read()

replacements = [
    # Section h4 title
    (
        'Пользовательские маршруты текущей ноды',
        'Direct-правила - домены и IP, которые остаются на этой ноде',
    ),
    # Old simple description (replace only if present)
    (
        '<p class="text-muted">Эти правила оставляют выбранные домены и IP на текущей ноде.'
        ' Всё, что не попадает под эти правила, встроенные суффиксы текущей ноды и geoip:ru,'
        ' уходит через внешнюю ноду.</p>',
        '<p class="text-muted">Здесь управляются только <strong>direct</strong>-правила -'
        ' домены и IP, трафик к которым выходит напрямую через текущий сервер, минуя upstream-ноду.<br>'
        'Правила с политикой <em>upstream</em> или <em>block</em>, импортированные через Источники,'
        ' в этом списке не показываются и не затрагиваются при сохранении.</p>'
        '<div class="alert alert-warning py-2 small mb-2">&#9888; «Сохранить» перезаписывает'
        ' <strong>только direct-правила</strong> из поля ниже.'
        ' Правила upstream и block управляются исключительно через'
        ' <strong>Источники правил маршрутизации</strong>.</div>',
    ),
    # Format hints header
    (
        '<p class="text-muted mb-2">Формат ввода:</p>',
        '<p class="text-muted mb-1"><strong>Формат ввода</strong>'
        ' (один элемент на строку, строки с # игнорируются):</p>',
    ),
    # List item punctuation
    (
        '<li><code>domain:example.com</code> - только конкретный домен</li>',
        '<li><code>domain:example.com</code> - точное совпадение домена</li>',
    ),
    (
        '<li><code>suffix:example.com</code> - домен и все поддомены</li>',
        '<li><code>suffix:example.com</code> - домен и все поддомены (наиболее частый случай)</li>',
    ),
    (
        '<li><code>ip:1.2.3.4</code> - один IP</li>',
        '<li><code>ip:1.2.3.4</code> - один IP-адрес</li>',
    ),
    (
        '<li><code>cidr:1.2.3.0/24</code> - подсеть</li>',
        '<li><code>cidr:1.2.3.0/24</code> - подсеть (CIDR)</li>',
    ),
    # Field label
    (
        '<label for="{{ form.custom_ru_rules_bulk.id }}">Список правил</label>',
        '<label for="{{ form.custom_ru_rules_bulk.id }}">Direct-правила'
        ' <small class="text-muted fw-normal">(только прямой выход - upstream/block управляются через Источники)</small></label>',
    ),
    # Rule count
    (
        '<p class="text-muted">Сейчас сохранено правил: <strong>{{ commercial_routing_summary.custom_rules_total }}</strong>.</p>',
        '<p class="text-muted">Direct-правил сохранено: <strong>{{ commercial_routing_summary.custom_rules_total }}</strong>.'
        ' Правила с другими политиками (upstream/block) управляются через Источники и в этот счётчик не входят.</p>',
    ),
]

changed = 0
for old, new in replacements:
    if old in content:
        content = content.replace(old, new, 1)
        changed += 1

with open(out_path, 'w', encoding='utf-8') as f:
    f.write(content)

print(f"Applied {changed}/{len(replacements)} label replacements")
PY

    cat "$patched_tmp" > "$tmpl"
    rm -f "$patched_tmp"

    record_installed_file "$tmpl"
    printf '%s\n' "$tmpl" > "$BACKUP_DIR/direct-labels-tmpl-path.txt"
    printf 'true\n' > "$BACKUP_DIR/direct-labels-patched.flag"
    log "business-settings.html: direct-rules labels updated"
}

# Patch admin-layout.html sidebar: add Внешние ноды and Источники правил links.
# Uses HTML comment guard markers; idempotent. Rollback via file restore.
patch_admin_layout_routing_sidebar() {
    local layout="$1"

    [[ -f "$layout" ]] || die "admin-layout.html not found: $layout"

    if grep -q 'ROUTING_SIDEBAR_BEGIN' "$layout"; then
        log "admin-layout.html already has routing sidebar links, skipping"
        return 0
    fi

    grep -q 'routing-sidebar' "$layout" \
        || die "admin-layout.html: routing-sidebar block not found"

    backup_target "$layout"

    local py patched_tmp
    py="$(detect_venv_python)"
    patched_tmp="$(mktemp)"

    "$py" - "$layout" "$patched_tmp" <<'PY'
import sys
orig_path, out_path = sys.argv[1], sys.argv[2]

sidebar_links = (
    "          <!-- ROUTING_SIDEBAR_BEGIN -->\n"
    "          {{ render_nav_item('admin.RoutingAdmin:upstream_list',\n"
    "          icon('solid','server','nav-icon')+'Внешние ноды',_use_li=True) }}\n"
    "          {{ render_nav_item('admin.RoutingAdmin:rule_source_list',\n"
    "          icon('solid','list','nav-icon')+'Источники правил',_use_li=True) }}\n"
    "          <!-- ROUTING_SIDEBAR_END -->\n"
)

with open(orig_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find the closing </div> of routing-sidebar block
insert_before = None
in_routing_sidebar = False
for i, line in enumerate(lines):
    if 'id="routing-sidebar"' in line:
        in_routing_sidebar = True
    if in_routing_sidebar and '</div>' in line:
        insert_before = i
        break

if insert_before is None:
    print("ERROR: could not find end of routing-sidebar div", file=sys.stderr)
    sys.exit(1)

new_lines = lines[:insert_before] + [sidebar_links] + lines[insert_before:]

with open(out_path, 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print(f"Patched admin-layout.html: routing sidebar links inserted before line {insert_before + 1}")
PY

    cat "$patched_tmp" > "$layout"
    rm -f "$patched_tmp"

    record_installed_file "$layout"
    printf '%s\n' "$layout" > "$BACKUP_DIR/routing-sidebar-path.txt"
    printf 'true\n' > "$BACKUP_DIR/routing-sidebar-patched.flag"
    log "admin-layout.html patched with routing sidebar links"
}

create_app_smoke_with_rule_source_routes() {
    local py
    py="$(detect_venv_python)"
    sudo -H -u "$PANEL_USER" env PYTHONUNBUFFERED=1 \
        bash -lc "cd '$INSTALL_ROOT/hiddify-panel' && '$py' -" <<'PY'
from hiddifypanel import create_app

app = create_app()
endpoints = {rule.endpoint for rule in app.url_map.iter_rules()}
rules_by_endpoint = {rule.endpoint: rule.rule for rule in app.url_map.iter_rules()}

required = [
    'admin.RoutingAdmin:rule_source_list',
    'admin.RoutingAdmin:rule_source_add',
    'admin.RoutingAdmin:rule_source_edit',
    'admin.RoutingAdmin:rule_source_delete',
    'admin.RoutingAdmin:rule_source_toggle',
    'admin.RoutingAdmin:rule_source_preview',
    'admin.RoutingAdmin:rule_source_import',
]

missing = [ep for ep in required if ep not in endpoints]
if missing:
    raise AssertionError("Missing rule source endpoints: " + ", ".join(missing))

for ep in required:
    print(f"endpoint-ok {ep} -> {rules_by_endpoint.get(ep, '?')}")
PY
}

rollback_backup_dir() {
    local backup_dir="$1"
    local restore_db="${2:-0}"
    local restart_services="${3:-0}"
    local target backup_path created_file

    [[ -d "$backup_dir" ]] || die "Backup dir not found: $backup_dir"

    # Restore patched commander.py if needed (sed fallback before file restore)
    if [[ -f "$backup_dir/commander-patched.flag" && -f "$COMMANDER_PATH" ]]; then
        sed -i '/# ROUTING_INSTALL_BEGIN/,/# ROUTING_INSTALL_END/d' "$COMMANDER_PATH" || true
        log "commander.py routing patch removed"
    fi

    # Restore patched __init__.py if needed.
    # Priority: file restore from backup (reliable) > sed fallback (unreliable with nested markers).
    if [[ -f "$backup_dir/upstream-admin-init-patched.flag" && -f "$backup_dir/upstream-admin-init-path.txt" ]]; then
        local init_py
        init_py="$(cat "$backup_dir/upstream-admin-init-path.txt")"
        if [[ -f "$init_py" ]]; then
            # Try file-level restore first (backup_target saves to files/ subdir)
            local init_backup="$backup_dir/files/${init_py#/}"
            if [[ -f "$init_backup" ]]; then
                cp -a "$init_backup" "$init_py"
                log "__init__.py RoutingUpstreamAdmin patch removed (file restore from backup)"
            else
                # Fallback to sed if no backup file exists
                sed -i '/# ROUTING_UPSTREAM_ADMIN_BEGIN/,/# ROUTING_UPSTREAM_ADMIN_END/d' "$init_py" || true
                log "__init__.py RoutingUpstreamAdmin patch removed (sed fallback)"
            fi
        fi
    fi

    # Restore patched business-settings.html (upstream link) if needed
    if [[ -f "$backup_dir/upstream-ui-tmpl-patched.flag" && -f "$backup_dir/upstream-ui-tmpl-path.txt" ]]; then
        local tmpl
        tmpl="$(cat "$backup_dir/upstream-ui-tmpl-path.txt")"
        if [[ -f "$tmpl" ]]; then
            sed -i '/<!-- ROUTING_UPSTREAM_UI_BEGIN -->/,/<!-- ROUTING_UPSTREAM_UI_END -->/d' "$tmpl" || true
            log "business-settings.html upstream link removed (sed fallback)"
        fi
    fi

    # Restore patched business-settings.html (legacy upstream hide) if needed
    if [[ -f "$backup_dir/legacy-upstream-hide-patched.flag" && -f "$backup_dir/legacy-upstream-hide-tmpl-path.txt" ]]; then
        local tmpl
        tmpl="$(cat "$backup_dir/legacy-upstream-hide-tmpl-path.txt")"
        if [[ -f "$tmpl" ]]; then
            sed -i '/<!-- ROUTING_LEGACY_UPSTREAM_HIDE_BEGIN -->/,/<!-- ROUTING_LEGACY_UPSTREAM_HIDE_END -->/d' "$tmpl" || true
            log "business-settings.html legacy upstream hide removed (sed fallback)"
        fi
    fi

    # Restore patched __init__.py for RoutingRuleSourceAdmin (Stage 2E)
    if [[ -f "$backup_dir/rule-source-admin-init-patched.flag" && -f "$backup_dir/rule-source-admin-init-path.txt" ]]; then
        local init_py
        init_py="$(cat "$backup_dir/rule-source-admin-init-path.txt")"
        if [[ -f "$init_py" ]]; then
            # Try file-level restore first — sed on nested markers is unreliable
            local init_backup="$backup_dir/files/${init_py#/}"
            if [[ -f "$init_backup" ]]; then
                cp -a "$init_backup" "$init_py"
                log "__init__.py RoutingRuleSourceAdmin patch removed (file restore from backup)"
            else
                sed -i '/# ROUTING_RULE_SOURCE_ADMIN_BEGIN/,/# ROUTING_RULE_SOURCE_ADMIN_END/d' "$init_py" || true
                log "__init__.py RoutingRuleSourceAdmin patch removed (sed fallback)"
            fi
        fi
    fi

    # Restore patched business-settings.html (rule source link, Stage 2E)
    if [[ -f "$backup_dir/rule-source-ui-tmpl-patched.flag" && -f "$backup_dir/rule-source-ui-tmpl-path.txt" ]]; then
        local tmpl
        tmpl="$(cat "$backup_dir/rule-source-ui-tmpl-path.txt")"
        if [[ -f "$tmpl" ]]; then
            sed -i '/<!-- ROUTING_RULE_SOURCE_UI_BEGIN -->/,/<!-- ROUTING_RULE_SOURCE_UI_END -->/d' "$tmpl" || true
            log "business-settings.html rule source link removed (sed fallback)"
        fi
    fi

    # Restore admin-layout.html sidebar links (Stage 2F)
    if [[ -f "$backup_dir/routing-sidebar-patched.flag" && -f "$backup_dir/routing-sidebar-path.txt" ]]; then
        local layout
        layout="$(cat "$backup_dir/routing-sidebar-path.txt")"
        if [[ -f "$layout" ]]; then
            sed -i '/<!-- ROUTING_SIDEBAR_BEGIN -->/,/<!-- ROUTING_SIDEBAR_END -->/d' "$layout" || true
            log "admin-layout.html routing sidebar links removed (sed fallback)"
        fi
    fi

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
        mysql "$DB_NAME" < "$backup_dir/db-dump.sql"
    fi

    if [[ "$restart_services" == "1" ]]; then
        systemctl restart "$SERVICE_PANEL" "$SERVICE_BG" || true
        sleep 10
        check_services_active || true
    fi
}

write_routing_manifest() {
    local runtime_path="$1"
    local stamp
    stamp="$(date '+%Y-%m-%d %H:%M:%S')"
    {
        printf 'release_version=%s\n' "$RELEASE_VERSION"
        printf 'release_tag=%s\n' "$RELEASE_TAG"
        printf 'git_commit=%s\n' "$RELEASE_COMMIT"
        printf 'timestamp=%s\n' "$stamp"
        printf 'runtime_path=%s\n' "$runtime_path"
        printf 'backup_dir=%s\n' "$BACKUP_DIR"
        printf 'installed_files:\n'
        sed 's/^/  /' "$BACKUP_DIR/installed-files.txt"
        printf '\n'
    } > "$MANIFEST_PATH"
}

collect_routing_checkpoint() {
    local out_dir="$1"
    mkdir -p "$out_dir"
    systemctl status "$SERVICE_PANEL" "$SERVICE_BG" "$XRAY_ROUTER_SERVICE" --no-pager \
        > "$out_dir/systemctl-status.txt" 2>&1 || true
    ss -lntp > "$out_dir/ss-lntp.txt" 2>&1 || true
    create_app_smoke > "$out_dir/create-app.txt" 2>&1 || true
    mysql "$DB_NAME" -e "SELECT key, value FROM bool_config WHERE child_id=0 AND key LIKE 'commercial_routing%';" \
        > "$out_dir/routing-config.txt" 2>&1 || true
    mysql "$DB_NAME" -e "SELECT COUNT(*) AS custom_rules FROM commercial_routing_custom_rule WHERE enabled=1;" \
        >> "$out_dir/routing-config.txt" 2>&1 || true
    journalctl -u "$SERVICE_PANEL" -u "$XRAY_ROUTER_SERVICE" -n 80 --no-pager \
        > "$out_dir/journal-tail.txt" 2>&1 || true
}
