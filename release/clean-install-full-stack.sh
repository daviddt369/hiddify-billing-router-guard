#!/usr/bin/env bash
# clean-install-full-stack.sh
# Thin wrapper: installs business → routing → antishare on a fresh Hiddify 12.x base.
# Does NOT modify production config. Does NOT call apply_configs.sh automatically.
# Usage: sudo bash clean-install-full-stack.sh [--force]
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_ROOT="${INSTALL_ROOT:-/opt/hiddify-manager}"
TIMESTAMP="$(date '+%Y%m%d_%H%M%S')"
LOG_DIR="${LOG_DIR:-$INSTALL_ROOT/clean-install-logs/$TIMESTAMP}"
FORCE=0

# ── Argument parsing ────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --force)   FORCE=1 ;;
        --help|-h)
            cat <<'EOF'
Usage: sudo bash clean-install-full-stack.sh [--force]

  Installs: business → routing → antishare on a base Hiddify 12.x system.

  --force   Allow running even when addon manifests already exist.
            USE WITH CAUTION — existing addon files will be overwritten.

  Environment overrides:
    INSTALL_ROOT   Default: /opt/hiddify-manager
    LOG_DIR        Default: $INSTALL_ROOT/clean-install-logs/<timestamp>
EOF
            exit 0
            ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
    shift
done

