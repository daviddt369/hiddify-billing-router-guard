#!/usr/bin/env bash
# rollback-upgrade.sh — rollback an upgrade to pre-upgrade state.
#
# By default: file rollback only (replaces modified Python/template/systemd files).
# DB restore: ONLY with --restore-db AND CONFIRM_RESTORE_DB=YES environment variable.
#
# Usage:
#   sudo bash rollback-upgrade.sh [--backup-dir DIR]
#   CONFIRM_RESTORE_DB=YES sudo bash rollback-upgrade.sh --restore-db [--backup-dir DIR]
#
# WARNING: --restore-db is destructive and will overwrite ALL current DB data
# with the backup dump. Requires explicit CONFIRM_RESTORE_DB=YES to prevent
# accidental data loss.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common-upgrade.sh"

UPGRADE_BLOCK="rollback"
RESTORE_DB=0
BACKUP_DIR_ARG=""

usage() {
    cat <<'EOF'
Usage:
  sudo bash rollback-upgrade.sh [--backup-dir DIR]
  CONFIRM_RESTORE_DB=YES sudo bash rollback-upgrade.sh --restore-db [--backup-dir DIR]

  --backup-dir DIR  Use specific backup dir (default: latest)
  --restore-db      Also restore database from backup dump (DESTRUCTIVE)

CONFIRM_RESTORE_DB=YES must be set in environment to enable --restore-db.
This protects against accidental full DB overwrites.
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --restore-db) RESTORE_DB=1 ;;
        --backup-dir) shift; BACKUP_DIR_ARG="$1" ;;
        --help|-h) usage ;;
        *) die "Unknown argument: $1" ;;
    esac
    shift
done

require_root

# Resolve backup dir
if [[ -n "$BACKUP_DIR_ARG" ]]; then
    UPGRADE_BACKUP_DIR="$BACKUP_DIR_ARG"
    [[ -d "$UPGRADE_BACKUP_DIR" ]] || die "Backup directory not found: $UPGRADE_BACKUP_DIR"
else
    require_backup_exists
fi

BD="$UPGRADE_BACKUP_DIR"
log "Using backup: $BD"

# Guard for DB restore
if [[ $RESTORE_DB -eq 1 ]]; then
    [[ "${CONFIRM_RESTORE_DB:-}" == "YES" ]] \
        || die "DB restore requires CONFIRM_RESTORE_DB=YES in environment. This is DESTRUCTIVE."
    [[ -f "$BD/db-dump.sql" ]] \
        || die "No db-dump.sql found in backup. Cannot restore DB."
fi

# ─── Step 1: Restore Python runtime files ────────────────────────────────────
step "Restoring runtime Python files"
runtime_path="$(detect_runtime_path)"

if [[ -d "$BD/runtime" ]]; then
    while IFS= read -r -d '' src_file; do
        rel="${src_file#$BD/runtime/}"
        dst="$runtime_path/$rel"
        dst_dir="$(dirname "$dst")"
        mkdir -p "$dst_dir"
        cp -p "$src_file" "$dst"
        log "  restored: $rel"
    done < <(find "$BD/runtime" -type f -print0)
else
    warn "No runtime/ directory in backup — Python files not restored"
fi

# ─── Step 2: Restore templates ───────────────────────────────────────────────
step "Restoring HTML templates"
if [[ -d "$BD/templates" ]]; then
    while IFS= read -r -d '' src_file; do
        rel="${src_file#$BD/templates/}"
        dst="$runtime_path/$rel"
        dst_dir="$(dirname "$dst")"
        mkdir -p "$dst_dir"
        cp -p "$src_file" "$dst"
        log "  restored: $rel"
    done < <(find "$BD/templates" -type f -print0)
fi

