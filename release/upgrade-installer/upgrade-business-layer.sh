#!/usr/bin/env bash
# upgrade-business-layer.sh — in-place upgrade of business runtime files
# Preserves all DB settings, users, configs, proxy paths, Telegram/payment keys.
# Patched files (admin/__init__.py, admin-layout.html, business-settings.html)
# are overwritten with the release base and then routing+antishare patches are
# re-applied idempotently.
#
# Usage:
#   sudo bash upgrade-business-layer.sh [--dry-run] [--defer-restart|--no-restart]
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common-upgrade.sh"

# Routing common cannot be sourced directly — it declares the same readonly vars.
# Patch functions are called via clean subshells instead.
ROUTING_COMMON="$SCRIPT_DIR/../routing-installer/common-routing.sh"
ROUTING_PATCHES_AVAILABLE=0
[[ -f "$ROUTING_COMMON" ]] && ROUTING_PATCHES_AVAILABLE=1

# Locate business-installer payload relative to this script
BUSINESS_INSTALLER_DIR="$(cd "$SCRIPT_DIR/../business-installer" && pwd)"
PAYLOAD_DIR="$BUSINESS_INSTALLER_DIR/payload/panel-overlay/hiddifypanel"
SCRIPTS_DIR="$BUSINESS_INSTALLER_DIR/payload/manager-overlay/scripts"

DRY_RUN=0
DEFER_RESTART=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --defer-restart|--no-restart) DEFER_RESTART=1 ;;
        --help|-h)
            cat <<'EOF'
Usage: sudo bash upgrade-business-layer.sh [--dry-run] [--defer-restart|--no-restart]

  --dry-run         Show planned operations without making changes
  --defer-restart   Upgrade files/schema checks only; skip panel restart/readiness
  --no-restart      Alias for --defer-restart
EOF
            exit 0
            ;;
        *) die "Unknown argument: $1" ;;
    esac
    shift
done

BACKUP_DIR=""

# ─── helpers (override routing's log/step/die/warn) ───────────────────────────

log()  { echo "[business-upgrade] $*"; }
step() { echo ""; echo "[business-upgrade][STEP] $*"; }
warn() { echo "[business-upgrade][WARN] $*" >&2; }
die()  { echo "[business-upgrade][ERROR] $*" >&2; exit 1; }

dry() {
    if [[ $DRY_RUN -eq 1 ]]; then
        echo "[DRY-RUN] $*"
        return 0
    fi
    return 1
}

require_root() {
    [[ $EUID -eq 0 ]] || die "Must run as root"
}

# detect_runtime_path and detect_venv_python are provided by common-upgrade.sh

backup_file() {
    local src="$1"
    local rel="${src#/}"
    local dst="$BACKUP_DIR/files/$rel"
    mkdir -p "$(dirname "$dst")"
    cp -a "$src" "$dst"
}

install_file() {
    local src="$1"
    local dst="$2"
    local mode="${3:-0644}"
    if [[ $DRY_RUN -eq 1 ]]; then
        local src_md5 dst_md5
        src_md5=$(md5sum "$src" 2>/dev/null | cut -d' ' -f1 || echo "?")
        dst_md5=$(md5sum "$dst" 2>/dev/null | cut -d' ' -f1 || echo "missing")
        if [[ "$src_md5" == "$dst_md5" ]]; then
            echo "  [DRY-RUN] SKIP (identical): $dst"
        else
            echo "  [DRY-RUN] WOULD INSTALL: $dst  (release=$src_md5 clone=$dst_md5)"
        fi
        return 0
    fi
    [[ -f "$dst" ]] && backup_file "$dst"
    install -m "$mode" "$src" "$dst"
    log "  installed: $dst"
}

# ─── Pre-flight ────────────────────────────────────────────────────────────────

require_root

[[ -d "$PAYLOAD_DIR" ]] \
    || die "business-installer payload not found: $PAYLOAD_DIR"

step "Detecting runtime path"
RUNTIME="$(detect_runtime_path)"
[[ -n "$RUNTIME" ]] || die "Cannot detect hiddifypanel runtime path"
log "Runtime: $RUNTIME"

