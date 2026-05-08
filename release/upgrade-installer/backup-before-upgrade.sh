#!/usr/bin/env bash
# backup-before-upgrade.sh — create comprehensive pre-upgrade backup.
#
# Usage: sudo bash backup-before-upgrade.sh [--label LABEL]
#
# Creates:
#   $UPGRADE_BACKUP_DIR/
#     db-dump.sql               — full mysqldump
#     db-schema.sql             — schema only (for diff)
#     checksums-before.txt      — md5 of all backed-up files
#     runtime/                  — venv Python files
#     templates/                — HTML templates
#     manager-overlay/          — commander.py, xray/singbox templates
#     systemd/                  — systemd overrides and unit files
#     sudoers/                  — sudoers entries
#     manifests/                — addon manifests
#     preflight-report.txt      — preflight audit snapshot
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common-upgrade.sh"

UPGRADE_BLOCK="backup"
LABEL="upgrade"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --label) shift; LABEL="$1" ;;
        --help|-h) echo "Usage: sudo bash backup-before-upgrade.sh [--label LABEL]"; exit 0 ;;
        *) die "Unknown argument: $1" ;;
    esac
    shift
done

require_root
need_cmd mysqldump
need_cmd md5sum

begin_upgrade_backup "$LABEL"
BD="$UPGRADE_BACKUP_DIR"

# ─── DB dump ─────────────────────────────────────────────────────────────────
step "Backing up database"
mysqldump "$DB_NAME" > "$BD/db-dump.sql"
log "Full dump: $BD/db-dump.sql ($(du -sh "$BD/db-dump.sql" | cut -f1))"

mysqldump --no-data "$DB_NAME" > "$BD/db-schema.sql"
log "Schema-only dump: $BD/db-schema.sql"

# Row counts snapshot
mysql "$DB_NAME" -e "
SELECT table_name, table_rows
FROM information_schema.tables
WHERE table_schema='$DB_NAME'
ORDER BY table_name;" 2>/dev/null > "$BD/table-row-counts.txt" || true
log "Table row counts: $BD/table-row-counts.txt"

# ─── Runtime Python files ─────────────────────────────────────────────────────
step "Backing up runtime Python files"
runtime_path="$(detect_runtime_path)"
mkdir -p "$BD/runtime"

for rel in \
    hutils/commercial_routing.py \
    hutils/commercial_routing_source_parser.py \
    hutils/network/net.py \
    hutils/proxy/router_core.py \
    models/commercial_routing_custom_rule.py \
    models/commercial_routing_upstream.py \
    models/commercial_routing_rule_source.py \
    panel/admin/RoutingUpstreamAdmin.py \
    panel/admin/RoutingRuleSourceAdmin.py \
    panel/admin/AntiShareAdmin.py \
    panel/admin/BusinessAdmin.py \
    panel/admin/__init__.py \
    antishare/__init__.py \
    antishare/config.py \
    antishare/models.py \
    antishare/runner.py \
    antishare/scoring.py \
    antishare/nftables.py \
    antishare/traffic.py \
    antishare/telegram.py \
    panel/commercial/capabilities.py; do
    src="$runtime_path/$rel"
    if [[ -f "$src" ]]; then
        dst_dir="$BD/runtime/$(dirname "$rel")"
        mkdir -p "$dst_dir"
        cp -p "$src" "$BD/runtime/$rel"
        log "  backed up: $rel"
    else
        log "  skip (missing): $rel"
    fi
done

# ─── HTML templates ──────────────────────────────────────────────────────────
step "Backing up HTML templates"
mkdir -p "$BD/templates"
for tmpl in \
    "templates/admin-layout.html" \
    "templates/business-settings.html" \
    "templates/anti-share-settings.html" \
    "panel/admin/templates/routing-upstream.html" \
    "panel/admin/templates/routing-rule-source.html"; do
    src="$runtime_path/$tmpl"
    if [[ -f "$src" ]]; then
        dst_dir="$BD/templates/$(dirname "$tmpl")"
        mkdir -p "$dst_dir"
        cp -p "$src" "$BD/templates/$tmpl"
        log "  backed up: $tmpl"
    fi
done

