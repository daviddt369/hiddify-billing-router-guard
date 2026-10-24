#!/bin/bash
# Anti-share: white list of shared (CDN) addresses in the admin page. RU, root. Backup first; compile/import/template checks BEFORE the panel restart; auto-revert on failure.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
PK=/opt/hiddify-manager/.venv313/lib/python3.13/site-packages/hiddifypanel
PYV=/opt/hiddify-manager/.venv313/bin/python
BK=/root/.claude-backups/antishare-ui-$(date +%Y%m%d-%H%M%S)
install -d -m 700 "$BK"
FILES=("antishare/models.py" "antishare/shared.py" "antishare/runner.py" "panel/admin/AntiShareAdmin.py" "templates/anti-share-settings.html")
SRC=("antishare/models.py" "antishare/shared.py" "antishare/runner.py" "panel_admin/AntiShareAdmin.py" "templates/anti-share-settings.html")

echo "== 1. backup -> $BK"
for f in "${FILES[@]}"; do mkdir -p "$BK/$(dirname $f)"; cp -a "$PK/$f" "$BK/$f"; done
revert() { echo "== REVERT"; for f in "${FILES[@]}"; do cp -a "$BK/$f" "$PK/$f"; done; rm -f $PK/antishare/__pycache__/*.pyc; systemctl restart hiddify-panel; }

echo "== 2. files (owner and mode kept)"
for i in "${!FILES[@]}"; do
  f=${FILES[$i]}; s=${SRC[$i]}
  cp --no-preserve=mode,ownership "$HERE/payload/$s" "$PK/$f.new" && chmod --reference="$PK/$f" "$PK/$f.new" && chown --reference="$PK/$f" "$PK/$f.new" && mv -f "$PK/$f.new" "$PK/$f"
done
rm -f $PK/antishare/__pycache__/*.pyc

echo "== 3. checks BEFORE the restart: compile, import, template syntax"
$PYV -m py_compile $PK/antishare/models.py $PK/antishare/shared.py $PK/antishare/runner.py $PK/panel/admin/AntiShareAdmin.py && echo "  OK    py_compile" || { revert; exit 1; }
( cd /opt/hiddify-manager/hiddify-panel && $PYV - <<'PY'
import os
os.environ.setdefault("HIDDIFY_CFG_PATH", "/opt/hiddify-manager/hiddify-panel/app.cfg")
os.environ.setdefault("HIDDIFY_CONFIG_PATH", "/opt/hiddify-manager/")
from hiddifypanel import create_app
app = create_app(app_mode="cli")
with app.app_context():
    import importlib.util, jinja2     # the admin PACKAGE needs the web app (babel); the single file does not
    spec = importlib.util.spec_from_file_location("antishare_admin_under_check", "/opt/hiddify-manager/.venv313/lib/python3.13/site-packages/hiddifypanel/panel/admin/AntiShareAdmin.py")
    A = importlib.util.module_from_spec(spec); spec.loader.exec_module(A)
    jinja2.Environment().parse(open("/opt/hiddify-manager/.venv313/lib/python3.13/site-packages/hiddifypanel/templates/anti-share-settings.html", encoding="utf-8").read())
    print("  OK    admin view imported, template parsed")
    from hiddifypanel.antishare.runner import ensure_tables
    ensure_tables()
    from hiddifypanel.antishare import shared
    from hiddifypanel.antishare.models import AntiShareSharedIP
    from hiddifypanel.database import db
    view = A.AntiShareAdmin()
    # add / duplicate / too wide / garbage / remove, through the real handler
    def act(**form):
        with app.test_request_context("/anti-share-admin/", method="POST", data=form):
            view._handle_shared_action(form["action"])
            from flask import get_flashed_messages
            return get_flashed_messages(with_categories=True)
    print("  add        :", act(action="shared_add", shared_cidr="192.0.2.0/24", shared_note="lab check"))
    print("  in lists   :", any(str(n) == "192.0.2.0/24" for n in shared.all_networks()), "| file list:", len(shared.load_networks()))
    print("  duplicate  :", act(action="shared_add", shared_cidr="192.0.2.77/24"))
    print("  too wide   :", act(action="shared_add", shared_cidr="10.0.0.0/8"))
    print("  garbage    :", act(action="shared_add", shared_cidr="not-an-ip"))
    rid = AntiShareSharedIP.query.filter_by(cidr="192.0.2.0/24").first().id
    print("  remove     :", act(action="shared_remove", shared_id=str(rid)))
    print("  after      :", any(str(n) == "192.0.2.0/24" for n in shared.all_networks()), "| rows:", AntiShareSharedIP.query.count())
PY
) 2>&1 | grep -v "INFO\|WARNING" || { revert; exit 1; }

echo "== 4. restart the panel (the admin pages are loaded at start) and wait"
systemctl restart hiddify-panel
for i in $(seq 1 60); do curl -s -o /dev/null -m 2 http://127.0.0.1:9000/ && break; sleep 1; done
sleep 3
echo "  hiddify-panel: $(systemctl is-active hiddify-panel)"
echo "== 5. the next anti-share cycle uses both lists (wait for it)"
sleep 130
journalctl -u hiddify-anti-share --since "4 minutes ago" --no-pager 2>/dev/null | grep -i -E "cycle done|failed|Traceback" | tail -2 | cut -c1-200
mysql -N -B hiddifypanel -e "select state, count(*) from anti_share_state group by state" | paste -sd" " | sed 's/^/  states: /'
mysql -N -B hiddifypanel -e "select count(*) from anti_share_event where event_type='cycle_summary' and created_at > utc_timestamp() - interval 4 minute and json_extract(payload,'$.shared_ips_ignored') > 0" | sed 's/^/  cycle rows that ignored a CDN edge in the last 4 min: /'
echo "Rollback: for each file in $BK copy it back to $PK (same relative path), then systemctl restart hiddify-panel (the table anti_share_shared_ip may stay, it is harmless)"
