#!/usr/bin/env bash
# compare-db-preservation.sh — detailed DB schema and row-count comparison
# between a backup dump and the live database.
#
# Usage: sudo bash compare-db-preservation.sh [--backup-dir DIR]
#
# Compares:
#   1. Table presence before vs after
#   2. Row counts delta for critical tables
#   3. Schema diff for modified tables (commercial_*, anti_share_*)
#   4. Critical column presence
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../common-upgrade.sh"

UPGRADE_BLOCK="db-compare"
BACKUP_DIR_ARG=""

usage() {
    echo "Usage: sudo bash compare-db-preservation.sh [--backup-dir DIR]"
    echo "       --backup-dir DIR  Path to backup dir (default: latest from $BACKUP_ROOT)"
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --backup-dir) shift; BACKUP_DIR_ARG="$1" ;;
        --help|-h) usage ;;
        *) die "Unknown argument: $1" ;;
    esac
    shift
done

require_root
need_cmd mysqldump

# Resolve backup dir
if [[ -n "$BACKUP_DIR_ARG" ]]; then
    UPGRADE_BACKUP_DIR="$BACKUP_DIR_ARG"
else
    require_backup_exists
fi

BD="$UPGRADE_BACKUP_DIR"
[[ -f "$BD/table-row-counts.txt" ]] || die "No table-row-counts.txt in backup. Run backup-before-upgrade.sh first."

echo "================================================================"
echo " DB PRESERVATION COMPARISON"
echo " Backup: $BD"
echo " Live:   $DB_NAME"
echo " Time:   $(date '+%Y-%m-%d %H:%M:%S')"
echo "================================================================"

failures=0

# ─── Table presence ──────────────────────────────────────────────────────────
echo
echo "── TABLE PRESENCE ──────────────────────────────────────────"

CRITICAL_TABLES=(
    user domain admin_user child proxy show_domain daily_usage
    commercial_plan commercial_subscription
    commercial_routing_custom_rule
    anti_share_config anti_share_state anti_share_ip_profile
    anti_share_event anti_share_user_override
    bool_config str_config
)

for tbl in "${CRITICAL_TABLES[@]}"; do
    backup_had=$(grep -c "^$tbl	" "$BD/table-row-counts.txt" 2>/dev/null || echo 0)
    live_has=$(table_exists "$tbl" && echo 1 || echo 0)

    if [[ "$backup_had" -ge 1 && "$live_has" -eq 1 ]]; then
        echo "  OK   $tbl"
    elif [[ "$backup_had" -ge 1 && "$live_has" -eq 0 ]]; then
        echo "  FAIL $tbl — was in backup, now MISSING in live DB"
        ((failures++))
    elif [[ "$backup_had" -eq 0 && "$live_has" -eq 1 ]]; then
        echo "  NEW  $tbl — added by upgrade (OK)"
    fi
done

# ─── Row count delta ─────────────────────────────────────────────────────────
echo
echo "── ROW COUNTS ──────────────────────────────────────────────"
printf "%-45s %10s %10s %10s %s\n" "TABLE" "BACKUP" "LIVE" "DELTA" "STATUS"

while IFS=$'\t' read -r tbl before_rows; do
    # Skip info_schema header line
    [[ "$tbl" == "table_name" ]] && continue

    if ! table_exists "$tbl" 2>/dev/null; then
        printf "%-45s %10s %10s %10s %s\n" "$tbl" "$before_rows" "MISSING" "-" "FAIL"
        ((failures++))
        continue
    fi

    live_rows=$(db_count "$tbl" 2>/dev/null || echo "?")
    if [[ "$live_rows" =~ ^[0-9]+$ && "$before_rows" =~ ^[0-9]+$ ]]; then
        delta=$(( live_rows - before_rows ))
        # Data tables should not lose rows
        critical=0
        for ct in user commercial_plan commercial_subscription \
                   anti_share_state anti_share_ip_profile; do
            [[ "$tbl" == "$ct" ]] && critical=1
        done

        if [[ $critical -eq 1 && $delta -lt 0 ]]; then
            status="FAIL (lost rows!)"
            ((failures++))
        elif [[ $delta -gt 0 ]]; then
            status="OK (+$delta new)"
        else
            status="OK"
        fi
        printf "%-45s %10s %10s %10s %s\n" "$tbl" "$before_rows" "$live_rows" "$delta" "$status"
    else
        printf "%-45s %10s %10s %10s %s\n" "$tbl" "$before_rows" "${live_rows:-?}" "-" "UNKNOWN"
    fi
done < "$BD/table-row-counts.txt"

# ─── Critical columns check ──────────────────────────────────────────────────
echo
echo "── CRITICAL COLUMNS ────────────────────────────────────────"

declare -A REQUIRED_COLS=(
    ["user:id"]="user table primary key"
    ["user:uuid"]="subscription UUID"
    ["user:enable"]="active flag (Hiddify uses enable, not is_active)"
    ["user:telegram_id"]="telegram link"
    ["commercial_subscription:user_id"]="subscription FK"
    ["commercial_subscription:status"]="subscription status"
    ["anti_share_config:nft_enabled"]="nft enforcement flag"
    ["anti_share_config:nft_dry_run"]="dry-run flag"
    ["anti_share_config:telegram_enabled"]="telegram notification flag"
    ["anti_share_state:user_id"]="state FK"
    ["anti_share_state:score"]="scoring"
    ["anti_share_state:state"]="state machine"
    ["commercial_routing_custom_rule:id"]="routing rule PK"
    ["commercial_routing_custom_rule:rule_type"]="routing rule type"
)

for spec in "${!REQUIRED_COLS[@]}"; do
    tbl="${spec%%:*}"
    col="${spec#*:}"
    desc="${REQUIRED_COLS[$spec]}"

    if ! table_exists "$tbl" 2>/dev/null; then
        echo "  SKIP   $spec  ($tbl missing)"
        continue
    fi

    if col_exists "$tbl" "$col"; then
        echo "  OK     $spec  ($desc)"
    else
        echo "  FAIL   $spec  MISSING  ($desc)"
        ((failures++))
    fi
done

# ─── Summary ─────────────────────────────────────────────────────────────────
echo
echo "================================================================"
if [[ $failures -eq 0 ]]; then
    echo "DB PRESERVATION: PASSED — 0 issues"
else
    echo "DB PRESERVATION: FAILED — $failures issue(s)"
fi
echo "================================================================"
exit $failures
