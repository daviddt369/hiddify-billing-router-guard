#!/bin/bash
# Stand test (real systemd): ExecStartPost marker only after success + Restart=on-failure for a oneshot unit, as used by hiddify-db-daily-backup.service.
# Run in the stand as root: bash /work/tests/test_backup_unit_semantics.sh
set -u
PASS=0; FAIL=0
ok() { echo "  PASS  $1"; PASS=$((PASS+1)); }
no() { echo "  FAIL  $1"; FAIL=$((FAIL+1)); }
rm -f /var/lib/vpn-backup/t-*.ok
mk() { # name, exec
cat > /etc/systemd/system/t-$1.service <<EOF
[Unit]
Description=test $1
StartLimitIntervalSec=60
StartLimitBurst=3
[Service]
Type=oneshot
ExecStart=$2
ExecStartPost=/usr/bin/touch /var/lib/vpn-backup/t-$1.ok
StateDirectory=vpn-backup
StateDirectoryMode=0700
Restart=on-failure
RestartSec=3
EOF
}
mk good /bin/true
mk bad  /bin/false
systemctl daemon-reload
systemctl start --wait t-good.service
[ -e /var/lib/vpn-backup/t-good.ok ] && ok "marker appears after a successful run" || no "no marker after success"
systemctl start t-bad.service 2>/dev/null &
sleep 12
n=$(journalctl -u t-bad.service --no-pager --since "-30 sec" | grep -c 'Starting t-bad')
[ ! -e /var/lib/vpn-backup/t-bad.ok ] && ok "NO marker after a failing run" || no "marker created although the job failed"
[ "$n" -ge 2 ] && ok "failed run is retried automatically ($n starts in 12 s with RestartSec=3)" || no "no retry (starts: $n)"
[ "$(stat -c %a /var/lib/vpn-backup)" = 700 ] && ok "state dir is 0700" || no "state dir mode $(stat -c %a /var/lib/vpn-backup)"
systemctl stop t-bad.service 2>/dev/null; wait 2>/dev/null
rm -f /etc/systemd/system/t-good.service /etc/systemd/system/t-bad.service /var/lib/vpn-backup/t-*.ok; systemctl daemon-reload; systemctl reset-failed 2>/dev/null
echo "RESULT: $PASS passed, $FAIL failed"; [ $FAIL = 0 ]