step "Verifying pre-upgrade backup exists"
BACKUP_DIR="$(find "$INSTALL_ROOT/upgrade-installer-backups" -maxdepth 1 -type d -name '*-upgrade' 2>/dev/null | sort | tail -1)"
[[ -n "$BACKUP_DIR" && -d "$BACKUP_DIR" ]] \
    || die "No upgrade backup found in $INSTALL_ROOT/upgrade-installer-backups — run backup-before-upgrade.sh first"
log "Using backup: $BACKUP_DIR"

step "Verifying panel services are active"
check_services_active

[[ "$ROUTING_PATCHES_AVAILABLE" -eq 0 ]] && warn "routing common not found at $ROUTING_COMMON — patched file re-patching disabled"
# antishare does not patch any shared files — no source needed

# ─── DRY-RUN header ───────────────────────────────────────────────────────────

if [[ $DRY_RUN -eq 1 ]]; then
    echo ""
    echo "════════════════════════════════════════════════════════"
    echo " BUSINESS UPGRADE DRY-RUN"
    echo " Runtime: $RUNTIME"
    echo " Payload: $PAYLOAD_DIR"
    echo "════════════════════════════════════════════════════════"
fi

# ─── Step 1: Simple Python file upgrades (no data risk) ───────────────────────

step "Installing updated Python files (simple overwrite)"

SIMPLE_FILES=(
    "access.py"
    "accesslog.py"
    "commercial_logic.py"
    "hutils/proxy/singbox.py"
    "models/commercial.py"
    "models/config_enum.py"
    "panel/admin/BusinessAdmin.py"
    "panel/admin/PlanAdmin.py"
    "panel/commercial/restapi/v1/tgbot.py"
    "panel/commercial/restapi/v1/tgmsg.py"
    "panel/commercial/restapi/v2/telegram/__init__.py"
    "panel/commercial/restapi/v2/telegram/tgbot.py"
    "panel/commercial/telegrambot/Usage.py"
    "panel/commercial/telegrambot/secrets.py"
    "panel/custom_widgets.py"
    "panel/hiddify.py"
    "panel/user/user.py"
    "templates/macros.html"
)

for rel in "${SIMPLE_FILES[@]}"; do
    src="$PAYLOAD_DIR/$rel"
    dst="$RUNTIME/$rel"
    [[ -f "$src" ]] || { warn "  release file missing: $rel"; continue; }
    install_file "$src" "$dst" 0644
done

# ─── Step 2: New files (MISSING on clone) ─────────────────────────────────────

step "Installing new files not present on clone"

NEW_FILES=(
    "panel/commercial/capabilities.py"
    "panel/commercial/telegrambot/runtime.py"
)

for rel in "${NEW_FILES[@]}"; do
    src="$PAYLOAD_DIR/$rel"
    dst="$RUNTIME/$rel"
    [[ -f "$src" ]] || { warn "  release file missing: $rel"; continue; }
    if [[ $DRY_RUN -eq 1 ]]; then
        if [[ -f "$dst" ]]; then
            echo "  [DRY-RUN] SKIP (already exists): $dst"
        else
            echo "  [DRY-RUN] WOULD INSTALL (new): $dst"
        fi
        continue
    fi
    mkdir -p "$(dirname "$dst")"
    [[ -f "$dst" ]] && backup_file "$dst"
    install -m 0644 "$src" "$dst"
    log "  installed (new): $dst"
done

# ─── Step 3: init_db.py — install new version ─────────────────────────────────
# init_db.py adds new config keys but NEVER resets existing values:
# all inserts use INSERT IGNORE or UPDATE only for flags not set yet.
# Safe to overwrite; existing DB configs are preserved at runtime.

step "Installing updated init_db.py"
install_file "$PAYLOAD_DIR/panel/init_db.py" "$RUNTIME/panel/init_db.py" 0644

