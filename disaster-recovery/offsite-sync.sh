#!/usr/bin/env bash
# disaster-recovery/offsite-sync.sh
#
# Encrypts the most recent (or a specified) disaster-recovery/snapshot.sh
# bundle with `age` (asymmetric — this server only ever gets the PUBLIC key,
# so compromising this server alone cannot decrypt any offsite backup, past
# or future) and uploads it to the Selectel S3 bucket configured in
# /etc/vpn-ru-node/backup.env. Applies retention both offsite and locally.
#
# Usage:
#   sudo bash offsite-sync.sh [snapshot-dir]
#     snapshot-dir defaults to the newest directory under
#     /root/dr-snapshots/ (i.e. whatever snapshot.sh just produced).
#
# Env overrides:
#   DR_SNAPSHOTS_ROOT     default /root/dr-snapshots
#   DR_OFFSITE_KEEP       how many generations to keep in the bucket (default 8)
#   DR_LOCAL_KEEP         how many generations to keep on local disk (default 3)
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common-dr.sh"

DR_BLOCK="offsite-sync"
trap dr_error_trap ERR

DR_SNAPSHOTS_ROOT="${DR_SNAPSHOTS_ROOT:-/root/dr-snapshots}"
DR_OFFSITE_KEEP="${DR_OFFSITE_KEEP:-8}"
DR_LOCAL_KEEP="${DR_LOCAL_KEEP:-3}"

pick_snapshot_dir() {
    if [[ -n "${1:-}" ]]; then
        printf '%s\n' "$1"
        return 0
    fi
    local newest
    newest="$(find "$DR_SNAPSHOTS_ROOT" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)"
    [[ -n "$newest" ]] || dr_die "No snapshot directories found under $DR_SNAPSHOTS_ROOT — run snapshot.sh first"
    printf '%s\n' "$newest"
}

encrypt_and_upload() {
    local snap_dir="$1"
    local stamp
    stamp="$(basename "$snap_dir")"
    local archive_name="hiddify-dr-${stamp}.tar.gz.age"
    local tmp_archive
    tmp_archive="$(mktemp /tmp/dr-offsite.XXXXXX.tar.gz.age)"

    dr_step "Encrypting $snap_dir -> $archive_name (age, recipient key only — this server cannot decrypt it)"
    tar -C "$(dirname "$snap_dir")" -czf - "$stamp" | age -r "$DR_AGE_PUBLIC_KEY" -o "$tmp_archive"
    local size_mb
    size_mb=$(( $(stat -c%s "$tmp_archive") / 1024 / 1024 ))
    dr_log "Encrypted archive: ${size_mb} MB"

    dr_step "Uploading to Selectel: s3://$SELECTEL_S3_BUCKET/$DR_OFFSITE_PREFIX/$archive_name"
    rclone copyto "$tmp_archive" "$(dr_rclone_remote)/$DR_OFFSITE_PREFIX/$archive_name" --s3-no-check-bucket
    rm -f "$tmp_archive"
    dr_log "Upload complete."
}

prune_offsite() {
    dr_step "Enforcing offsite retention (keep last $DR_OFFSITE_KEEP)"
    local remote
    remote="$(dr_rclone_remote)/$DR_OFFSITE_PREFIX/"
    mapfile -t objects < <(rclone lsf "$remote" --files-only 2>/dev/null | sort)
    local count="${#objects[@]}"
    if (( count <= DR_OFFSITE_KEEP )); then
        dr_log "Offsite has $count generation(s), within limit ($DR_OFFSITE_KEEP) — nothing to prune."
        return 0
    fi
    local to_delete=$(( count - DR_OFFSITE_KEEP ))
    dr_log "Offsite has $count generation(s), deleting oldest $to_delete."
    for ((i = 0; i < to_delete; i++)); do
        dr_log "  deleting offsite: ${objects[$i]}"
        rclone deletefile "${remote}${objects[$i]}"
    done
}

prune_local() {
    dr_step "Enforcing local retention (keep last $DR_LOCAL_KEEP under $DR_SNAPSHOTS_ROOT)"
    mapfile -t dirs < <(find "$DR_SNAPSHOTS_ROOT" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' 2>/dev/null | sort -rn | cut -d' ' -f2-)
    local count="${#dirs[@]}"
    if (( count <= DR_LOCAL_KEEP )); then
        dr_log "Local has $count generation(s), within limit ($DR_LOCAL_KEEP) — nothing to prune."
        return 0
    fi
    for ((i = DR_LOCAL_KEEP; i < count; i++)); do
        dr_log "  deleting local: ${dirs[$i]}"
        rm -rf "${dirs[$i]}"
    done
}

main() {
    dr_require_root
    dr_ensure_offsite_tools
    dr_load_backup_env

    local snap_dir
    snap_dir="$(pick_snapshot_dir "${1:-}")"
    [[ -d "$snap_dir" ]] || dr_die "Snapshot directory not found: $snap_dir"
    [[ -f "$snap_dir/mariadb-full-dump.sql" ]] || dr_die "$snap_dir does not look like a snapshot.sh output (no mariadb-full-dump.sql)"

    encrypt_and_upload "$snap_dir"
    prune_offsite
    prune_local

    dr_step "OFFSITE SYNC COMPLETE"
}

main "$@"
