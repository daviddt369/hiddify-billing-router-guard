#!/bin/bash
# Anti-share: (1) a CDN edge takes one slot of the limit but is never banned  (2) "clear all statistics" button + CLI, (3) the first reset now.   RU, root.
# Backup (files + a compressed dump of the three tables) first; compile/import/template/handler checks BEFORE the panel restart; auto-revert on failure.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
PK=/opt/hiddify-manager/.venv313/lib/python3.13/site-packages/hiddifypanel
PYV=/opt/hiddify-manager/.venv313/bin/python
BK=/root/.claude-backups/antishare-reset-$(date +%Y%m%d-%H%M%S)
install -d -m 700 "$BK"
FILES=("antishare/runner.py" "panel/admin/AntiShareAdmin.py" "templates/anti-share-settings.html")
SRC=("antishare/runner.py" "panel_admin/AntiShareAdmin.py" "templates/anti-share-settings.html")
DO_RESET=${DO_RESET:-1}

echo "== 0. before: the files must be the state left by the previous step"
grep -q "all_networks(" $PK/antishare/runner.py && ! grep -q real_allowed $PK/antishare/runner.py || { echo "runner.py is not the expected previous version (or already new): stop"; exit 2; }
grep -q "_handle_shared_action" $PK/panel/admin/AntiShareAdmin.py && ! grep -q _handle_reset_all $PK/panel/admin/AntiShareAdmin.py || { echo "AntiShareAdmin.py is not the expected previous version (or already new): stop"; exit 2; }
for t in anti_share_event anti_share_ip_profile anti_share_state; do echo "  $t: $(mysql -N -B hiddifypanel -e "select count(*) from $t") rows"; done
echo "  blocked/warned now: $(mysql -N -B hiddifypanel -e "select state, count(*) from anti_share_state group by state" | paste -sd' ')"

echo "== 1. backup -> $BK"
for f in "${FILES[@]}"; do mkdir -p "$BK/$(dirname $f)"; cp -a "$PK/$f" "$BK/$f"; done
mysqldump --single-transaction --no-tablespaces hiddifypanel anti_share_event anti_share_ip_profile anti_share_state 2>/dev/null | gzip > "$BK/anti_share_tables.sql.gz"
ls -la "$BK/anti_share_tables.sql.gz" | awk '{print "  dump: " $5 " bytes"}'
revert() { echo "== REVERT"; for f in "${FILES[@]}"; do cp -a "$BK/$f" "$PK/$f"; done; rm -f $PK/antishare/reset.py $PK/antishare/__pycache__/runner.*.pyc $PK/antishare/__pycache__/reset.*.pyc; systemctl restart hiddify-panel; }

echo "== 2. files (owner and mode kept; reset.py new, root:root 644)"
for i in "${!FILES[@]}"; do
  f=${FILES[$i]}; s=${SRC[$i]}
  cp --no-preserve=mode,ownership "$HERE/payload/$s" "$PK/$f.new" && chmod --reference="$PK/$f" "$PK/$f.new" && chown --reference="$PK/$f" "$PK/$f.new" && mv -f "$PK/$f.new" "$PK/$f"
done
install -m 644 -o root -g root "$HERE/payload/antishare/reset.py" $PK/antishare/reset.py
rm -f $PK/antishare/__pycache__/runner.*.pyc $PK/antishare/__pycache__/reset.*.pyc

echo "== 3. checks BEFORE the restart: compile, import, template, the handler refuses a wrong confirmation word"
$PYV -m py_compile $PK/antishare/runner.py $PK/antishare/reset.py $PK/panel/admin/AntiShareAdmin.py && echo "  OK    py_compile" || { revert; exit 1; }
( cd /opt/hiddify-manager/hiddify-panel && $PYV - <<'PY'
import os
os.environ.setdefault("HIDDIFY_CFG_PATH", "/opt/hiddify-manager/hiddify-panel/app.cfg")
os.environ.setdefault("HIDDIFY_CONFIG_PATH", "/opt/hiddify-manager/")
from hiddifypanel import create_app
app = create_app(app_mode="cli")
with app.app_context():
    import importlib.util, jinja2
    PKG = "/opt/hiddify-manager/.venv313/lib/python3.13/site-packages/hiddifypanel"
    spec = importlib.util.spec_from_file_location("antishare_admin_under_check", PKG + "/panel/admin/AntiShareAdmin.py")
    A = importlib.util.module_from_spec(spec); spec.loader.exec_module(A)
    jinja2.Environment().parse(open(PKG + "/templates/anti-share-settings.html", encoding="utf-8").read())
    from hiddifypanel.antishare import runner, reset                      # noqa: F401
    from hiddifypanel.database import db
    from sqlalchemy import text
    n0 = db.session.execute(text("select count(*) from anti_share_event")).scalar()
    view = A.AntiShareAdmin()
    with app.test_request_context("/anti-share-admin/", method="POST", data={"action": "reset_all", "reset_confirm": "нет"}):
        view._handle_reset_all()
        from flask import get_flashed_messages
        msg = get_flashed_messages(with_categories=True)
    n1 = db.session.execute(text("select count(*) from anti_share_event")).scalar()
    print("  OK    admin view imported, template parsed, reset module imported")
    print("  wrong word :", msg, "| events before/after:", n0, n1)
    assert n1 >= n0 and msg and msg[0][0] == "danger"
PY
) 2>&1 | grep -v "INFO\|WARNING" || { revert; exit 1; }

echo "== 4. restart the panel (admin pages are loaded at start) and wait"
systemctl restart hiddify-panel
for i in $(seq 1 60); do curl -s -o /dev/null -m 2 http://127.0.0.1:9000/ && break; sleep 1; done
sleep 3
echo "  hiddify-panel: $(systemctl is-active hiddify-panel)"

if [ "$DO_RESET" = 1 ]; then
  echo "== 5. the first reset now (as the panel user: the nft helper is allowed for it)"
  ( cd /opt/hiddify-manager/hiddify-panel && runuser -u hiddify-panel -- $PYV -m hiddifypanel.antishare.reset --yes 2>&1 | grep -v "INFO\|WARNING" | tail -3 | cut -c1-400 )
  for t in anti_share_event anti_share_ip_profile anti_share_state; do echo "  $t: $(mysql -N -B hiddifypanel -e "select count(*) from $t") rows"; done
  mysql -N -B hiddifypanel -e "show tables like 'anti_share_bak_%'" | sed 's/^/  copy table: /'
fi

echo "== 6. the next cycle runs with the new code (wait for it)"
sleep 140
journalctl -u hiddify-anti-share --since "4 minutes ago" --no-pager 2>/dev/null | grep -i -E "cycle done|failed|Traceback" | tail -2 | cut -c1-200
mysql -N -B hiddifypanel -e "select state, count(*) from anti_share_state group by state" | paste -sd" " | sed 's/^/  states: /'
mysql -N -B hiddifypanel -e "select count(*) from anti_share_event where event_type='cycle_summary'" | sed 's/^/  new cycle rows after the reset: /'
echo "Rollback: copy back the three files from $BK (same relative paths under $PK), rm $PK/antishare/reset.py, systemctl restart hiddify-panel.  Data: zcat $BK/anti_share_tables.sql.gz | mysql hiddifypanel   (or the anti_share_bak_* tables)"
