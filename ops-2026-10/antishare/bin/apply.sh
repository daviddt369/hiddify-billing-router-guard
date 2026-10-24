#!/bin/bash
# Anti-share: (1) CDN edge addresses are not clients (no counting, no ban)  (2) retention for anti_share_event.   RU, root.
# Backup first; the next anti-share cycle (every 2 minutes) uses the new code by itself, no restart.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
P=/opt/hiddify-manager/.venv313/lib/python3.13/site-packages/hiddifypanel/antishare
BK=/root/.claude-backups/antishare-$(date +%Y%m%d-%H%M%S)
PYV=/opt/hiddify-manager/.venv313/bin/python
install -d -m 700 /root/.claude-backups "$BK"
ok()  { echo "  OK    $*"; }
bad() { echo "  FAIL  $*"; }

echo "== 0. before"
echo "   runner.py sha: $(sha256sum $P/runner.py | cut -c1-16) (expected 2277e7f7c47c342d)"
[ "$(sha256sum $P/runner.py | cut -c1-16)" = 2277e7f7c47c342d ] || [ "$(sha256sum $P/runner.py | cut -c1-16)" = "$(sha256sum $HERE/payload/antishare/runner.py | cut -c1-16)" ] || { echo "runner.py is neither the known original nor the new one: stop"; exit 2; }
mysql -N -B hiddifypanel -e "select 'rows', count(*), 'MB', round((data_length+index_length)/1024/1024,1) from information_schema.tables t, (select count(*) from anti_share_event) c where table_schema='hiddifypanel' and table_name='anti_share_event'" 2>/dev/null | head -1
mysql -N -B hiddifypanel -e "select state, count(*) from anti_share_state group by state" | paste -sd" "

echo "== 1. backup: the code (and the whole event table, compressed)"
cp -a $P/runner.py "$BK/runner.py"
mysqldump --single-transaction --no-tablespaces hiddifypanel anti_share_event 2>/dev/null | gzip > "$BK/anti_share_event.sql.gz"
ls -la "$BK" | awk '{print "   " $5, $9}' | tail -3

echo "== 2. files"
install -m 644 -o root -g root "$HERE/payload/antishare/shared.py" "$HERE/payload/antishare/prune.py" $P/
cp --no-preserve=mode,ownership "$HERE/payload/antishare/runner.py" $P/runner.py.new && chmod --reference=$P/runner.py $P/runner.py.new && chown --reference=$P/runner.py $P/runner.py.new && mv -f $P/runner.py.new $P/runner.py
install -d -m 755 /etc/hiddify-panel
install -m 644 -o root -g root "$HERE/payload/gcore-public-ip-list.json" /etc/hiddify-panel/antishare-shared-ips.json
rm -f $P/__pycache__/runner.*.pyc
$PYV -m py_compile $P/runner.py $P/shared.py $P/prune.py && ok "py_compile" || { bad "py_compile"; cp -a "$BK/runner.py" $P/runner.py; exit 1; }
install -m 644 "$HERE/units/hiddify-anti-share-prune.service" "$HERE/units/hiddify-anti-share-prune.timer" /etc/systemd/system/
systemctl daemon-reload

echo "== 3. the new cycle runs by itself: wait for it and read the result"
sleep 150
journalctl -u hiddify-anti-share --since "4 minutes ago" --no-pager 2>/dev/null | grep -i -E "cycle done|failed|Traceback" | tail -3 | cut -c1-200
mysql -N -B hiddifypanel -e "select count(*) from anti_share_event where event_type='cycle_summary' and payload like '%shared_ips_ignored%' and created_at > utc_timestamp() - interval 5 minute" | sed 's/^/   cycle rows with the new field in the last 5 min: /'
mysql -N -B hiddifypanel -e "select user_id, json_extract(payload,'$.current_ip_count'), json_extract(payload,'$.shared_ips_ignored') from anti_share_event where event_type='cycle_summary' and created_at > utc_timestamp() - interval 5 minute and json_extract(payload,'$.shared_ips_ignored') > 0 order by id desc limit 6" | sed 's/^/   user, ips counted, CDN edge ignored: /'
mysql -N -B hiddifypanel -e "select state, count(*) from anti_share_state group by state" | paste -sd" " | sed 's/^/   states now: /'

echo "== 4. retention: dry run, then the real prune (14 days for cycle rows, 365 for the rest) and the table rebuild"
cd /opt/hiddify-manager/hiddify-panel && runuser -u hiddify-panel -- $PYV -m hiddifypanel.antishare.prune --dry-run 2>&1 | tail -1
runuser -u hiddify-panel -- $PYV -m hiddifypanel.antishare.prune 2>&1 | tail -1
mysql -e "optimize table hiddifypanel.anti_share_event" 2>&1 | tail -2
mysql -N -B hiddifypanel -e "select 'after: rows', (select count(*) from anti_share_event), 'MB', round((data_length+index_length)/1024/1024,1) from information_schema.tables where table_schema='hiddifypanel' and table_name='anti_share_event'"
mysql -N -B hiddifypanel -e "select event_type, count(*) from anti_share_event group by event_type" | paste -sd" "
systemctl enable --now hiddify-anti-share-prune.timer
systemctl list-timers hiddify-anti-share-prune.timer --no-pager | head -3
echo "Rollback: cp -a $BK/runner.py $P/runner.py; rm -f $P/shared.py /etc/hiddify-panel/antishare-shared-ips.json; (rows: zcat $BK/anti_share_event.sql.gz | mysql hiddifypanel)"
