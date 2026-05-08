#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common-antishare.sh"

usage() {
    echo "Usage: sudo bash rollback-antishare.sh [--backup-dir DIR] [--restore-db]"
    echo ""
    echo "  --backup-dir DIR   Use specific backup directory (default: latest)"
    echo "  --restore-db       Also restore database from backup dump (DROPS anti_share_* data)"
    exit 1
}

main() {
    INSTALL_BLOCK="rollback-antishare"
    require_root

    local backup_dir="" restore_db=0

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --backup-dir) shift; backup_dir="$1" ;;
            --restore-db) restore_db=1 ;;
            --help|-h) usage ;;
            *) die "Unknown argument: $1" ;;
        esac
        shift
    done

    # Resolve backup dir
    if [[ -z "$backup_dir" ]]; then
        [[ -f "$BACKUP_ROOT/latest" ]] \
            || die "No latest backup found. Use --backup-dir to specify one."
        backup_dir="$(cat "$BACKUP_ROOT/latest")"
    fi

    [[ -d "$backup_dir" ]] || die "Backup directory not found: $backup_dir"
    BACKUP_DIR="$backup_dir"
    step "Using backup: $backup_dir"

    # Stop and disable anti-share timer and service
    step "Stopping hiddify-anti-share timer and service"
    systemctl stop  "$ANTISHARE_TIMER"   2>/dev/null || true
    systemctl stop  "$ANTISHARE_SERVICE" 2>/dev/null || true
    systemctl disable "$ANTISHARE_TIMER" 2>/dev/null || true

    # Remove systemd unit files if they were created by this install
    if [[ -f "$backup_dir/created-files.txt" ]]; then
        for unit_file in "$ANTISHARE_SERVICE_FILE" "$ANTISHARE_TIMER_FILE"; do
            if grep -q "$unit_file" "$backup_dir/created-files.txt" 2>/dev/null; then
                step "Removing systemd unit: $unit_file"
                rm -f "$unit_file"
            fi
        done
    fi

    # Remove sudoers if it was created by this install
    if [[ -f "$backup_dir/created-files.txt" ]]; then
        if grep -q "$SUDOERS_FILE" "$backup_dir/created-files.txt" 2>/dev/null; then
            step "Removing sudoers file (was created by antishare install)"
            rm -f "$SUDOERS_FILE"
        fi
    fi

    # Run common rollback (restores files, optionally restores DB)
    step "Rolling back installed files"
    rollback_backup_dir "$backup_dir" "$restore_db" 1

    # Reload systemd after file changes
    systemctl daemon-reload || true

    # Remove manifest
    if [[ -f "$MANIFEST_PATH" ]]; then
        step "Removing anti-share manifest"
        rm -f "$MANIFEST_PATH"
    fi

    step "Rollback completed"
    echo "Anti-share addon rollback OK"
    if [[ "$restore_db" == "0" ]]; then
        echo ""
        echo "Note: anti_share_* DB tables were NOT dropped (user data preserved)."
        echo "To also restore the database: sudo bash rollback-antishare.sh --restore-db"
    fi
}

main "$@"
