#!/usr/bin/env bash
# disaster-recovery/node-backup/nodes-backup-check.sh  (runs on the RU node, hourly, read-only for the cloud)
#
# The exit-node backups are pulled by the operator's PC and uploaded to <bucket>/nodes/<name>/. Nothing on the node side can notice that the PC is off
# or the chain is broken, so the RU node looks at the CLOUD: for every node it finds the newest object and sets the marker
# /var/lib/vpn-backup/node-<name>.ok to that object's own time. vpn-watch alerts when a marker is older than its limit (or missing).
# Only an object that really exists in the cloud refreshes a marker; a failed listing changes nothing (the marker just keeps ageing).
#
# env: NODE_BACKUP_NAMES (default "de nl"), MARKER_DIR (default /var/lib/vpn-backup), DR_BACKUP_ENV via common-dr.sh
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common-dr.sh"
DR_BLOCK="nodes-check"
trap dr_error_trap ERR

NAMES="${NODE_BACKUP_NAMES:-de nl}"
MARKER_DIR="${MARKER_DIR:-/var/lib/vpn-backup}"

main() {
    dr_require_root
    dr_need_cmd rclone
    dr_load_backup_env
    mkdir -p "$MARKER_DIR"; chmod 700 "$MARKER_DIR"
    local base n listing newest ts path epoch
    base="$(dr_rclone_remote)"
    for n in $NAMES; do
        # TZ=UTC: rclone prints modification times in the LOCAL zone (RU runs on MSK); we parse them as UTC. "time;path" per object; a failed listing (rclone exit code) is different from an empty one
        if ! listing="$(TZ=UTC rclone lsf --format tp "$base/nodes/$n/" --files-only 2>/dev/null)"; then
            dr_warn "$n: listing failed - marker left as it is"; continue
        fi
        newest="$(printf '%s
' "$listing" | grep -E ";node-$n-.*\.tar\.gz\.age$" | sort | tail -n 1 || true)"
        if [[ -z "$newest" ]]; then dr_warn "$n: no backup objects in the cloud"; continue; fi
        ts="${newest%%;*}"; path="${newest#*;}"
        epoch="$(date -u -d "$ts" +%s 2>/dev/null || true)"
        [[ -n "$epoch" ]] || { dr_warn "$n: cannot parse the object time '$ts'"; continue; }
        touch "$MARKER_DIR/node-$n.ok"
        touch -d "@$epoch" "$MARKER_DIR/node-$n.ok"
        dr_log "$n: newest $path ($ts UTC) -> marker set ($(( ( $(date +%s) - epoch ) / 3600 )) h old)"
    done
}

main "$@"
