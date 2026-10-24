#!/usr/bin/env bash
# One-off: issue the Let's Encrypt certificate for ROUTING_HOST via the shared acme.sh webroot
# (nginx serves /.well-known/acme-challenge/ for this host permanently), then stash + sync.
set -euo pipefail
# shellcheck source=cert-common.sh
. "$(dirname "$0")/cert-common.sh"

# Refuse to start unless the name already points at this server (otherwise Let's Encrypt only burns rate limit).
mine="$(curl -fsS --max-time 10 https://api.ipify.org 2>/dev/null || true)"
resolved="$(dig +short -t a "$HOST" @1.1.1.1 | tail -1)"
clog "A($HOST)=$resolved this-server=$mine"
[ -n "$resolved" ] || { clog "ERROR: $HOST does not resolve"; exit 1; }
[ -z "$mine" ] || [ "$resolved" = "$mine" ] || { clog "ERROR: $HOST points to $resolved, not to this server ($mine)"; exit 1; }

if stash_ok; then clog "stash already has a valid certificate - skipping issuance"; else
    mkdir -p "$WEBROOT/.well-known/acme-challenge"
    chown -R nginx "$WEBROOT" 2>/dev/null || true
    acme --issue -d "$HOST" -w "$WEBROOT" --server letsencrypt --keylength ec-256 \
        --log /opt/hiddify-manager/log/system/acme.log
    install_stash_from_acme
fi
cert_sync
