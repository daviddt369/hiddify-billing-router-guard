#!/usr/bin/env bash
# preflight-upgrade-audit.sh — read-only audit of an existing Hiddify server
# before upgrade. Produces a structured report with no secrets in plaintext.
#
# Usage: sudo bash preflight-upgrade-audit.sh [--output FILE]
#
# Completely read-only. No services stopped, no files modified.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common-upgrade.sh"

UPGRADE_BLOCK="preflight"
OUTPUT_FILE=""

usage() {
    echo "Usage: sudo bash preflight-upgrade-audit.sh [--output FILE]"
    echo ""
    echo "  --output FILE   Also write report to FILE (default: stdout only)"
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --output) shift; OUTPUT_FILE="$1" ;;
        --help|-h) usage ;;
        *) die "Unknown argument: $1" ;;
    esac
    shift
done

require_root

# If output file requested, tee to it
if [[ -n "$OUTPUT_FILE" ]]; then
    exec > >(tee "$OUTPUT_FILE") 2>&1
fi

echo "============================================================"
echo " HIDDIFY UPGRADE PREFLIGHT AUDIT"
echo " $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================"

# ─── Section 1: System ───────────────────────────────────────────────────────
echo
echo "── SYSTEM ──────────────────────────────────────────────────"

echo "Hiddify panel version: $(get_hiddify_version)"
echo "Kernel:  $(uname -r)"
echo "OS:      $(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME" || uname -s)"
echo "Uptime:  $(uptime -p 2>/dev/null || uptime)"
echo "Install root: $INSTALL_ROOT"

# Python venv
venv_py=$(find "$INSTALL_ROOT" -name 'python3*' -path '*/venv*/bin/*' -type f 2>/dev/null | sort | head -1 || true)
echo "Venv python: ${venv_py:-NOT FOUND}"

runtime_path=$(find "$INSTALL_ROOT" -type d -path '*/site-packages/hiddifypanel' 2>/dev/null | head -1 || true)
echo "Runtime path: ${runtime_path:-NOT FOUND}"

# ─── Section 2: Services ─────────────────────────────────────────────────────
echo
echo "── SERVICES ────────────────────────────────────────────────"

declare -A KNOWN_SERVICES=(
    [hiddify-panel]="panel"
    [hiddify-panel-background-tasks]="bg-tasks"
    [hiddify-xray]="xray"
    [hiddify-singbox]="singbox"
    [hiddify-haproxy]="haproxy"
    [hiddify-nginx]="nginx"
    [hiddify-redis]="redis"
    [mariadb]="mariadb"
    [xray-router]="xray-router"
    [hiddify-anti-share.timer]="antishare-timer"
    [hiddify-anti-share.service]="antishare-svc"
    [hiddify-cli]="hiddify-cli"
)

for svc in "${!KNOWN_SERVICES[@]}"; do
    state=$(systemctl is-active "$svc" 2>/dev/null || echo "not-found")
    enabled=$(systemctl is-enabled "$svc" 2>/dev/null || echo "n/a")
    restarts=$(systemctl show "$svc" -p NRestarts 2>/dev/null | cut -d= -f2 || echo "?")
    result=$(systemctl show "$svc" -p Result 2>/dev/null | cut -d= -f2 || echo "?")
    printf "  %-40s active=%-10s enabled=%-10s restarts=%-4s result=%s\n" \
        "$svc" "$state" "$enabled" "$restarts" "$result"
done

echo
echo "Failed units:"
systemctl --failed --no-pager 2>/dev/null | grep -E "●|loaded" | grep -v "^Legend" || echo "  (none)"

# ─── Section 3: Addon Manifests ──────────────────────────────────────────────
echo
echo "── ADDON MANIFESTS ─────────────────────────────────────────"

for mf in "$BUSINESS_MANIFEST" "$ROUTING_MANIFEST" "$ANTISHARE_MANIFEST"; do
    label="$(basename "$mf")"
    if [[ -f "$mf" ]]; then
        echo "  PRESENT: $label"
        # Show version/timestamp lines, mask any secrets
        grep -E 'version|timestamp|INSTALL_TIMESTAMP|SCRIPT_VERSION|release_version|git_commit|ADDON_REF' \
            "$mf" 2>/dev/null | head -5 | sed 's/^/    /'
    else
        echo "  MISSING: $label"
    fi
done

# ─── Section 4: DB State ─────────────────────────────────────────────────────
echo
echo "── DATABASE ────────────────────────────────────────────────"

if ! mysql "$DB_NAME" -e "SELECT 1;" >/dev/null 2>&1; then
    warn "Cannot connect to database $DB_NAME"
