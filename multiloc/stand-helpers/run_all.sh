#!/bin/bash
# Full verification run INSIDE a stand started from the GOLD image (pristine prod-like baseline, multi-location feature absent).
# usage: run_all.sh <run-name> [default|random:<seed>]
# Everything goes to /work/out/runs/<run-name>/ ; results.json is the machine-readable verdict.
set -uo pipefail
RUN=${1:?run name}; ORDER=${2:-default}; MODE=${3:-full}      # MODE: full | fast
OUT=/work/out/runs/$RUN; rm -rf "$OUT"; mkdir -p "$OUT"
exec > >(tee -a "$OUT/run_all.log") 2>&1
log(){ echo "[$(date +%T)] $*"; }
SECONDS=0     # monotonic: the WSL wall clock can step backwards
PY=/opt/hiddify-manager/.venv313/bin/python
INST="python3 /work/src/multiloc_installer.py"
UNITS="mariadb hiddify-redis hiddify-panel hiddify-panel-background-tasks hiddify-nginx hiddify-haproxy hiddify-xray hiddify-singbox xray-router stand-lo stand-whoami stand-dns stand-exit-a stand-exit-b stand-shim-a stand-shim-b"

finish_gate_failed(){
  python3 - "$OUT" "$RUN" "$1" <<'PYEOF'
import json, sys
out, run, why = sys.argv[1:4]
json.dump({"run": run, "gate_ok": False, "gate_problem": why, "all_ok": False}, open(out + "/results.json", "w"), indent=1)
print("GATE FAILED:", why)
PYEOF
  exit 3
}

log "== environment"
{ echo "kernel $(uname -r), cpus $(nproc), mem $(free -m | awk '/Mem:/{print $2" MB"}')"
  /opt/hiddify-manager/xray/bin/xray version | head -1; /usr/sbin/haproxy -v | head -1; /usr/sbin/nginx -v 2>&1
  mariadb --version; redis-server --version | cut -c1-70; $PY -VV; } | tee "$OUT/versions.txt"

log "== wait for the stack (systemd started it from the image)"
for u in $UNITS; do
  for i in $(seq 1 180); do systemctl is-active --quiet "$u" && break; sleep 1; done
  printf '%-34s %s\n' "$u" "$(systemctl is-active "$u")"
done | tee "$OUT/services-before.txt"
for i in $(seq 1 120); do curl -s -o /dev/null --max-time 2 http://127.0.0.1:9000/ && break; sleep 1; done
sleep 10
grep -qv ' active$' "$OUT/services-before.txt" && finish_gate_failed "some service did not start from the image: $(grep -v ' active$' "$OUT/services-before.txt" | tr '\n' ';')"

log "== gate: the baseline really is pristine (feature absent, every touched prod file equals its recorded hash)"
bash /work/tools/check_pristine.sh > "$OUT/gate-pristine.txt" 2>&1 || finish_gate_failed "prod files are not pristine: $(grep MODIFIED "$OUT/gate-pristine.txt" | tr '\n' ';')"
[ -z "$(mysql -N hiddifypanel -e "show tables like 'commercial_routing_location%'" 2>&1)" ] || finish_gate_failed "feature tables already exist in the baseline"
$INST status > "$OUT/gate-status.txt" 2>&1
[ "$(grep -c '^applied' "$OUT/gate-status.txt")" = "0" ] || finish_gate_failed "installer reports files of the feature already in place"
[ ! -e /opt/hiddify-manager/multiloc-installer-backups ] || finish_gate_failed "installer backups left in the baseline"
{ mysql -N hiddifypanel -e "select 'db_version', value from str_config where \`key\`='db_version' limit 1" 2>&1 | head -1
  mysql -N hiddifypanel -e "select 'users', count(*) from user" 2>&1 | head -1
  mysql -N hiddifypanel -e "select 'filler_rules', count(*) from commercial_routing_custom_rule where comment='stand-filler'" 2>&1 | head -1; } | tee "$OUT/gate-db.txt"
[ "$(awk '$1=="filler_rules"{print $2}' "$OUT/gate-db.txt")" = "0" ] || finish_gate_failed "performance filler rules are still in the baseline"
echo "gate ok" | tee "$OUT/gate.txt"

log "== baseline traffic through the real chain (legacy profiles, feature absent)"
( cd /work/tests && $PY -W ignore - <<'PYEOF'
import sys
sys.path.insert(0, '/work/tests')
from stand_lib import *
router_ready(120)
L = dict(links('active'))
r = {k: via(L[k]) for k in ('RU_GLOBAL tls WS direct vless', 'RU_GLOBAL tls_h2 xhttp direct vless dl=h2')}
r['ru_direct'] = via(L['RU_GLOBAL tls WS direct vless'], RU_DOMAIN)
print('BASELINE', r)
sys.exit(0 if all(v in ('exit-a', 'exit-b') for k, v in r.items() if k != 'ru_direct') and r['ru_direct'] == 'direct' else 1)
PYEOF
) 2>&1 | tee "$OUT/baseline-traffic.txt"
BASE_RC=${PIPESTATUS[0]}

