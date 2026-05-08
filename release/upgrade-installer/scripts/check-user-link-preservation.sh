#!/usr/bin/env bash
# check-user-link-preservation.sh — snapshot user/subscription data before and
# after upgrade, then compare to verify nothing critical was lost.
#
# Usage:
#   sudo bash check-user-link-preservation.sh --before
#   sudo bash check-user-link-preservation.sh --after
#   sudo bash check-user-link-preservation.sh --compare
#
# The script is schema-safe: it inspects SHOW COLUMNS before querying,
# building SELECT only from columns that actually exist.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../common-upgrade.sh"

UPGRADE_BLOCK="user-preservation"
MODE=""

usage() {
    echo "Usage: sudo bash check-user-link-preservation.sh --before | --after | --compare"
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --before) MODE="before" ;;
        --after)  MODE="after" ;;
        --compare) MODE="compare" ;;
        --help|-h) usage ;;
        *) die "Unknown argument: $1" ;;
    esac
    shift
done

[[ -n "$MODE" ]] || die "Specify --before, --after, or --compare"
require_root

# Snapshot file locations
SNAPSHOT_DIR="${UPGRADE_BACKUP_ROOT:-/opt/hiddify-manager/upgrade-installer-backups}/user-preservation"
mkdir -p "$SNAPSHOT_DIR"
BEFORE_FILE="$SNAPSHOT_DIR/snapshot-before.txt"
AFTER_FILE="$SNAPSHOT_DIR/snapshot-after.txt"

# ---------------------------------------------------------------------------
# Schema-safe column collector
# ---------------------------------------------------------------------------
get_existing_cols() {
    local table="$1"
    shift
    local wanted=("$@")
    local existing=()
    for col in "${wanted[@]}"; do
        col_exists "$table" "$col" && existing+=("$col")
    done
    printf '%s\n' "${existing[@]}"
}

