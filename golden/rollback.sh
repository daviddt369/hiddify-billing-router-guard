#!/usr/bin/env bash
# golden/rollback.sh <backup-dir> - puts back the files the overlay replaced and removes the files it added (backup dir is printed by install.sh).
set -Eeuo pipefail
B="${1:?backup dir of golden/install.sh (/root/golden-backup-...)}"
[[ "$(id -u)" -eq 0 ]] || { echo "run as root" >&2; exit 1; }
[[ -d "$B" ]] || { echo "no such backup: $B" >&2; exit 1; }
cd "$B"
n=0
while IFS= read -r -d '' f; do
    rel="${f#./}"; [[ "$rel" == NEW_FILES ]] && continue
    mkdir -p "/$(dirname "$rel")"; cp -a "$f" "/$rel"; n=$((n + 1))
done < <(find . -type f -print0)
r=0
if [[ -f NEW_FILES ]]; then
    while IFS= read -r p; do [[ -n "$p" && -f "$p" ]] && { rm -f "$p"; r=$((r + 1)); }; done < NEW_FILES
fi
systemctl daemon-reload
systemctl restart hiddify-panel hiddify-panel-background-tasks 2>/dev/null || true
echo "restored $n files, removed $r files the overlay had added; panel: $(systemctl is-active hiddify-panel)"
