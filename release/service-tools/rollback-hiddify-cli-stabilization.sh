#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE_NAME="hiddify-cli"
BACKUP_ROOT="/opt/hiddify-manager/hiddify-cli-stabilization-backups"
OVERRIDE_DIR="/etc/systemd/system/${SERVICE_NAME}.service.d"
OVERRIDE_FILE="$OVERRIDE_DIR/10-codex-stabilize.conf"
MARKER_FILE="/opt/hiddify-manager/HIDDIFY_CLI_DEGRADED_EXPECTED"
BACKUP_ARG=""

log() {
  echo "[rollback-hiddify-cli] $*"
}

die() {
  echo "[rollback-hiddify-cli][ERROR] $*" >&2
  exit 1
}

require_root() {
  [[ "$(id -u)" -eq 0 ]] || die "Run as root."
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --backup-dir)
      BACKUP_ARG="${2:-}"
      shift 2
      ;;
    *)
      die "Unknown argument: $1"
      ;;
  esac
done

main() {
  require_root

  local backup_dir=""
  if [[ -n "$BACKUP_ARG" ]]; then
    backup_dir="$BACKUP_ARG"
  elif [[ -f "$BACKUP_ROOT/latest" ]]; then
    backup_dir="$(<"$BACKUP_ROOT/latest")"
  fi

  if [[ -n "$backup_dir" && -d "$backup_dir/service.d" ]]; then
    log "Restoring override directory from backup: $backup_dir"
    rm -rf "$OVERRIDE_DIR"
    cp -a "$backup_dir/service.d" "$OVERRIDE_DIR"
  else
    log "Removing Codex override file"
    rm -f "$OVERRIDE_FILE"
    rmdir "$OVERRIDE_DIR" 2>/dev/null || true
  fi

  rm -f "$MARKER_FILE"
  systemctl daemon-reload
  systemctl reset-failed "$SERVICE_NAME" || true
  systemctl enable "$SERVICE_NAME" || true
  systemctl start "$SERVICE_NAME" || true
  systemctl status "$SERVICE_NAME" --no-pager -l || true
}

main "$@"