else
    # db_version
    db_ver=$(db_query "SELECT value FROM str_config WHERE \`key\`='db_version' LIMIT 1;" | head -1 || echo "?")
    echo "  db_version: $db_ver"
    [[ "$db_ver" == "136" ]] && echo "    ✓ db_version OK" || echo "    ✗ db_version needs advance (celery beat affected)"

    # Tables
    echo
    echo "  Tables:"
    for tbl in commercial_plan commercial_subscription commercial_routing_custom_rule \
                commercial_routing_upstream commercial_routing_rule_source \
                anti_share_config anti_share_state anti_share_ip_profile \
                anti_share_event anti_share_user_override bool_config str_config; do
        if table_exists "$tbl"; then
            cnt=$(db_count "$tbl" 2>/dev/null || echo "?")
            echo "    PRESENT: $tbl  (rows: $cnt)"
        else
            echo "    MISSING: $tbl"
        fi
    done

    # commercial_routing_custom_rule: source_id?
    if table_exists "commercial_routing_custom_rule"; then
        if col_exists "commercial_routing_custom_rule" "source_id"; then
            echo "    PRESENT: commercial_routing_custom_rule.source_id (Stage 2F applied)"
        else
            echo "    MISSING: commercial_routing_custom_rule.source_id (Stage 2F not applied)"
        fi
    fi

    # Users
    echo
    echo "  Users:"
    total=$(db_query "SELECT COUNT(*) FROM user;" | head -1 || echo "?")
    active=$(db_query "SELECT COUNT(*) FROM user WHERE is_active=1;" | head -1 || echo "?")
    echo "    total=$total  active=$active"

    # telegram_id
    if col_exists "user" "telegram_id"; then
        tg_cnt=$(db_query "SELECT COUNT(*) FROM user WHERE telegram_id IS NOT NULL AND telegram_id != 0 AND telegram_id != '';" | head -1 || echo "?")
        echo "    with_telegram_id=$tg_cnt"
    else
        echo "    telegram_id column: NOT FOUND in user table"
    fi

    # max_ips
    if col_exists "user" "max_ips"; then
        max_ips_cnt=$(db_query "SELECT COUNT(*) FROM user WHERE max_ips IS NOT NULL AND max_ips > 0;" | head -1 || echo "?")
        echo "    with_max_ips=$max_ips_cnt"
    fi

    # last_online
    if col_exists "user" "last_online"; then
        active_7d=$(db_query "SELECT COUNT(*) FROM user WHERE last_online > DATE_SUB(NOW(), INTERVAL 7 DAY);" | head -1 || echo "?")
        echo "    active_last_7d=$active_7d"
    fi

    # Plans/subscriptions
    if table_exists "commercial_plan"; then
        plans=$(db_count "commercial_plan")
        echo "    commercial_plan rows: $plans"
    fi
    if table_exists "commercial_subscription"; then
        subs=$(db_count "commercial_subscription")
        active_subs=$(db_query "SELECT COUNT(*) FROM commercial_subscription WHERE status='active';" | head -1 || echo "?")
        echo "    commercial_subscription rows: $subs  (active: $active_subs)"
    fi

    # commercial configs
    echo
    echo "  Commercial configs (masked):"
    SENSITIVE_KEYS="telegram_bot_token telegram_payment_provider_token commercial_de_endpoint commercial_de_public_key commercial_de_private_key_ref commercial_de_vless_uri commercial_de_trojan_uri"

    if table_exists "str_config"; then
        while IFS=$'\t' read -r key val; do
            if echo "$SENSITIVE_KEYS" | grep -qw "$key"; then
                printf "    str_config[%s] = %s\n" "$key" "$(mask_secret "$val")"
            else
                printf "    str_config[%s] = %s\n" "$key" "$val"
            fi
        done < <(db_query "SELECT \`key\`, COALESCE(value,'NULL') FROM str_config
                           WHERE \`key\` LIKE 'commercial_%'
                           ORDER BY \`key\`;" 2>/dev/null || true)
    fi

    if table_exists "bool_config"; then
        while IFS=$'\t' read -r key val; do
            printf "    bool_config[%s] = %s\n" "$key" "$val"
        done < <(db_query "SELECT \`key\`, COALESCE(CAST(value AS CHAR),'NULL') FROM bool_config
                           WHERE \`key\` LIKE 'commercial_%'
                           ORDER BY \`key\`;" 2>/dev/null || true)
    fi

    # anti_share_config
    echo
    echo "  Anti-share config:"
    if table_exists "anti_share_config"; then
        db_query "SELECT enabled, nft_enabled, nft_dry_run, telegram_enabled,
                         window_seconds, scan_limit, created_at, updated_at
                  FROM anti_share_config LIMIT 1;" 2>/dev/null \
            | awk 'BEGIN{OFS="\n  "}
                   NR==1{print "    enabled="$1, "nft_enabled="$2, "nft_dry_run="$3,
                               "telegram_enabled="$4, "window_seconds="$5, "scan_limit="$6,
                               "created_at="$7, "updated_at="$8}' || true
        # Warn on active enforcement
        nft_en=$(db_query "SELECT nft_enabled FROM anti_share_config LIMIT 1;" | head -1 || echo 0)
        dry=$(db_query "SELECT nft_dry_run FROM anti_share_config LIMIT 1;" | head -1 || echo 1)
        tg=$(db_query "SELECT telegram_enabled FROM anti_share_config LIMIT 1;" | head -1 || echo 0)
        [[ "$nft_en" == "1" && "$dry" == "0" ]] && \
            echo "    ⚠ ENFORCEMENT ACTIVE: nft_enabled=1, nft_dry_run=0 — live bans running"
        [[ "$tg" == "1" ]] && \
            echo "    ⚠ TELEGRAM ACTIVE: telegram_enabled=1 — notifications running"
    fi

    # anti_share_state
    if table_exists "anti_share_state"; then
        total_states=$(db_count "anti_share_state")
        non_learning=$(db_query "SELECT COUNT(*) FROM anti_share_state WHERE state != 'learning';" | head -1 || echo "?")
        echo "    anti_share_state: total=$total_states  non_learning=$non_learning"
    fi
fi

# ─── Section 5: Paths & Domains ──────────────────────────────────────────────
echo
echo "── PATHS AND DOMAINS ───────────────────────────────────────"

if mysql "$DB_NAME" -e "SELECT 1;" >/dev/null 2>&1; then
    DOMAIN_KEYS="proxy_path proxy_path_admin proxy_path_client"
    for key in $DOMAIN_KEYS; do
        val=$(db_query "SELECT value FROM str_config WHERE \`key\`='$key' LIMIT 1;" | head -1 || echo "?")
        echo "  $key = $val"
    done
    domain_cnt=$(db_count "domain" 2>/dev/null || echo "?")
    echo "  domain table rows: $domain_cnt"
fi

# ─── Section 6: Runtime Files ────────────────────────────────────────────────
echo
echo "── RUNTIME FILES ───────────────────────────────────────────"

[[ -n "$runtime_path" ]] && rtp="$runtime_path" || rtp=""
if [[ -n "$rtp" ]]; then
    echo "  Routing files:"
    for f in hutils/commercial_routing.py \
              hutils/commercial_routing_source_parser.py \
              hutils/proxy/router_core.py \
              models/commercial_routing_custom_rule.py \
              models/commercial_routing_upstream.py \
              models/commercial_routing_rule_source.py \
              panel/admin/RoutingUpstreamAdmin.py \
              panel/admin/RoutingRuleSourceAdmin.py; do
        if [[ -f "$rtp/$f" ]]; then
            md5=$(md5sum "$rtp/$f" 2>/dev/null | cut -d' ' -f1)
            echo "    PRESENT $f  [$md5]"
        else
            echo "    MISSING $f"
        fi
    done

    echo "  Anti-share files:"
    for f in antishare/__init__.py antishare/config.py antishare/models.py \
              antishare/runner.py antishare/scoring.py antishare/nftables.py \
              antishare/traffic.py antishare/telegram.py \
              panel/admin/AntiShareAdmin.py; do
        if [[ -f "$rtp/$f" ]]; then
            md5=$(md5sum "$rtp/$f" 2>/dev/null | cut -d' ' -f1)
            echo "    PRESENT $f  [$md5]"
        else
            echo "    MISSING $f"
        fi
    done
fi

# ─── Section 7: Xray access log ──────────────────────────────────────────────
echo
echo "── XRAY ACCESS LOG ─────────────────────────────────────────"

if [[ -f "$XRAY_LOG_CONFIG" ]]; then
    access_val=$(python3 -c "
import json
with open('$XRAY_LOG_CONFIG') as f:
    d=json.load(f)
print(d.get('log',{}).get('access','none'))
" 2>/dev/null || echo "parse-error")
    echo "  00_log.json access = $access_val"
else
    echo "  00_log.json: NOT FOUND"
fi

if [[ -f "$XRAY_ACCESS_LOG" ]]; then
    perms=$(stat -c "%a %U:%G %s" "$XRAY_ACCESS_LOG" 2>/dev/null || echo "?")
    echo "  xray.access.log: EXISTS  perms=$perms"
    sudo -u "$PANEL_USER" test -r "$XRAY_ACCESS_LOG" 2>/dev/null \
        && echo "  hiddify-panel can read log: YES" \
        || echo "  hiddify-panel can read log: NO"
else
    echo "  xray.access.log: NOT FOUND"
fi

echo "  Xray override dir: $XRAY_OVERRIDE_DIR"
if [[ -d "$XRAY_OVERRIDE_DIR" ]]; then
    ls -la "$XRAY_OVERRIDE_DIR/" 2>/dev/null | grep -v '^total' | sed 's/^/  /'
else
    echo "  (no override dir)"
fi

# ─── Section 8: Base stability ───────────────────────────────────────────────
echo
echo "── BASE STABILITY ──────────────────────────────────────────"

# net.py
net_py=$(find "$INSTALL_ROOT" -path '*/site-packages/hiddifypanel/hutils/network/net.py' 2>/dev/null | head -1 || true)
if [[ -n "$net_py" ]]; then
    if grep -q 'IDENT_ME_TIMEOUT' "$net_py" 2>/dev/null; then
        timeout_val=$(grep 'IDENT_ME_TIMEOUT\s*=' "$net_py" 2>/dev/null | head -1)
        echo "  net.py: timeout via IDENT_ME_TIMEOUT constant: $timeout_val"
        echo "  net.py: status = COMPATIBLE (no patch needed)"
    elif grep -q 'timeout=5.*ident\|ident.*timeout=5' "$net_py" 2>/dev/null; then
        echo "  net.py: timeout=5 patch APPLIED"
        echo "  net.py: status = PATCHED"
    else
        echo "  net.py: NO timeout for ident.me  ← apply-base-stability.sh needed"
    fi
else
    echo "  net.py: NOT FOUND"
fi

# celerybeat
sched="$INSTALL_ROOT/hiddify-panel/celerybeat-schedule"
if [[ -f "$sched" ]]; then
    age_s=$(( $(date +%s) - $(stat -c %Y "$sched" 2>/dev/null || echo 0) ))
    age_min=$(( age_s / 60 ))
    echo "  celerybeat-schedule: age=${age_min}min"
    [[ $age_s -gt 600 ]] && echo "  celerybeat: STALE (>10min) — stabilize-celery-beat.sh may be needed"
else
    echo "  celerybeat-schedule: NOT FOUND"
fi

# nft antishare rules
if nft list ruleset 2>/dev/null | grep -q 'hiddify_antishare'; then
    banned=$(nft list set inet hiddify_antishare blocked_ipv4 2>/dev/null | grep -c 'elements' || echo "?")
    echo "  nft hiddify_antishare: ACTIVE  (table present in ruleset)"
else
    echo "  nft hiddify_antishare: not present"
fi

# ─── Summary ─────────────────────────────────────────────────────────────────
echo
echo "============================================================"
echo " PREFLIGHT SUMMARY"
echo "============================================================"

issues=0
db_ver=$(db_query "SELECT value FROM str_config WHERE \`key\`='db_version' LIMIT 1;" 2>/dev/null | head -1 || echo "?")
[[ "$db_ver" != "136" ]] && { echo "  ✗ db_version=$db_ver (needs 136 — routing installer will fix)"; ((issues++)); } \
                          || echo "  ✓ db_version=136"

if table_exists "commercial_routing_upstream"; then
    echo "  ✓ commercial_routing_upstream exists"
else
    echo "  ✗ commercial_routing_upstream MISSING (routing upgrade needed)"
    ((issues++))
fi

if table_exists "commercial_routing_rule_source"; then
    echo "  ✓ commercial_routing_rule_source exists"
else
    echo "  ✗ commercial_routing_rule_source MISSING (routing upgrade needed)"
    ((issues++))
fi

if [[ -f "$ROUTING_MANIFEST" ]]; then
    echo "  ✓ routing manifest present"
else
    echo "  ✗ routing manifest MISSING (routing installer will create)"
    ((issues++))
fi

if [[ -f "$ANTISHARE_MANIFEST" ]]; then
    echo "  ✓ antishare manifest present"
else
    echo "  ✗ antishare manifest MISSING"
    ((issues++))
fi

if [[ -f "$BUSINESS_MANIFEST" ]]; then
    echo "  ✓ business manifest present"
else
    echo "  ✗ business manifest MISSING"
    ((issues++))
fi

nft_en=$(db_query "SELECT nft_enabled FROM anti_share_config LIMIT 1;" 2>/dev/null | head -1 || echo 0)
dry=$(db_query "SELECT nft_dry_run FROM anti_share_config LIMIT 1;" 2>/dev/null | head -1 || echo 1)
if [[ "$nft_en" == "1" && "$dry" == "0" ]]; then
    echo "  ⚠ nft enforcement ACTIVE (nft_enabled=1, nft_dry_run=0) — preserved during upgrade"
fi

echo
if [[ $issues -eq 0 ]]; then
    echo "Preflight: $issues blocking issues — ready for upgrade"
else
    echo "Preflight: $issues issue(s) found — review before upgrade"
fi
echo "Timestamp: $(date '+%Y-%m-%d %H:%M:%S')"