# MODE=fast (~7 min): golden captures only for the two users that matter (tester + active), a subset of the suites. MODE=full (default): everything.
gcap(){ if [ "$MODE" = fast ]; then python3 -c "import sys; sys.path.insert(0,'/work/tools'); import golden; golden.capture('$1', only_users=['u-tester','u-active'])" | tail -1
        else python3 /work/tools/golden.py capture "$1" | tail -1; fi; }
gcmp(){ if [ "$MODE" = fast ]; then python3 -c "import sys; sys.path.insert(0,'/work/tools'); import golden; golden.compare('$1','$2',users=['u-tester','u-active'])" 2>&1 | tail -4
        else python3 /work/tools/golden.py compare "$1" "$2" 2>&1 | tail -4; fi; }
log "== golden capture + byte snapshot of the pristine state (mode: $MODE)"
bash /work/tools/stand_flush_cache.sh | tail -1
gcap "$OUT/golden-pre"
[ "$MODE" = fast ] || python3 /work/tools/tree_snapshot.py take "$OUT/snap-pre.json" | tail -1

log "== installer: preflight, apply, second apply (must be a no-op)"
$INST preflight 2>&1 | tail -4 | tee "$OUT/preflight.txt"
$INST apply 2>&1 | tee "$OUT/apply.txt" | tail -6
$INST apply 2>&1 | tee "$OUT/apply-again.txt" | tail -2

log "== installed but no country enabled: every subscription must be IDENTICAL to the pristine capture"
bash /work/tools/stand_flush_cache.sh | tail -1
gcap "$OUT/golden-installed-off"
gcmp "$OUT/golden-pre" "$OUT/golden-installed-off" | tee "$OUT/golden-installed-off-compare.txt"

log "== canonical test state (Germany=exit A, USA=exit B, AUTO, one tester) + router apply"
( cd /work/tests && $PY -W ignore - <<'PYEOF'
import sys
sys.path.insert(0, '/work/tests')
from stand_lib import *
reset_locations_to_stand_default()
apply_router()
print('canonical state ready')
PYEOF
) 2>&1 | tail -2 | tee "$OUT/canonical.txt"

if [ "$MODE" = fast ]; then
  MODS="test_01_unit test_02_subscription test_05_migration test_06_installer test_08_render_reboot test_10_groups_wire test_11_bot_links test_12_blocks test_13_apply test_14_unified_apply test_15_full_apply test_16_roscom_field"
else
  MODS="test_01_unit test_02_subscription test_03_dataplane test_04_lifecycle test_05_migration test_06_installer test_07_ui test_08_render_reboot test_09_router_apply test_10_groups_wire test_11_bot_links test_12_blocks test_13_apply test_14_unified_apply test_15_full_apply test_16_roscom_field"