# ─── Step 3: Restore manager overlay ────────────────────────────────────────
step "Restoring manager overlay files"
if [[ -d "$BD/manager-overlay" ]]; then
    # commander.py
    [[ -f "$BD/manager-overlay/common/commander.py" ]] && \
        cp -p "$BD/manager-overlay/common/commander.py" "$INSTALL_ROOT/common/commander.py"

    # xray/singbox templates
    for f in "$BD/manager-overlay/xray/configs/"*; do
        [[ -f "$f" ]] && cp -p "$f" "$INSTALL_ROOT/xray/configs/" || true
    done
    for f in "$BD/manager-overlay/singbox/configs/"*; do
        [[ -f "$f" ]] && cp -p "$f" "$INSTALL_ROOT/singbox/configs/" || true
    done

    # nft helper
    [[ -f "$BD/manager-overlay/common/hiddify-antishare-nft.sh" ]] && \
        cp -p "$BD/manager-overlay/common/hiddify-antishare-nft.sh" \
               "$INSTALL_ROOT/common/hiddify-antishare-nft.sh"

    log "Manager overlay restored"
fi

# ─── Step 4: Restore systemd units ───────────────────────────────────────────
step "Restoring systemd units"
if [[ -d "$BD/systemd" ]]; then
    for f in "$BD/systemd/"*.service "$BD/systemd/"*.timer; do
        [[ -f "$f" ]] || continue
        basename_f="$(basename "$f")"
        cp -p "$f" "/etc/systemd/system/$basename_f"
        log "  restored: /etc/systemd/system/$basename_f"
    done

    # Override dirs
    for override_bdir in "$BD/systemd/"*.d; do
        [[ -d "$override_bdir" ]] || continue
        target_dir="/etc/systemd/system/$(basename "$override_bdir")"
        mkdir -p "$target_dir"
        cp -p "$override_bdir/"* "$target_dir/" 2>/dev/null || true
        log "  restored override dir: $target_dir"
    done

    systemctl daemon-reload || true
    log "Systemd reloaded"
fi

# ─── Step 5: Restore sudoers ─────────────────────────────────────────────────
step "Restoring sudoers"
if [[ -d "$BD/sudoers" ]]; then
    for f in "$BD/sudoers/"*; do
        [[ -f "$f" ]] || continue
        basename_f="$(basename "$f")"
        cp -p "$f" "/etc/sudoers.d/$basename_f"
        chmod 0440 "/etc/sudoers.d/$basename_f"
        log "  restored: /etc/sudoers.d/$basename_f"
    done
fi

# ─── Step 6: Restore manifests ───────────────────────────────────────────────
step "Restoring manifests"
if [[ -d "$BD/manifests" ]]; then
    for f in "$BD/manifests/"*; do
        [[ -f "$f" ]] || continue
        dst="$INSTALL_ROOT/$(basename "$f")"
        cp -p "$f" "$dst"
        log "  restored: $dst"
    done
fi

# ─── Step 7: DB restore (conditional) ───────────────────────────────────────
if [[ $RESTORE_DB -eq 1 ]]; then
    step "RESTORING DATABASE (DESTRUCTIVE)"
    warn "This will OVERWRITE all current DB data with the backup dump"
    warn "Backup: $BD/db-dump.sql"
    warn "Size: $(du -sh "$BD/db-dump.sql" | cut -f1)"

    log "Restoring database..."
    mysql "$DB_NAME" < "$BD/db-dump.sql"
    log "Database restored from backup"
else
    log "DB NOT restored (use --restore-db with CONFIRM_RESTORE_DB=YES to restore)"
fi

# ─── Step 8: Restart services ────────────────────────────────────────────────
step "Restarting panel services"
systemctl restart "$SERVICE_PANEL" "$SERVICE_BG" || warn "Service restart had errors"
sleep 10
check_services_active

echo
log "Rollback completed"
echo "rollback-upgrade OK"
if [[ $RESTORE_DB -eq 0 ]]; then
    echo ""
    echo "Note: Database was NOT restored."
    echo "If DB restore is needed:"
    echo "  CONFIRM_RESTORE_DB=YES sudo bash rollback-upgrade.sh --restore-db"
fi
