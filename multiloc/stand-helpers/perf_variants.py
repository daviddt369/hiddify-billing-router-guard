#!/usr/bin/env python3
"""Where does the xray-router config load time go?  Times `xray run -test` (pinned to 2 cores like prod) for variants of the live router config.
Variants are written to /tmp only; the live config and the running router are not touched.
usage: perf_variants.py [cpus=0,1]"""
import copy
import json
import subprocess
import sys
import time

CPUS = sys.argv[1] if len(sys.argv) > 1 else "0,1"
SRC = "/etc/xray-router/config.json"
cfg = json.load(open(SRC))
rules = cfg["routing"]["rules"]


def timed(name, c):
    p = f"/tmp/variant-{name}.json"
    json.dump(c, open(p, "w"))
    t0 = time.time()
    r = subprocess.run(["taskset", "-c", CPUS, "/usr/bin/xray", "run", "-test", "-config", p], capture_output=True, text=True)
    dt = time.time() - t0
    ok = r.returncode == 0 and "Configuration OK" in (r.stdout + r.stderr)
    n = len(c["routing"]["rules"])
    print(f"{name:34s} rules={n:6d} wall={dt:7.1f}s ok={ok}", flush=True)
    return dt


def with_rules(new_rules):
    c = copy.deepcopy(cfg)
    c["routing"]["rules"] = new_rules
    return c


def merged(rs):
    """merge CONSECUTIVE rules that differ only in one list key (ip/domain) and share the target: same first-match semantics"""
    out = []
    for r in rs:
        keys = set(r) - {"type"}
        if keys in ({"ip", "outboundTag"}, {"domain", "outboundTag"}):
            k = "ip" if "ip" in r else "domain"
            if out and set(out[-1]) == set(r) and out[-1]["outboundTag"] == r["outboundTag"]:
                out[-1][k] = out[-1][k] + r[k]
                continue
            out.append({**r, k: list(r[k])})
        else:
            out.append(r)
    return out


ip_rules = [r for r in rules if set(r) == {"type", "ip", "outboundTag"}]
dom_rules = [r for r in rules if set(r) == {"type", "domain", "outboundTag"}]
other = [r for r in rules if r not in ip_rules and r not in dom_rules]
print(f"rules: total={len(rules)} single-ip={len(ip_rules)} single-domain={len(dom_rules)} other={len(other)}  cpus={CPUS}", flush=True)

timed("tiny (other rules only)", with_rules(other))
timed("only single-domain rules + other", with_rules(other + dom_rules))
timed("only single-ip rules + other", with_rules(other + ip_rules))
m = merged(rules)
timed("consecutive rules merged", with_rules(m))
print(f"merged rule count: {len(m)}  (semantics identical: first-match order preserved)", flush=True)


def grouped(rs):
    """EXPERIMENT ONLY: merge all single-entry ip rules (and all single-entry domain rules) that share the same outbound into ONE rule placed at the
    first occurrence. Equivalent to the original only if no rule with a different target that could match the same traffic sits between them."""
    out, first = [], {}
    for r in rs:
        keys = set(r) - {"type"}
        if keys in ({"ip", "outboundTag"}, {"domain", "outboundTag"}):
            k = "ip" if "ip" in r else "domain"
            key = (k, r["outboundTag"])
            if key in first:
                first[key][k].extend(r[k])
                continue
            nr = {**r, k: list(r[k])}
            first[key] = nr
            out.append(nr)
        else:
            out.append(r)
    return out


g = grouped(rules)
t = timed("grouped by (kind,outbound)", with_rules(g))
print(f"grouped rule count: {len(g)}", flush=True)
