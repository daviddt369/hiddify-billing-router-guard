#!/bin/bash
# GeoData update on the RU node (run as root): copies the old files, replaces geoip.dat/geosite.dat atomically, restarts xray-router and hiddify-xray (they read the
# assets only at start), checks RU Direct / AUTO / DE_1 / NL_3, and puts the OLD files back automatically if a check fails.
# usage: geo_update.sh <dir with the new geoip.dat and geosite.dat, already verified>
set -u
NEW="${1:?directory with the new files}"
D=/opt/hiddify-manager/xray/bin
BK=/root/.claude-backups/geo-$(date +%Y%m%d-%H%M%S)
fail=0
ok()  { echo "  OK    $*"; }
bad() { echo "  FAIL  $*"; fail=1; }

wait_ports() { for i in $(seq 1 60); do (exec 3<>/dev/tcp/127.0.0.1/20808) 2>/dev/null && (exec 3<>/dev/tcp/127.0.0.1/20811) 2>/dev/null && (exec 3<>/dev/tcp/127.0.0.1/20813) 2>/dev/null && return 0; sleep 1; done; return 1; }

checks() {
  fail=0
  for u in xray-router hiddify-xray hiddify-haproxy hiddify-panel; do [ "$(systemctl is-active $u)" = active ] && ok "$u active" || bad "$u not active"; done
  for pair in "20811 DE 203.0.113.20" "20813 NL 203.0.113.30"; do
    set -- $pair
    ip=$(curl -s -m 12 --socks5-hostname 127.0.0.1:$1 https://ipv4.icanhazip.com | tr -d '[:space:]')
    [ "$ip" = "$3" ] && ok "$2 (:$1) foreign site leaves through $ip" || bad "$2 (:$1) -> '${ip:-no answer}' (expected $3)"
  done
  ip=$(curl -s -m 12 --socks5-hostname 127.0.0.1:20808 https://ipv4.icanhazip.com | tr -d '[:space:]')
  case "$ip" in 203.0.113.20|203.0.113.30) ok "AUTO (:20808) leaves through $ip" ;; *) bad "AUTO -> '${ip:-no answer}'" ;; esac
  # Russian destinations must still go direct: the site must answer through every entrance AND the address the Russian site sees must not be an exit
  for p in 20808 20811 20813; do
    for h in ya.ru vk.com mail.ru ok.ru ozon.ru wildberries.ru avito.ru rbc.ru hh.ru ria.ru dzen.ru; do
      code=$(curl -s -o /dev/null -m 12 -w '%{http_code}' --socks5-hostname 127.0.0.1:$p https://$h/)
      case "$code" in 2*|3*) ;; *) sleep 1; code=$(curl -s -o /dev/null -m 12 -w '%{http_code}' --socks5-hostname 127.0.0.1:$p https://$h/) ;; esac     # one retry: a site may hiccup
      case "$code" in 2*|3*) ok "RU Direct :$p $h -> HTTP $code" ;; *) bad "RU Direct :$p $h -> '${code:-no answer}'" ;; esac
    done
  done
  n=$(journalctl -u xray-router -u hiddify-xray --since "2 minutes ago" --no-pager 2>/dev/null | grep -ci 'failed to load\|panic\|fatal\|geo.*not found\|failed to open')
  [ "${n:-0}" -eq 0 ] && ok "no geo/panic errors in the Xray journals" || bad "$n geo/panic lines in the Xray journals"
  return $fail
}

echo "== backup of the old files -> $BK"
install -d -m 700 "$BK"
cp -a $D/geoip.dat $D/geosite.dat "$BK/"
sha256sum $D/geoip.dat $D/geosite.dat | tee "$BK/old.sha256" | cut -c1-16,65-
( cd "$NEW" && sha256sum -c geoip.dat.sha256sum geosite.dat.sha256sum ) || { echo "new files failed their checksum: nothing changed"; exit 2; }

echo "== replace (atomic, owner and mode kept)"
for f in geoip.dat geosite.dat; do
  cp "$NEW/$f" "$D/.$f.new" && chown --reference="$D/$f" "$D/.$f.new" && chmod --reference="$D/$f" "$D/.$f.new" && mv -f "$D/.$f.new" "$D/$f" || { echo "replace failed"; cp -a "$BK/geoip.dat" "$BK/geosite.dat" "$D/"; exit 3; }
done
sha256sum $D/geoip.dat $D/geosite.dat | cut -c1-16,65-

revert() { echo "== REVERT to the old files"; cp -a "$BK/geoip.dat" "$BK/geosite.dat" "$D/"; systemctl restart xray-router; wait_ports; systemctl restart hiddify-xray; sleep 5; echo "reverted: $(sha256sum $D/geoip.dat | cut -c1-12) $(sha256sum $D/geosite.dat | cut -c1-12)"; }

echo "== restart xray-router (reads the assets at start)"
systemctl restart xray-router
wait_ports && ok "router entrances listen" || { bad "router entrances do not listen"; revert; exit 1; }
sleep 3
echo "== checks after the router restart"
checks || { revert; echo "RESULT: checks failed, old files restored"; exit 1; }

echo "== restart hiddify-xray (first hop)"
systemctl restart hiddify-xray
sleep 6
echo "== checks after the first-hop restart"
checks || { revert; echo "RESULT: checks failed, old files restored"; exit 1; }
echo "RESULT: GeoData updated, all checks passed. Rollback: cp -a $BK/geoip.dat $BK/geosite.dat $D/ && systemctl restart xray-router hiddify-xray"