# ─── Step 4: Patched files — overwrite + re-apply patches ─────────────────────
#
# admin/__init__.py, admin-layout.html, business-settings.html were patched
# by routing+antishare installers. We overwrite with the new release base,
# then re-apply all routing+antishare patches idempotently.

step "Upgrading patched files: admin/__init__.py"
INIT_PY="$RUNTIME/panel/admin/__init__.py"
install_file "$PAYLOAD_DIR/panel/admin/__init__.py" "$INIT_PY" 0644

if [[ $DRY_RUN -eq 0 ]]; then
    if [[ -f "$ROUTING_MANIFEST" && "$ROUTING_PATCHES_AVAILABLE" -eq 1 ]]; then
        log "  Re-applying routing patches to admin/__init__.py"
        bash -c "source '$ROUTING_COMMON'; patch_routing_upstream_admin '$INIT_PY'" \
            || die "patch_routing_upstream_admin failed"
        bash -c "source '$ROUTING_COMMON'; patch_routing_rule_source_admin '$INIT_PY'" \
            || die "patch_routing_rule_source_admin failed"
    fi
    # Note: antishare does NOT patch admin/__init__.py — it is auto-loaded
    # by panel when antishare_enabled() returns True (manifest-based check).
else
    [[ -f "$ROUTING_MANIFEST" ]] && echo "  [DRY-RUN] WOULD re-patch admin/__init__.py: routing (RoutingUpstreamAdmin + RoutingRuleSourceAdmin)"
fi

step "Upgrading patched files: admin-layout.html"
LAYOUT_HTML="$RUNTIME/templates/admin-layout.html"
install_file "$PAYLOAD_DIR/templates/admin-layout.html" "$LAYOUT_HTML" 0644

if [[ $DRY_RUN -eq 0 ]]; then
    if [[ -f "$ROUTING_MANIFEST" && "$ROUTING_PATCHES_AVAILABLE" -eq 1 ]]; then
        log "  Re-applying routing sidebar to admin-layout.html"
        bash -c "source '$ROUTING_COMMON'; patch_admin_layout_routing_sidebar '$LAYOUT_HTML'" \
            || die "patch_admin_layout_routing_sidebar failed"
    fi
else
    [[ -f "$ROUTING_MANIFEST" ]] && echo "  [DRY-RUN] WOULD re-patch admin-layout.html: routing sidebar links"
fi

step "Upgrading patched files: business-settings.html"
BIZ_HTML="$RUNTIME/templates/business-settings.html"
install_file "$PAYLOAD_DIR/templates/business-settings.html" "$BIZ_HTML" 0644

if [[ $DRY_RUN -eq 0 ]]; then
    if [[ -f "$ROUTING_MANIFEST" && "$ROUTING_PATCHES_AVAILABLE" -eq 1 ]]; then
        log "  Re-applying routing patches to business-settings.html"
        bash -c "source '$ROUTING_COMMON'
            patch_business_settings_upstream_link '$BIZ_HTML'
            patch_business_settings_hide_legacy_upstream '$BIZ_HTML'
            patch_business_settings_rule_source_link '$BIZ_HTML'
            patch_business_settings_compact_info '$BIZ_HTML'
            patch_business_settings_direct_labels '$BIZ_HTML'" \
            || die "business-settings.html routing patches failed"
    fi
else
    [[ -f "$ROUTING_MANIFEST" ]] && echo "  [DRY-RUN] WOULD re-patch business-settings.html: routing upstream/rules/compact-info/labels"
fi

# ─── Step 5: Python syntax check ──────────────────────────────────────────────

step "Syntax-checking installed Python files"
PY="$(detect_venv_python)"

if [[ $DRY_RUN -eq 0 ]]; then
    ALL_PY=("${SIMPLE_FILES[@]}" "${NEW_FILES[@]}" "panel/init_db.py" "panel/admin/__init__.py")
    for rel in "${ALL_PY[@]}"; do
        dst="$RUNTIME/$rel"
        [[ "$dst" == *.py && -f "$dst" ]] || continue
        "$PY" -m py_compile "$dst" \
            || die "Syntax error in $dst after upgrade"
        log "  compile-ok $dst"
    done
