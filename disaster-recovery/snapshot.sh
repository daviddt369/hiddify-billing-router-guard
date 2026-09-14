#!/usr/bin/env bash
# disaster-recovery/snapshot.sh
#
# Run on the LIVE production server. Captures everything that exists ONLY on
# this box and nowhere else (DB, Selectel custom CDN templates, secrets, TLS
# certs) into one timestamped bundle, so a hosting-provider disaster doesn't
# mean losing it. This automates what was previously done by hand — see
# AGENT_COORDINATION_VPN.md, "РУЧНОЙ АВАРИЙНЫЙ БЭКАП" entry.
#
# Does NOT touch running services, does NOT modify production. Pure capture.
#
# Usage: sudo bash snapshot.sh [output-dir]
#   output-dir defaults to /root/dr-snapshots/<timestamp>/
#
# IMPORTANT: copy the resulting directory OFF this server immediately.
# A snapshot that only exists on the server it protects against is useless.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common-dr.sh"

DR_BLOCK="snapshot"

STAMP="$(date +%Y%m%d-%H%M%S)"
OUT_DIR="${1:-/root/dr-snapshots/$STAMP}"

SELECTEL_FILES=(
    "/opt/hiddify-manager/xray/configs/05_inbounds_09_cdn_xhttp.json.j2"
    "/opt/hiddify-manager/haproxy/backends/v10-cdn.cfg.pj2"
)
# haproxy.cfg.j2 and the two maps/path_*.j2 files are stock Hiddify files with
# one appended line each — captured whole here too, so restore.sh has the
# option to diff/verify rather than only ever append blindly.
SELECTEL_CONTEXT_FILES=(
    "/opt/hiddify-manager/haproxy/haproxy.cfg.j2"
    "/opt/hiddify-manager/haproxy/maps/path_v10.j2"
    "/opt/hiddify-manager/haproxy/maps/path_h2.j2"
)

