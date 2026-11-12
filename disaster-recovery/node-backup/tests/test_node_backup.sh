#!/bin/bash
# Stand test of disaster-recovery/node-backup/node-backup.sh: real tar, age, mariadb-dump; throw-away keys.
# Run in the stand as root:  DR_SRC=/work/node-backup-src bash /work/tests/test_node_backup.sh
set -uo pipefail
SRC="${DR_SRC:-/work/node-backup-src}/node-backup.sh"
T=/tmp/nbt; rm -rf $T; mkdir -p $T
PASS=0; FAIL=0
ok() { echo "  PASS  $1"; PASS=$((PASS+1)); }
no() { echo "  FAIL  $1"; FAIL=$((FAIL+1)); }
age-keygen -o $T/key.txt 2>/dev/null; grep -o 'age1[a-z0-9]*' $T/key.txt | head -1 > $T/recipient.pub
mkdir -p /etc/wireguard /opt/hiddify-manager/hiddify-panel/backup /opt/hiddify-manager/data/log
echo "FAKE-WG-KEY-PLACEHOLDER" > /etc/wireguard/test-node-backup.conf
echo "junk" > /opt/hiddify-manager/hiddify-panel/backup/should_be_excluded.json
echo "junk" > /opt/hiddify-manager/data/log/should_be_excluded.log

echo "# 1 normal run"
NODE_BACKUP_RECIPIENT=$T/recipient.pub bash $SRC > $T/out.age 2> $T/err.txt; rc=$?
[ $rc = 0 ] && ok "exit 0" || { no "exit $rc"; cat $T/err.txt | tail -5; }
head -c 21 $T/out.age | grep -q 'age-encryption.org' && ok "stdout is an age stream (no log lines mixed in)" || no "stdout is not a clean age stream"
age -d -i $T/key.txt $T/out.age 2>/dev/null | tar tz > $T/list.txt; [ -s $T/list.txt ] && ok "decrypts with the private key and unpacks" || no "decrypt/unpack"
grep -q '^MANIFEST.txt$' $T/list.txt && ok "manifest inside" || no "manifest missing"
grep -q '^node-db.sql$' $T/list.txt && ok "node database dump inside" || no "db dump missing"
grep -q 'etc/wireguard/test-node-backup.conf' $T/list.txt && ok "WireGuard config inside" || no "wireguard missing"
grep -q 'hiddify-panel/backup/should_be_excluded' $T/list.txt && no "panel backup dir not excluded" || ok "rotating panel backups excluded"
grep -q 'data/log/should_be_excluded' $T/list.txt && no "log dir not excluded" || ok "logs excluded"
age -d -i $T/key.txt $T/out.age 2>/dev/null | tar xzO node-db.sql 2>/dev/null | tail -n 2 | grep -q 'Dump completed' && ok "database dump inside is complete" || no "dump content"
grep -q 'FAKE-WG-KEY-PLACEHOLDER' $T/out.age && no "PLAINTEXT visible in the stream" || ok "no plaintext in the stream"
ls /dev/shm/vpn-node-backup.* 2>/dev/null | grep -q . && no "temp dir left in /dev/shm" || ok "no temp files left"
grep -q 'taking' $T/err.txt && ok "log goes to stderr" || no "no log on stderr"

echo "# 2 no recipient key -> clean failure, empty stdout"
NODE_BACKUP_RECIPIENT=/nonexistent bash $SRC > $T/out2.age 2> $T/err2.txt; rc=$?
[ $rc = 2 ] && [ ! -s $T/out2.age ] && ok "exit 2 and nothing on stdout" || no "missing recipient handling (rc=$rc, bytes=$(stat -c%s $T/out2.age))"

echo "# 3 a changing file during the read does not fail the backup (tar exit 1 tolerated)"
( for i in $(seq 1 200); do echo $i >> /etc/wireguard/test-node-backup.conf; done ) &
NODE_BACKUP_RECIPIENT=$T/recipient.pub bash $SRC > $T/out3.age 2> $T/err3.txt; rc=$?; wait
[ $rc = 0 ] && ok "still exit 0" || no "exit $rc with a changing file"

rm -f /etc/wireguard/test-node-backup.conf; rm -rf /opt/hiddify-manager/hiddify-panel/backup/should_be_excluded.json /opt/hiddify-manager/data/log/should_be_excluded.log
echo; echo "RESULT: $PASS passed, $FAIL failed"; [ $FAIL = 0 ]
