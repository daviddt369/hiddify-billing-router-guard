#!/usr/bin/env bash
# disaster-recovery/db-daily-backup.sh
#
# Light DAILY backup of the panel database only (users, subscriptions, payments, settings, routing rules).
# The full snapshot (snapshot.sh + offsite-sync.sh, Mon+Thu) stays as it is; this closes the gap of up to 4 days between two snapshots.
#
#   mariadb-dump (--single-transaction, no table locks) -> gzip -> age (PUBLIC key only: this server cannot decrypt it)
#   -> Selectel bucket, prefix db-daily/ -> size check of the uploaded object -> keep the newest DR_DB_KEEP (default 14) objects.
# Nothing stays on the server (the temp file is removed); credentials come from app.cfg and never reach the log.
#
# Env overrides: DR_DB_KEEP (14), DR_DB_MIN_BYTES (100000: a dump smaller than this is treated as broken and NOT uploaded),
#                DR_DB_LOCAL_DIR (if set: also keep the last DR_DB_LOCAL_KEEP=2 encrypted copies there)
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common-dr.sh"

DR_BLOCK="db-daily"
trap dr_error_trap ERR

DB_PREFIX="db-daily"
DR_DB_KEEP="${DR_DB_KEEP:-14}"
DR_DB_MIN_BYTES="${DR_DB_MIN_BYTES:-100000}"
DR_DB_LOCAL_KEEP="${DR_DB_LOCAL_KEEP:-2}"

TMP_PLAIN=""
TMP_ENC=""
cleanup() { dr_shred_db_creds; [[ -n "$TMP_PLAIN" ]] && rm -f "$TMP_PLAIN"; [[ -n "$TMP_ENC" ]] && rm -f "$TMP_ENC"; return 0; }
trap cleanup EXIT

main() {
    dr_require_root
    dr_need_cmd mariadb-dump
    dr_need_cmd gzip
    dr_ensure_offsite_tools
    dr_load_backup_env

    local stamp name remote
    stamp="$(date +%Y%m%d-%H%M%S)"
    name="hiddify-db-${stamp}.sql.gz.age"
    remote="$(dr_rclone_remote)/$DB_PREFIX"

    dr_step "Dumping the database (mariadb-dump --single-transaction)"
    dr_parse_db_uri
    TMP_PLAIN="$(mktemp /root/.dr-dbdaily.XXXXXX.sql.gz)"
    chmod 600 "$TMP_PLAIN"
    MYSQL_PWD="$(dr_db_pass)" mariadb-dump \
        --single-transaction --routines --triggers --events \
        -h "$(dr_db_host)" -P "$(dr_db_port)" -u "$(dr_db_user)" "$(dr_db_name)" \
        | gzip -9 > "$TMP_PLAIN"
    dr_shred_db_creds

    dr_step "Checking the dump before it is trusted"
    gzip -t "$TMP_PLAIN" || dr_die "gzip test failed - the dump is broken, nothing uploaded"
    local plain_size
    plain_size="$(stat -c%s "$TMP_PLAIN")"
    (( plain_size >= DR_DB_MIN_BYTES )) || dr_die "dump is only $plain_size bytes (< $DR_DB_MIN_BYTES) - treated as broken, nothing uploaded"
    zcat "$TMP_PLAIN" | tail -n 3 | grep -q -- '-- Dump completed' || dr_die "no 'Dump completed' trailer - the dump is cut, nothing uploaded"
    zcat "$TMP_PLAIN" | grep -q 'CREATE TABLE `user`' || dr_die "table 'user' not found in the dump, nothing uploaded"
    dr_log "dump ok: $((plain_size / 1024)) KB gzipped"

    dr_step "Encrypting (age, recipient key only) -> $name"
    TMP_ENC="$(mktemp /root/.dr-dbdaily.XXXXXX.age)"
    age -r "$DR_AGE_PUBLIC_KEY" -o "$TMP_ENC" "$TMP_PLAIN"
    rm -f "$TMP_PLAIN"; TMP_PLAIN=""
    local enc_size
    enc_size="$(stat -c%s "$TMP_ENC")"
    dr_log "encrypted: $((enc_size / 1024)) KB"

    dr_step "Uploading to s3://$SELECTEL_S3_BUCKET/$DB_PREFIX/$name"
    rclone copyto "$TMP_ENC" "$remote/$name" --s3-no-check-bucket
    local got
    got="$(rclone lsf --format s "$remote/$name" 2>/dev/null | head -1 | tr -d '[:space:]')"
    [[ "$got" == "$enc_size" ]] || dr_die "uploaded size mismatch (local $enc_size, remote '${got:-none}') - check the offsite object"
    dr_log "upload verified: $got bytes"

    if [[ -n "${DR_DB_LOCAL_DIR:-}" ]]; then
        mkdir -p "$DR_DB_LOCAL_DIR"; chmod 700 "$DR_DB_LOCAL_DIR"
        cp "$TMP_ENC" "$DR_DB_LOCAL_DIR/$name"
        ls -1t "$DR_DB_LOCAL_DIR"/hiddify-db-*.age 2>/dev/null | tail -n +$((DR_DB_LOCAL_KEEP + 1)) | xargs -r rm -f
    fi

    dr_step "Retention: keep the newest $DR_DB_KEEP objects under $DB_PREFIX/"
    local objects count to_delete i
    mapfile -t objects < <(rclone lsf "$remote/" --files-only 2>/dev/null | grep '^hiddify-db-.*\.sql\.gz\.age$' | sort)
    count="${#objects[@]}"
    if (( count > DR_DB_KEEP )); then
        to_delete=$((count - DR_DB_KEEP))
        for ((i = 0; i < to_delete; i++)); do
            dr_log "  deleting offsite: ${objects[$i]}"
            rclone deletefile "$remote/${objects[$i]}"
        done
    else
        dr_log "$count object(s), within the limit"
    fi
    dr_step "DB DAILY BACKUP COMPLETE ($name)"
}

main "$@"