# ── Helpers ─────────────────────────────────────────────────────────────────
_log()  { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG_DIR/clean-install.log"; }
_ok()   { echo "[  OK  ] $*" | tee -a "$LOG_DIR/clean-install.log"; }
_fail() { echo "[ FAIL ] $*" | tee -a "$LOG_DIR/clean-install.log" >&2; }
_die()  { _fail "$*"; exit 1; }

# ── Preflight ────────────────────────────────────────────────────────────────
preflight() {
    _log "=== PREFLIGHT ==="
    _log "Hostname   : $(hostname -f 2>/dev/null || hostname)"
    _log "Date/Time  : $(date)"
    _log "Script dir : $SCRIPT_DIR"
    _log "Install root: $INSTALL_ROOT"

    _log "--- OS ---"
    cat /etc/os-release 2>/dev/null | grep -E '^(NAME|VERSION)=' | tee -a "$LOG_DIR/clean-install.log" || true

    _log "--- IP addresses ---"
    ip -4 addr show scope global 2>/dev/null | grep inet | awk '{print $2}' | tee -a "$LOG_DIR/clean-install.log" || true

    _log "--- Required binaries ---"
    for cmd in bash find mysql mysqldump systemctl curl python3; do
        command -v "$cmd" >/dev/null 2>&1 && _ok "$cmd" || _die "Missing required binary: $cmd"
    done

    _log "--- Hiddify base install check ---"
    [[ -d "$INSTALL_ROOT" ]]                   || _die "INSTALL_ROOT not found: $INSTALL_ROOT"
    [[ -d "$INSTALL_ROOT/hiddify-panel" ]]     || _die "hiddify-panel not found under $INSTALL_ROOT"
    [[ -d "$INSTALL_ROOT/.venv313" ]]          || _die "Python venv not found: $INSTALL_ROOT/.venv313"

    _log "--- Python venv ---"
    local py
    py="$(find "$INSTALL_ROOT" -path '*/.venv*/bin/python*' -not -name '*.pyc' | sort | tail -1)"
    [[ -x "$py" ]] || _die "Python binary not executable: $py"
    _ok "Python: $py"

    _log "--- Panel services ---"
    for svc in hiddify-panel hiddify-panel-background-tasks; do
        systemctl is-active "$svc" >/dev/null 2>&1 \
            && _ok "$svc active" \
            || _die "Panel service not active: $svc (is base Hiddify installed and healthy?)"
    done

    _log "--- DB connectivity ---"
    mysql "$DB_NAME" -e "SELECT db_version FROM str_config LIMIT 1;" >/dev/null 2>&1 \
        || _die "Cannot connect to DB '$DB_NAME' — is MariaDB running and hiddifypanel DB accessible?"

    _log "--- DB version ---"
    local db_ver
    db_ver="$(mysql "$DB_NAME" -N -e "SELECT value FROM str_config WHERE \`key\`='db_version' LIMIT 1;" 2>/dev/null || echo unknown)"
    _log "  db_version = $db_ver"

    _log "--- Manifest check ---"
    local manifests_found=0
    for mf in \
        "$INSTALL_ROOT/business-addon.manifest" \
        "$INSTALL_ROOT/routing-addon.manifest" \
        "$INSTALL_ROOT/anti-share-addon.manifest"; do
        [[ -f "$mf" ]] && { _log "  EXISTS: $mf"; manifests_found=1; }
    done
    if [[ "$manifests_found" -eq 1 ]]; then
        if [[ "$FORCE" -eq 1 ]]; then
            _log "  --force passed: proceeding despite existing manifests"
        else
            _die "Addon manifests already exist. This does not look like a clean VM. Pass --force to override."
        fi
    else
        _ok "No existing addon manifests — clean target confirmed"
    fi

    _log "--- Payload directories ---"
    for d in \
        "$SCRIPT_DIR/business-installer" \
        "$SCRIPT_DIR/routing-installer" \
        "$SCRIPT_DIR/antishare-installer"; do
        [[ -d "$d" ]] || _die "Installer directory not found: $d"
        _ok "$(basename $d) directory present"
    done

    _log "--- installer script syntax check ---"
    for sh in \
        "$SCRIPT_DIR/business-installer/install-business.sh" \
        "$SCRIPT_DIR/business-installer/common.sh" \
        "$SCRIPT_DIR/routing-installer/install-routing.sh" \
        "$SCRIPT_DIR/routing-installer/common-routing.sh" \
        "$SCRIPT_DIR/antishare-installer/install-antishare.sh" \
        "$SCRIPT_DIR/antishare-installer/common-antishare.sh"; do
        bash -n "$sh" 2>/dev/null || _die "Shell syntax error in: $sh"
        _ok "syntax OK: $(basename $sh)"
    done

    _log "=== PREFLIGHT PASSED ==="
}

# ── Stage runner ─────────────────────────────────────────────────────────────
run_stage() {
    local name="$1"
    local installer="$2"
    local stage_log="$LOG_DIR/${name}.log"

    _log "=== STAGE: $name ==="
    _log "  Running: $installer"

    if bash "$installer" 2>&1 | tee "$stage_log"; then
        _ok "STAGE $name PASSED"
    else
        _fail "STAGE $name FAILED — see $stage_log"
        _die "Stopping. Fix the error above before proceeding."
    fi
}

run_smoke() {
    local name="$1"
    local smoke_script="$2"
    local smoke_log="$LOG_DIR/smoke-${name}.log"
    local extra_args="${3:-}"

    _log "--- Smoke: $name ---"
    # shellcheck disable=SC2086
    if bash "$smoke_script" $extra_args 2>&1 | tee "$smoke_log"; then
        _ok "SMOKE $name PASSED"
    else
        _fail "SMOKE $name FAILED — see $smoke_log"
        _die "Smoke check failed for $name. Installation may be incomplete."
    fi
}

# ── Final summary ─────────────────────────────────────────────────────────────
print_summary() {
    _log ""
    _log "========================================"
    _log "  CLEAN INSTALL SUMMARY"
    _log "  $(date)"
    _log "========================================"

    _log "--- Services ---"
    for svc in hiddify-panel hiddify-panel-background-tasks hiddify-xray xray-router hiddify-anti-share; do
        local status
        status="$(systemctl is-active "$svc" 2>/dev/null || echo not-found)"
        _log "  $svc: $status"
    done

    _log "--- Ports ---"
    ss -tlnp 2>/dev/null | grep -E ':9000|:20808' | tee -a "$LOG_DIR/clean-install.log" || true

    _log "--- Manifests ---"
    for mf in \
        "$INSTALL_ROOT/business-addon.manifest" \
        "$INSTALL_ROOT/routing-addon.manifest" \
        "$INSTALL_ROOT/anti-share-addon.manifest"; do
        [[ -f "$mf" ]] && _ok "$(basename $mf) present" || _fail "$(basename $mf) MISSING"
    done

    _log "--- Failed systemd units ---"
    systemctl --failed --no-legend 2>/dev/null | tee -a "$LOG_DIR/clean-install.log" || true

    _log "--- Logs saved to ---"
    _log "  $LOG_DIR/"
    ls "$LOG_DIR/" | tee -a "$LOG_DIR/clean-install.log" || true

    _log ""
    _log "Rollback notes:"
    _log "  Business  : sudo bash $SCRIPT_DIR/business-installer/rollback-business.sh"
    _log "  Routing   : sudo bash $SCRIPT_DIR/routing-installer/rollback-routing.sh"
    _log "  Anti-share: sudo bash $SCRIPT_DIR/antishare-installer/rollback-antishare.sh"
    _log ""
    _log "Post-install: to apply routing config run:"
    _log "  sudo bash $INSTALL_ROOT/apply_configs.sh"
    _log ""
    _log "========================================"
    _log "  CLEAN INSTALL COMPLETE"
    _log "========================================"
}

# ── Main ─────────────────────────────────────────────────────────────────────
main() {
    [[ "$(id -u)" -eq 0 ]] || { echo "Run as root: sudo bash $0" >&2; exit 1; }

    local DB_NAME="${DB_NAME:-hiddifypanel}"

    mkdir -p "$LOG_DIR"
    _log "Log directory: $LOG_DIR"

    preflight

    run_stage  "business"   "$SCRIPT_DIR/business-installer/install-business.sh"
    run_smoke  "business"   "$SCRIPT_DIR/business-installer/smoke-business.sh"

    run_stage  "routing"    "$SCRIPT_DIR/routing-installer/install-routing.sh"
    run_smoke  "routing"    "$SCRIPT_DIR/routing-installer/smoke-routing.sh"

    run_stage  "antishare"  "$SCRIPT_DIR/antishare-installer/install-antishare.sh"
    run_smoke  "antishare"  "$SCRIPT_DIR/antishare-installer/smoke-antishare.sh"

    print_summary
}

main "$@"
