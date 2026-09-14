#!/usr/bin/env bash
# Shared helpers for disaster-recovery/{snapshot,bootstrap,restore}.sh.
# Mirrors the conventions already used in release/business-installer/common.sh
# (log/step/die/warn, require_root, detect_runtime_path/detect_venv_python)
# so the DR tooling behaves consistently with the rest of this repo's installers.
set -Eeuo pipefail

readonly DR_INSTALL_ROOT="${INSTALL_ROOT:-/opt/hiddify-manager}"
readonly DR_SERVICE_PANEL="hiddify-panel"
readonly DR_SERVICE_BG="hiddify-panel-background-tasks"
readonly DR_APP_CFG="${DR_APP_CFG:-$DR_INSTALL_ROOT/hiddify-panel/app.cfg}"
readonly DR_PANEL_SECRETS="/etc/hiddify-panel/panel-secrets.env"
readonly DR_SELECTEL_PLACEHOLDER="__SELECTEL_XHTTP_PATH__"

DR_BLOCK="dr"

dr_log()  { echo "[$DR_BLOCK] $*"; }
dr_step() { echo; echo "[$DR_BLOCK][STEP] $*"; }
dr_warn() { echo "[$DR_BLOCK][WARN] $*" >&2; }
dr_die()  { echo "[$DR_BLOCK][ERROR] $*" >&2; exit 1; }

# With `set -e`, any unhandled pipeline/command failure anywhere in the
# script exits silently — no message at all, just a dead process. This was
# discovered the hard way: a regex bug in restore.sh's Selectel path
# extraction failed a pipeline and the script just stopped mid-run with zero
# diagnostic output. Every top-level script should `trap dr_error_trap ERR`
# right after sourcing this file so a failure always prints where it died.
dr_error_trap() {
    local exit_code=$?
    echo "[$DR_BLOCK][ERROR] Command failed (exit $exit_code) at ${BASH_SOURCE[1]:-?}:${BASH_LINENO[0]:-?}: ${BASH_COMMAND}" >&2
    exit "$exit_code"
}

dr_need_cmd() {
    command -v "$1" >/dev/null 2>&1 || dr_die "Missing command: $1"
}

dr_require_root() {
    [[ "$(id -u)" -eq 0 ]] || dr_die "Run as root."
}

# Finds the real, live-imported hiddifypanel package path, never the unused
# hiddify-panel/src/ reference checkout. This exact confusion (two copies,
# only one of which is actually imported by the running service) already cost
# real debugging time on this project — see docs/VPN_RU_NODE_CUSTOM_STATE.ru.md.
dr_detect_runtime_path() {
    local found
    mapfile -t found < <(find "$DR_INSTALL_ROOT" -type d -path '*/site-packages/hiddifypanel' 2>/dev/null | sort)
    [[ "${#found[@]}" -gt 0 ]] || dr_die "Cannot detect live hiddifypanel path under $DR_INSTALL_ROOT (site-packages copy not found)"
    printf '%s\n' "${found[0]}"
}

dr_detect_venv_python() {
    local py="$DR_INSTALL_ROOT/.venv313/bin/python"
    [[ -x "$py" ]] || dr_die "Runtime python not found: $py"
    printf '%s\n' "$py"
}

# Parses SQLALCHEMY_DATABASE_URI out of app.cfg and writes creds to a
# root-only (0600) temp file — never echoed to stdout/logs. Caller is
# responsible for `dr_shred_db_creds` once done using them.
DR_DBCREDS_FILE=""
dr_parse_db_uri() {
    [[ -f "$DR_APP_CFG" ]] || dr_die "app.cfg not found: $DR_APP_CFG"
    DR_DBCREDS_FILE="$(mktemp /root/.dr-dbcreds.XXXXXX)"
    chmod 600 "$DR_DBCREDS_FILE"
    "$(dr_detect_venv_python)" - "$DR_APP_CFG" "$DR_DBCREDS_FILE" <<'PY'
import re
import sys

cfg_path, out_path = sys.argv[1], sys.argv[2]
cfg = {}
with open(cfg_path) as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        cfg[k.strip()] = v.strip().strip('"').strip("'")

uri = cfg.get("SQLALCHEMY_DATABASE_URI", "")
m = re.match(r"mysql\+\w+://([^:]+):([^@]+)@([^/:]+)(?::(\d+))?/(\w+)", uri)
if not m:
    raise SystemExit("PARSE_FAIL: could not parse SQLALCHEMY_DATABASE_URI")
user, pw, host, port, db = m.groups()
with open(out_path, "w") as f:
    f.write(f"{user}\n{pw}\n{host}\n{port or 3306}\n{db}\n")
PY
    [[ -s "$DR_DBCREDS_FILE" ]] || dr_die "Failed to parse DB credentials from $DR_APP_CFG"
}

