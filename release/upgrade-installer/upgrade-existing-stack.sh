#!/usr/bin/env bash
# upgrade-existing-stack.sh — upgrade an existing production Hiddify stack
# (business + routing + antishare) to the current validated release.
#
# IMPORTANT — READ BEFORE RUNNING:
#   This script is for upgrading an EXISTING production server.
#   It is NOT the same as a clean install.
#   It preserves: users, subscription links, tariffs, domains, routing rules,
#                 upstream config, anti_share_config (nft/telegram settings).
#
# Usage: sudo bash upgrade-existing-stack.sh [--dry-run]
#
# Pre-requisites:
#   1. sudo bash backup-before-upgrade.sh
#   2. sudo bash scripts/check-user-link-preservation.sh --before
#   3. sudo bash preflight-upgrade-audit.sh
#
# The script will ABORT if no backup exists.
#
# What this script does NOT do:
#   - Does NOT run business installer (business addon already installed)
#   - Does NOT run apply_configs.sh
#   - Does NOT reset anti_share_config (nft_enabled/telegram_enabled preserved)
#   - Does NOT touch users, subscriptions, or tariffs
#   - Does NOT change DNS, webhooks, or YooKassa settings
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common-upgrade.sh"

UPGRADE_BLOCK="upgrade"
DRY_RUN=0

usage() {
    cat <<'EOF'
Usage: sudo bash upgrade-existing-stack.sh [--dry-run]

  --dry-run    Show what would be done without making changes

Pre-requisites (must run before this script):
  sudo bash backup-before-upgrade.sh
  sudo bash scripts/check-user-link-preservation.sh --before
  sudo bash preflight-upgrade-audit.sh

This script upgrades an existing production stack:
  routing: adds Stage 2D (upstreams), Stage 2E (rule sources), FK fixes
  antishare: replaces addon files with current release
  base: advances db_version, stabilizes celery beat

Does NOT: run apply_configs.sh, reset anti_share_config, touch users/plans.
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --help|-h) usage ;;
        *) die "Unknown argument: $1" ;;
    esac
    shift
done

require_root

if [[ $DRY_RUN -eq 1 ]]; then
    log "DRY RUN mode — no changes will be made"
fi

# ─── 0. Guard: backup must exist ─────────────────────────────────────────────
step "Verifying pre-upgrade backup exists"
require_backup_exists
log "Backup confirmed: $UPGRADE_BACKUP_DIR"

# ─── 1. Guard: core services must be active ─────────────────────────────────
step "Verifying panel services are active"
check_services_active

# ─── 2. Detect runtime ───────────────────────────────────────────────────────
step "Detecting runtime path"
runtime_path="$(detect_runtime_path)"
log "Runtime: $runtime_path"

# ─── Stage B: Base stability ─────────────────────────────────────────────────
step "Stage B: Base stability tools"
if [[ $DRY_RUN -eq 0 ]]; then
    # apply-base-stability.sh is net.py-aware (handles IDENT_ME_TIMEOUT)
    # See compatibility-fixes.md section A
    if [[ -f "$SCRIPT_DIR/../service-tools/apply-base-stability.sh" ]]; then
        bash "$SCRIPT_DIR/../service-tools/apply-base-stability.sh" \
            || warn "apply-base-stability returned non-zero — continuing"
    else
        warn "service-tools/apply-base-stability.sh not found — skipping"
    fi

    if [[ -f "$SCRIPT_DIR/../service-tools/stabilize-celery-beat.sh" ]]; then
        bash "$SCRIPT_DIR/../service-tools/stabilize-celery-beat.sh" \
            || warn "stabilize-celery-beat returned non-zero — continuing"
    else
        warn "service-tools/stabilize-celery-beat.sh not found — skipping"
    fi
else
    log "[DRY-RUN] Would run: apply-base-stability.sh, stabilize-celery-beat.sh"
fi

