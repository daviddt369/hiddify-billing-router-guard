#!/usr/bin/env python3
"""writes the director config from the panel's country slots: usage make_config.py <out file> [probe_url]
countries = enabled country slots that have at least one enabled node (the same slots the router layer turns into loopback outbounds)"""
import json
import subprocess
import sys

out = sys.argv[1]
probe = sys.argv[2] if len(sys.argv) > 2 else "http://cp.cloudflare.com/generate_204"
r = subprocess.run(["/opt/hiddify-manager/.venv313/bin/python", "/opt/hiddify-manager/scripts/multiloc_ctl.py", "status", "--json"],
                   capture_output=True, text=True, cwd="/opt/hiddify-manager/hiddify-panel", timeout=120)
txt = r.stdout[r.stdout.index("{"):]
st = json.loads(txt)
countries = []
for loc in st["locations"]:
    if loc.get("kind") != "country" or not loc.get("enabled"):
        continue
    if not any(n.get("enabled") for n in loc.get("nodes", [])):
        continue
    slot = int(loc["slot"])
    name = (loc.get("iso") or loc.get("code") or f"S{slot}").upper()
    countries.append({"name": name, "slot": slot, "entrance": 20810 + slot, "outbound": f"auto-via-s{slot}"})
if len(countries) != 2:
    sys.exit(f"the director needs exactly two enabled countries with nodes, found {[c['name'] for c in countries]}")
conf = {"enabled": True, "api_socket": "/run/xray-router-api.sock", "balancer": "auto-director", "block_outbound": "block",
        "countries": countries, "probe_url": probe, "probe_ok_codes": [200, 204],
        "interval_s": 10, "probes_per_cycle": 2, "probe_timeout_s": 5,
        "margin_ms": 20, "confirm_cycles": 3, "fail_cycles": 3, "min_dwell_s": 300, "ewma_alpha": 0.3, "recover_cycles": 2}
with open(out, "w") as f:
    json.dump(conf, f, indent=2)
print("countries:", [(c["name"], c["entrance"], c["outbound"]) for c in countries])
