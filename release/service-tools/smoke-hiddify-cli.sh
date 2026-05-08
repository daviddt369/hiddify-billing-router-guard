#!/usr/bin/env bash
set -Eeuo pipefail

MARKER_FILE="/opt/hiddify-manager/HIDDIFY_CLI_DEGRADED_EXPECTED"

log() {
  echo "[smoke-hiddify-cli] $*"
}

die() {
  echo "[smoke-hiddify-cli][FAIL] $*" >&2
  exit 1
}

read_restart_count() {
  systemctl show hiddify-cli -p NRestarts --value 2>/dev/null || echo 0
}

main() {
  local active substate result r1 r2 delta
  active="$(systemctl show hiddify-cli -p ActiveState --value 2>/dev/null || echo unknown)"
  substate="$(systemctl show hiddify-cli -p SubState --value 2>/dev/null || echo unknown)"
  result="$(systemctl show hiddify-cli -p Result --value 2>/dev/null || echo unknown)"

  log "Initial state: active=$active substate=$substate result=$result"

  r1="$(read_restart_count)"
  sleep 60
  r2="$(read_restart_count)"
  delta=$((r2-r1))
  log "NRestarts before=$r1 after=$r2 delta=$delta"

  active="$(systemctl show hiddify-cli -p ActiveState --value 2>/dev/null || echo unknown)"
  substate="$(systemctl show hiddify-cli -p SubState --value 2>/dev/null || echo unknown)"

  if [[ -f "$MARKER_FILE" ]]; then
    echo "HIDDIFY_CLI_DEGRADED_EXPECTED"
    echo "state=$active/$substate"
    exit 0
  fi

  if [[ "$active" == "active" && "$delta" -eq 0 ]]; then
    echo "hiddify-cli smoke OK"
    exit 0
  fi

  if (( delta > 1 )); then
    die "Restart loop detected"
  fi

  die "hiddify-cli is not stable: active=$active substate=$substate delta=$delta"
}

main "$@"
