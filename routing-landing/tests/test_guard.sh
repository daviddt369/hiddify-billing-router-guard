#!/usr/bin/env bash
# Tests guard_check() from deploy/cert-common.sh with stubbed curl/nginx/systemctl (touches nothing real).
set -uo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
W="$(mktemp -d)"; BIN="$W/bin"; mkdir -p "$BIN"
PASS=0; FAIL=0
ok(){ PASS=$((PASS+1)); echo "  PASS  $1"; }; bad(){ FAIL=$((FAIL+1)); echo "  FAIL  $1"; }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

cat > "$BIN/curl" <<'EOF'
#!/usr/bin/env bash
echo "curl $*" >> "$STUB_DIR/calls.log"
[ -e "$STUB_DIR/curl_fails" ] && exit 7
cat "$STUB_DIR/curl_body"
EOF
cat > "$BIN/nginx" <<'EOF'
#!/usr/bin/env bash
echo "nginx $*" >> "$STUB_DIR/calls.log"
[ -e "$STUB_DIR/nginx_t_fails" ] && exit 1 || exit 0
EOF
cat > "$BIN/systemctl" <<'EOF'
#!/usr/bin/env bash
echo "systemctl $*" >> "$STUB_DIR/calls.log"
EOF
chmod +x "$BIN"/*
export STUB_DIR="$W" PATH="$BIN:$PATH"
export ROUTING_HOST=routing.test.example ROUTING_TLS_STASH="$W/stash" HIDDIFY_SSL_DIR="$W/ssl" ROUTING_NGINX_CONF="$W/zz-routing-links.conf" ROUTING_GUARD_HOST=other.test.example
# shellcheck disable=SC1091
. "$HERE/deploy/cert-common.sh"
reset() { rm -f "$W/calls.log" "$W/curl_fails" "$W/nginx_t_fails" "$ROUTING_NGINX_CONF" "$ROUTING_NGINX_CONF.disabled"; echo "x" > "$ROUTING_NGINX_CONF"; }
n() { local c=0; [ -f "$W/calls.log" ] && c="$(grep -c "$1" "$W/calls.log" || true)"; echo "$c"; }

echo "== guard_check"
reset; rm -f "$ROUTING_NGINX_CONF"; echo "decoy" > "$W/curl_body"; guard_check; rc=$?
check "no nginx block installed -> no-op (rc=0, no curl)"                      '[ "$rc" = 0 ] && [ "$(n "^curl")" = 0 ]'
reset; echo "<html>404 Not Found</html>" > "$W/curl_body"; guard_check; rc=$?
check "foreign host gets its normal page -> keep block (rc=0)"                  '[ "$rc" = 0 ] && [ -e "$ROUTING_NGINX_CONF" ] && [ "$(n "^systemctl")" = 0 ]'
reset; echo '<button id="copy-url">x</button>' > "$W/curl_body"; guard_check >/dev/null; rc=$?
check "foreign host gets OUR page -> block disabled, nginx tested + reloaded"   '[ "$rc" = 1 ] && [ ! -e "$ROUTING_NGINX_CONF" ] && [ -e "$ROUTING_NGINX_CONF.disabled" ] && [ "$(n "^nginx -t")" = 1 ] && [ "$(n "^systemctl reload hiddify-nginx")" = 1 ]'
reset; echo '<button id="copy-url">x</button>' > "$W/curl_body"; touch "$W/nginx_t_fails"; guard_check >/dev/null; rc=$?
check "leak + nginx -t fails -> still disabled, but NO reload attempted"         '[ "$rc" = 1 ] && [ -e "$ROUTING_NGINX_CONF.disabled" ] && [ "$(n "^systemctl reload")" = 0 ]'
reset; touch "$W/curl_fails"; echo "" > "$W/curl_body"; guard_check; rc=$?
check "curl failure (network blip) -> do NOT disable anything"                  '[ "$rc" = 0 ] && [ -e "$ROUTING_NGINX_CONF" ]'
echo "== bash -n"
for f in "$HERE"/deploy/*.sh; do bash -n "$f" && ok "syntax $(basename "$f")" || bad "syntax $(basename "$f")"; done
echo; echo "GUARD RESULT: $PASS passed, $FAIL failed"; rm -rf "$W"; [ "$FAIL" = 0 ]