# ---------------------------------------------------------------------------
# Snapshot generation
# ---------------------------------------------------------------------------
take_snapshot() {
    local outfile="$1"
    {
        echo "snapshot_time=$(date '+%Y-%m-%d %H:%M:%S')"
        echo "db=$DB_NAME"
        echo ""

        # --- users ---
        echo "[users]"
        echo "total=$(db_count "user")"
        echo "active=$(db_count "user" "is_active=1")"

        # Optional columns in user table
        col_exists "user" "telegram_id" && \
            echo "with_telegram=$(db_query "SELECT COUNT(*) FROM user WHERE telegram_id IS NOT NULL AND telegram_id != 0 AND telegram_id != '';" | head -1)"
        col_exists "user" "max_ips" && \
            echo "with_max_ips=$(db_query "SELECT COUNT(*) FROM user WHERE max_ips IS NOT NULL AND max_ips > 0;" | head -1)"
        col_exists "user" "last_online" && \
            echo "active_7d=$(db_query "SELECT COUNT(*) FROM user WHERE last_online > DATE_SUB(NOW(), INTERVAL 7 DAY);" | head -1)"

        # UUID count (subscription links depend on UUID uniqueness)
        if col_exists "user" "uuid"; then
            echo "distinct_uuids=$(db_query "SELECT COUNT(DISTINCT uuid) FROM user;" | head -1)"
            # Duplicate UUIDs would break links
            dup_uuids=$(db_query "SELECT COUNT(*) FROM (SELECT uuid FROM user GROUP BY uuid HAVING COUNT(*)>1) t;" | head -1 || echo 0)
            echo "duplicate_uuids=$dup_uuids"
        fi

        # --- proxy paths (subscription URL structure) ---
        echo ""
        echo "[paths]"
        for key in proxy_path proxy_path_admin proxy_path_client; do
            val=$(db_query "SELECT value FROM str_config WHERE \`key\`='$key' LIMIT 1;" | head -1 || echo "MISSING")
            echo "$key=$val"
        done

        # --- domains ---
        echo ""
        echo "[domains]"
        echo "total=$(db_count "domain")"
        # If domain table has alias column, count aliases too
        col_exists "domain" "show_domain_alias" && \
            echo "aliases=$(db_query "SELECT COUNT(*) FROM domain WHERE show_domain_alias=1;" | head -1)"

        # --- commercial plans/subscriptions ---
        echo ""
        echo "[business]"
        if table_exists "commercial_plan"; then
            echo "plans=$(db_count "commercial_plan")"
        else
            echo "plans=TABLE_MISSING"
        fi

        if table_exists "commercial_subscription"; then
            echo "subscriptions=$(db_count "commercial_subscription")"
            for status in active cancelled expired; do
                cnt=$(db_query "SELECT COUNT(*) FROM commercial_subscription WHERE status='$status';" | head -1 || echo "?")
                echo "subscriptions_${status}=$cnt"
            done
            # Check subscription-to-user FK integrity
            orphan=$(db_query "SELECT COUNT(*) FROM commercial_subscription cs LEFT JOIN user u ON cs.user_id=u.id WHERE u.id IS NULL;" | head -1 || echo "?")
            echo "orphan_subscriptions=$orphan"
        else
            echo "subscriptions=TABLE_MISSING"
        fi

        # Telegram bot token presence (masked — just check it exists)
        tg_token=$(db_query "SELECT value FROM str_config WHERE \`key\`='telegram_bot_token' LIMIT 1;" | head -1 || echo "")
        echo "telegram_bot_configured=$([ -n "$tg_token" ] && echo yes || echo no)"

        # Telegram webhook domain
        tg_domain=$(db_query "SELECT value FROM str_config WHERE \`key\`='telegram_webhook_domain' LIMIT 1;" | head -1 || echo "")
        echo "telegram_webhook_domain=$(mask_secret "$tg_domain")"

        # --- commercial configs key count ---
        echo ""
        echo "[commercial_configs]"
        str_cnt=$(db_query "SELECT COUNT(*) FROM str_config WHERE \`key\` LIKE 'commercial_%';" | head -1 || echo 0)
        bool_cnt=$(db_query "SELECT COUNT(*) FROM bool_config WHERE \`key\` LIKE 'commercial_%';" | head -1 || echo 0)
        echo "str_config_commercial_keys=$str_cnt"
        echo "bool_config_commercial_keys=$bool_cnt"

        # --- routing ---
        echo ""
        echo "[routing]"
        if table_exists "commercial_routing_custom_rule"; then
            echo "custom_rules=$(db_count "commercial_routing_custom_rule")"
        else
            echo "custom_rules=TABLE_MISSING"
        fi
        if table_exists "commercial_routing_upstream"; then
            echo "upstreams=$(db_count "commercial_routing_upstream")"
        else
            echo "upstreams=TABLE_MISSING"
        fi
        if table_exists "commercial_routing_rule_source"; then
            echo "rule_sources=$(db_count "commercial_routing_rule_source")"
        else
            echo "rule_sources=TABLE_MISSING"
        fi

        # --- anti-share ---
        echo ""
        echo "[antishare]"
        if table_exists "anti_share_config"; then
            nft_en=$(db_query "SELECT nft_enabled FROM anti_share_config LIMIT 1;" | head -1 || echo "?")
            dry=$(db_query "SELECT nft_dry_run FROM anti_share_config LIMIT 1;" | head -1 || echo "?")
            tg=$(db_query "SELECT telegram_enabled FROM anti_share_config LIMIT 1;" | head -1 || echo "?")
            echo "nft_enabled=$nft_en"
            echo "nft_dry_run=$dry"
            echo "telegram_enabled=$tg"
        fi
        if table_exists "anti_share_state"; then
            echo "state_rows=$(db_count "anti_share_state")"
        fi

    } > "$outfile"
    log "Snapshot written to $outfile"
    cat "$outfile" | grep -v '^#' | head -60
}

