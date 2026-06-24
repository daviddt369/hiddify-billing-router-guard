#!/bin/bash
# Fix: acme.sh cert_utils.sh - ZeroSSL fallback never triggered
#
# Bug: $err variable is checked before it's assigned (line 78 vs 89).
# The undefined $err makes `[ "$err" -ne 0 ]` always false → ZeroSSL
# fallback never runs when Let's Encrypt fails for a non-IP domain.
# Fix: use $? (exit code of the preceding acmecmd call) instead.
#
# Affects: hiddify-manager 12.0.0 and below
# Fixed upstream: hiddify-manager 12.3.3 (acme.sh/cert_utils.sh)
# Risk: minimal — one character change, only runs during cert renewal

set -euo pipefail

CERT_UTILS="/opt/hiddify-manager/acme.sh/cert_utils.sh"

if [ ! -f "$CERT_UTILS" ]; then
    echo "ERROR: $CERT_UTILS not found" >&2
    exit 1
fi

if grep -q '"$err" -ne 0' "$CERT_UTILS"; then
    sed -i 's/if \[ "\$err" -ne 0 \] && is_ok_domain_zerossl/if [ "$?" -ne 0] \&\& is_ok_domain_zerossl/' "$CERT_UTILS"
    # sed with special chars — use Python for reliability
    python3 - << 'EOF'
path = "/opt/hiddify-manager/acme.sh/cert_utils.sh"
content = open(path).read()
fixed = content.replace(
    'if [ "$err" -ne 0 ] && is_ok_domain_zerossl',
    'if [ "$?" -ne 0 ] && is_ok_domain_zerossl'
)
if fixed == content:
    raise SystemExit("ERROR: pattern not found — already patched or file changed")
open(path, 'w').write(fixed)
print("OK: ZeroSSL fallback fix applied")
EOF
else
    echo "Already patched or pattern not found (check manually)"
fi
