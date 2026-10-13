#!/bin/bash
# After a REAL restart of the distro (wsl --terminate + start: systemd boots everything by itself, nobody renders/starts anything).
# usage: post_reboot_smoke.sh <run-name>      -> /work/out/runs/<run-name>/post-reboot.log + post-reboot.json
set -uo pipefail
RUN=${1:?run name}
OUT=/work/out/runs/$RUN; mkdir -p "$OUT"
exec > >(tee "$OUT/post-reboot.log") 2>&1
PY=/opt/hiddify-manager/.venv313/bin/python
UNITS="mariadb hiddify-redis hiddify-panel hiddify-panel-background-tasks hiddify-nginx hiddify-haproxy hiddify-xray hiddify-singbox xray-router stand-lo stand-whoami stand-dns stand-exit-a stand-exit-b stand-shim-a stand-shim-b"
echo "== uptime $(cut -d' ' -f1 /proc/uptime)s ; services started by systemd on its own"
for u in $UNITS; do
  for i in $(seq 1 180); do systemctl is-active --quiet "$u" && break; sleep 1; done
  printf '%-34s %s\n' "$u" "$(systemctl is-active "$u")"
done | tee "$OUT/post-reboot-services.txt"
for i in $(seq 1 120); do curl -s -o /dev/null --max-time 2 http://127.0.0.1:9000/ && break; sleep 1; done
sleep 12
INACTIVE=$(grep -vc ' active$' "$OUT/post-reboot-services.txt")

cd /work/tests && $PY -W ignore - "$RUN" <<'PYEOF'
import json, sys
sys.path.insert(0, "/work/tests")
from stand_lib import *
router_ready(120)
T = links("tester")
res = {}
res["DE"] = sorted(set(via_many(pick(T, "Германия", "XHTTP")[1], 4)))
res["US"] = sorted(set(via_many(pick(T, "США", "WS", exclude=("Trojan",))[1], 4)))
res["US_trojan"] = sorted(set(via_many(pick(T, "США", "Trojan")[1], 3)))
G = {c["remarks"]: c for c in json.loads(sub_text("tester", fmt="happ/", ua="Happ/3.5.0"))}      # the three buttons (dedicated Happ / INCY link)
res["buttons"] = list(G)


def group_via(label, n=3):
    """a real client for the button's full-xray config; wait for the balancer, then ask n times"""
    with sc.JsonClient(G[label]) as c:
        out = []
        for i in range(n + 25):
            try:
                st, body = c.get(FOREIGN, timeout=4)
                if isinstance(body, dict) and body.get("via"):
                    out.append(body["via"])
                    if len(out) == n:
                        break
            except Exception:
                time.sleep(1)
        return sorted(set(out))


import time
res["AUTO_button"] = group_via("🟢 AUTO")
res["DE_button"] = group_via("🇩🇪 Германия")
res["US_button"] = group_via("🇺🇸 США")
res["RU_from_DE"] = via(pick(T, "Германия", "WS", exclude=("Trojan",))[1], RU_DOMAIN)
A = dict(links("active"))
res["RU_from_legacy"] = via(A["RU_GLOBAL tls WS direct vless"], RU_CIDR)
res["legacy"] = via(A["RU_GLOBAL tls WS direct vless"])
res["regular_user_sees_no_country"] = not any(("Германия" in n or "США" in n or "AUTO" in n) for n, _ in links("active"))
print(json.dumps(res, ensure_ascii=False))
ok = (res["DE"] == ["exit-a"] and res["US"] == ["exit-b"] and res["US_trojan"] == ["exit-b"]
      and res["buttons"] == ["🟢 AUTO", "🇩🇪 Германия", "🇺🇸 США"]
      and res["AUTO_button"] and set(res["AUTO_button"]) <= {"exit-a", "exit-b"} and res["DE_button"] == ["exit-a"] and res["US_button"] == ["exit-b"]
      and res["RU_from_DE"] == "direct" and res["RU_from_legacy"] == "direct" and res["legacy"] in ("exit-a", "exit-b") and res["regular_user_sees_no_country"])
json.dump({"smoke_ok": bool(ok), **res}, open("/work/out/runs/%s/post-reboot.json" % sys.argv[1], "w"), ensure_ascii=False, indent=1)
print("POST-REBOOT SMOKE", "OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
PYEOF
RC=$?
echo "== installer view after the restart"
python3 /work/src/multiloc_installer.py status | awk '{print $1}' | sort | uniq -c
python3 /work/src/multiloc_installer.py verify | tail -1
echo "inactive_units=$INACTIVE smoke_rc=$RC"
[ "$INACTIVE" = "0" ] && [ "$RC" = "0" ]
