#!/bin/bash
# Stand test of disaster-recovery/node-backup/nodes-backup-check.sh with a stub rclone (cloud = local folder).
# Run in the stand as root: bash /work/tests/test_nodes_backup_check.sh   (sources in /work/node-backup-src: nodes-backup-check.sh + common-dr.sh)
set -uo pipefail
SRC=/work/node-backup-src
T=/tmp/nbc; rm -rf $T; mkdir -p $T/bin $T/cloud/bkt/nodes/de $T/cloud/bkt/nodes/nl $T/markers
PASS=0; FAIL=0
ok() { echo "  PASS  $1"; PASS=$((PASS+1)); }
no() { echo "  FAIL  $1"; FAIL=$((FAIL+1)); }
cat > $T/backup.env <<EOF
SELECTEL_S3_ACCESS_KEY=FAKEACCESS
SELECTEL_S3_SECRET_KEY=FAKESECRETdoNotLeak
SELECTEL_S3_ENDPOINT=https://s3.fake.local
SELECTEL_S3_BUCKET=bkt
DR_AGE_PUBLIC_KEY=age1fake
EOF
cat > $T/bin/rclone <<'EOF'
#!/bin/bash
# stub: lsf --format tp <remote>/path/ --files-only  ->  "YYYY-MM-DD HH:MM:SS;name" (UTC); STUB_FAIL=1 makes it fail
[ "${STUB_FAIL:-0}" = 1 ] && exit 1
d="/tmp/nbc/cloud/${4##*:}"; d="${d%/}"
[ -d "$d" ] || exit 0
for f in "$d"/*; do [ -f "$f" ] && printf '%s;%s\n' "$(date -d @"$(stat -c %Y "$f")" '+%Y-%m-%d %H:%M:%S')" "$(basename "$f")"; done
exit 0
EOF
chmod +x $T/bin/rclone
export PATH="$T/bin:$PATH" DR_BACKUP_ENV=$T/backup.env MARKER_DIR=$T/markers
export TZ=Europe/Moscow   # the server zone of RU: a script that forgets TZ=UTC for rclone would put the markers 3 hours into the future
mk() { touch "$T/cloud/bkt/nodes/$1/$2"; touch -d "$3" "$T/cloud/bkt/nodes/$1/$2"; }
NOW=$(date +%s)
mk de node-de-20261110-120000.tar.gz.age "@$((NOW - 50*3600))"
mk de node-de-20261112-120000.tar.gz.age "@$((NOW - 3*3600))"
mk de node-de-20261112-120000.tar.gz.age.partial "@$((NOW - 60))"     # must be ignored (not .age)
run() { bash $SRC/nodes-backup-check.sh 2>&1; }

echo "# 1 newest object sets the marker to ITS time (not 'now')"
out="$(run)"; rc=$?
[ -e $T/markers/node-de.ok ] && ok "marker for de created" || no "marker de missing"
age_h=$(( ( $(date +%s) - $(stat -c %Y $T/markers/node-de.ok) ) / 3600 ))
[ "$age_h" = 3 ] && ok "marker time = newest object time (3 h old), not the older 50 h object and not now" || no "marker age ${age_h} h"
echo "$out" | grep -q 'FAKESECRET' && no "secret printed" || ok "no secret in the output"
[ ! -e $T/markers/node-nl.ok ] && ok "no marker for nl (no objects there)" || no "marker created for an empty node"
echo "$out" | grep -q 'nl: no backup objects' && ok "warning for the empty node" || no "no warning for nl"

echo "# 2 a failing listing changes nothing"
old=$(stat -c %Y $T/markers/node-de.ok)
STUB_FAIL=1 bash $SRC/nodes-backup-check.sh >/dev/null 2>&1
[ "$(stat -c %Y $T/markers/node-de.ok)" = "$old" ] && ok "marker untouched after a failed listing" || no "marker changed on failure"

echo "# 3 a new object refreshes the marker"
mk nl node-nl-20261112-120500.tar.gz.age "@$((NOW - 600))"
run >/dev/null
[ -e $T/markers/node-nl.ok ] && ok "marker for nl appears with the first object" || no "nl marker missing"

echo; echo "RESULT: $PASS passed, $FAIL failed"; [ $FAIL = 0 ]
