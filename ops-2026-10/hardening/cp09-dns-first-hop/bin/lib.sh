# CP-09 v2 · общие функции (подключается из check-before / apply / check-after / rollback; сам ничего не делает).
# Переменные окружения (по умолчанию боевые): ROOT DB DNS_SERVER CONFDIR UNIT BIN RESTART_CMD CP09_SKIP_CAPTURE CP09_SKIP_PREFLIGHT CP09_ALLOW_ANY_DNS
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
TPL="$HERE/../templates"
ROOT=${ROOT:-/opt/hiddify-manager}
DB=${DB:-hiddifypanel}
fail=0
ok()   { echo "  OK    $*"; }
bad()  { echo "  FAIL  $*"; fail=1; }
warn() { echo "  WARN  $*"; }

setup_engine() {   # $1 = xray | singbox
  ENGINE="${1:-}"
  case "$ENGINE" in
    xray)    CONFDIR=${CONFDIR:-$ROOT/xray/configs};    UNIT=${UNIT:-hiddify-xray};    BIN=${BIN:-$ROOT/xray/bin/xray};         PROC=xray ;;
    singbox) CONFDIR=${CONFDIR:-$ROOT/singbox/configs}; UNIT=${UNIT:-hiddify-singbox}; BIN=${BIN:-$ROOT/singbox/hiddify-core}; PROC=hiddify-core ;;
    *) echo "укажите движок первого хопа: xray | singbox"; exit 2 ;;
  esac
  J2="$CONFDIR/02_dns.json.j2"; GEN="$CONFDIR/02_dns.json"
  T_BEFORE="$TPL/$ENGINE.02_dns.json.j2.before"; T_AFTER="$TPL/$ENGINE.02_dns.json.j2.after"
  RESTART_CMD=${RESTART_CMD:-systemctl restart $UNIT}
  PTR="/run/cp09.last-backup.$ENGINE"
}

dns_server() {
  local v="${DNS_SERVER:-}"
  [ -n "$v" ] || v=$(mysql -N -B "$DB" -e "select value from str_config where \`key\`='dns_server' and child_id=0" 2>/dev/null)
  echo "${v:-1.1.1.1}"
}

sha16() { sha256sum "$1" 2>/dev/null | cut -c1-16; }

# render <template> <dns_server> <out>: the same single substitution Hiddify's Jinja makes; refuses anything else
render() {
  python3 - "$1" "$2" "$3" <<'PY'
import json, re, sys
tpl = open(sys.argv[1], encoding="utf-8").read()
rx = re.compile(r"\{\{\s*hconfigs\['dns_server'\]\s+or\s+\"1\.1\.1\.1\"\s*\}\}")
assert rx.search(tpl), "в шаблоне нет ожидаемого выражения dns_server"
out = rx.sub(sys.argv[2], tpl)
assert "{{" not in out and "{%" not in out, "в шаблоне остались неотрисованные выражения"
json.loads(re.sub(r",(\s*[}\]])", r"\1", out))      # the stock sing-box template has a trailing comma (sing-box accepts it; Hiddify normalizes the generated file)
open(sys.argv[3], "w", encoding="utf-8").write(out)
PY
}

same_json() {
  python3 - "$1" "$2" <<'PY'
import json, re, sys
def load(p):
    return json.loads(re.sub(r",(\s*[}\]])", r"\1", open(p, encoding="utf-8").read()))
sys.exit(0 if load(sys.argv[1]) == load(sys.argv[2]) else 1)
PY
}

free_port() { python3 -c "import socket; s=socket.socket(); s.bind(('127.0.0.1',0)); print(s.getsockname()[1])"; }

# DoH endpoints are reached straight from this node (https+local / type https without a detour): list which of them answer right now
doh_reachability() {
  local q='AAABAAABAAAAAAAAB2V4YW1wbGUDY29tAAABAAE' good=0 h code
  for h in "$@"; do
    code=$(curl -s -m 6 -o /dev/null -w '%{http_code}' -H 'accept: application/dns-message' "https://$h/dns-query?dns=$q" 2>/dev/null)
    if [ "$code" = 200 ]; then ok "DoH https://$h/dns-query отвечает напрямую с этого узла"; good=$((good + 1)); else warn "DoH https://$h/dns-query: HTTP ${code:-нет ответа}"; fi
  done
  DOH_GOOD=$good
}