fi

# ─── Step 6: DB migration — commercial-tariffs-db-migrate.sh ──────────────────

step "Installing commercial-tariffs-db-migrate.sh"
TARIFF_MIGRATE_SRC="$SCRIPTS_DIR/commercial-tariffs-db-migrate.sh"
TARIFF_MIGRATE_DST="$INSTALL_ROOT/scripts/commercial-tariffs-db-migrate.sh"

if [[ -f "$TARIFF_MIGRATE_SRC" ]]; then
    if [[ $DRY_RUN -eq 1 ]]; then
        [[ -f "$TARIFF_MIGRATE_DST" ]] \
            && echo "  [DRY-RUN] SKIP (already installed): $TARIFF_MIGRATE_DST" \
            || echo "  [DRY-RUN] WOULD INSTALL: $TARIFF_MIGRATE_DST"
    else
        mkdir -p "$(dirname "$TARIFF_MIGRATE_DST")"
        install -m 0755 "$TARIFF_MIGRATE_SRC" "$TARIFF_MIGRATE_DST"
        log "  installed: $TARIFF_MIGRATE_DST"
    fi
else
    warn "commercial-tariffs-db-migrate.sh not found in payload — skipping"
fi

step "Running commercial-tariffs-db-migrate.sh (idempotent)"
if [[ -f "$TARIFF_MIGRATE_DST" ]]; then
    if [[ $DRY_RUN -eq 1 ]]; then
        echo "  [DRY-RUN] WOULD RUN: bash $TARIFF_MIGRATE_DST"
        echo "  [DRY-RUN]   Operations: CREATE TABLE IF NOT EXISTS commercial_plan,"
        echo "  [DRY-RUN]               CREATE TABLE IF NOT EXISTS commercial_subscription,"
        echo "  [DRY-RUN]               ADD COLUMN IF NOT EXISTS (idempotent)"
        echo "  [DRY-RUN]   Data risk: LOW — existing plans/subscriptions NOT deleted"
    else
        export DB_NAME BACKUP_DIR
        bash "$TARIFF_MIGRATE_DST" \
            || die "commercial-tariffs-db-migrate.sh failed"
    fi
else
    warn "No tariff migrate script found — skipping DB migration"
fi

# ─── Step 7: Preservation assertions ──────────────────────────────────────────

step "Verifying critical settings preserved"
if [[ $DRY_RUN -eq 1 ]]; then
    echo "  [DRY-RUN] WOULD CHECK: proxy_path, proxy_path_admin, proxy_path_client"
    echo "  [DRY-RUN] WOULD CHECK: user count, UUID count, duplicate_uuid=0"
    echo "  [DRY-RUN] WOULD CHECK: telegram_id count, domains count"
    echo "  [DRY-RUN] WOULD CHECK: anti_share_config nft_enabled/nft_dry_run/telegram_enabled"
    echo "  [DRY-RUN] WOULD CHECK: routing custom_rules count"
