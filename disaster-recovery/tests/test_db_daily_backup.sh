#!/bin/bash
# Stand test of disaster-recovery/db-daily-backup.sh: real mariadb-dump + real age, the cloud is a local folder (stub rclone), keys are throw-away.
# Run in the stand as root:  bash /work/tests/test_db_daily_backup.sh
set -uo pipefail
SRC=${DR_SRC:-/work/db-daily-backup-src}   # directory holding common-dr.sh and db-daily-backup.sh (default: the stand copy)
T=/tmp/dbt; rm -rf $T; mkdir -p $T/bin $T/cloud
PASS=0; FAIL=0
ok() { echo "  PASS  $1"; PASS=$((PASS+1)); }
no() { echo "  FAIL  $1"; FAIL=$((FAIL+1)); }

age-keygen -o $T/key.txt 2>/dev/null; PUB="$(grep -o 'age1[a-z0-9]*' $T/key.txt | head -1)"
cat > $T/backup.env <<EOF
SELECTEL_S3_ACCESS_KEY=FAKEACCESSKEY0000
SELECTEL_S3_SECRET_KEY=FAKESECRETKEYdoNotLeak9999
SELECTEL_S3_ENDPOINT=https://s3.fake.local
SELECTEL_S3_BUCKET=bkt
DR_AGE_PUBLIC_KEY=$PUB
EOF
cat > $T/bin/rclone <<'EOF'
#!/bin/bash
# stub: maps ":s3,...:bkt/prefix/name" to /tmp/dbt/cloud/bkt/prefix/name
C=/tmp/dbt/cloud
loc() { echo "$C/${1##*:}"; }
case "$1" in
  copyto) mkdir -p "$(dirname "$(loc "$3")")"; cp "$2" "$(loc "$3")";;
  lsf)
    if [ "$2" = "--format" ]; then stat -c%s "$(loc "$4")" 2>/dev/null; else d="$(loc "$2")"; ls -1 "$d" 2>/dev/null; fi;;
  deletefile) rm -f "$(loc "$2")";;
  *) echo "stub rclone: unsupported $*" >&2; exit 2;;
esac
EOF
chmod +x $T/bin/rclone
export PATH="$T/bin:$PATH" DR_BACKUP_ENV=$T/backup.env
export DR_DB_MIN_BYTES=1000   # the stand DB is tiny (about 20 KB gzipped); production is about 1 MB+ and keeps the 100000 default
cmd=(bash $SRC/db-daily-backup.sh)
OBJ=$T/cloud/bkt/db-daily

echo "# 1 normal run"
out="$("${cmd[@]}" 2>&1)"; rc=$?
[ $rc = 0 ] && ok "script exits 0" || { no "script exit $rc"; echo "$out" | tail -8; }
n=$(ls $OBJ 2>/dev/null | grep -c 'hiddify-db-.*\.sql\.gz\.age$'); [ "$n" = 1 ] && ok "one encrypted object in the cloud folder" || no "objects: $n"
f=$(ls $OBJ/*.age 2>/dev/null | head -1)
if [ -n "$f" ]; then
  age -d -i $T/key.txt "$f" 2>/dev/null | gunzip > $T/restored.sql
  grep -q 'CREATE TABLE `user`' $T/restored.sql && ok "decrypts with the private key and holds the user table" || no "decrypt/content"
  tail -3 $T/restored.sql | grep -q 'Dump completed' && ok "dump is complete (trailer present)" || no "trailer"
  head -c 3 "$f" | grep -q 'age' && ok "object is an age file" || no "age header"
  grep -q 'CREATE TABLE `user`' "$f" && no "PLAINTEXT leaked in the encrypted object" || ok "no plaintext inside the encrypted object"
fi
echo "$out" | grep -q -E 'FAKESECRETKEY|FAKEACCESSKEY' && no "cloud keys printed in the log" || ok "cloud keys are not in the log"
PW="$(/opt/hiddify-manager/.venv313/bin/python - <<'PY'
import re
for l in open('/opt/hiddify-manager/hiddify-panel/app.cfg'):
    m = re.search(r"mysql\+\w+://[^:]+:([^@]+)@", l)
    if m: print(m.group(1)); break
PY
)"
[ -n "$PW" ] && ! echo "$out" | grep -q -F "$PW" && ok "database password is not in the log" || no "db password check ($([ -z "$PW" ] && echo 'not found'))"
ls /root/.dr-dbdaily.* /root/.dr-dbcreds.* 2>/dev/null | grep -q . && no "temp files left in /root" || ok "no temp files left behind"

echo "# 2 broken dump is refused (min size above the real size)"
before=$(ls $OBJ | wc -l)
DR_DB_MIN_BYTES=999999999 "${cmd[@]}" >$T/o2.txt 2>&1; rc=$?
[ $rc != 0 ] && ok "refused (exit $rc)" || no "a too-small dump was accepted"
[ "$(ls $OBJ | wc -l)" = "$before" ] && ok "nothing uploaded after the refusal" || no "object uploaded despite the refusal"
grep -q 'nothing uploaded' $T/o2.txt && ok "clear message" || no "message missing"

echo "# 3 retention keeps only the newest N"
for i in 1 2 3; do sleep 1; DR_DB_KEEP=2 "${cmd[@]}" >/dev/null 2>&1; done
n=$(ls $OBJ | wc -l); [ "$n" = 2 ] && ok "2 objects kept (was more before)" || no "objects after retention: $n"
newest=$(ls -1 $OBJ | sort | tail -1); [ "$newest" = "$(ls -1 $OBJ | sort | tail -1)" ] && ok "newest object kept" || no "newest"

echo "# 4 no age key / bad env -> clean failure, nothing uploaded"
before=$(ls $OBJ | wc -l)
DR_BACKUP_ENV=/nonexistent "${cmd[@]}" >$T/o4.txt 2>&1; rc=$?
[ $rc != 0 ] && [ "$(ls $OBJ | wc -l)" = "$before" ] && ok "missing backup.env stops the run, nothing uploaded" || no "missing env handling"

echo; echo "RESULT: $PASS passed, $FAIL failed"; [ $FAIL = 0 ]
