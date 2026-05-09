#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE_NAME="hiddify-cli"
BACKUP_ROOT="/opt/hiddify-manager/hiddify-cli-stabilization-backups"
OVERRIDE_DIR="/etc/systemd/system/${SERVICE_NAME}.service.d"
OVERRIDE_FILE="$OVERRIDE_DIR/10-codex-stabilize.conf"
MARKER_FILE="/opt/hiddify-manager/HIDDIFY_CLI_DEGRADED_EXPECTED"
STAMP="$(date +%F-%H%M%S)"
BACKUP_DIR="$BACKUP_ROOT/$STAMP"

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
  systemctl show "$SERVICE_NAME" -p NRestarts --value 2>/dev/null || echo 0
}

confirm_restart_storm() {
  local before after delta
  before="$(read_restart_count)"
  sleep 60
  after="$(read_restart_count)"
  delta=$((after - before))
  echo "NRestarts before=$before after=$after delta=$delta"
  (( delta >= 2 ))
}

main() {
  require_root
  mkdir -p "$BACKUP_DIR"

  log "Confirming hiddify-cli restart storm"
  if ! confirm_restart_storm; then
    log "No confirmed restart storm. No stabilization applied."
    exit 0
  fi

  log "Confirmed restart storm. Saving current service state."
  printf '%s\n' "$BACKUP_DIR" > "$BACKUP_ROOT/latest"
  systemctl cat "$SERVICE_NAME".service > "$BACKUP_DIR/hiddify-cli.service.full.txt" 2>&1 || true
  cp -a /etc/systemd/system/hiddify-cli.service "$BACKUP_DIR/hiddify-cli.service" 2>/dev/null || true
  cp -a "$OVERRIDE_DIR" "$BACKUP_DIR/service.d" 2>/dev/null || true
  systemctl show "$SERVICE_NAME" -p ActiveState -p SubState -p Result -p NRestarts > "$BACKUP_DIR/show-before.txt" 2>&1 || true

  log "Applying systemd override to slow restart storm safely"
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
  systemctl reset-failed "$SERVICE_NAME" || true
  systemctl restart "$SERVICE_NAME" || true

  log "Re-checking restart behavior after override"
  if confirm_restart_storm; then
    log "Restart loop persists. Switching to explicit degraded mode."
    systemctl stop "$SERVICE_NAME" || true
    systemctl disable "$SERVICE_NAME" || true
    cat > "$MARKER_FILE" <<EOF
timestamp=$(date '+%F %T')
reason=restart-storm
override=$OVERRIDE_FILE
backup_dir=$BACKUP_DIR
EOF
    chmod 644 "$MARKER_FILE"
    echo "HIDDIFY_CLI_DEGRADED_EXPECTED"
  else
    rm -f "$MARKER_FILE"
    log "Restart storm slowed by override. Service left enabled."
  fi

  systemctl show "$SERVICE_NAME" -p ActiveState -p SubState -p Result -p NRestarts > "$BACKUP_DIR/show-after.txt" 2>&1 || true
  systemctl status "$SERVICE_NAME" --no-pager -l || true
  log "Stabilization artifacts: $BACKUP_DIR"
}

main "$@"
