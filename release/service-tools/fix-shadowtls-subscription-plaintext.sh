#!/bin/bash
# Fix: ShadowTLS plain text line breaks subscription import on strict clients (Happ Plus iOS)
#
# Bug: to_link() in xray.py returns the string
#   "ShadowTLS is Not Supported for this platform"
# instead of a dict with 'msg' key. The filter in make_v2ray_configs()
#   `if 'msg' not in link`
# checks for 'msg' substring/key — a plain string passes through and gets
# appended to the base64 subscription as a non-URL line.
#
# Position in subscription: line 11 (right after the first 10 real proxies).
# Strict parsers like Happ Plus iOS encounter this line and either abort the
# entire import (losing lines 12-51) or mark the whole subscription invalid.
# This is why iOS stopped working after ShadowTLS was added to the server config.
#
# Root cause: other "unsupported" paths return {'msg': '...'} — this one returns
# a plain string. One-character class difference, passes the wrong type.
#
# Fix: return a dict with 'msg' key so the existing filter catches it.
#
# Affects: all base64/v2ray subscription clients (Happ Plus, V2Box, FoXray,
#          Shadowrocket, Loon, v2rayNG, etc.)
# NOT affected: HiddifyApp (uses sing-box JSON, different code path)
# Fixed in: hiddify-panel v12.3.3 upstream? — NO, upstream does not have this fix.
#           This is a custom fix.
# Risk: minimal — one-line change, no logic change, just type correction

set -euo pipefail

INSTALLED="/opt/hiddify-manager/.venv313/lib/python3.13/site-packages/hiddifypanel/hutils/proxy/xray.py"
SRC="/opt/hiddify-manager/hiddify-panel/src/hiddifypanel/hutils/proxy/xray.py"

fix_file() {
    local file="$1"
    [ -f "$file" ] || return 0
    python3 - "$file" << 'EOF'
import sys
path = sys.argv[1]
content = open(path).read()
old = 'return "ShadowTLS is Not Supported for this platform"'
new = 'return {"msg": "ShadowTLS is Not Supported for this platform"}'
if old not in content:
    print(f"  already patched or pattern not found: {path}")
    exit(0)
open(path, 'w').write(content.replace(old, new, 1))
print(f"  OK: {path}")
EOF
}

echo "Applying ShadowTLS plain-text subscription fix..."
fix_file "$INSTALLED"
fix_file "$SRC"

# Clear bytecode cache
CACHE=$(dirname "$INSTALLED")/__pycache__
rm -f "$CACHE"/xray.cpython*.pyc 2>/dev/null && echo "  pyc cache cleared"

# Restart panel to pick up the change
if systemctl is-active --quiet hiddify-panel; then
    systemctl restart hiddify-panel
    sleep 3
    systemctl is-active hiddify-panel && echo "  panel restarted OK"
fi

echo "Done. Verify: curl <sub_url> | base64 -d | grep -v '^[a-z]*://' | grep -v '^#'"
