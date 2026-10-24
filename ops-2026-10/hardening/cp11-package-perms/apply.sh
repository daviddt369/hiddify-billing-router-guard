#!/bin/bash
# CP-11: the code of the web panel (python package hiddifypanel) becomes root-owned and read-only for the panel user.
# Why: root jobs (probe-routing-health, multiloc_ctl, sudo commander.py) import this code; while the panel user could rewrite it, a hole in the web panel
# meant root. Run as root. Backup = a list of every owner/mode before the change (rollback.sh restores it). Any failed check -> automatic revert.
set -uo pipefail
P=${CP11_PKG:-/opt/hiddify-manager/.venv313/lib/python3.13/site-packages/hiddifypanel}
PYV=${CP11_PY:-/opt/hiddify-manager/.venv313/bin/python}
BK=${CP11_BK:-/root/.claude-backups/cp11-$(date +%Y%m%d-%H%M%S)}
SVC_ALL="hiddify-panel hiddify-panel-background-tasks hiddify-anti-share hiddify-xray xray-router hiddify-haproxy"
install -d -m 700 /root/.claude-backups "$BK"
ok()  { echo "  OK    $*"; }
bad() { echo "  FAIL  $*"; }

wait_panel() { for i in $(seq 1 60); do curl -s -o /dev/null -m 2 http://127.0.0.1:9000/ && return 0; sleep 1; done; return 1; }
revert() {
  echo "== REVERT: owners and modes back from $BK/owners.txt"
  while read -r u g m p; do chown "$u:$g" "$p" 2>/dev/null; chmod "$m" "$p" 2>/dev/null; done < "$BK/owners.txt"
  for s in hiddify-panel hiddify-panel-background-tasks; do systemctl is-enabled "$s" >/dev/null 2>&1 && systemctl restart "$s"; done
  wait_panel && echo "  panel answers again" || echo "  PANEL DOES NOT ANSWER: see journalctl -u hiddify-panel"
}

echo "== 0. before"
[ -d "$P" ] || { echo "no package dir $P"; exit 2; }
ACTIVE=""; for s in $SVC_ALL; do systemctl is-active --quiet "$s" && ACTIVE="$ACTIVE $s"; done
echo "  active:$ACTIVE"
wait_panel || { echo "panel does not answer before the change: stop"; exit 2; }
echo "  not root-owned entries (without caches): $(find "$P" -not -path '*__pycache__*' -not -user root | wc -l) of $(find "$P" -not -path '*__pycache__*' | wc -l)"
echo "  files the panel user can write: $(runuser -u hiddify-panel -- bash -c "find '$P' -not -path '*__pycache__*' -type f -writable | wc -l" 2>/dev/null)"

echo "== 1. backup: owner, group, mode of every entry -> $BK/owners.txt"
find "$P" -printf '%u %g %m %p\n' > "$BK/owners.txt"
wc -l < "$BK/owners.txt" | sed 's/^/  entries: /'

echo "== 2. root owns the code, nobody else can write it; bytecode compiled by root"
chown -R root:root "$P" && chmod -R go-w "$P" && ok "chown root:root, chmod go-w" || { bad "chown"; revert; exit 1; }
"$PYV" -m compileall -q -j 2 "$P" >/dev/null 2>&1 && ok "compileall (caches owned by root)" || echo "  (compileall reported problems: the caches are only an optimisation)"

echo "== 3. restart what loads the code, then check"
T0=$(date '+%Y-%m-%d %H:%M:%S')
for s in hiddify-panel hiddify-panel-background-tasks; do case " $ACTIVE " in *" $s "*) systemctl restart "$s";; esac; done
wait_panel && ok "panel answers" || { bad "panel does not answer"; revert; exit 1; }
sleep 6
FAIL=0
for s in $ACTIVE; do systemctl is-active --quiet "$s" && ok "$s active" || { bad "$s not active"; FAIL=1; }; done
PE=$(journalctl -u hiddify-panel -u hiddify-panel-background-tasks --since "$T0" --no-pager 2>/dev/null | grep -ci "PermissionError\|Permission denied")
[ "$PE" = 0 ] && ok "no permission errors in the panel journal since the restart" || { bad "$PE permission errors in the journal"; FAIL=1; }
CODE=$(curl -s -o /dev/null -m 10 -w '%{http_code}' http://127.0.0.1:9000/) ; echo "  panel root page http: $CODE"
runuser -u hiddify-panel -- bash -c "[ ! -w '$P/hutils/multiloc.py' ] && [ ! -w '$P' ] && [ -r '$P/hutils/multiloc.py' ]" && ok "panel user: can read the code, cannot write it or the directory" || { bad "panel user still can write (or cannot read)"; FAIL=1; }
runuser -u hiddify-panel -- "$PYV" -c "import hiddifypanel.hutils.multiloc, hiddifypanel.antishare.shared" 2>&1 | tail -1 | grep -q . && { bad "import as the panel user failed"; FAIL=1; } || ok "import as the panel user"
( cd /opt/hiddify-manager/hiddify-panel 2>/dev/null && "$PYV" -c "import hiddifypanel.hutils.multiloc_router" 2>&1 | tail -1 | grep -q . ) && { bad "import as root failed"; FAIL=1; } || ok "import as root"
[ $FAIL = 0 ] || { revert; exit 1; }
echo "DONE. Rollback: bash $(dirname "$0")/rollback.sh $BK"
