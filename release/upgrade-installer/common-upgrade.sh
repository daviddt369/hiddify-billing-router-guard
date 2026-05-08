#!/usr/bin/env bash
# common-upgrade.sh — shared constants and helpers for upgrade-installer.
# Source this file at the top of each upgrade script.
set -Eeuo pipefail

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
readonly INSTALL_ROOT="${INSTALL_ROOT:-/opt/hiddify-manager}"
readonly DB_NAME="${DB_NAME:-hiddifypanel}"
readonly PANEL_USER="${PANEL_USER:-hiddify-panel}"
readonly BACKUP_ROOT="${UPGRADE_BACKUP_ROOT:-$INSTALL_ROOT/upgrade-installer-backups}"

readonly SERVICE_PANEL="hiddify-panel"
readonly SERVICE_BG="hiddify-panel-background-tasks"
readonly SERVICE_XRAY="hiddify-xray"
readonly SERVICE_XRAY_ROUTER="xray-router"
readonly SERVICE_ANTISHARE_TIMER="hiddify-anti-share.timer"

readonly BUSINESS_MANIFEST="$INSTALL_ROOT/business-addon.manifest"
readonly ROUTING_MANIFEST="$INSTALL_ROOT/routing-addon.manifest"
readonly ANTISHARE_MANIFEST="$INSTALL_ROOT/anti-share-addon.manifest"

readonly XRAY_LOG_CONFIG="$INSTALL_ROOT/xray/configs/00_log.json"
readonly XRAY_ACCESS_LOG="$INSTALL_ROOT/log/system/xray.access.log"
readonly XRAY_OVERRIDE_DIR="/etc/systemd/system/${SERVICE_XRAY}.service.d"

# Upgrade state
UPGRADE_BACKUP_DIR=""
UPGRADE_BLOCK="upgrade"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log()  { echo "[$UPGRADE_BLOCK] $*"; }
step() { echo; echo "[$UPGRADE_BLOCK][STEP] $*"; }
die()  { echo "[$UPGRADE_BLOCK][ERROR] $*" >&2; exit 1; }
warn() { echo "[$UPGRADE_BLOCK][WARN] $*" >&2; }
info() { echo "[$UPGRADE_BLOCK][INFO] $*"; }

require_root() {
    [[ "$(id -u)" -eq 0 ]] || die "Run as root (sudo bash $0)."
}

need_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

# ---------------------------------------------------------------------------
# Secret masking
# ---------------------------------------------------------------------------
# Usage: mask_secret "value"  → prints first 4 chars + ****
mask_secret() {
    local val="${1:-}"
    if [[ -z "$val" || "$val" == "NULL" || "$val" == "None" ]]; then
        echo "(empty)"
        return
    fi
    local len="${#val}"
    if [[ $len -le 4 ]]; then
        echo "****"
    else
        echo "${val:0:4}****[len=$len]"
    fi
}

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
db_query() {
    # Usage: db_query "SQL" [db_name]
    local sql="$1"
    local db="${2:-$DB_NAME}"
    mysql "$db" -N -B -e "$sql" 2>/dev/null
}

db_count() {
    # Usage: db_count "table" ["WHERE clause"]
    local table="$1"
    local where="${2:-1=1}"
    db_query "SELECT COUNT(*) FROM \`$table\` WHERE $where;" | head -1
}

col_exists() {
    # Usage: col_exists table_name column_name [db_name]
    local table="$1" col="$2" db="${3:-$DB_NAME}"
    local cnt
    cnt=$(mysql "$db" -N -B -e \
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
         WHERE TABLE_SCHEMA='$db' AND TABLE_NAME='$table' AND COLUMN_NAME='$col';" \
        2>/dev/null | head -1 || echo 0)
    [[ "${cnt:-0}" -ge 1 ]]
}

table_exists() {
    local table="$1" db="${2:-$DB_NAME}"
    local cnt
    cnt=$(mysql "$db" -N -B -e \
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES
         WHERE TABLE_SCHEMA='$db' AND TABLE_NAME='$table';" \
        2>/dev/null | head -1 || echo 0)
    [[ "${cnt:-0}" -ge 1 ]]
}

# ---------------------------------------------------------------------------
# Detect venv Python
# ---------------------------------------------------------------------------
detect_venv_python() {
    local found
    found=$(find "$INSTALL_ROOT" -name 'python3*' -path '*/venv*/bin/*' -type f 2>/dev/null | sort | head -1)
    [[ -n "$found" ]] || die "Cannot find venv python under $INSTALL_ROOT"
    echo "$found"
}

detect_runtime_path() {
    local found
    found=$(find "$INSTALL_ROOT" -type d -path '*/site-packages/hiddifypanel' 2>/dev/null | sort | head -1)
    [[ -n "$found" ]] || die "Cannot detect hiddifypanel runtime path under $INSTALL_ROOT"
    echo "$found"
}

# ---------------------------------------------------------------------------
# Backup dir
# ---------------------------------------------------------------------------
begin_upgrade_backup() {
    local label="${1:-upgrade}"
    local ts
    ts=$(date '+%Y-%m-%d-%H%M%S')
    UPGRADE_BACKUP_DIR="$BACKUP_ROOT/${ts}-${label}"
    mkdir -p "$UPGRADE_BACKUP_DIR"
    log "Backup directory: $UPGRADE_BACKUP_DIR"
    echo "$UPGRADE_BACKUP_DIR" > "$BACKUP_ROOT/latest"
}

require_backup_exists() {
    local latest="$BACKUP_ROOT/latest"
    [[ -f "$latest" ]] || die "No upgrade backup found. Run backup-before-upgrade.sh first."
    local bdir
    bdir="$(cat "$latest")"
    [[ -d "$bdir" ]] || die "Backup directory not found: $bdir"
    UPGRADE_BACKUP_DIR="$bdir"
    log "Using backup: $UPGRADE_BACKUP_DIR"
}

# ---------------------------------------------------------------------------
# Service helpers
# ---------------------------------------------------------------------------
check_services_active() {
    for svc in "$SERVICE_PANEL" "$SERVICE_BG"; do
        [[ "$(systemctl is-active "$svc" 2>/dev/null)" == "active" ]] \
            || die "Required service $svc is not active"
    done
    log "Core panel services active"
}

# ---------------------------------------------------------------------------
# Hiddify version
# ---------------------------------------------------------------------------
get_hiddify_version() {
    python3 - <<'PY' 2>/dev/null || echo "unknown"
import importlib.metadata
try:
    print(importlib.metadata.version("hiddifypanel"))
except:
    pass
PY
}