# passive capture: outgoing plain DNS (UDP/53) from this node to the public resolvers that were used before the fix
plain_dns_capture() {   # $1 = seconds
  python3 - "${1:-15}" <<'PY'
import socket, sys, time
secs = int(sys.argv[1])
s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3)); s.settimeout(0.5)
bad = ("1.1.1.1", "1.0.0.1", "8.8.8.8", "8.8.4.4", "9.9.9.9", "149.112.112.112")
n = pk = 0; seen = {}; t0 = time.time()
while time.time() - t0 < secs:
    try:
        b, meta = s.recvfrom(2048)
    except socket.timeout:
        continue
    if meta[2] != 4 or len(b) < 42 or b[12:14] != b"\x08\x00" or b[23] != 17:
        continue
    pk += 1
    ihl = (b[14] & 15) * 4
    dport = int.from_bytes(b[14 + ihl + 2:14 + ihl + 4], "big")
    dst = socket.inet_ntoa(b[30:34])
    if dport == 53 and dst in bad:
        n += 1; seen[dst] = seen.get(dst, 0) + 1
print(f"  исходящих UDP-пакетов за {secs} с: {pk}; открытый DNS к публичным резолверам напрямую с этого узла: {n}" + (f" {seen}  <-- открытый DNS" if n else ""))
sys.exit(1 if n else 0)
PY
}

# temporary instance of the REAL engine binary with the NEW dns section on a random loopback port: resolves a real name through the new servers before anything is replaced
preflight_instance() {   # $1 = rendered new 02_dns.json ; returns 0 when a real name was resolved and fetched through the new DNS
  local newdns="$1" port wd pid code rc=1
  port=$(free_port); wd=$(mktemp -d /tmp/cp09-pre.XXXXXX)
  if [ "$ENGINE" = xray ]; then
    python3 - "$newdns" "$wd/pre.json" "$port" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))["dns"]
json.dump({"log": {"loglevel": "warning"}, "dns": d,
           "inbounds": [{"listen": "127.0.0.1", "port": int(sys.argv[3]), "protocol": "socks", "settings": {"auth": "noauth", "udp": False}}],
           "outbounds": [{"protocol": "freedom", "settings": {"domainStrategy": "UseIPv4"}}]}, open(sys.argv[2], "w"))
PY
    "$BIN" run -c "$wd/pre.json" >"$wd/out.log" 2>&1 & pid=$!
  else
    mkdir -p "$wd/cfg" "$wd/run"
    python3 - "$newdns" "$wd/cfg" "$port" <<'PY'
import json, os, sys
out = sys.argv[2]
open(os.path.join(out, "02_dns.json"), "w").write(open(sys.argv[1]).read())
json.dump({"log": {"level": "info"}}, open(os.path.join(out, "00_log.json"), "w"))
json.dump({"inbounds": [{"type": "socks", "tag": "pre-in", "listen": "127.0.0.1", "listen_port": int(sys.argv[3])}]}, open(os.path.join(out, "05_inbounds_pre.json"), "w"))
json.dump({"outbounds": [{"type": "direct", "tag": "direct"}]}, open(os.path.join(out, "06_outbounds.json"), "w"))
json.dump({"route": {"rules": [{"action": "sniff"}, {"action": "hijack-dns", "protocol": "dns"}], "final": "direct"}}, open(os.path.join(out, "03_routing.json"), "w"))
PY
    "$BIN" srun -C "$wd/cfg" -D "$wd/run" >"$wd/out.log" 2>&1 & pid=$!
  fi
  for _ in $(seq 1 50); do (exec 3<>/dev/tcp/127.0.0.1/$port) 2>/dev/null && break; sleep 0.2; done
  code=$(curl -s -m 12 -o /dev/null -w '%{http_code}' --socks5-hostname 127.0.0.1:$port http://example.com/ 2>/dev/null)
  case "$code" in
    2*|3*) ok "временный экземпляр $ENGINE с НОВЫМ DNS разрешил example.com и открыл его (HTTP $code)"; rc=0 ;;
    *) bad "временный экземпляр $ENGINE с НОВЫМ DNS не открыл example.com (HTTP ${code:-нет ответа}); журнал: $(tail -2 "$wd/out.log" | tr '\n' ' ' | cut -c1-200)" ;;
  esac
  local tcp udp
  tcp=$(ss -tnp 2>/dev/null | grep "pid=$pid," | grep -cE ':443 ')
  udp=$(ss -unp 2>/dev/null | grep "pid=$pid," | grep -cE ':53 ')
  echo "  соединения временного экземпляра: TCP/443 к резолверам: $tcp, UDP/53: $udp"
  kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null
  rm -rf "$wd"
  return $rc
}
