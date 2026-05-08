#!/usr/bin/env bash
# apply-base-stability.sh — Operational patch: add timeout=5 to ident.me urlopen calls in net.py.
#
# Context:
#   hiddifypanel/hutils/network/net.py calls urlopen(ident.me) without timeout.
#   bjoern WSGI is single-threaded — a hung DNS/TCP call freezes the entire panel.
#   This affects: Domain Admin page load, Actions.reinstall flow.
#
# WARNING: This patch targets the venv copy of net.py.
#   It WILL BE OVERWRITTEN when hiddify-panel package is updated.
#   Re-run this script after any panel upgrade.
#   This is an operational workaround, not a permanent fix.
#
# Idempotent: safe to run multiple times.
set -Eeuo pipefail

die()  { echo "[base-stability][ERROR] $*" >&2; exit 1; }
log()  { echo "[base-stability] $*"; }
warn() { echo "[base-stability][WARN] $*" >&2; }

require_root() { [[ "$(id -u)" -eq 0 ]] || die "Run as root."; }
require_root

INSTALL_ROOT="${INSTALL_ROOT:-/opt/hiddify-manager}"

# --- Step 1: Locate net.py in active venv ---
log "Locating net.py in hiddify-manager venv"
mapfile -t candidates < <(find "$INSTALL_ROOT" \
    -path '*/site-packages/hiddifypanel/hutils/network/net.py' \
    -not -path '*/__pycache__/*' \
    2>/dev/null | sort)

if [[ "${#candidates[@]}" -eq 0 ]]; then
    die "net.py not found under $INSTALL_ROOT — is hiddify-panel installed?"
fi

NET_PY="${candidates[0]}"
log "Found: $NET_PY"

# --- Step 2: Check if already patched ---
if grep -q "ident\.me.*timeout=5\|timeout=5.*ident\.me" "$NET_PY" 2>/dev/null; then
    log "Already patched — timeout=5 already present in net.py. Nothing to do."
    exit 0
fi

# Verify the expected pattern exists before patching
if ! grep -q "urlopen.*ident\.me" "$NET_PY" 2>/dev/null; then
    warn "Expected pattern 'urlopen.*ident.me' not found in $NET_PY"
    warn "Panel may have been updated and this patch no longer applies."
    warn "Check net.py manually: $NET_PY"
    exit 0
fi

# --- Step 3: Backup with timestamp ---
BACKUP_FILE="${NET_PY}.bak.$(date +%Y%m%d-%H%M%S)"
cp -p "$NET_PY" "$BACKUP_FILE"
log "Backup: $BACKUP_FILE"

# --- Step 4: Apply patch — add timeout=5 to both urlopen(ident.me) calls ---
# Pattern: urlopen(f'https://v{version}.ident.me/')  OR  urlopen(f'https://ident.me/')
# Add timeout=5 as second argument (before closing paren).
sed -i \
    "s|urlopen(f'https://v\(.*\)ident\.me/')|urlopen(f'https://v\1ident.me/', timeout=5)|g;
     s|urlopen(f'https://ident\.me/')|urlopen(f'https://ident.me/', timeout=5)|g;
     s|urlopen('https://v\(.*\)ident\.me/')|urlopen('https://v\1ident.me/', timeout=5)|g;
     s|urlopen('https://ident\.me/')|urlopen('https://ident.me/', timeout=5)|g" \
    "$NET_PY"

# --- Step 5: Verify patch landed ---
patched_count="$(grep -c "ident\.me.*timeout=5" "$NET_PY" 2>/dev/null || echo 0)"
if [[ "$patched_count" -lt 1 ]]; then
    cp -p "$BACKUP_FILE" "$NET_PY"
    die "Patch verification failed — no patched lines found. Restored from backup."
fi
log "Patched $patched_count ident.me urlopen call(s) with timeout=5"

# --- Step 6: Syntax check ---
venv_python="$(dirname "$NET_PY")"
venv_python="$(find "$INSTALL_ROOT" -name 'python*' -path '*venv*/bin/*' -type f | sort | head -n1)"
if [[ -n "$venv_python" ]]; then
    "$venv_python" -m py_compile "$NET_PY" \
        || { cp -p "$BACKUP_FILE" "$NET_PY"; die "Syntax check failed — restored from backup."; }
    log "Syntax check passed"
else
    python3 -m py_compile "$NET_PY" \
        || { cp -p "$BACKUP_FILE" "$NET_PY"; die "Syntax check failed — restored from backup."; }
    log "Syntax check passed (system python3)"
fi

log "apply-base-stability OK — net.py timeout=5 patch applied"
log "NOTE: This patch will be lost on panel package upgrade. Re-run after upgrade."