# ─── Stage C: Routing upgrade ────────────────────────────────────────────────
step "Stage C: Routing upgrade (idempotent)"
# Pre-conditions checked here; actual upgrade delegated to routing installer.
# The routing installer:
#   - Adds missing files (source_parser, upstream/rule_source models, admin views)
#   - Adds missing tables (commercial_routing_upstream, commercial_routing_rule_source)
#   - Adds source_id column to commercial_routing_custom_rule
#   - Seeds upstream-1 from legacy commercial_de_* if table is empty (idempotent)
#   - Advances db_version 134→136 (Step 15, guarded)
#   - Creates routing manifest if missing
#   - Does NOT reset commercial_de_* or routing custom rules

if [[ -f "$SCRIPT_DIR/../routing-installer/install-routing.sh" ]]; then
    if [[ $DRY_RUN -eq 0 ]]; then
        log "Running routing installer (idempotent)"
        bash "$SCRIPT_DIR/../routing-installer/install-routing.sh" \
            || die "Routing installer failed — check logs and run rollback if needed"
        log "Routing upgrade OK"
    else
        log "[DRY-RUN] Would run: routing-installer/install-routing.sh"
    fi
else
    die "routing-installer/install-routing.sh not found at expected path"
fi

# ─── Stage D: Antishare upgrade ──────────────────────────────────────────────
step "Stage D: Antishare upgrade (existing-config-preserved mode)"
# The antishare installer:
#   - Replaces antishare Python files with current release
#   - DB migration: creates tables if missing (idempotent CREATE TABLE IF NOT EXISTS)
#   - DB migration: does NOT overwrite anti_share_config if row already exists
#   - DB migration: cleans stale str_config entry if present
#   - Restarts panel services
#   - Does NOT reset nft_enabled, nft_dry_run, telegram_enabled
#
# IMPORTANT: smoke-antishare.sh must be run with --upgrade-existing-config
# to skip safe-defaults hard-check (production may have nft_enabled=1).
# See compatibility-fixes.md section B.

if [[ -f "$SCRIPT_DIR/../antishare-installer/install-antishare.sh" ]]; then
    if [[ $DRY_RUN -eq 0 ]]; then
        log "Running antishare installer"
        bash "$SCRIPT_DIR/../antishare-installer/install-antishare.sh" \
            || die "Antishare installer failed — check logs and run rollback if needed"
        log "Antishare upgrade OK"
    else
        log "[DRY-RUN] Would run: antishare-installer/install-antishare.sh"
    fi
else
    die "antishare-installer/install-antishare.sh not found at expected path"
fi

# ─── Stage E: Verify services ────────────────────────────────────────────────
step "Stage E: Verify services after upgrade"
if [[ $DRY_RUN -eq 0 ]]; then
    sleep 15
    check_services_active
    log "Services active after upgrade"
fi

# ─── Stage F: Record upgrade metadata ────────────────────────────────────────
if [[ $DRY_RUN -eq 0 ]]; then
    cat >> "$UPGRADE_BACKUP_DIR/backup-meta.txt" <<EOF
upgrade_timestamp=$(date '+%Y-%m-%d %H:%M:%S')
upgrade_completed=yes
EOF
fi

echo
if [[ $DRY_RUN -eq 1 ]]; then
    echo "upgrade-existing-stack DRY-RUN complete — no changes made"
else
    echo "upgrade-existing-stack OK"
    echo ""
    echo "Next steps:"
    echo "  1. sudo bash smoke-upgrade.sh"
    echo "  2. sudo bash scripts/check-user-link-preservation.sh --after"
    echo "  3. sudo bash scripts/check-user-link-preservation.sh --compare"
    echo "  4. sudo bash scripts/compare-db-preservation.sh"
    echo ""
    echo "IMPORTANT: apply_configs.sh NOT run automatically."
    echo "Run manually when ready: sudo bash /opt/hiddify-manager/apply_configs.sh"
fi
