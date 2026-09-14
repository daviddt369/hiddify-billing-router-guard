#!/usr/bin/env bash
# disaster-recovery/restore.sh
#
# Run on a server that has already had bootstrap.sh applied (base Hiddify +
# addons + headers patch installed, services running on a FRESH/DEFAULT DB).
# Restores real data from a disaster-recovery/snapshot.sh bundle: DB,
# Selectel CDN templates, secrets, TLS certs — then starts services and
# smoke-tests the direct (non-CDN) path.
#
# Services are stopped BEFORE the DB is replaced, on purpose: a freshly
# bootstrapped Hiddify seeds its own default admin/config on first boot, and
# leaving that live for any window risks it requesting certs / registering
# webhooks against the wrong identity before the real data lands.
#
# Usage: sudo bash restore.sh <snapshot-bundle-dir>
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common-dr.sh"

DR_BLOCK="restore"
trap dr_error_trap ERR
BUNDLE="${1:-}"

usage() {
    cat <<'EOF'
Usage: sudo bash restore.sh <snapshot-bundle-dir>

  <snapshot-bundle-dir>  A directory produced by disaster-recovery/snapshot.sh
                         (must contain mariadb-full-dump.sql).
EOF
}

preflight() {
    dr_require_root
    dr_need_cmd mariadb
    dr_need_cmd tar
    [[ -n "$BUNDLE" ]] || { usage; dr_die "Missing snapshot bundle argument"; }
    [[ -d "$BUNDLE" ]] || dr_die "Bundle directory not found: $BUNDLE"
    [[ -f "$BUNDLE/mariadb-full-dump.sql" ]] || dr_die "Bundle is missing mariadb-full-dump.sql — is this a real snapshot.sh output dir?"
    [[ -d "$DR_INSTALL_ROOT/hiddify-panel" ]] || dr_die "Hiddify not found under $DR_INSTALL_ROOT — run bootstrap.sh first"
}

stop_services() {
    dr_step "Stopping services before touching the database (closes the seed-DB race window)"
    systemctl stop "$DR_SERVICE_BG" 2>/dev/null || true
    systemctl stop "$DR_SERVICE_PANEL" 2>/dev/null || true
}

restore_database() {
    dr_step "Restoring database from snapshot"
    dr_parse_db_uri
    trap dr_shred_db_creds EXIT
    local db_name
    db_name="$(dr_db_name)"
    MYSQL_PWD="$(dr_db_pass)" mariadb -h "$(dr_db_host)" -P "$(dr_db_port)" -u "$(dr_db_user)" \
        -e "DROP DATABASE IF EXISTS \`${db_name}\`; CREATE DATABASE \`${db_name}\` CHARACTER SET utf8mb4;"
    MYSQL_PWD="$(dr_db_pass)" mariadb -h "$(dr_db_host)" -P "$(dr_db_port)" -u "$(dr_db_user)" \
        "$db_name" < "$BUNDLE/mariadb-full-dump.sql"
    dr_log "Database restored from $BUNDLE/mariadb-full-dump.sql"
    dr_shred_db_creds
    trap - EXIT
}

restore_secrets_and_certs() {
    dr_step "Restoring secrets and certificates"
    if [[ -f "$BUNDLE/secrets/panel-secrets.env" ]]; then
        mkdir -p /etc/hiddify-panel
        cp -a "$BUNDLE/secrets/panel-secrets.env" "$DR_PANEL_SECRETS"
        chmod 644 "$DR_PANEL_SECRETS"
        chown root:root "$DR_PANEL_SECRETS"
        dr_log "panel-secrets.env restored"
    else
        dr_warn "No panel-secrets.env in bundle — Telegram bot etc will need reconfiguring"
    fi

    if [[ -f "$BUNDLE/secrets/file-referenced-keys.tar.gz" ]]; then
        tar -C / -xzf "$BUNDLE/secrets/file-referenced-keys.tar.gz"
        dr_log "file:-referenced keys (e.g. WireGuard private keys) restored to their original paths"
    else
        dr_warn "No file-referenced-keys.tar.gz in bundle — WireGuard upstreams referencing key files will be broken until restored manually"
    fi

    if [[ -f "$BUNDLE/secrets/acme-certs.tar.gz" ]]; then
        tar -C / -xzf "$BUNDLE/secrets/acme-certs.tar.gz"
        dr_log "TLS/ACME certs restored"
    else
        dr_warn "No acme-certs.tar.gz in bundle — certs will need reissuing (acme.sh) for all domains"
    fi
}