dr_db_user() { sed -n '1p' "$DR_DBCREDS_FILE"; }
dr_db_pass() { sed -n '2p' "$DR_DBCREDS_FILE"; }
dr_db_host() { sed -n '3p' "$DR_DBCREDS_FILE"; }
dr_db_port() { sed -n '4p' "$DR_DBCREDS_FILE"; }
dr_db_name() { sed -n '5p' "$DR_DBCREDS_FILE"; }

dr_shred_db_creds() {
    [[ -n "$DR_DBCREDS_FILE" && -f "$DR_DBCREDS_FILE" ]] || return 0
    shred -u "$DR_DBCREDS_FILE" 2>/dev/null || rm -f "$DR_DBCREDS_FILE"
    DR_DBCREDS_FILE=""
}

dr_check_services_active() {
    [[ "$(systemctl is-active "$DR_SERVICE_PANEL" 2>/dev/null)" == "active" ]] || dr_die "$DR_SERVICE_PANEL is not active"
    [[ "$(systemctl is-active "$DR_SERVICE_BG" 2>/dev/null)" == "active" ]] || dr_die "$DR_SERVICE_BG is not active"
}

dr_check_port_9000() {
    local waited=0 interval=5 max=120
    while ! ss -lntp 2>/dev/null | grep -qE '127\.0\.0\.1:9000|0\.0\.0\.0:9000|:::9000'; do
        if [[ $waited -ge $max ]]; then
            dr_die "port 9000 is not listening after ${max}s"
        fi
        sleep "$interval"
        waited=$((waited + interval))
        dr_log "waiting for port 9000... ${waited}s"
    done
}

dr_check_logs_since() {
    local since="$1"
    local error_re='Traceback|ImportError|ModuleNotFoundError|AttributeError|TypeError|NameError|RuntimeError'
    local noise_filter='Telegram bot token is not configured|YooKassa credentials are empty'
    local errors
    errors="$(journalctl -u "$DR_SERVICE_PANEL" --since "$since" --no-pager -o cat 2>/dev/null | grep -Ev "$noise_filter" | grep -Ei "$error_re" || true)"
    [[ -z "$errors" ]] || dr_die "Panel log errors found since $since:
$errors"
}

# Renders one of the Selectel template files (replaces the placeholder token
# with the real secret path) and installs it, mirroring install_payload_file's
# backup-then-install pattern from common.sh, but self-contained here since
# these files don't live in an addon payload/ tree.
dr_render_selectel_file() {
    local src="$1" dest="$2" mode="$3" secret_path="$4"
    [[ -n "$secret_path" ]] || dr_die "SELECTEL_XHTTP_PATH is empty — refusing to render $dest"
    mkdir -p "$(dirname "$dest")"
    if [[ -f "$dest" ]]; then
        cp -a "$dest" "${dest}.dr-backup-$(date +%Y%m%d-%H%M%S)"
    fi
    sed "s#${DR_SELECTEL_PLACEHOLDER}#${secret_path#/}#g" "$src" > "$dest.tmp"
    install -m "$mode" "$dest.tmp" "$dest"
    rm -f "$dest.tmp"
}

# Idempotently appends a snippet block to an existing file if a marker
# string from that snippet isn't already present — used for the two Selectel
# additions that are single lines inserted into otherwise-stock Hiddify
# files (haproxy.cfg.j2's include line, and the path map entries), so
# re-running restore.sh never duplicates them.
dr_append_snippet_if_missing() {
    local target="$1" snippet_file="$2" marker="$3"
    [[ -f "$target" ]] || dr_die "Target file not found, cannot append snippet: $target"
    if grep -qF "$marker" "$target"; then
        dr_log "snippet already present in $target, skipping"
        return 0
    fi
    cp -a "$target" "${target}.dr-backup-$(date +%Y%m%d-%H%M%S)"
    printf '\n' >> "$target"
    cat "$snippet_file" >> "$target"
    dr_log "appended snippet to $target"
}
