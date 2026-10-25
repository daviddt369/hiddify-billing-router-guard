#!/usr/bin/env bash
# disaster-recovery/bootstrap.sh
#
# Run on a FRESH Ubuntu 22.04/24.04 VPS, as root. Installs the base platform
# only: pinned Hiddify Manager 12.0.0, this repo's business/routing/antishare
# addons, the xray.py headers-serialization fix, and the Selectel custom CDN
# templates (parameterized — the real secret path is never stored in git).
#
# Does NOT restore any data (DB/secrets/certs) — that is restore.sh's job,
# and it must run AFTER this script, because the base Hiddify installer seeds
# its own default DB/admin on first boot. Do not point real DNS/CDN traffic
# at this host until restore.sh has completed.
#
# Usage: sudo bash bootstrap.sh
#   Optional env vars:
#     SELECTEL_XHTTP_PATH   If set, installs the Selectel CDN templates now.
#                           If unset, this step is skipped (restore.sh can
#                           also do it once secrets are restored).
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/common-dr.sh"

DR_BLOCK="bootstrap"
trap dr_error_trap ERR
readonly HIDDIFY_TAG="v12.0.0"
LOG_DIR="/opt/hiddify-manager/dr-bootstrap-logs/$(date +%Y%m%d-%H%M%S)"

preflight() {
    dr_step "Preflight"
    dr_require_root
    dr_need_cmd curl
    dr_need_cmd bash

    dr_log "OS: $(grep -E '^(NAME|VERSION)=' /etc/os-release 2>/dev/null | tr '\n' ' ')"

    if [[ -d "$DR_INSTALL_ROOT/hiddify-panel" ]]; then
        dr_die "$DR_INSTALL_ROOT/hiddify-panel already exists — this does not look like a fresh VPS. Refusing to run (use a real throwaway/clean box)."
    fi

    [[ -d "$REPO_ROOT/release/business-installer" ]] || dr_die "Cannot find release/business-installer next to this script — run from a checkout of hiddify-billing-router-guard"
    for sh in "$REPO_ROOT/release/business-installer/common.sh" \
              "$REPO_ROOT/release/business-installer/install-business.sh" \
              "$REPO_ROOT/release/clean-install-full-stack.sh"; do
        bash -n "$sh" || dr_die "Shell syntax error in: $sh"
    done
}

# Hiddify's own installer runs the database upgrade (all-configs) while the panel and its background worker are already up on the same fresh
# database. Their idle connections can keep a read lock on `str_config` for good, and the upgrade (ALTER TABLE) then waits for it forever (seen on
# clean boxes: waits of 18 hours, the install never finished; the same install passes on other runs, it depends on timing). Closing the idle
# connections frees the upgrade at once (the panel reconnects by itself). So: when a session has waited for a table metadata lock longer than
# MDL_WAIT_LIMIT seconds, close every idle panel connection; if that does not help within a minute, stop the panel and its worker, the
# installer's own command then finishes the upgrade alone and the installer starts the services again afterwards.
mdl_waiting() {
    mysql -N -B -e "select count(*) from information_schema.processlist where state like 'Waiting for table metadata lock' and time > ${MDL_WAIT_LIMIT:-90}" 2>/dev/null || echo 0
}
mdl_watchdog() {
    local n id
    while sleep 15; do
        n="$(mdl_waiting)"
        [[ "${n:-0}" =~ ^[0-9]+$ && "${n:-0}" -gt 0 ]] || continue
        dr_warn "database upgrade is blocked (${n} session(s) wait for a metadata lock for more than ${MDL_WAIT_LIMIT:-90}s): closing the idle panel connections"
        for id in $(mysql -N -B -e "select id from information_schema.processlist where user='hiddifypanel' and command='Sleep'" 2>/dev/null); do
            mysql -e "kill $id" 2>/dev/null || true
        done
        sleep 45
        n="$(mdl_waiting)"
        if [[ "${n:-0}" =~ ^[0-9]+$ && "${n:-0}" -gt 0 ]]; then
            dr_warn "still blocked: stopping the panel and its worker so the installer can finish the upgrade alone"
            systemctl stop "$DR_SERVICE_PANEL" hiddify-panel-background-tasks 2>/dev/null || true
            sleep 60
        fi
    done
}

