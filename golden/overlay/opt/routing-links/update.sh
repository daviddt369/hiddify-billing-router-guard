#!/usr/bin/env bash
# Refresh the routing landing page from the upstream RoscomVPN deeplinks.
# Never publishes anything that did not pass validation: on any failure the
# currently served page stays as it is.
#
#   APP  - code + clients.json (read-only for the service user)
#   DATA - writable state: www/, last-good/, dynamic-links.json, status.json
set -euo pipefail

APP="${ROUTING_APP_DIR:-/opt/routing-links}"
DATA="${ROUTING_DATA_DIR:-/var/lib/routing-links}"
SRC="${ROUTING_SRC_BASE:-https://raw.githubusercontent.com/hydraponique/roscomvpn-routing/refs/heads/main}"
PY="${PYTHON:-python3}"
WWW="$DATA/www"
LAST="$DATA/last-good"

log() { echo "[routing-links] $(date -u +%FT%TZ) $*"; }

mkdir -p "$WWW" "$LAST"
tmp="$(mktemp -d "$DATA/.tmp.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT

fetch() { curl -fsS --max-time 30 --retry 2 --retry-delay 3 -o "$2" "$SRC/$1"; }

if ! fetch HAPP/DEFAULT.DEEPLINK "$tmp/happ.deeplink" || ! fetch INCY/DEFAULT.DEEPLINK "$tmp/incy.deeplink"; then
    log "ERROR: download failed, keeping current page"
    exit 1
fi

# Best effort: current INCY desktop/android file URLs (page falls back to the releases page).
"$PY" "$APP/resolve_links.py" "$DATA/dynamic-links.json" || log "WARN: INCY links not refreshed"

if ! "$PY" "$APP/build.py" --clients "$APP/clients.json" --happ "$tmp/happ.deeplink" --incy "$tmp/incy.deeplink" \
        --dynamic "$DATA/dynamic-links.json" --out "$tmp/index.html"; then
    log "ERROR: validation/build failed, keeping current page"
    exit 2
fi

changed=0
cmp -s "$tmp/index.html" "$WWW/index.html" 2>/dev/null || changed=1
if [ "$changed" = 1 ]; then
    cp "$tmp/happ.deeplink" "$LAST/happ.deeplink"
    cp "$tmp/incy.deeplink" "$LAST/incy.deeplink"
    install -m 644 "$tmp/index.html" "$WWW/.index.html.new"
    mv -f "$WWW/.index.html.new" "$WWW/index.html"
    log "OK: page updated"
else
    log "OK: no changes"
fi
printf '{"checked_at":"%s","changed":%s}\n' "$(date -u +%FT%TZ)" "$([ "$changed" = 1 ] && echo true || echo false)" > "$DATA/status.json"
