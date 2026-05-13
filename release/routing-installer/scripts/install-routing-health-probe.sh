#!/usr/bin/env bash
# install-routing-health-probe.sh
# Installs the routing upstream health probe as a systemd timer (every 60s).
#
# Usage: sudo bash install-routing-health-probe.sh
set -Eeuo pipefail

INSTALL_ROOT="${INSTALL_ROOT:-/opt/hiddify-manager}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROBE_SRC="$SCRIPT_DIR/probe-routing-health.py"
PROBE_DST="$INSTALL_ROOT/scripts/probe-routing-health.py"
VAR_DIR="$INSTALL_ROOT/var"

[[ "$(id -u)" -eq 0 ]] || { echo "Run as root." >&2; exit 1; }
[[ -f "$PROBE_SRC" ]]   || { echo "probe-routing-health.py not found: $PROBE_SRC" >&2; exit 1; }

VENV_PY=$(find "$INSTALL_ROOT" -name 'python3*' -path '*venv*/bin/*' \( -type f -o -type l \) 2>/dev/null | sort | head -1)
[[ -n "$VENV_PY" ]] || { echo "Cannot find venv python under $INSTALL_ROOT" >&2; exit 1; }

echo "[routing-health] Installing probe script to $PROBE_DST"
mkdir -p "$INSTALL_ROOT/scripts" "$VAR_DIR"
install -m 0755 "$PROBE_SRC" "$PROBE_DST"

echo "[routing-health] Writing systemd units"

cat > /etc/systemd/system/hiddify-routing-health.service << EOF
[Unit]
Description=Hiddify routing upstream health probe
After=network-online.target mysql.service

[Service]
Type=oneshot
ExecStart=$VENV_PY $PROBE_DST
WorkingDirectory=$INSTALL_ROOT
User=root
StandardOutput=journal
StandardError=journal
EOF

cat > /etc/systemd/system/hiddify-routing-health.timer << 'EOF'
[Unit]
Description=Hiddify routing upstream health probe — every 60s

[Timer]
OnBootSec=30s
OnUnitActiveSec=60s
AccuracySec=5s

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now hiddify-routing-health.timer
echo "[routing-health] Timer enabled"

# Run once immediately
systemctl start hiddify-routing-health.service
echo "[routing-health] First probe run triggered"

sleep 5
if [[ -f "$VAR_DIR/commercial-routing-status.json" ]]; then
    echo "[routing-health] Status file created:"
    python3 -c "
import json
d = json.load(open('$VAR_DIR/commercial-routing-status.json'))
for uid, u in d['upstreams'].items():
    ms = f\"{u['latency_ms']} ms\" if u['latency_ms'] else '-'
    print(f\"  upstream-{uid} ({u['tunnel_type']}): {u['status']} {ms}\")
"
else
    echo "[routing-health] WARN: status file not yet created"
    journalctl -u hiddify-routing-health.service -n 20 --no-pager 2>/dev/null | tail -15
fi
