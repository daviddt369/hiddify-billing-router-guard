#!/bin/bash
# CP-11 rollback: owners and modes of the panel package back from the list written by apply.sh. usage: rollback.sh <backup dir>
set -uo pipefail
BK=${1:?backup dir (printed by apply.sh)}
[ -f "$BK/owners.txt" ] || { echo "no $BK/owners.txt"; exit 2; }
while read -r u g m p; do chown "$u:$g" "$p" 2>/dev/null; chmod "$m" "$p" 2>/dev/null; done < "$BK/owners.txt"
for s in hiddify-panel hiddify-panel-background-tasks; do systemctl is-enabled "$s" >/dev/null 2>&1 && systemctl restart "$s"; done
for i in $(seq 1 60); do curl -s -o /dev/null -m 2 http://127.0.0.1:9000/ && { echo "panel answers; owners restored from $BK/owners.txt"; exit 0; }; sleep 1; done
echo "PANEL DOES NOT ANSWER after the rollback"; exit 1
