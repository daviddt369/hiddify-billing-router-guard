#!/usr/bin/env bash
set -Eeuo pipefail

OUT_ROOT="${OUT_ROOT:-/opt/hiddify-manager/hiddify-cli-audit}"
STAMP="$(date +%F-%H%M%S)"
OUT_DIR="$OUT_ROOT/$STAMP"

log() {
  echo "[audit-hiddify-cli] $*"
}

die() {
  echo "[audit-hiddify-cli][ERROR] $*" >&2
  exit 1
}

require_root() {
  [[ "$(id -u)" -eq 0 ]] || die "Run as root."
}

main() {
  require_root
  mkdir -p "$OUT_DIR"

  log "Collecting hiddify-cli baseline into $OUT_DIR"

  systemctl is-active hiddify-panel hiddify-panel-background-tasks hiddify-cli > "$OUT_DIR/is-active.txt" 2>&1 || true
  systemctl cat hiddify-cli.service > "$OUT_DIR/unit.txt" 2>&1 || true
  systemctl status hiddify-cli --no-pager -l > "$OUT_DIR/status.txt" 2>&1 || true
  systemctl show hiddify-cli -p ActiveState -p SubState -p Result -p NRestarts > "$OUT_DIR/show.txt" 2>&1 || true
  journalctl -u hiddify-cli -n 200 --no-pager -o cat > "$OUT_DIR/journal.txt" 2>&1 || true

  local r1 r2 active substate result
  r1="$(systemctl show hiddify-cli -p NRestarts --value 2>/dev/null || echo 0)"
  sleep 60
  r2="$(systemctl show hiddify-cli -p NRestarts --value 2>/dev/null || echo 0)"
  echo "NRestarts before=$r1 after=$r2" | tee "$OUT_DIR/restarts.txt"

  active="$(systemctl show hiddify-cli -p ActiveState --value 2>/dev/null || echo unknown)"
  substate="$(systemctl show hiddify-cli -p SubState --value 2>/dev/null || echo unknown)"
  result="$(systemctl show hiddify-cli -p Result --value 2>/dev/null || echo unknown)"

  {
    echo "active_state=$active"
    echo "sub_state=$substate"
    echo "result=$result"
    echo "nrestarts_before=$r1"
    echo "nrestarts_after=$r2"
    if (( r2 > r1 )); then
      echo "restart_storm_detected=yes"
    else
      echo "restart_storm_detected=no"
    fi
  } | tee "$OUT_DIR/summary.txt"

  log "Audit complete: $OUT_DIR"
  echo "$OUT_DIR"
}

main "$@"
