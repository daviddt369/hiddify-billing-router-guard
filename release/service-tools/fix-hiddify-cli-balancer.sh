#!/usr/bin/env bash
# fix-hiddify-cli-balancer.sh
# Fixes hiddify-core v4.1.0 bug and sets up proxy-stats UI with auth.
#
# Problems fixed:
#   1. execute-config-as-is=false causes hiddify-core to produce a "balance"
#      balancer outbound with no strategy, which sing-box 1.13.1 rejects with
#      "unknown load balance strategy:". Fix: set execute-config-as-is=true +
#      balancer-strategy=round-robin.
#   2. proxy-stats /ui/ returns empty page — yacd-meta assets not present.
#      Fix: download yacd-meta into webui/ directory.
#   3. proxy-stats API has no authentication. Fix: add Bearer token check in
#      HAProxy proxy_stats_api_backend.
#
# Usage: sudo bash fix-hiddify-cli-balancer.sh
set -Eeuo pipefail

CLI_DIR="/opt/hiddify-manager/other/hiddify-cli"
CONFIG="$CLI_DIR/h_client_config.json"
WEBUI_DIR="$CLI_DIR/webui"
HAPROXY_CFG="/opt/hiddify-manager/haproxy/haproxy.cfg"
PROXY_STATS_SECRET="hiddify"

log()  { echo "[fix-hiddify-cli] $*"; }
warn() { echo "[fix-hiddify-cli][WARN] $*" >&2; }
die()  { echo "[fix-hiddify-cli][ERROR] $*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "Run as root."
[[ -f "$CONFIG" ]]      || die "h_client_config.json not found: $CONFIG"

# ─── Step 1: Fix h_client_config.json ────────────────────────────────────────

log "Backing up $CONFIG"
cp -a "$CONFIG" "${CONFIG}.bak.$(date +%F-%H%M%S)"

log "Applying fix: execute-config-as-is=true, balancer-strategy=round-robin"
python3 - "$CONFIG" << 'PY'
import json, sys
f = sys.argv[1]
d = json.load(open(f))
d["execute-config-as-is"] = True
d["balancer-strategy"] = "round-robin"
d.pop("external-ui", None)
open(f, "w").write(json.dumps(d, indent=2))
print(f"  execute-config-as-is: {d['execute-config-as-is']}")
print(f"  balancer-strategy:    {d['balancer-strategy']}")
PY

# ─── Step 2: Download yacd-meta UI ───────────────────────────────────────────

if [[ -f "$WEBUI_DIR/index.html" ]]; then
    log "yacd-meta UI already present in $WEBUI_DIR — skipping download"
else
    log "Downloading yacd-meta UI..."
    TMP_ZIP=$(mktemp /tmp/yacd-meta.XXXXXX.zip)
    TMP_DIR=$(mktemp -d /tmp/yacd-extract.XXXXXX)
    curl -sLo "$TMP_ZIP" \
        'https://github.com/MetaCubeX/Yacd-meta/archive/gh-pages.zip' \
        || die "Failed to download yacd-meta"
    unzip -q "$TMP_ZIP" -d "$TMP_DIR"
    mkdir -p "$WEBUI_DIR"
    mv "$TMP_DIR"/Yacd-meta-gh-pages/* "$WEBUI_DIR"/
    rm -rf "$TMP_ZIP" "$TMP_DIR"
    chown -R hiddify-cli:hiddify-cli "$WEBUI_DIR" 2>/dev/null || true
    log "yacd-meta installed to $WEBUI_DIR"
fi

# ─── Step 3: HAProxy auth for proxy_stats_api_backend ────────────────────────

AUTH_LINE="    http-request deny unless { req.hdr(Authorization) -m str \"Bearer $PROXY_STATS_SECRET\" } or { url_param(secret) -m str \"$PROXY_STATS_SECRET\" }"

if [[ -f "$HAPROXY_CFG" ]]; then
    if grep -q 'http-request deny unless.*Bearer' "$HAPROXY_CFG" 2>/dev/null; then
        log "HAProxy proxy-stats auth already present — skipping"
    else
        log "Adding auth to HAProxy proxy_stats_api_backend"
        python3 - "$HAPROXY_CFG" "$PROXY_STATS_SECRET" << 'PY'
import sys
f, secret = sys.argv[1], sys.argv[2]
content = open(f).read()
auth = f'    http-request deny unless {{ req.hdr(Authorization) -m str "Bearer {secret}" }} or {{ url_param(secret) -m str "{secret}" }}'
marker = 'backend proxy_stats_api_backend'
if marker not in content:
    print(f"  proxy_stats_api_backend not found in {f} — skipping auth patch")
    sys.exit(0)
idx = content.index(marker)
# Insert auth after the mode http line
insert_after = 'mode http\n'
pos = content.index(insert_after, idx) + len(insert_after)
if 'http-request deny' not in content[idx:idx+300]:
    content = content[:pos] + auth + '\n' + content[pos:]
    open(f, 'w').write(content)
    print(f"  auth added to {marker}")
else:
    print("  auth already present")
PY
        # Reload haproxy
        if haproxy -c -f "$HAPROXY_CFG" &>/dev/null; then
            kill -USR2 "$(cat /var/run/haproxy.pid 2>/dev/null)" 2>/dev/null \
                || systemctl reload haproxy 2>/dev/null || true
            log "HAProxy reloaded"
        else
            warn "HAProxy config invalid — skipping reload"
        fi
    fi
else
    warn "haproxy.cfg not found at $HAPROXY_CFG — skipping auth patch"
fi

# ─── Step 4: Clean up leftover drop-in overrides ─────────────────────────────

if [[ -f /etc/systemd/system/hiddify-cli.service.d/patch-balance.conf ]]; then
    log "Removing leftover drop-in override"
    rm -f /etc/systemd/system/hiddify-cli.service.d/patch-balance.conf
    systemctl daemon-reload
fi

# ─── Step 5: Restart hiddify-cli ─────────────────────────────────────────────

log "Restarting hiddify-cli"
systemctl restart hiddify-cli
sleep 10
if systemctl is-active hiddify-cli &>/dev/null; then
    log "hiddify-cli is active"
    if ss -lntp | grep -q ':16756'; then
        log "proxy-stats API listening on port 16756 — OK"
    else
        warn "port 16756 not yet listening — give it a few more seconds"
    fi
else
    warn "hiddify-cli not active — check: journalctl -u hiddify-cli -n 30"
fi
