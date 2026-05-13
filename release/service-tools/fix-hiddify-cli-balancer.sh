#!/usr/bin/env bash
# fix-hiddify-cli-balancer.sh
# Fixes hiddify-core v4.1.0 bug: balancer outbound generated without strategy.
#
# Root cause: execute-config-as-is=false causes hiddify-core to produce a
# "balance" balancer outbound with no strategy field, which sing-box 1.13.1
# rejects with "unknown load balance strategy:".
#
# Fix: set execute-config-as-is=true and balancer-strategy=round-robin in
# h_client_config.json. hiddify-core then passes the singbox subscription
# config as-is (which uses urltest/selector, no broken balancer).
#
# Usage: sudo bash fix-hiddify-cli-balancer.sh
set -Eeuo pipefail

CLI_DIR="/opt/hiddify-manager/other/hiddify-cli"
CONFIG="$CLI_DIR/h_client_config.json"

[[ "$(id -u)" -eq 0 ]] || { echo "Run as root." >&2; exit 1; }
[[ -f "$CONFIG" ]]      || { echo "h_client_config.json not found: $CONFIG" >&2; exit 1; }

echo "[fix-hiddify-cli] Backing up $CONFIG"
cp -a "$CONFIG" "${CONFIG}.bak.$(date +%F-%H%M%S)"

echo "[fix-hiddify-cli] Applying fix: execute-config-as-is=true, balancer-strategy=round-robin"
python3 - "$CONFIG" << 'PY'
import json, sys
f = sys.argv[1]
d = json.load(open(f))
d["execute-config-as-is"] = True
d["balancer-strategy"] = "round-robin"
open(f, "w").write(json.dumps(d, indent=2))
print(f"  execute-config-as-is: {d['execute-config-as-is']}")
print(f"  balancer-strategy:    {d['balancer-strategy']}")
PY

echo "[fix-hiddify-cli] Removing any leftover drop-in overrides from this fix"
rm -f /etc/systemd/system/hiddify-cli.service.d/patch-balance.conf
systemctl daemon-reload

echo "[fix-hiddify-cli] Restarting hiddify-cli"
systemctl restart hiddify-cli
sleep 10
systemctl is-active hiddify-cli && echo "[fix-hiddify-cli] hiddify-cli is active" \
                                || echo "[fix-hiddify-cli] WARN: hiddify-cli not active yet — check journalctl -u hiddify-cli"
