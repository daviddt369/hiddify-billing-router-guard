#!/usr/bin/env bash
set -Eeuo pipefail

OVERRIDE_DIR="/etc/systemd/system/hiddify-cli.service.d"
OVERRIDE_FILE="$OVERRIDE_DIR/10-codex-stabilize.conf"
MARKER_FILE="/opt/hiddify-manager/HIDDIFY_CLI_DEGRADED_EXPECTED"
BACKUP_DIR="/opt/hiddify-manager/hiddify-cli-stabilization-backups/$(date +%F-%H%M%S)"

log() {
  echo "[stabilize-hiddify-cli] $*"
}

die() {
  echo "[stabilize-hiddify-cli][ERROR] $*" >&2
  exit 1
}

require_root() {
  [[ "$(id -u)" -eq 0 ]] || die "Run as root."
}

read_restart_count() {
  systemctl show hiddify-cli -p NRestarts --value 2>/dev/null || echo 0
}

main() {
  require_root
  mkdir -p "$BACKUP_DIR"

  log "Auditing hiddify-cli restart behavior"
  local r1 r2 delta
  r1="$(read_restart_count)"
  sleep 15
  r2="$(read_restart_count)"
  delta=$((r2-r1))
  echo "NRestarts before=$r1 after=$r2 delta=$delta"

  if (( delta < 2 )); then
    log "No confirmed restart storm. No stabilization applied."
    exit 0
  fi

  log "Confirmed restart storm. Backing up current unit and override state."
  systemctl cat hiddify-cli.service > "$BACKUP_DIR/hiddify-cli.service.full.txt" 2>&1 || true
  cp -a /etc/systemd/system/hiddify-cli.service "$BACKUP_DIR/hiddify-cli.service" 2>/dev/null || true
  cp -a "$OVERRIDE_DIR" "$BACKUP_DIR/service.d" 2>/dev/null || true

  log "Applying systemd override to slow restart storm"
  mkdir -p "$OVERRIDE_DIR"
  cat > "$OVERRIDE_FILE" <<'EOF'
[Unit]
StartLimitIntervalSec=300
StartLimitBurst=3

[Service]
Restart=always
RestartSec=30
EOF

  systemctl daemon-reload
  systemctl restart hiddify-cli || true
  sleep 35

  r1="$(read_restart_count)"
  sleep 30
  r2="$(read_restart_count)"
  delta=$((r2-r1))
  echo "Post-override NRestarts before=$r1 after=$r2 delta=$delta"

  if (( delta > 0 )); then
    log "Restart loop persists. Entering explicit degraded mode."
    systemctl stop hiddify-cli || true
    systemctl disable hiddify-cli || true
    cat > "$MARKER_FILE" <<EOF
timestamp=$(date '+%F %T')
reason=restart-storm
override=$OVERRIDE_FILE
backup_dir=$BACKUP_DIR
EOF
    echo "HIDDIFY_CLI_DEGRADED_EXPECTED"
  else
    log "Restart storm slowed successfully with override."
  fi

  systemctl status hiddify-cli --no-pager -l || true
  log "Stabilization artifacts: $BACKUP_DIR"
}

main "$@"
