#!/usr/bin/env bash
# fix-hiddify-cli-dependency.sh
#
# Root-cause fix for the hiddify-cli restart storm.
#
# Problem:
#   hiddify-cli.service has After=network-online.target only. hiddify-panel
#   (a Python app on port 9000) takes 30-60 s to initialize after boot.
#   hiddify-core starts earlier, fetches the subscription URL, gets an empty
#   or connection-refused response, saves an empty config, and exits with
#   code 0. systemd (Restart=always, RestartSec=3) immediately relaunches it.
#   This produces a restart counter in the tens of thousands over days.
#
# Fix:
#   A systemd drop-in (20-panel-dependency.conf) that:
#     - adds After=hiddify-panel.service + Requires=hiddify-panel.service
#     - adds ExecStartPre health-check waiting for port 9000 (up to 60 s)
#     - increases RestartSec to 10 s to reduce log noise on any future failure
#
# This script does NOT touch the main hiddify-cli.service file, so it
# survives hiddify-manager upgrades.
#
# Usage: sudo bash fix-hiddify-cli-dependency.sh
set -Eeuo pipefail

SERVICE_NAME="hiddify-cli"
OVERRIDE_DIR="/etc/systemd/system/${SERVICE_NAME}.service.d"
OVERRIDE_FILE="$OVERRIDE_DIR/20-panel-dependency.conf"
BACKUP_ROOT="/opt/hiddify-manager/hiddify-cli-stabilization-backups"
STAMP="$(date +%F-%H%M%S)"
BACKUP_DIR="$BACKUP_ROOT/dep-fix-$STAMP"

log()  { echo "[fix-hiddify-cli-dependency] $*"; }
die()  { echo "[fix-hiddify-cli-dependency][ERROR] $*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "Run as root."

# ─── Backup ──────────────────────────────────────────────────────────────────

mkdir -p "$BACKUP_DIR"
cp -a /etc/systemd/system/hiddify-cli.service "$BACKUP_DIR/hiddify-cli.service" 2>/dev/null || true
cp -a "$OVERRIDE_DIR" "$BACKUP_DIR/service.d" 2>/dev/null || true
systemctl show "$SERVICE_NAME" -p ActiveState -p NRestarts \
  > "$BACKUP_DIR/show-before.txt" 2>&1 || true
log "Backup saved to $BACKUP_DIR"

# ─── Apply drop-in ───────────────────────────────────────────────────────────

if [[ -f "$OVERRIDE_FILE" ]]; then
  log "Drop-in already exists: $OVERRIDE_FILE — overwriting"
fi

mkdir -p "$OVERRIDE_DIR"
cat > "$OVERRIDE_FILE" <<'EOF'
[Unit]
After=hiddify-panel.service
Requires=hiddify-panel.service

[Service]
RestartSec=10
ExecStartPre=/bin/bash -c 'for i in $(seq 1 30); do curl -sf --max-time 3 http://127.0.0.1:9000/ > /dev/null 2>&1 && break || sleep 2; done'
EOF

log "Drop-in written: $OVERRIDE_FILE"

# ─── Apply & verify ──────────────────────────────────────────────────────────

systemctl daemon-reload
systemctl reset-failed "$SERVICE_NAME" || true
systemctl restart "$SERVICE_NAME"
sleep 8

systemctl status "$SERVICE_NAME" --no-pager -l || true

if systemctl is-active "$SERVICE_NAME" &>/dev/null; then
  log "hiddify-cli is active — fix applied successfully"
else
  die "hiddify-cli did not start. Check: journalctl -u hiddify-cli -n 50"
fi