fi
# fast mode runs only the most valuable tests of the two big modules (the full run covers everything)
targets_of(){
  case "$MODE:$1" in
    fast:test_02_subscription) echo "test_02_subscription.TestUserPanelList test_02_subscription.TestHappLink test_02_subscription.TestRoscomRoutingInTheJson";;
    fast:test_06_installer) echo "test_06_installer.TestInstaller.test_apply_is_idempotent_second_run_is_a_noop_with_no_restarts test_06_installer.TestInstaller.test_rollback_restores_the_exact_pre_install_state_byte_for_byte test_06_installer.TestInstaller.test_a_failure_in_the_middle_of_the_table_creation_rolls_back_the_tables_too test_06_installer.TestInstaller.test_a_missing_stock_file_is_refused_by_preflight_and_nothing_changes test_06_installer.TestInstaller.test_a_rollback_that_could_not_finish_stays_open_and_a_plain_rollback_completes_it test_06_installer.TestInstaller.test_a_crash_right_after_a_file_was_replaced_is_still_rolled_back test_06_installer.TestInstaller.test_a_failing_database_dump_stops_the_install_before_anything_changes test_06_installer.TestInstaller.test_two_installer_runs_cannot_overlap test_06_installer.TestInstaller.test_an_unreadable_run_state_is_reported_not_crashed_on";;
    *) echo "$1";;
  esac
}
if [[ "$ORDER" == random:* ]]; then
  SEED=${ORDER#random:}
  MODS=$(python3 -c "import random; m='$MODS'.split(); random.Random($SEED).shuffle(m); print(' '.join(m))")
fi
log "== suites, in this order: $MODS" | tee "$OUT/order.txt"
: > "$OUT/suites.txt"
for m in $MODS; do
  S=$SECONDS
  ( cd /work/tests && $PY -W ignore -m unittest -v $(targets_of "$m") ) > "$OUT/$m.log" 2>&1; rc=$?
  E=$(( SECONDS - S ))
  printf '%s rc=%s %ss :: %s\n' "$m" "$rc" "$E" "$(grep -E '^Ran|^OK|^FAILED' "$OUT/$m.log" | tr '\n' ' ')" | tee -a "$OUT/suites.txt"
  grep -E '^(ERROR|FAIL):' "$OUT/$m.log" | tee -a "$OUT/suites.txt"
  # a suite that failed halfway may leave the feature removed: put it back so the next suite measures itself, not the leftovers
  $INST status 2>&1 | grep -q '^OPEN' && $INST rollback > /dev/null 2>&1      # an unfinished run would make apply refuse
  $INST apply > /dev/null 2>&1
done

log "== behaviour smoke check of the owner's rule (usual link never changes, /happ/ = three buttons, bot, panel list)"
S=$SECONDS
python3 /work/tools/smoke_new_rule.py > "$OUT/test_zz_smoke_rules.log" 2>&1; rc=$?
E=$(( SECONDS - S ))
N=$(grep -c '^OK ' "$OUT/test_zz_smoke_rules.log"); F=$(grep -c '^FAIL ' "$OUT/test_zz_smoke_rules.log")
if [ "$rc" = 0 ] && [ "$F" = 0 ]; then VERDICT=OK; else VERDICT=FAILED; fi
printf '%s rc=%s %ss :: Ran %s tests in %ss %s\n' "test_zz_smoke_rules" "$rc" "$E" "$((N + F))" "$E" "$VERDICT" | tee -a "$OUT/suites.txt"
grep '^FAIL ' "$OUT/test_zz_smoke_rules.log" | sed 's/^/FAIL: /' | tee -a "$OUT/suites.txt"
$INST status 2>&1 | grep -q '^OPEN' && $INST rollback > /dev/null 2>&1
$INST apply > /dev/null 2>&1

log "== final state"
$INST verify 2>&1 | tail -1 | tee "$OUT/final-verify.txt"
$INST status 2>&1 | awk '{print $1}' | sort | uniq -c | tee "$OUT/final-status.txt"
systemctl --failed --no-pager --no-legend | tee "$OUT/failed-units.txt"

EXPECT=$(( $(echo $MODS | wc -w) + 1 ))      # the modules + the behaviour smoke check
python3 - "$OUT" "$RUN" "$ORDER" "$SECONDS" "$BASE_RC" "$MODE" "$EXPECT" <<'PYEOF'
import json, re, sys
out, run, order, seconds, base_rc, mode, expect = sys.argv[1:8]
res = {"run": run, "mode": mode, "order": order, "gate_ok": True, "baseline_traffic_ok": base_rc == "0", "seconds": int(seconds), "suites": {}}
for line in open(out + "/suites.txt", encoding="utf-8"):
    m = re.match(r"(test_\w+) rc=(\d+) (-?\d+)s :: (.*)", line)
    if m:
        ran = re.search(r"Ran (\d+) tests", m.group(4))
        res["suites"][m.group(1)] = {"rc": int(m.group(2)), "seconds": int(m.group(3)), "tests": int(ran.group(1)) if ran else None,
                                     "ok": m.group(2) == "0" and "OK" in m.group(4)}
res["suites_count"] = len(res["suites"])
res["total_tests"] = sum(s["tests"] or 0 for s in res["suites"].values())
res["golden_installed_off_identical"] = "IDENTICAL" in open(out + "/golden-installed-off-compare.txt").read()
res["reapply_noop"] = "already applied" in open(out + "/apply-again.txt").read()
res["installer_verify_ok"] = "problems: none" in open(out + "/final-verify.txt").read()
res["failed_units"] = [l.split()[1] if len(l.split()) > 1 else l for l in open(out + "/failed-units.txt").read().splitlines() if l.strip()]
res["all_ok"] = bool(res["suites"]) and len(res["suites"]) == int(expect) and all(s["ok"] for s in res["suites"].values()) \
    and res["baseline_traffic_ok"] and res["golden_installed_off_identical"] and res["reapply_noop"] and res["installer_verify_ok"]
json.dump(res, open(out + "/results.json", "w"), indent=1)
print(json.dumps(res))
PYEOF
log "== run $RUN finished"
