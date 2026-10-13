#!/usr/bin/env bash
# Runs every few minutes: (1) re-install the certificate if Hiddify removed it, (2) once a day let
# acme.sh renew it when due and refresh the stash.
set -euo pipefail
# shellcheck source=cert-common.sh
. "$(dirname "$0")/cert-common.sh"

guard_check || exit 0   # our nginx block was just disabled; nothing else to do this tick

STAMP="$STASH/.last-renew-check"
if [ -d "$STASH" ] && { [ ! -e "$STAMP" ] || [ -n "$(find "$STAMP" -mmin +1440 2>/dev/null)" ]; }; then
    if acme_has_cert; then
        set +e
        # shellcheck disable=SC2046
        out="$(acme --renew -d "$HOST" $(ecc_flag) --log /opt/hiddify-manager/log/system/acme.log 2>&1)"
        rc=$?
        set -e
        case "$rc" in
            0) clog "renewed"; install_stash_from_acme ;;
            2) clog "renewal not due" ;;
            *) clog "WARN: renew failed (rc=$rc): $(echo "$out" | tail -3 | tr '\n' ' ')" ;;
        esac
    fi
    touch "$STAMP"
fi
cert_sync
