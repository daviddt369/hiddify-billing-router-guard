#!/usr/bin/env bash
# disaster-recovery/node-backup/node-backup.sh  (runs ON an exit node, as root, started over SSH by the operator's PC)
#
# Streams ONE encrypted archive to stdout: everything on this exit node that cannot be rebuilt from the repository
# (WireGuard keys, firewall rules, AdGuard + unbound settings, certificates, SSH access, custom units, the node's own panel database).
# The archive is encrypted with the age PUBLIC key in /opt/vpn-node-backup/age-recipient.pub: this node can NOT decrypt it, and
# NO cloud credentials live on the node (a hacked exit node must not be able to delete the backups) - the PC pulls the stream and stores/uploads it.
# Logs go to stderr only. Nothing is written to disk except a temp dump in RAM (/dev/shm), removed at the end.
#
# exit codes: 0 ok; 2 no recipient key; 3 age missing; 4 database dump failed; anything else = a failed pipeline (the PC rejects the file)
set -Eeuo pipefail

RECIPIENT_FILE="${NODE_BACKUP_RECIPIENT:-/opt/vpn-node-backup/age-recipient.pub}"
log() { echo "[node-backup $(date +%H:%M:%S)] $*" >&2; }

[[ -s "$RECIPIENT_FILE" ]] || { log "no recipient key file: $RECIPIENT_FILE"; exit 2; }
command -v age >/dev/null || { log "age is not installed"; exit 3; }
RECIPIENT="$(tr -d '[:space:]' < "$RECIPIENT_FILE")"

WORK="$(mktemp -d /dev/shm/vpn-node-backup.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT
chmod 700 "$WORK"

# what to take: only paths that exist on this node (Hiddify 12 layout on DE, Hiddify 13 layout on NL, common system pieces on both)
CANDIDATES=(
  /etc/wireguard /etc/iptables /etc/nftables.conf /etc/unbound /etc/adguardhome /etc/vpn-watch /etc/vpn-ru-node
  /etc/ssh/sshd_config /etc/ssh/sshd_config.d /root/.ssh /root/.acme.sh /root/.config /etc/cron.d /etc/crontab
  /etc/systemd/system /usr/local/bin /usr/local/sbin /opt/vpn-watch /opt/amnezia
  /opt/AdGuardHome/AdGuardHome.yaml /opt/AdGuardHome/userfilters
  /opt/hiddify-manager/hiddify-panel/app.cfg /opt/hiddify-manager/xray/configs /opt/hiddify-manager/singbox/configs
  /opt/hiddify-manager/haproxy /opt/hiddify-manager/nginx /opt/hiddify-manager/ssl
  /opt/hiddify-manager/services/panel/app.cfg /opt/hiddify-manager/data/current.json /opt/hiddify-manager/data/hiddify-core
  /opt/hiddify-manager/data/hiddify-panel /opt/hiddify-manager/data/ssl /opt/hiddify-manager/data/services /opt/hiddify-manager/generated
)
EXISTING=()
for p in "${CANDIDATES[@]}"; do [[ -e "$p" ]] && EXISTING+=("${p#/}"); done
log "taking ${#EXISTING[@]} paths"

# the node's own panel database (reproducible from the parent panel, but cheap and saves a re-registration)
if command -v mariadb-dump >/dev/null && mariadb -N -e 'select 1' hiddifypanel >/dev/null 2>&1; then
  mariadb-dump --single-transaction --routines --triggers --events hiddifypanel > "$WORK/node-db.sql" || { log "database dump failed"; exit 4; }
  tail -n 2 "$WORK/node-db.sql" | grep -q 'Dump completed' || { log "database dump is cut"; exit 4; }
  log "database dump ok ($(( $(stat -c%s "$WORK/node-db.sql") / 1024 )) KB)"
else
  log "no local hiddifypanel database - skipped"
fi

{
  echo "host: $(hostname)"
  echo "created_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "kernel: $(uname -r)"
  echo "hiddify_version: $(cat /opt/hiddify-manager/VERSION 2>/dev/null || echo unknown)"
  echo "paths:"; printf '  /%s\n' "${EXISTING[@]}"
} > "$WORK/MANIFEST.txt"

# the archive goes straight into age: no plaintext archive is ever stored; --exclude keeps the big rotating/derived data out
set +e
tar -C / --ignore-failed-read --warning=no-file-changed --warning=no-file-removed -czf -     --exclude='opt/hiddify-manager/hiddify-panel/backup' --exclude='opt/hiddify-manager/data/log' --exclude='opt/hiddify-manager/data/mysql'     --exclude='*.sock' --exclude='root/.acme.sh/*/*.log'     "${EXISTING[@]}" -C "$WORK" MANIFEST.txt $( [[ -f "$WORK/node-db.sql" ]] && echo node-db.sql )   | age -r "$RECIPIENT"
RC=("${PIPESTATUS[@]}")
set -e
# tar exit 1 = "a file changed while it was read" (a live node): the archive is still good; 2 or more, or an age failure, is a real error
if (( RC[0] > 1 || RC[1] != 0 )); then log "FAILED: tar=${RC[0]} age=${RC[1]}"; exit 5; fi
log "stream finished (tar=${RC[0]})"
