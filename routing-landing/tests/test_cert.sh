#!/usr/bin/env bash
# Tests cert_sync()/stash_ok() from deploy/cert-common.sh with stubbed systemctl/pgrep (touches nothing real).
set -uo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
W="$(mktemp -d)"; BIN="$W/bin"; mkdir -p "$BIN"
PASS=0; FAIL=0
ok(){ PASS=$((PASS+1)); echo "  PASS  $1"; }; bad(){ FAIL=$((FAIL+1)); echo "  FAIL  $1"; }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

# stubs: systemctl logs calls; behaviour switched by files in $W
cat > "$BIN/systemctl" <<'EOF'
#!/usr/bin/env bash
echo "$*" >> "$STUB_DIR/systemctl.log"
case "$1" in
  is-active) [ -e "$STUB_DIR/haproxy_down" ] && exit 3 || exit 0 ;;
  reload)    [ -e "$STUB_DIR/reload_fails" ] && exit 1 || exit 0 ;;
esac
EOF
cat > "$BIN/pgrep" <<'EOF'
#!/usr/bin/env bash
[ -e "$STUB_DIR/install_running" ] && exit 0 || exit 1
EOF
chmod +x "$BIN/systemctl" "$BIN/pgrep"
export STUB_DIR="$W" PATH="$BIN:$PATH"

export ROUTING_HOST="routing.test.example" ROUTING_TLS_STASH="$W/stash" HIDDIFY_SSL_DIR="$W/ssl"
mkdir -p "$W/stash" "$W/ssl"
SUBJ="/CN=$ROUTING_HOST"; case "$(uname -s)" in MINGW*|MSYS*) SUBJ="//CN=$ROUTING_HOST";; esac   # Git Bash rewrites a leading "/"
mkcert() { # $1 days, $2 out-prefix
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days "$1" -subj "$SUBJ" \
    -keyout "$2.key" -out "$2.crt" >/dev/null 2>&1 || { echo "mkcert failed"; exit 99; }
}
# shellcheck disable=SC1091
. "$HERE/deploy/cert-common.sh"
calls() { local n=0; [ -f "$W/systemctl.log" ] && n="$(grep -c '^reload hiddify-haproxy' "$W/systemctl.log" || true)"; echo "$n"; }
reset() { rm -f "$W/systemctl.log" "$W/haproxy_down" "$W/reload_fails" "$W/install_running" "$SSL/$HOST".crt* "$STASH/.last-install"; }

echo "== cert_sync"
reset; cert_sync >/dev/null
check "no stash -> no-op, no reload"                                   '[ ! -e "$SSL/$HOST.crt" ] && [ "$(calls)" = 0 ]'

mkcert 60 "$W/good"; cp "$W/good.crt" "$STASH/$HOST.crt"; cp "$W/good.key" "$STASH/$HOST.crt.key"
reset; cert_sync >/dev/null
check "valid stash, ssl dir empty -> installs both files + 1 reload"   '[ -s "$SSL/$HOST.crt" ] && [ -s "$SSL/$HOST.crt.key" ] && [ "$(calls)" = 1 ]'
check "installed files identical to stash"                             'cmp -s "$STASH/$HOST.crt" "$SSL/$HOST.crt" && cmp -s "$STASH/$HOST.crt.key" "$SSL/$HOST.crt.key"'
rm -f "$W/systemctl.log"; cert_sync >/dev/null
check "already installed -> no second reload (idempotent)"             '[ "$(calls)" = 0 ]'

reset; touch "$W/install_running"; cert_sync >/dev/null
check "Hiddify install running -> deferred, nothing written"           '[ ! -e "$SSL/$HOST.crt" ] && [ "$(calls)" = 0 ]'
reset; touch "$W/haproxy_down"; cert_sync >/dev/null
check "HAProxy not active -> deferred, nothing written"                '[ ! -e "$SSL/$HOST.crt" ] && [ "$(calls)" = 0 ]'
reset; touch "$W/reload_fails"; cert_sync >/dev/null; rc=$?
check "reload fails -> our files removed again (can't break next start)" '[ "$rc" != 0 ] && [ ! -e "$SSL/$HOST.crt" ] && [ ! -e "$SSL/$HOST.crt.key" ]'

echo "== stash_ok rejects bad stashes"
mkcert 60 "$W/other"
cp "$W/good.crt" "$STASH/$HOST.crt"; cp "$W/other.key" "$STASH/$HOST.crt.key"
reset; cert_sync >/dev/null
check "key does not match certificate -> not installed"                '[ ! -e "$SSL/$HOST.crt" ] && [ "$(calls)" = 0 ]'
mkcert 1 "$W/short"      # valid for exactly 1 day -> "expires within 86400 s" -> must be rejected
cp "$W/short.crt" "$STASH/$HOST.crt"; cp "$W/short.key" "$STASH/$HOST.crt.key"
reset; cert_sync >/dev/null
check "cert expiring within a day -> not installed"                    '[ ! -e "$SSL/$HOST.crt" ] && [ "$(calls)" = 0 ]'
# positive control for the two negative cases above: the same code path installs a good pair
cp "$W/good.crt" "$STASH/$HOST.crt"; cp "$W/good.key" "$STASH/$HOST.crt.key"
reset; cert_sync >/dev/null
check "control: restoring a good pair installs it again"               '[ -s "$SSL/$HOST.crt" ] && [ "$(calls)" = 1 ]'
echo "== reload-storm brake"
reset; cert_sync >/dev/null                       # first install: stamps .last-install
rm -f "$SSL/$HOST".crt* "$W/systemctl.log"        # something deletes the cert again right away (what apply_users does)
cert_sync >/dev/null
check "deleted again <30 min after install -> NO second reload, nothing written" '[ ! -e "$SSL/$HOST.crt" ] && [ "$(calls)" = 0 ]'
echo $(( $(date +%s) - 4000 )) > "$STASH/.last-install"; rm -f "$W/systemctl.log"
cert_sync >/dev/null
check "same situation but last install was >30 min ago -> reinstalls (1 reload)"  '[ -s "$SSL/$HOST.crt" ] && [ "$(calls)" = 1 ]'
ROUTING_REINSTALL_BACKOFF=0 reset; ROUTING_REINSTALL_BACKOFF=0 cert_sync >/dev/null; rm -f "$SSL/$HOST".crt* "$W/systemctl.log"; ROUTING_REINSTALL_BACKOFF=0 cert_sync >/dev/null
check "backoff configurable (0 = always reinstall)"                              '[ -s "$SSL/$HOST.crt" ] && [ "$(calls)" = 1 ]'
echo "== bash -n on all shell scripts"
for f in "$HERE"/update.sh "$HERE"/deploy/*.sh; do bash -n "$f" && ok "syntax $(basename "$f")" || bad "syntax $(basename "$f")"; done

echo; echo "CERT RESULT: $PASS passed, $FAIL failed"; rm -rf "$W"; [ "$FAIL" = 0 ]
