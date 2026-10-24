# Shared helpers for the routing-links TLS certificate (sourced, not executed).
# Hiddify's replace_variables.sh deletes every /opt/hiddify-manager/ssl/*.crt whose domain is not in
# its own domain table (on each install/apply, incl. the weekly reboot). So the master copy lives in
# a stash outside ssl/, and cert_sync() puts it back (and reloads HAProxy seamlessly) when it is gone.

HOST="${ROUTING_HOST:?ROUTING_HOST not set}"
STASH="${ROUTING_TLS_STASH:-/var/lib/routing-links-tls}"
SSL="${HIDDIFY_SSL_DIR:-/opt/hiddify-manager/ssl}"
ACME_BIN="${ACME_BIN:-/opt/hiddify-manager/acme.sh/lib/acme.sh}"
ACME_CFG="${ACME_CFG:-/opt/hiddify-manager/acme.sh/lib/data}"
ACME_CERTS="${ACME_CERTS:-/opt/hiddify-manager/acme.sh/lib/certs}"
WEBROOT="${ACME_WEBROOT:-/opt/hiddify-manager/acme.sh/www/}"

NGINX_CONF="${ROUTING_NGINX_CONF:-/opt/hiddify-manager/nginx/conf.d/zz-routing-links.conf}"
NGINX_MAIN="${ROUTING_NGINX_MAIN:-/opt/hiddify-manager/nginx/nginx.conf}"
GUARD_HOST="${ROUTING_GUARD_HOST:-@@BRAND_DOMAIN@@}"   # an existing Hiddify domain that must never get our page

clog() { echo "[routing-links-cert] $(date -u +%FT%TZ) $*"; }

# Safety net: our nginx block must only answer for ROUTING_HOST. nginx hands unknown Host headers to the
# FIRST server block on the socket, so if file ordering ever changes our block could hijack every other
# domain (panel, subscriptions, decoy). Detect that and switch our block off.
guard_check() {
    [ -e "$NGINX_CONF" ] || return 0
    local body
    body="$(curl -sk -m 8 --resolve "$GUARD_HOST:443:127.0.0.1" "https://$GUARD_HOST/happ" 2>/dev/null || true)"
    if grep -q 'id="copy-url"' <<<"$body"; then
        clog "GUARD: landing page is served for foreign host $GUARD_HOST - disabling $NGINX_CONF"
        mv -f "$NGINX_CONF" "$NGINX_CONF.disabled"
        if nginx -t -c "$NGINX_MAIN" >/dev/null 2>&1; then systemctl reload hiddify-nginx; fi
        return 1
    fi
    return 0
}

acme() {
    LE_WORKING_DIR="$(dirname "$ACME_BIN")" "$ACME_BIN" --config-home "$ACME_CFG" "$@"
}

# We always issue ec-256 (see cert-issue.sh), so every acme.sh call for our host needs --ecc.
ecc_flag() { echo "--ecc"; }
# Where acme.sh keeps the issued cert depends on its CERT_HOME setting: Hiddify's own domains end up in
# lib/certs, ours (issued with only --config-home) in lib/data. Accept either.
acme_has_cert() { [ -d "$ACME_CFG/${HOST}_ecc" ] || [ -d "$ACME_CERTS/${HOST}_ecc" ]; }

# stash holds a usable pair?  (cert not expiring within a day, key matches cert)
stash_ok() {
    [ -s "$STASH/$HOST.crt" ] && [ -s "$STASH/$HOST.crt.key" ] || return 1
    openssl x509 -in "$STASH/$HOST.crt" -noout -checkend 86400 >/dev/null 2>&1 || return 1
    local a b
    a="$(openssl x509 -in "$STASH/$HOST.crt" -noout -pubkey 2>/dev/null | openssl sha256)" || return 1
    b="$(openssl pkey -in "$STASH/$HOST.crt.key" -pubout 2>/dev/null | openssl sha256)" || return 1
    [ -n "$a" ] && [ "$a" = "$b" ]
}

install_stash_from_acme() {
    install -d -m 700 "$STASH"
    # shellcheck disable=SC2046
    acme --install-cert -d "$HOST" $(ecc_flag) \
        --fullchain-file "$STASH/$HOST.crt" --key-file "$STASH/$HOST.crt.key" >/dev/null
    chmod 600 "$STASH/$HOST.crt" "$STASH/$HOST.crt.key"
}

# Put the stash copy into Hiddify's ssl dir (if missing/different) and reload HAProxy once.
cert_sync() {
    stash_ok || { clog "stash has no valid certificate yet - nothing to sync"; return 0; }
    if cmp -s "$STASH/$HOST.crt" "$SSL/$HOST.crt" && cmp -s "$STASH/$HOST.crt.key" "$SSL/$HOST.crt.key"; then
        return 0
    fi
    # Hiddify's install/apply may be mid-flight (it stops/starts HAProxy itself) - try again next tick.
    if pgrep -f '/opt/hiddify-manager/(install|apply_configs)\.sh' >/dev/null 2>&1; then
        clog "Hiddify install/apply is running - deferring"
        return 0
    fi
    systemctl is-active --quiet hiddify-haproxy || { clog "hiddify-haproxy not active - deferring"; return 0; }
    # Reload-storm brake: Hiddify's minutely `install.sh apply_users` deletes unknown certs from ssl/ unless
    # the keep-marker patch is in place. If our cert vanished again shortly after we installed it, do NOT
    # keep reloading HAProxy (every reload leaves an old worker draining) - back off and shout in the log.
    local now last
    now="$(date +%s)"; last="$(cat "$STASH/.last-install" 2>/dev/null || echo 0)"
    if [ "$((now - last))" -lt "${ROUTING_REINSTALL_BACKOFF:-1800}" ]; then
        clog "WARN: cert missing again $((now - last))s after the last install - backing off (is the keep-marker patch of replace_variables.sh gone?)"
        return 0
    fi
    install -m 600 "$STASH/$HOST.crt.key" "$SSL/$HOST.crt.key"
    install -m 600 "$STASH/$HOST.crt" "$SSL/$HOST.crt"
    if systemctl reload hiddify-haproxy; then
        echo "$now" > "$STASH/.last-install"
        clog "certificate for $HOST installed, HAProxy reloaded"
    else
        clog "ERROR: HAProxy reload failed - removing our files so the next start cannot break"
        rm -f "$SSL/$HOST.crt" "$SSL/$HOST.crt.key"
        return 1
    fi
}
