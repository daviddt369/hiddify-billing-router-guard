#!/bin/bash
# Install the personal cabinet into a panel (stand or node): backup, copy files, patch user.py (one place), compile, restart the panel.
# usage: install_cabinet.sh <source dir with cabinet.py cabinet.html> [--no-restart]
set -Eeuo pipefail
SRC="${1:?source dir}"; RESTART=1; [ "${2:-}" = "--no-restart" ] && RESTART=0
P="$(ls -d /opt/hiddify-manager/.venv313/lib/python3.*/site-packages/hiddifypanel | head -1)"
PY=/opt/hiddify-manager/.venv313/bin/python
TS="$(date +%Y%m%d-%H%M%S)"; B="/root/cabinet-backup-$TS"
OWNER="$(stat -c %U:%G "$P/panel/user/user.py")"
mkdir -m 700 "$B"
cp -a "$P/panel/user/user.py" "$B/user.py"
[ -f "$P/panel/user/cabinet.py" ] && cp -a "$P/panel/user/cabinet.py" "$B/cabinet.py" || true
[ -f "$P/panel/user/templates/cabinet/cabinet.html" ] && cp -a "$P/panel/user/templates/cabinet/cabinet.html" "$B/cabinet.html" || true
echo "backup: $B"

install -d -m 755 -o "${OWNER%%:*}" -g "${OWNER##*:}" "$P/panel/user/templates/cabinet"
install -m 644 -o "${OWNER%%:*}" -g "${OWNER##*:}" "$SRC/cabinet.py" "$P/panel/user/cabinet.py"
install -m 644 -o "${OWNER%%:*}" -g "${OWNER##*:}" "$SRC/cabinet.html" "$P/panel/user/templates/cabinet/cabinet.html"
install -d -m 755 /opt/hiddify-manager/cabinet
if [ ! -f /opt/hiddify-manager/cabinet/cabinet.json ]; then
    printf '{\n  "enabled": false,\n  "yookassa_shop_id": "0",\n  "support_url": "",\n  "brand": ""\n}\n' > /opt/hiddify-manager/cabinet/cabinet.json
    echo "created /opt/hiddify-manager/cabinet/cabinet.json (enabled=false: the cabinet is shown only with ?cabinet=1)"
fi

# the one change in the stock view: new() renders the cabinet when wanted, and falls back to the stock page on ANY error
"$PY" - "$P/panel/user/user.py" <<'PYEOF'
import sys
p = sys.argv[1]
s = open(p, encoding="utf-8").read()
if "panel.user import cabinet" in s:
    print("user.py already patched")
    sys.exit(0)
old = """        c = get_common_data(g.account.uuid, mode="new")
        user_agent = user_agents.parse(request.user_agent.string)
        # return render_template('home/multi.html', **c, ua=user_agent)
        return render_template('new.html', **c, ua=user_agent)"""
new = """        c = get_common_data(g.account.uuid, mode="new")
        user_agent = user_agents.parse(request.user_agent.string)
        # personal cabinet (panel/user/cabinet.py); the stock page below is the fallback for ?classic=1 and for any error
        try:
            from hiddifypanel.panel.user import cabinet
            if cabinet.wanted():
                return cabinet.render(c, user_agent)
        except Exception:
            import traceback
            traceback.print_exc()
        # return render_template('home/multi.html', **c, ua=user_agent)
        return render_template('new.html', **c, ua=user_agent)"""
assert old in s, "the anchor of new() was not found: the panel code differs from the expected one"
open(p, "w", encoding="utf-8", newline="").write(s.replace(old, new, 1))
print("user.py patched")
PYEOF
chown "$OWNER" "$P/panel/user/user.py"
"$PY" -m py_compile "$P/panel/user/user.py" "$P/panel/user/cabinet.py" && echo "compiled"
if [ "$RESTART" = 1 ]; then systemctl restart hiddify-panel; for _ in $(seq 1 60); do curl -s -o /dev/null -m 2 http://127.0.0.1:9000/ && break; sleep 1; done; echo "panel: $(systemctl is-active hiddify-panel)"; fi
echo "rollback: cp -a $B/user.py $P/panel/user/user.py (and remove cabinet.py / templates/cabinet), then restart hiddify-panel"
