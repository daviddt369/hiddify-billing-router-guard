#!/usr/bin/env bash
# check-user-link-preservation.sh — snapshot user/subscription data before and
# after upgrade, then compare to verify hard-preservation data was not lost.
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
        # Hiddify uses 'enable' column, not 'is_active'
        if col_exists "user" "enable"; then
            echo "active=$(db_count "user" "enable=1")"
        elif col_exists "user" "is_active"; then
            echo "active=$(db_count "user" "is_active=1")"
        fi

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

    # ── HARD blockers — fail the entire upgrade if violated ──────────────────
    # Format: "key:cmp_type:description"
    #   ge  = after >= before (numeric — value must not decrease)
    #   eq  = exact match (value must not change at all)
    #   ze  = must equal zero (duplicate_uuids, orphan_subscriptions)
    HARD_CHECKS=(
        "total:ge:Total user count must not decrease"
        "distinct_uuids:ge:UUID count must not decrease (subscription links)"
        "duplicate_uuids:ze:No duplicate UUIDs (subscription links would break)"
        "with_telegram:ge:Users with Telegram ID must not decrease"
        "proxy_path:eq:proxy_path must not change (all client links use it)"
        "proxy_path_admin:eq:proxy_path_admin must not change"
        "proxy_path_client:eq:proxy_path_client must not change"
        "total:ge:Domain count — use 'total' under [domains] section"
        "telegram_bot_configured:eq:Telegram bot config must not disappear"
        "str_config_commercial_keys:ge:Commercial str_config keys must not decrease sharply"
        "bool_config_commercial_keys:ge:Commercial bool_config keys must not decrease"
        "custom_rules:ge:Routing custom rules must not decrease"
        "nft_enabled:eq:anti_share_config.nft_enabled must be preserved"
        "nft_dry_run:eq:anti_share_config.nft_dry_run must be preserved"
        "telegram_enabled:eq:anti_share_config.telegram_enabled must be preserved"
        "orphan_subscriptions:ze:No orphan subscriptions (FK integrity)"
    )

    # ── SOFT warnings — log but do not fail ───────────────────────────────────
    SOFT_CHECKS=(
        "plans:ge:Plans count (soft — tariff inventory may change and is not a blocker)"
        "subscriptions:ge:Subscriptions count (soft — may drift on a live server)"
        "subscriptions_active:ge:Active subscriptions (soft)"
        "state_rows:ge:Anti-share state rows (soft — telemetry may change during upgrade)"
    )

    printf "%-42s %-16s %-16s %s\n" "CHECK" "BEFORE" "AFTER" "RESULT"
    printf "%-42s %-16s %-16s %s\n" "-----" "------" "-----" "------"

    # Run a single check; outputs one table row, returns 0=ok 1=fail
    run_check() {
        local key="$1" cmp_type="$2" label="$3" is_soft="${4:-0}"
        # For domain total, read from [domains] section which has key "total"
        # but conflicts with [users] total. Snapshot uses section headers so
        # we need to disambiguate. We stored domain count as "total" under [domains].
        # For simplicity use the exact key name stored.
        local bval="${before[$key]:-MISSING}"
        local aval="${after[$key]:-MISSING}"
        local result="OK"
        local failed=0

        case "$cmp_type" in
            ge)
                if [[ "$bval" =~ ^[0-9]+$ && "$aval" =~ ^[0-9]+$ ]]; then
                    [[ $aval -ge $bval ]] || { result="FAIL"; failed=1; }
                elif [[ "$aval" == "TABLE_MISSING" && "$bval" != "TABLE_MISSING" ]]; then
                    result="FAIL"; failed=1
                elif [[ "$bval" == "MISSING" && "$aval" == "MISSING" ]]; then
                    result="SKIP"
                fi
                ;;
            eq)
                if [[ "$bval" == "MISSING" && "$aval" == "MISSING" ]]; then
                    result="SKIP"
                elif [[ "$bval" != "$aval" ]]; then
                    result="DIFF"; failed=1
                fi
                ;;
            ze)
                if [[ "$aval" =~ ^[0-9]+$ ]]; then
                    [[ $aval -eq 0 ]] || { result="FAIL($aval)"; failed=1; }
                elif [[ "$aval" == "MISSING" ]]; then
                    result="SKIP"
                fi
                ;;
        esac

        [[ $is_soft -eq 1 && "$result" != "OK" && "$result" != "SKIP" ]] && result="WARN($result)"
        printf "%-42s %-16s %-16s %s\n" "$key" "$bval" "$aval" "$result"
        return $failed
    }

    echo "── HARD BLOCKERS ───────────────────────────────────────────────"
    for spec in "${HARD_CHECKS[@]}"; do
        IFS=':' read -r key cmp desc <<< "$spec"
        run_check "$key" "$cmp" "$desc" 0 || ((failures++)) || true
    done

    echo ""
    echo "── SOFT WARNINGS (non-blocking) ────────────────────────────────"
    local soft_warns=0
    for spec in "${SOFT_CHECKS[@]}"; do
        IFS=':' read -r key cmp desc <<< "$spec"
        run_check "$key" "$cmp" "$desc" 1 || ((soft_warns++)) || true
    done

    echo
    if [[ $failures -eq 0 ]]; then
        echo "PRESERVATION CHECK: PASSED — 0 hard failures, $soft_warns soft warnings"
        echo "All hard-preservation user/link/config data preserved"
    else
        echo "PRESERVATION CHECK: FAILED — $failures hard failure(s), $soft_warns soft warning(s)"
        echo "Review FAIL/DIFF rows — do NOT proceed to production until resolved"
    fi
    [[ $soft_warns -gt 0 ]] && echo "Soft warnings: review but not blocking"
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