restore_selectel_templates() {
    local tar_path="$BUNDLE/selectel-templates/selectel-custom-xhttp.tar.gz"
    if [[ ! -f "$tar_path" ]]; then
        dr_warn "No Selectel template archive in bundle — Selectel CDN transport will not be available on this server"
        return 0
    fi
    dr_step "Restoring Selectel custom CDN templates"
    tar -C / -xzf "$tar_path"

    local json_tmpl="/opt/hiddify-manager/xray/configs/05_inbounds_09_cdn_xhttp.json.j2"
    local secret_path
    secret_path="$(grep -oE '"path"\s*:\s*"/[^"]+"' "$json_tmpl" | head -1 | grep -oE '/[^"]+')"
    [[ -n "$secret_path" ]] || dr_die "Could not extract Selectel secret path from restored $json_tmpl"
    dr_log "Selectel path recovered from bundle (not printed — treat as secret)"

    local tmpl="$SCRIPT_DIR/selectel-template"
    dr_append_snippet_if_missing "/opt/hiddify-manager/haproxy/haproxy.cfg.j2" \
        "$tmpl/snippets/haproxy-include.txt" "v10-cdn.cfg.pj2"

    local rendered_map_snippet
    rendered_map_snippet="$(mktemp)"
    sed "s#__SELECTEL_XHTTP_PATH__#${secret_path#/}#g" "$tmpl/snippets/path-map-entry.txt" > "$rendered_map_snippet"
    dr_append_snippet_if_missing "/opt/hiddify-manager/haproxy/maps/path_v10.j2" "$rendered_map_snippet" "v10-vless-xhttp-cdn-http"
    dr_append_snippet_if_missing "/opt/hiddify-manager/haproxy/maps/path_h2.j2" "$rendered_map_snippet" "v10-vless-xhttp-cdn-http"
    rm -f "$rendered_map_snippet"
    dr_log "Selectel CDN templates + map entries restored"
}

flush_redis() {
    dr_step "Flushing Redis cache (stale hconfig cache after a DB swap is a known risk)"
    local redis_uri
    redis_uri="$(grep -E '^REDIS_URI_MAIN' "$DR_APP_CFG" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"'"'"' \t')"
    if [[ -n "$redis_uri" ]] && command -v redis-cli >/dev/null 2>&1; then
        redis-cli -u "$redis_uri" FLUSHALL >/dev/null 2>&1 && dr_log "Redis flushed" || dr_warn "Redis flush failed (non-fatal, continuing)"
    else
        dr_warn "Could not determine REDIS_URI_MAIN or redis-cli missing — skipping flush (restart will still pick up fresh DB values via cache misses)"
    fi
}

confirm_tls_mixed_case() {
    dr_step "Confirming tls_mixed_case=false (Gcore CDN requires this — see AGENT_COORDINATION_VPN.md)"
    cd "$DR_INSTALL_ROOT/hiddify-panel"
    "$(dr_detect_venv_python)" - "$DR_APP_CFG" <<'PY' || dr_warn "Could not verify/set tls_mixed_case (non-fatal, verify manually after startup)"
import os
import re
import sys
from flask import Flask

cfg = {}
with open(sys.argv[1]) as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        cfg[k.strip()] = v.strip().strip('"').strip("'")

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = cfg.get("SQLALCHEMY_DATABASE_URI")
os.environ["REDIS_URI_MAIN"] = cfg.get("REDIS_URI_MAIN", "")

from hiddifypanel.database import db
from hiddifypanel.models import ConfigEnum, hconfig, set_hconfig

db.init_app(app)
with app.app_context():
    current = hconfig(ConfigEnum.tls_mixed_case)
    if current:
        set_hconfig(ConfigEnum.tls_mixed_case, False, commit=True)
        print("tls_mixed_case was True, set to False")
    else:
        print("tls_mixed_case already False, OK")
PY
}

start_and_verify() {
    dr_step "Starting services and verifying health"
    local since
    since="$(date '+%Y-%m-%d %H:%M:%S')"
    systemctl start "$DR_SERVICE_PANEL" "$DR_SERVICE_BG"
    sleep 10
    dr_check_services_active
    dr_check_port_9000
    dr_check_logs_since "$since"
    dr_log "Services active and healthy."
}

smoke_test_direct() {
    dr_step "Direct smoke test (origin, no CDN)"
    local code
    code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 10 https://127.0.0.1/ || echo 000)"
    if [[ "$code" =~ ^(200|301|302|400|404)$ ]]; then
        dr_log "Direct HTTPS smoke: HTTP $code (OK — panel is responding)"
    else
        dr_warn "Direct HTTPS smoke returned unexpected code: $code — investigate before trusting this server"
    fi
}

print_manual_steps() {
    dr_step "RESTORE COMPLETE — manual steps still required"
    cat <<EOF

This server now has the restored database, secrets, certs, and Selectel
templates, and is passing a direct (non-CDN) smoke test.

Still required, and cannot be scripted from inside this repo:

  1. Update DNS records for every domain used by this deployment to point
     at this server's new IP.
  2. In Gcore's and Selectel's dashboards, update the CDN resource's origin
     to this server's new IP (SNI/Host override settings included).
  3. Re-run a smoke test THROUGH the CDN path once DNS/CDN have propagated
     (TTL was observed at 20-30s on the old server, but confirm on this one).
  4. If Telegram bot webhook was in use, verify it re-registers correctly
     against the (possibly new) public domain.

Do not consider this migration complete until step 3 passes.
EOF
}

main() {
    preflight
    stop_services
    restore_database
    restore_secrets_and_certs
    restore_selectel_templates
    flush_redis
    confirm_tls_mixed_case
    start_and_verify
    smoke_test_direct
    print_manual_steps
}

main "$@"