install_base_hiddify() {
    dr_step "Installing base Hiddify Manager $HIDDIFY_TAG (pinned upstream installer, non-interactive)"
    mdl_watchdog & MDL_WD=$!
    trap '[[ -n "${MDL_WD:-}" ]] && kill "$MDL_WD" 2>/dev/null' EXIT
    # --no-gui / NO_UI=true are required here: without them, upstream's
    # installer launches a cli-progress/urwid TUI that calls
    # asyncio add_reader() on stdin, which raises
    # "PermissionError: [Errno 1] Operation not permitted" the moment there is
    # no real controlling terminal (any detached/scripted/automated run —
    # exactly how this DR tool needs to work). Confirmed by an actual failed
    # run on a throwaway VPS before this flag was added; see
    # AGENT_COORDINATION_VPN.md for the reproduction.
    NO_UI=true bash <(curl -fsSL "https://raw.githubusercontent.com/hiddify/Hiddify-Manager/refs/tags/${HIDDIFY_TAG}/common/download.sh") "$HIDDIFY_TAG" --no-gui
    kill "$MDL_WD" 2>/dev/null || true; MDL_WD=""

    dr_log "Waiting for base panel services to come up..."
    local waited=0 interval=5 max=180 reset_done=0
    while [[ "$(systemctl is-active "$DR_SERVICE_PANEL" 2>/dev/null)" != "active" ]]; do
        # The base installer itself restarts hiddify-panel repeatedly while
        # applying per-stage config (base cert, then business/routing/
        # antishare). On a fresh box this can trip systemd's StartLimitBurst
        # ("Start request repeated too quickly"), leaving the unit in
        # `failed` even though the app itself is fine — confirmed live: a
        # plain `systemctl reset-failed && systemctl start` immediately
        # brought it up healthy. Try that once before falling back to
        # waiting/dying, so this doesn't need manual intervention.
        if [[ "$reset_done" -eq 0 && ( "$(systemctl is-failed "$DR_SERVICE_PANEL" 2>/dev/null)" == "failed" || "$(systemctl is-active "$DR_SERVICE_PANEL" 2>/dev/null)" == "inactive" ) ]]; then
            dr_warn "$DR_SERVICE_PANEL is in 'failed' state (likely systemd start-rate-limit from the installer's own restarts) — resetting and starting once"
            systemctl reset-failed "$DR_SERVICE_PANEL" || true
            systemctl start "$DR_SERVICE_PANEL" || true
            reset_done=1
            sleep 2
            continue
        fi
        if [[ $waited -ge $max ]]; then
            dr_die "$DR_SERVICE_PANEL did not become active within ${max}s after base install"
        fi
        sleep "$interval"
        waited=$((waited + interval))
    done
    dr_check_port_9000
    dr_log "Base Hiddify $HIDDIFY_TAG installed and running (with its own DEFAULT/seed DB — restore.sh replaces this next)."
}

install_addons() {
    dr_step "Installing business+routing+antishare addons (release/clean-install-full-stack.sh)"
    bash "$REPO_ROOT/release/clean-install-full-stack.sh"
}

apply_headers_patch() {
    dr_step "Checking/applying the xray.py headers-serialization patch"
    local runtime_path xray_py checker patch_result
    runtime_path="$(dr_detect_runtime_path)"
    xray_py="$runtime_path/hutils/proxy/xray.py"
    checker="$REPO_ROOT/tools/check-hiddifypanel-xray-headers-json.py"
    [[ -f "$xray_py" ]] || dr_die "xray.py not found at expected path: $xray_py"
    [[ -f "$checker" ]] || dr_die "Checker tool not found: $checker"

    # exit 0 / 1 / 2 are ANSWERS of the checker (fix present / bug present / unknown layout): `|| patch_result=$?` keeps them away from errexit AND from the ERR trap
    # (found by the clean-room rebuild 2026-10-24: with plain `set +e` the trap still fired on the normal answer 1 and stopped the run)
    patch_result=0
    "$(dr_detect_venv_python)" "$checker" "$xray_py" || patch_result=$?

    case "$patch_result" in
        0)
            dr_log "FIX_PRESENT — upstream/base install already has the fix, nothing to do."
            ;;
        1)
            dr_log "BUG_PRESENT — applying the one-line patch."
            cp -a "$xray_py" "${xray_py}.dr-backup-$(date +%Y%m%d-%H%M%S)"
            "$(dr_detect_venv_python)" - "$xray_py" <<'PY'
