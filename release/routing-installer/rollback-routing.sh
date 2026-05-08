#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common-routing.sh"

usage() {
    echo "Usage: sudo bash rollback-routing.sh [--backup-dir DIR] [--restore-db]"
    echo ""
    echo "  --backup-dir DIR   Use specific backup directory (default: latest)"
    echo "  --restore-db       Also restore database from backup dump"
    exit 1
}

main() {
    INSTALL_BLOCK="rollback-routing"
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

    # Stop and disable xray-router
    step "Stopping xray-router.service"
    systemctl stop "$XRAY_ROUTER_SERVICE" 2>/dev/null || true
    systemctl disable "$XRAY_ROUTER_SERVICE" 2>/dev/null || true

    # Remove sudoers if it was created by this install (in created-files.txt)
    if [[ -f "$backup_dir/created-files.txt" ]]; then
        if grep -q "$SUDOERS_FILE" "$backup_dir/created-files.txt"; then
            step "Removing sudoers file (was created by routing install)"
            rm -f "$SUDOERS_FILE"
        fi
    fi

    # Remove xray-router service file if it was created by this install
    if [[ -f "$backup_dir/created-files.txt" ]]; then
        if grep -q "$XRAY_ROUTER_SERVICE_FILE" "$backup_dir/created-files.txt"; then
            step "Removing xray-router.service file (was created by routing install)"
            rm -f "$XRAY_ROUTER_SERVICE_FILE"
        fi
    fi

    # Run common rollback (restores files, optionally restores DB, removes commander patch)
    step "Rolling back installed files"
    rollback_backup_dir "$backup_dir" "$restore_db" 1

    # Reload systemd after file changes
    systemctl daemon-reload || true

    # Remove manifest
    if [[ -f "$MANIFEST_PATH" ]]; then
        step "Removing routing manifest"
        rm -f "$MANIFEST_PATH"
    fi

    step "Rollback completed"
    echo "Routing addon rollback OK"
    echo "Note: run apply_configs.sh --no-gui to regenerate Hiddify configs without routing templates."
    echo "Then run service-tools stabilize-hiddify-cli.sh if needed."
}

main "$@"
