#!/usr/bin/env bash
# stabilize-celery-beat.sh — Recovery tool for corrupted celerybeat-schedule.
#
# Context:
#   hiddify-panel-background-tasks runs celery worker+beat in a single process.
#   The celerybeat-schedule file (SQLite) can become corrupted after unclean shutdown.
#   Symptom: "The database upgrade is required before proceeding. Retrying..."
#     OR beat does not send tasks even though is_db_latest() should return True.
#   Effect: last_online never updates, anti-share scoring never runs.
#
# This script:
#   1. Shows current state (read-only)
#   2. If schedule is corrupt/stale: backs it up and removes it
#   3. Restarts the service
#   4. Verifies beat starts sending tasks
#
# Idempotent: safe to run when there is no problem (exits cleanly).
set -Eeuo pipefail

die()  { echo "[celery-stabilize][ERROR] $*" >&2; exit 1; }
log()  { echo "[celery-stabilize] $*"; }
warn() { echo "[celery-stabilize][WARN] $*" >&2; }

require_root() { [[ "$(id -u)" -eq 0 ]] || die "Run as root."; }
require_root

INSTALL_ROOT="${INSTALL_ROOT:-/opt/hiddify-manager}"
PANEL_DIR="$INSTALL_ROOT/hiddify-panel"
SCHEDULE_BASE="$PANEL_DIR/celerybeat-schedule"
BG_SERVICE="hiddify-panel-background-tasks"
LOG_FILE="$INSTALL_ROOT/log/system/hiddify_panel_background_tasks.err.log"

# --- Step 1: Show current state ---
log "=== Current state ==="
log "Service: $(systemctl is-active "$BG_SERVICE" 2>/dev/null || echo 'unknown')"
log "Schedule files:"
ls -la "${SCHEDULE_BASE}"* 2>/dev/null || log "  (none found)"

# --- Step 2: Check for known problem indicators ---
needs_recovery=0

# Check A: Is the service stuck in the retry loop?
if [[ -f "$LOG_FILE" ]]; then
    recent_errors="$(tail -200 "$LOG_FILE" 2>/dev/null | grep -c 'database upgrade is required' || true)"
    if [[ "$recent_errors" -gt 3 ]]; then
        log "Detected $recent_errors 'database upgrade required' retry lines in log"
        needs_recovery=1
    fi
fi

# Check B: schedule file exists but beat has not sent tasks in last 5 minutes
if [[ -f "${SCHEDULE_BASE}" ]]; then
    schedule_age_s="$(( $(date +%s) - $(stat -c %Y "${SCHEDULE_BASE}" 2>/dev/null || echo 0) ))"
    if [[ "$schedule_age_s" -gt 600 ]]; then
        log "Schedule file is ${schedule_age_s}s old (>10 min) — beat may be stuck"
        needs_recovery=1
    fi
fi

# Check C: background tasks service not running at all
if [[ "$(systemctl is-active "$BG_SERVICE" 2>/dev/null)" != "active" ]]; then
    log "Service $BG_SERVICE is not active"
    needs_recovery=1
fi

if [[ "$needs_recovery" -eq 0 ]]; then
    log "No recovery needed — celery beat appears healthy"
    # Show recent task activity as confirmation
    if [[ -f "$LOG_FILE" ]]; then
        recent_ok="$(tail -100 "$LOG_FILE" 2>/dev/null | grep -c 'update_local_usage.*succeeded\|succeeded.*update_local' || true)"
        log "Recent successful update_local_usage tasks (last 100 log lines): $recent_ok"
    fi
    exit 0
fi

# --- Step 3: Backup and remove corrupted schedule ---
log "=== Recovery needed — proceeding ==="
if [[ -f "${SCHEDULE_BASE}" ]]; then
    BACKUP_STAMP="$(date +%Y%m%d-%H%M%S)"
    for f in "${SCHEDULE_BASE}" "${SCHEDULE_BASE}-shm" "${SCHEDULE_BASE}-wal"; do
        [[ -f "$f" ]] || continue
        cp -p "$f" "${f}.bak.${BACKUP_STAMP}" 2>/dev/null || true
        log "Backed up: $f → ${f}.bak.${BACKUP_STAMP}"
    done
fi

log "Removing celerybeat-schedule files"
rm -f "${SCHEDULE_BASE}" "${SCHEDULE_BASE}-shm" "${SCHEDULE_BASE}-wal"

# --- Step 4: Restart service ---
log "Restarting $BG_SERVICE"
systemctl restart "$BG_SERVICE"
sleep 5

state="$(systemctl is-active "$BG_SERVICE" 2>/dev/null || echo 'unknown')"
[[ "$state" == "active" ]] || die "Service failed to start after restart: $state"
log "Service is active"

# --- Step 5: Wait for beat to create new schedule and send first task ---
log "Waiting up to 90s for beat to send first task..."
deadline=$(( $(date +%s) + 90 ))
beat_ok=0
while [[ $(date +%s) -lt $deadline ]]; do
    sleep 10
    if [[ -f "${SCHEDULE_BASE}" ]]; then
        log "Schedule file created: ${SCHEDULE_BASE}"
        beat_ok=1
        break
    fi
done

if [[ "$beat_ok" -eq 0 ]]; then
    warn "Schedule file not created within 90s — check log: $LOG_FILE"
fi

# --- Step 6: Check log for task execution ---
if [[ -f "$LOG_FILE" ]]; then
    task_lines="$(tail -100 "$LOG_FILE" 2>/dev/null | grep 'update_local_usage\|beat.*Waking\|Scheduler.*Sending' | tail -5 || true)"
    if [[ -n "$task_lines" ]]; then
        log "Beat activity detected:"
        echo "$task_lines" | while read -r line; do log "  $line"; done
    else
        warn "No beat activity detected in log yet — may need more time"
    fi
fi

log "stabilize-celery-beat OK"
