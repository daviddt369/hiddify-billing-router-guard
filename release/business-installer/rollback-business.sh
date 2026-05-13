#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/common.sh"

RESTORE_DB=0
BACKUP_ARG=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --restore-db)
            RESTORE_DB=1
            shift
            ;;
        --backup-dir)
            BACKUP_ARG="${2:-}"
            shift 2
            ;;
        *)
            die "Unknown argument: $1"
            ;;
    esac
done

main() {
    INSTALL_BLOCK="rollback-business"
    require_root
    assert_install_root
    assert_services_exist

    local backup_dir
    if [[ -n "$BACKUP_ARG" ]]; then
        backup_dir="$BACKUP_ARG"
    elif [[ -f "$BACKUP_ROOT/latest" ]]; then
        backup_dir="$(<"$BACKUP_ROOT/latest")"
    else
        die "No latest backup marker found."
    fi

    rollback_backup_dir "$backup_dir" "$RESTORE_DB" 1
    echo "Business addon rollback OK: $backup_dir"
}

main "$@"