# ---------------------------------------------------------------------------
# Compare two snapshots
# ---------------------------------------------------------------------------
compare_snapshots() {
    [[ -f "$BEFORE_FILE" ]] || die "Before snapshot missing: $BEFORE_FILE. Run --before first."
    [[ -f "$AFTER_FILE" ]] || die "After snapshot missing: $AFTER_FILE. Run --after first."

    echo "================================================================"
    echo " USER/LINK PRESERVATION COMPARISON"
    echo " Before: $BEFORE_FILE"
    echo " After:  $AFTER_FILE"
    echo "================================================================"

    local failures=0

    # Parse both snapshots into associative arrays
    declare -A before after
    while IFS='=' read -r key val; do
        [[ "$key" =~ ^# || -z "$key" || "$key" =~ ^\[ ]] && continue
        before["$key"]="$val"
    done < "$BEFORE_FILE"
    while IFS='=' read -r key val; do
        [[ "$key" =~ ^# || -z "$key" || "$key" =~ ^\[ ]] && continue
        after["$key"]="$val"
    done < "$AFTER_FILE"

    # Define critical checks: key | comparison | description
    # comparison: ge = after >= before, eq = exact match, ne = must not change
    declare -A CRITICAL_CHECKS=(
        [total]="ge:Total users must not decrease"
        [active]="ge:Active users must not decrease"
        [distinct_uuids]="ge:UUID count must not decrease (subscription links)"
        [duplicate_uuids]="eq:No new duplicate UUIDs"
        [proxy_path]="eq:Proxy path must not change"
        [proxy_path_admin]="eq:Admin path must not change"
        [plans]="ge:Plans must not decrease"
        [subscriptions]="ge:Subscriptions must not decrease"
        [subscriptions_active]="ge:Active subscriptions must not decrease"
        [orphan_subscriptions]="eq:No new orphan subscriptions"
        [telegram_bot_configured]="eq:Telegram bot config must not disappear"
        [str_config_commercial_keys]="ge:Commercial config keys must not decrease"
        [custom_rules]="ge:Routing custom rules must not decrease"
        [nft_enabled]="eq:Anti-share nft_enabled must be preserved"
        [nft_dry_run]="eq:Anti-share nft_dry_run must be preserved"
        [telegram_enabled]="eq:Anti-share telegram_enabled must be preserved"
    )

    printf "%-40s %-15s %-15s %s\n" "CHECK" "BEFORE" "AFTER" "RESULT"
    printf "%-40s %-15s %-15s %s\n" "-----" "------" "-----" "------"

    for key in "${!CRITICAL_CHECKS[@]}"; do
        spec="${CRITICAL_CHECKS[$key]}"
        cmp_type="${spec%%:*}"
        desc="${spec#*:}"
        bval="${before[$key]:-MISSING}"
        aval="${after[$key]:-MISSING}"

        result="OK"
        case "$cmp_type" in
            ge)
                # after >= before (numeric)
                if [[ "$bval" =~ ^[0-9]+$ && "$aval" =~ ^[0-9]+$ ]]; then
                    [[ $aval -ge $bval ]] || { result="FAIL"; ((failures++)); }
                elif [[ "$aval" == "TABLE_MISSING" && "$bval" != "TABLE_MISSING" ]]; then
                    result="FAIL"; ((failures++))
                fi
                ;;
            eq)
                [[ "$bval" == "$aval" ]] || { result="DIFF"; ((failures++)); }
                ;;
        esac

        printf "%-40s %-15s %-15s %s\n" "$key" "$bval" "$aval" "$result"
    done

    echo
    if [[ $failures -eq 0 ]]; then
        echo "PRESERVATION CHECK: PASSED — $failures issues"
        echo "All critical user/subscription/config data preserved"
    else
        echo "PRESERVATION CHECK: FAILED — $failures issue(s)"
        echo "Review FAIL/DIFF rows before proceeding to production"
    fi
    return $failures
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
case "$MODE" in
    before)
        step "Taking pre-upgrade snapshot"
        take_snapshot "$BEFORE_FILE"
        echo ""
        echo "check-user-link-preservation --before OK"
        echo "Run --after after upgrade, then --compare"
        ;;
    after)
        step "Taking post-upgrade snapshot"
        take_snapshot "$AFTER_FILE"
        echo ""
        echo "check-user-link-preservation --after OK"
        echo "Run --compare to see diff"
        ;;
    compare)
        compare_snapshots
        ;;
esac
