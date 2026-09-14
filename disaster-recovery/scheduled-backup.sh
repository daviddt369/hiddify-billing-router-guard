#!/usr/bin/env bash
# disaster-recovery/scheduled-backup.sh
#
# Entry point for the systemd timer: snapshot.sh (capture) + offsite-sync.sh
# (encrypt + upload to Selectel + retention). Run this manually to test;
# the timer unit (disaster-recovery/systemd/hiddify-dr-backup.timer) is what
# actually schedules it in production.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common-dr.sh"

DR_BLOCK="scheduled-backup"
trap dr_error_trap ERR

main() {
    dr_require_root
    dr_step "Running snapshot.sh"
    bash "$SCRIPT_DIR/snapshot.sh"

    dr_step "Running offsite-sync.sh"
    bash "$SCRIPT_DIR/offsite-sync.sh"

    dr_step "SCHEDULED BACKUP COMPLETE"
}

main "$@"