import sys
path = sys.argv[1]
BUGGY = "                q[k] = v\n"
FIXED = "                q[k] = json.dumps(v) if isinstance(v, (dict, list)) else v\n"
text = open(path, encoding="utf-8").read()
assert text.count(BUGGY) == 1, f"expected exactly 1 occurrence of buggy line, found {text.count(BUGGY)}"
open(path, "w", encoding="utf-8").write(text.replace(BUGGY, FIXED))
PY
            "$(dr_detect_venv_python)" -m py_compile "$xray_py"
            find "$(dirname "$xray_py")/__pycache__" -name 'xray*.pyc' -delete 2>/dev/null || true
            dr_log "Patch applied and compiled OK. Will take effect after the next service restart (restore.sh restarts services)."
            ;;
        2)
            dr_die "UNKNOWN_LAYOUT — $xray_py does not match either the known buggy or fixed shape (different Hiddify version?). Stopping for manual review — do not guess."
            ;;
        *)
            dr_die "Unexpected checker exit code: $patch_result"
            ;;
    esac
}

install_selectel_templates() {
    local secret_path="${SELECTEL_XHTTP_PATH:-}"
    if [[ -z "$secret_path" ]]; then
        dr_warn "SELECTEL_XHTTP_PATH not set — skipping Selectel CDN template install. restore.sh can do this once secrets are restored."
        return 0
    fi
    dr_step "Installing Selectel custom CDN XHTTP templates (path parameterized, not stored in git)"
    local tmpl="$SCRIPT_DIR/selectel-template"

    dr_render_selectel_file "$tmpl/05_inbounds_09_cdn_xhttp.json.j2" \
        "/opt/hiddify-manager/xray/configs/05_inbounds_09_cdn_xhttp.json.j2" 0644 "$secret_path"
    dr_render_selectel_file "$tmpl/v10-cdn.cfg.pj2" \
        "/opt/hiddify-manager/haproxy/backends/v10-cdn.cfg.pj2" 0644 "$secret_path"

    dr_append_snippet_if_missing "/opt/hiddify-manager/haproxy/haproxy.cfg.j2" \
        "$tmpl/snippets/haproxy-include.txt" "v10-cdn.cfg.pj2"

    local rendered_map_snippet
    rendered_map_snippet="$(mktemp)"
    sed "s#__SELECTEL_XHTTP_PATH__#${secret_path#/}#g" "$tmpl/snippets/path-map-entry.txt" > "$rendered_map_snippet"
    dr_append_snippet_if_missing "/opt/hiddify-manager/haproxy/maps/path_v10.j2" "$rendered_map_snippet" "v10-vless-xhttp-cdn-http"
    dr_append_snippet_if_missing "/opt/hiddify-manager/haproxy/maps/path_h2.j2" "$rendered_map_snippet" "v10-vless-xhttp-cdn-http"
    rm -f "$rendered_map_snippet"

    dr_log "Selectel CDN templates installed. Run apply_configs.sh (same as any routing change) to push into live Xray/HAProxy."
}

main() {
    mkdir -p "$LOG_DIR"
    exec > >(tee -a "$LOG_DIR/bootstrap.log") 2>&1

    # DR_STAGES lets a failed run continue where it stopped, e.g. DR_STAGES="headers selectel" bash bootstrap.sh (default: everything)
    local stages=" ${DR_STAGES:-preflight offsite base addons headers selectel} " st
    for st in preflight offsite base addons headers selectel; do
        [[ "$stages" == *" $st "* ]] || { dr_log "skipping stage: $st"; continue; }
        case "$st" in
            preflight) preflight ;;
            offsite)   dr_step "Installing offsite tooling (rclone, age) so restore.sh --from-offsite works without extra setup"; dr_ensure_offsite_tools ;;
            base)      install_base_hiddify ;;
            addons)    install_addons ;;
            headers)   apply_headers_patch ;;
            selectel)  install_selectel_templates ;;
        esac
    done

    dr_step "BOOTSTRAP COMPLETE — platform only, no real data yet"
    dr_log "Services are currently running against a FRESH/DEFAULT database."
    dr_log "Next step (required before this server is real): sudo bash restore.sh <snapshot-bundle-dir>"
    dr_log "Do not point DNS/CDN at this server before restore.sh has run."
    dr_log "Full log: $LOG_DIR/bootstrap.log"
}

main "$@"