# ─── Manager overlay files ───────────────────────────────────────────────────
step "Backing up manager overlay files"
mkdir -p "$BD/manager-overlay/xray/configs"
mkdir -p "$BD/manager-overlay/singbox/configs"
mkdir -p "$BD/manager-overlay/common"

# commander.py
[[ -f "$INSTALL_ROOT/common/commander.py" ]] && \
    cp -p "$INSTALL_ROOT/common/commander.py" "$BD/manager-overlay/common/"

# xray/singbox routing templates
for f in \
    "$INSTALL_ROOT/xray/configs/03_routing.json.j2" \
    "$INSTALL_ROOT/xray/configs/06_outbounds.json.j2" \
    "$INSTALL_ROOT/xray/configs/00_log.json"; do
    [[ -f "$f" ]] && cp -p "$f" "$BD/manager-overlay/xray/configs/" || true
done

for f in \
    "$INSTALL_ROOT/singbox/configs/03_routing.json.j2" \
    "$INSTALL_ROOT/singbox/configs/06_outbounds.json.j2"; do
    [[ -f "$f" ]] && cp -p "$f" "$BD/manager-overlay/singbox/configs/" || true
done

# nft helper
[[ -f "$INSTALL_ROOT/common/hiddify-antishare-nft.sh" ]] && \
    cp -p "$INSTALL_ROOT/common/hiddify-antishare-nft.sh" "$BD/manager-overlay/common/"

log "Manager overlay backed up"

# ─── Systemd units ───────────────────────────────────────────────────────────
step "Backing up systemd units and overrides"
mkdir -p "$BD/systemd"

for unit in \
    /etc/systemd/system/xray-router.service \
    /etc/systemd/system/hiddify-anti-share.service \
    /etc/systemd/system/hiddify-anti-share.timer; do
    [[ -f "$unit" ]] && cp -p "$unit" "$BD/systemd/" || true
done

# Override dirs
for override_dir in \
    /etc/systemd/system/hiddify-xray.service.d \
    /etc/systemd/system/xray-router.service.d; do
    if [[ -d "$override_dir" ]]; then
        mkdir -p "$BD/systemd/$(basename "$override_dir").d"
        cp -p "$override_dir/"* "$BD/systemd/$(basename "$override_dir").d/" 2>/dev/null || true
    fi
done

log "Systemd files backed up"

# ─── Sudoers ─────────────────────────────────────────────────────────────────
step "Backing up sudoers entries"
mkdir -p "$BD/sudoers"
for f in /etc/sudoers.d/90-hiddify-panel-routing /etc/sudoers.d/91-hiddify-panel-antishare; do
    [[ -f "$f" ]] && cp -p "$f" "$BD/sudoers/" || true
done

# ─── Manifests ───────────────────────────────────────────────────────────────
step "Backing up addon manifests"
mkdir -p "$BD/manifests"
for mf in "$BUSINESS_MANIFEST" "$ROUTING_MANIFEST" "$ANTISHARE_MANIFEST"; do
    [[ -f "$mf" ]] && cp -p "$mf" "$BD/manifests/" || true
done

# ─── Preflight snapshot ───────────────────────────────────────────────────────
step "Running preflight audit snapshot"
bash "$SCRIPT_DIR/preflight-upgrade-audit.sh" > "$BD/preflight-report.txt" 2>&1 || true
log "Preflight report: $BD/preflight-report.txt"

# ─── Checksums ───────────────────────────────────────────────────────────────
step "Computing checksums"
find "$BD" -type f ! -name 'checksums-before.txt' | sort | \
    xargs md5sum 2>/dev/null > "$BD/checksums-before.txt"
log "Checksums: $BD/checksums-before.txt"

# ─── Record backup metadata ──────────────────────────────────────────────────
cat > "$BD/backup-meta.txt" <<EOF
backup_timestamp=$(date '+%Y-%m-%d %H:%M:%S')
backup_label=$LABEL
hiddify_version=$(get_hiddify_version)
db_name=$DB_NAME
install_root=$INSTALL_ROOT
runtime_path=$runtime_path
EOF

echo
log "Backup completed: $BD"
log "Total backup size: $(du -sh "$BD" | cut -f1)"
echo
echo "backup-before-upgrade OK"
echo "Backup dir: $BD"
echo "To restore DB:  sudo bash rollback-upgrade.sh --restore-db"