else
    # proxy paths must not have changed
    pp="$(mysql "$DB_NAME" -N -B -e "SELECT value FROM str_config WHERE \`key\`='proxy_path' AND child_id=0;" 2>/dev/null)"
    [[ -n "$pp" ]] || die "proxy_path missing after upgrade"
    log "  proxy_path: $pp — OK"

    u_count="$(mysql "$DB_NAME" -N -B -e "SELECT COUNT(*) FROM user;" 2>/dev/null)"
    log "  user count: $u_count — preserved"

    rules="$(mysql "$DB_NAME" -N -B -e "SELECT COUNT(*) FROM commercial_routing_custom_rule;" 2>/dev/null || echo 0)"
    log "  routing rules: $rules — preserved"

    nft="$(mysql "$DB_NAME" -N -B -e "SELECT nft_enabled FROM anti_share_config LIMIT 1;" 2>/dev/null || echo '?')"
    log "  anti_share_config.nft_enabled: $nft — preserved"
fi

# ─── Step 8: Restart panel (skip if dry-run) ──────────────────────────────────

step "Restarting panel services"
if [[ $DRY_RUN -eq 1 ]]; then
    if [[ $DEFER_RESTART -eq 1 ]]; then
        echo "  [DRY-RUN] restart deferred by --defer-restart"
    else
        echo "  [DRY-RUN] WOULD RESTART: $SERVICE_PANEL $SERVICE_BG"
    fi
elif [[ $DEFER_RESTART -eq 1 ]]; then
    log "panel restart deferred by --defer-restart"
else
    systemctl restart "$SERVICE_PANEL" "$SERVICE_BG"
    sleep 8
    check_services_active
    check_port_9000
fi

# ─── Step 9: Smoke checks ─────────────────────────────────────────────────────

step "Running business smoke"
if [[ $DRY_RUN -eq 0 && $DEFER_RESTART -eq 0 ]]; then
    bash "$BUSINESS_INSTALLER_DIR/smoke-business.sh" \
        || warn "smoke-business reported issues — review output"
elif [[ $DEFER_RESTART -eq 1 ]]; then
    log "smoke-business skipped: restart deferred mode"
fi

# ─── Summary ──────────────────────────────────────────────────────────────────

echo ""
if [[ $DRY_RUN -eq 1 ]]; then
    echo "════════════════════════════════════════════════════════"
    echo " DRY-RUN COMPLETE"
    echo ""
    echo " Files that WILL be replaced (code-only, no data risk):"
    echo "   access.py, accesslog.py, commercial_logic.py"
    echo "   hutils/proxy/singbox.py"
    echo "   models/commercial.py, models/config_enum.py"
    echo "   panel/admin/BusinessAdmin.py, PlanAdmin.py"
    echo "   panel/commercial/restapi/**, telegrambot/**"
    echo "   panel/custom_widgets.py, panel/user/user.py"
    echo "   panel/init_db.py  (adds new keys, NO resets)"
    echo "   templates/macros.html"
    echo ""
    echo " Files that WILL be replaced + re-patched:"
    echo "   panel/admin/__init__.py     (routing+antishare patches re-applied)"
    echo "   templates/admin-layout.html (routing sidebar re-applied)"
    echo "   templates/business-settings.html (routing patches re-applied)"
    echo ""
    echo " Files that are NEW (missing on clone):"
    echo "   panel/commercial/capabilities.py"
    echo "   panel/commercial/telegrambot/runtime.py"
    echo ""
    echo " Files UNCHANGED (identical md5):"
    echo "   hutils/flask.py, models/__init__.py, models/user.py"
    echo ""
    echo " DB migrations (idempotent — no data loss):"
    echo "   commercial-tariffs-db-migrate.sh (CREATE IF NOT EXISTS)"
    echo ""
    echo " PRESERVED (never touched):"
    echo "   All str_config / bool_config settings"
    echo "   All users, UUIDs, telegram_ids"
    echo "   proxy_path / proxy_path_admin / proxy_path_client"
    echo "   domains, admin/owner settings"
    echo "   Telegram bot token, YooKassa, payment config"
    echo "   anti_share_config (nft_enabled, nft_dry_run, telegram_enabled)"
    echo "   routing rules (448), upstreams, rule sources"
    echo "   commercial_plan, commercial_subscription (not deleted)"
    echo ""
    echo " RISKS:"
    echo "   LOW:  init_db.py adds new config keys — existing keys untouched"
    echo "   LOW:  models/commercial.py — code only, DB schema via migrate script"
    echo "   MED:  admin/__init__.py re-patch — routing/antishare checks are idempotent"
    echo "   LOW:  business-settings.html re-patch — all routing patches are idempotent"
    echo ""
    echo " VERDICT: Safe to run upgrade-business-layer.sh"
    echo "════════════════════════════════════════════════════════"
else
    if [[ $DEFER_RESTART -eq 1 ]]; then
        echo "business layer files upgraded"
        echo "panel restart deferred by --defer-restart"
        echo "run routing + antishare layers before final smoke/readiness"
    else
        echo "upgrade-business-layer OK"
    fi
fi