main() {
    dr_require_root
    dr_need_cmd mariadb-dump
    dr_need_cmd tar
    dr_need_cmd find

    mkdir -p "$OUT_DIR/secrets" "$OUT_DIR/selectel-templates"
    dr_step "Snapshot output: $OUT_DIR"

    dr_step "Dumping full database (mariadb-dump --single-transaction)"
    dr_parse_db_uri
    trap dr_shred_db_creds EXIT
    MYSQL_PWD="$(dr_db_pass)" mariadb-dump \
        --single-transaction --routines --triggers --events \
        -h "$(dr_db_host)" -P "$(dr_db_port)" -u "$(dr_db_user)" "$(dr_db_name)" \
        > "$OUT_DIR/mariadb-full-dump.sql"
    dr_shred_db_creds
    trap - EXIT
    dr_log "DB dump: $(wc -l < "$OUT_DIR/mariadb-full-dump.sql") lines"

    dr_step "Archiving Selectel custom CDN template files"
    local existing=()
    for f in "${SELECTEL_FILES[@]}" "${SELECTEL_CONTEXT_FILES[@]}"; do
        [[ -f "$f" ]] && existing+=("$f") || dr_warn "Selectel file not found, skipping: $f (Selectel CDN may not be configured on this server)"
    done
    if [[ "${#existing[@]}" -gt 0 ]]; then
        tar -C / -czf "$OUT_DIR/selectel-templates/selectel-custom-xhttp.tar.gz" \
            "${existing[@]#/}"
    else
        dr_warn "No Selectel files found at all — bundle will not include Selectel CDN support"
    fi

    dr_step "Copying panel secrets"
    if [[ -f "$DR_PANEL_SECRETS" ]]; then
        cp -a "$DR_PANEL_SECRETS" "$OUT_DIR/secrets/panel-secrets.env"
    else
        dr_warn "panel-secrets.env not found at $DR_PANEL_SECRETS"
    fi

    dr_step "Finding and archiving WireGuard private keys referenced in the DB"
    mapfile -t wg_keys < <(grep -oE "file:[^\"'\\,}]+" "$OUT_DIR/mariadb-full-dump.sql" | sed 's/^file://' | sort -u)
    if [[ "${#wg_keys[@]}" -gt 0 ]]; then
        local existing_keys=()
        for k in "${wg_keys[@]}"; do
            [[ -f "$k" ]] && existing_keys+=("$k") || dr_warn "file: reference not found on disk, skipping: $k"
        done
        if [[ "${#existing_keys[@]}" -gt 0 ]]; then
            tar -C / -czf "$OUT_DIR/secrets/file-referenced-keys.tar.gz" "${existing_keys[@]#/}"
            dr_log "archived ${#existing_keys[@]} file:-referenced key(s): ${wg_keys[*]}"
        fi
    else
        dr_warn "No file: references found in DB dump (no WireGuard upstream configured?)"
    fi

    dr_step "Archiving TLS/ACME certificates for all domains"
    # Two locations matter here, not just one: acme.sh/lib is its own
    # internal cert store, but Hiddify's actual web-facing proxy reads certs
    # from $DR_INSTALL_ROOT/ssl/<domain>.crt(.key) — acme.sh copies them
    # there on issue/renew via --install-cert. Missing this second directory
    # means a restore would have valid certs sitting unused in acme.sh's
    # store while the live proxy keeps serving the fresh bootstrap cert for
    # the NEW server's own IP. Confirmed by inspecting a real install.
    if [[ -d "$DR_INSTALL_ROOT/acme.sh/lib" || -d "$DR_INSTALL_ROOT/ssl" ]]; then
        local cert_paths=()
        [[ -d "$DR_INSTALL_ROOT/acme.sh/lib" ]] && cert_paths+=("${DR_INSTALL_ROOT#/}/acme.sh/lib")
        [[ -d "$DR_INSTALL_ROOT/ssl" ]] && cert_paths+=("${DR_INSTALL_ROOT#/}/ssl")
        [[ -f /root/.acme.sh/account.conf ]] && cert_paths+=("root/.acme.sh/account.conf")
        tar -C / -czf "$OUT_DIR/secrets/acme-certs.tar.gz" "${cert_paths[@]}"
        dr_log "domains found: $(find "$DR_INSTALL_ROOT/acme.sh/lib" -maxdepth 2 -iname '*_ecc' -type d 2>/dev/null | sed 's#.*/##; s/_ecc$//' | sort -u | tr '\n' ' ')"
    else
        dr_warn "Neither $DR_INSTALL_ROOT/acme.sh/lib nor $DR_INSTALL_ROOT/ssl found — no certs archived"
    fi

    dr_step "Writing manifest"
    local hiddify_version="unknown"
    if [[ -x "$DR_INSTALL_ROOT/.venv313/bin/python" ]]; then
        hiddify_version="$("$DR_INSTALL_ROOT/.venv313/bin/python" -c 'import hiddifypanel; print(getattr(hiddifypanel, "__version__", "unknown"))' 2>/dev/null || echo unknown)"
    fi
    cat > "$OUT_DIR/manifest.json" <<EOF
{
  "captured_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "hostname": "$(hostname -f 2>/dev/null || hostname)",
  "hiddifypanel_version": "$hiddify_version",
  "db_dump_bytes": $(stat -c%s "$OUT_DIR/mariadb-full-dump.sql" 2>/dev/null || echo 0),
  "selectel_files_captured": $(( ${#existing[@]} )),
  "wg_keys_captured": $(( ${#wg_keys[@]} ))
}
EOF

    cp "$SCRIPT_DIR/SNAPSHOT-README.txt" "$OUT_DIR/README.txt" 2>/dev/null || true

    dr_step "Done"
    dr_log "Snapshot written to: $OUT_DIR"
    dr_log "COPY THIS DIRECTORY OFF THIS SERVER NOW (scp/rsync to your own machine, or offsite storage)."
    dr_log "It contains live secrets (DB, WireGuard keys, TLS keys) — never commit it to git, never leave the only copy on this VPS."
}

main "$@"
