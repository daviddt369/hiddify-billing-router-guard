P=/opt/hiddify-manager/.venv313/lib/python3.13/site-packages/hiddifypanel
PK=opt/hiddify-manager/.venv313/lib/python3.13/site-packages/hiddifypanel
PKG=/work/stage2/CP-19-pkg; C18=/work/stage2/CP-18-pkg; C17=/work/stage2/CP-17-pkg
for f in hutils/tg_texts.py panel/admin/BusinessAdmin.py panel/admin/templates/routing-happ.html panel/commercial/telegrambot/Usage.py static/css/panel-theme.css templates/admin-layout.html templates/business-settings.html; do cp $C18/payload/$PK/$f $P/$f; done
cp $C17/payload/$PK/templates/master.html $P/templates/master.html
find $P -name "*.pyc" \( -path "*hutils*" -o -path "*telegrambot*" -o -path "*panel/admin*" \) -delete 2>/dev/null
systemctl restart hiddify-panel; for i in $(seq 1 60); do curl -s -o /dev/null -m 2 http://127.0.0.1:9000/ && break; sleep 1; done
python3 - <<PY
import hashlib, json
R = "/opt/hiddify-manager/multiloc-installer-backups/20261016-170859/STATE.json"
s = json.load(open(R))
for rel in ("hutils/tg_texts.py", "panel/admin/BusinessAdmin.py", "panel/admin/templates/routing-happ.html", "panel/commercial/telegrambot/Usage.py", "static/css/panel-theme.css", "templates/admin-layout.html", "templates/business-settings.html", "templates/master.html"):
    k = "$PK/" + rel
    s["installed_sha"][k] = hashlib.sha256(open("/" + k, "rb").read()).hexdigest()      # the record = the CP-18 state production has
json.dump(s, open(R, "w"), indent=1)
print("record reset to the CP-18 state")
PY
echo "== dry run"; python3 $PKG/multiloc_delta_deploy.py --new $PKG 2>&1 | sed "s#$PK/##" | cut -c1-200
echo "== apply"; python3 $PKG/multiloc_delta_deploy.py --new $PKG --apply --backup /root/cp19-delta-backup-$(date +%H%M%S) 2>&1 | tail -2 | cut -c1-200
echo "panel: $(systemctl is-active hiddify-panel)"
/opt/hiddify-manager/.venv313/bin/python /work/stage2/ru-check.py 2>&1 | grep -E "BAD|menu links"
cd /work/tests && /opt/hiddify-manager/.venv313/bin/python -W ignore -m unittest test_22_bot_texts 2>&1 | tail -3
bash /work/tools/run_suite.sh test_07_ui test_16_roscom_field test_11_bot_links test_19_routing_form 2>&1 | cut -c1-200 | tail -6
