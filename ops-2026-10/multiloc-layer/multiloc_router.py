"""xray-router layer for multi-location: ONE extra post-processing layer appended after the existing commercial_routing layers.

transform(config, plan) is a PURE function (unit-testable, idempotent):
  * adds one socks entrance per slot  (from-hiddify-s<N>, 127.0.0.1:<router_port>+2+N, same sniffing as the default entrance)
  * adds one balancer per slot with >=2 live-capable nodes (leastPing, `fallbackTag: "block"` -> a dead country fails closed: without a fallbackTag Xray
    hands a balancer that has no live node to its "default handler", which is the FIRST outbound of the file = some node of ANOTHER country)
  * inserts the slot final rules right BEFORE the generic catch-all, i.e. after every RU-direct / DNS / block rule that the
    existing layers produced, so RU traffic from any entrance still stays in Russia
  * duplicates the "global-upstream-bound" specific rules (e.g. proxy-DNS IPs -> upstream-balancer) per slot, so even those
    connections keep the chosen country
  * makes sure the observatory covers the slot nodes
  * makes the global (AUTO) balancer fail closed too (`fallbackTag: "block"`): with no live node in ANY country foreign traffic is blocked
  * optionally (plan["auto_director"].enabled) turns AUTO into a COUNTRY-level choice: balancer `auto-director` over loopback outbounds, one per country, plus an
    Xray API entrance on a unix socket so that an external controller can pin AUTO to a country with hysteresis (see _add_auto_director).
Nothing else in the existing layers (and not `_is_final_global_rule`) is modified.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

from loguru import logger

from hiddifypanel.hutils import multiloc

RUNTIME_PATH = "/etc/xray-router/config.json"
GLOBAL_BALANCER = "upstream-balancer"
FAIL_CLOSED_TAG = "block"           # blackhole outbound that a country balancer falls back to when none of its nodes is alive
DIRECTOR_BALANCER = "auto-director"  # AUTO = this balancer: its members are loopback outbounds, one per country; an external controller picks the member
LOOP_OUT_PREFIX = "auto-via-s"       # loopback outbound that re-enters the router as inbound `auto-in-s<N>` and is routed to the country's own balancer
LOOP_IN_PREFIX = "auto-in-s"
API_TAG = "api"                     # Xray API entrance (RoutingService only) on a root-only unix socket: lets the controller override the director balancer
AUTO_DIRECTOR_CONFIG = "/etc/auto-director/config.json"
_SPECIFIC_KEYS = ("domain", "ip", "port", "inboundTag", "protocol", "user", "source", "sourcePort", "attrs")


def _is_catch_all(rule: Any) -> bool:
    return (isinstance(rule, dict) and rule.get("type") == "field" and rule.get("network") == "tcp,udp"
            and not any(rule.get(k) for k in _SPECIFIC_KEYS))


def _targets_global_upstream(rule: dict) -> bool:
    ob = str(rule.get("outboundTag") or "")
    return rule.get("balancerTag") == GLOBAL_BALANCER or ob == "to-de" or ob.startswith("upstream-")


def _is_ours(rule: Any) -> bool:
    return isinstance(rule, dict) and any(str(t).startswith("from-hiddify-s") for t in (rule.get("inboundTag") or []))


def _is_director_rule(rule: Any) -> bool:
    """rules added for the AUTO director: the loopback pass of a country (`auto-in-s<N>`) and the API entrance rule"""
    if not isinstance(rule, dict):
        return False
    tags = [str(t) for t in (rule.get("inboundTag") or [])]
    return bool(tags) and all(t.startswith(LOOP_IN_PREFIX) or t == API_TAG for t in tags)


def strip(config: dict) -> dict:
    """Remove everything this layer adds (so transform() is idempotent and 'feature off' restores the legacy shape)."""
    c = copy.deepcopy(config)
    c["inbounds"] = [i for i in c.get("inbounds", []) if not str(i.get("tag", "")).startswith("from-hiddify-s") and i.get("tag") != API_TAG]
    r = c.setdefault("routing", {})
    r["rules"] = [x for x in r.get("rules", []) if not _is_ours(x) and not _is_director_rule(x)]
    for x in r["rules"]:
        if x.get("balancerTag") == DIRECTOR_BALANCER:       # the generic catch-all goes back to the plain global balancer
            x["balancerTag"] = GLOBAL_BALANCER
    c["outbounds"] = [o for o in c.get("outbounds", []) if not str(o.get("tag", "")).startswith(LOOP_OUT_PREFIX)]
    if (c.get("api") or {}).get("tag") == API_TAG:
        del c["api"]
    obs = c.get("observatory")
    if obs and obs.get("subjectSelector"):
        obs["subjectSelector"] = [t for t in obs["subjectSelector"] if not str(t).startswith(LOOP_OUT_PREFIX)]
    if "balancers" in r:
        r["balancers"] = [b for b in r["balancers"] if not str(b.get("tag", "")).startswith("balancer-s") and b.get("tag") != DIRECTOR_BALANCER]
        for b in r["balancers"]:
            if b.get("tag") == GLOBAL_BALANCER and b.get("fallbackTag") == FAIL_CLOSED_TAG:
                del b["fallbackTag"]                       # added by transform(): AUTO fails closed as well
        if not r["balancers"]:
            del r["balancers"]
    return c


def build_plan(base_port: int = 20808) -> dict:
    """Read the DB: {'slots': {n: {'enabled': bool, 'upstream_ids': [..]}}} - only when the schema + slot rows exist."""
    slots = {}
    if not multiloc.schema_present():
        return {"slots": slots, "base_port": base_port}
    nodes = multiloc.nodes_by_slot()
    for r in multiloc.get_rows("country"):
        slots[int(r.slot)] = {"enabled": bool(r.enabled), "upstream_ids": nodes.get(int(r.slot), [])}
    return {"slots": slots, "base_port": base_port}


def transform(config: dict, plan: dict) -> dict:
    """Pure function: legacy router config + plan -> config with slot entrances. Same input -> same output."""
    c = strip(config)
    slots = plan.get("slots") or {}
    if not slots:
        return c
    base_port = int(plan.get("base_port") or 20808)
    outbound_tags = {o.get("tag"): o for o in c.get("outbounds", [])}
    routing = c.setdefault("routing", {})
    rules = routing.setdefault("rules", [])

    # default entrance -> template for the slot entrances
    default_in = next((i for i in c.get("inbounds", []) if i.get("tag") == "from-hiddify"), None)
    global_bal = next((b for b in routing.get("balancers", []) if b.get("tag") == GLOBAL_BALANCER), None)
    strategy = copy.deepcopy(global_bal["strategy"]) if global_bal and global_bal.get("strategy") else {"type": "leastPing"}

    final_idx = None
    for idx in range(len(rules) - 1, -1, -1):
        if _is_catch_all(rules[idx]):
            final_idx = idx
            break
    if final_idx is None:
        final_idx = len(rules)

    new_inbounds, new_balancers = [], []
    slot_target: dict[int, dict] = {}
    used_selector: list[str] = []
    for slot in sorted(slots):
        info = slots[slot]
        tags = [f"upstream-{u}" for u in info.get("upstream_ids", [])]
        tags = [t for t in tags if t in outbound_tags and outbound_tags[t].get("protocol") != "blackhole"]
        tag_in = multiloc.router_inbound_tag(slot)
        inbound = copy.deepcopy(default_in) if default_in else {
            "listen": "127.0.0.1", "protocol": "socks", "settings": {"auth": "noauth", "udp": True, "ip": "127.0.0.1"},
            "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"], "routeOnly": True}}
        inbound["tag"] = tag_in
        inbound["port"] = multiloc.router_port(slot, base_port)
        new_inbounds.append(inbound)
        if not info.get("enabled") or not tags:
            target = {"outboundTag": "block"}                       # disabled / empty slot: fail closed
        elif len(tags) == 1:
            target = {"outboundTag": tags[0]}
            used_selector += tags
        else:
            bal = multiloc.balancer_tag(slot)
            # fallbackTag = blackhole: with no live node Xray must NOT use its default handler (the first outbound of the file = another country's node)
            new_balancers.append({"tag": bal, "selector": tags, "strategy": copy.deepcopy(strategy), "fallbackTag": FAIL_CLOSED_TAG})
            target = {"balancerTag": bal}
            used_selector += tags
        slot_target[slot] = target

    # per-slot copies of specific rules that point at the GLOBAL upstream (e.g. proxy DNS IPs) - inserted before the original
    out_rules: list[dict] = []
    for idx, rule in enumerate(rules[:final_idx]):
        if (isinstance(rule, dict) and (rule.get("ip") or rule.get("domain")) and not rule.get("inboundTag")
                and _targets_global_upstream(rule)):
            for slot in sorted(slots):
                cp = {k: v for k, v in rule.items() if k not in ("outboundTag", "balancerTag")}
                cp["inboundTag"] = [multiloc.router_inbound_tag(slot)]
                cp.update(slot_target[slot])
                out_rules.append(cp)
        out_rules.append(rule)
    for slot in sorted(slots):
        out_rules.append({"type": "field", "inboundTag": [multiloc.router_inbound_tag(slot)], **slot_target[slot]})
    out_rules.extend(rules[final_idx:])
    routing["rules"] = out_rules

    c["inbounds"] = list(c.get("inbounds", [])) + new_inbounds
    if new_balancers:
        routing["balancers"] = list(routing.get("balancers", [])) + new_balancers
        obs = c.get("observatory")
        if obs is None:
            c["observatory"] = {"subjectSelector": sorted(set(used_selector)), "probeUrl": plan.get("probe_url") or "https://1.1.1.1/",
                                "probeInterval": plan.get("probe_interval") or "1m", "enableConcurrency": True}
        else:
            sel = list(obs.get("subjectSelector", []))
            for t in used_selector:
                if not any(t == s or t.startswith(s) for s in sel):
                    sel.append(t)
            obs["subjectSelector"] = sel
    # AUTO (the global balancer) fails closed as well: when both countries are down, foreign traffic is blocked instead of being handed to the
    # first outbound of the file (the RU-direct rules sit before every balancer rule, so Russian destinations are not affected)
    for b in routing.get("balancers", []):
        if b.get("tag") == GLOBAL_BALANCER:
            b["fallbackTag"] = FAIL_CLOSED_TAG
    ad = plan.get("auto_director") or {}
    if ad.get("enabled"):
        _add_auto_director(c, slots, slot_target, ad)
    if plan.get("probe_interval") and isinstance(c.get("observatory"), dict) and new_balancers:
        c["observatory"]["probeInterval"] = plan["probe_interval"]        # the layer-only apply keeps the live interval equal to the panel setting
    return c


def _add_auto_director(c: dict, slots: dict, slot_target: dict, ad: dict) -> None:
    """AUTO = the `auto-director` balancer over one LOOPBACK outbound per country. A loopback outbound hands the connection back to the router as the inbound
    `auto-in-s<N>`, which is routed (first rule of the file, so it can never reach the catch-all again) to that country's own balancer: inside a country Xray still
    picks the best node and fails closed. WHICH country AUTO uses is decided by an external controller (hysteresis, smoothing, confirmation) through the Xray API
    (`xray api bo`: balancer override, no restart, open connections are not touched); without a controller the balancer's own leastPing over the countries'
    full-path probes decides, so AUTO keeps working. Needs two countries that can carry traffic."""
    live = [s for s in sorted(slot_target) if (slots.get(s) or {}).get("enabled") and slot_target[s].get("outboundTag") != FAIL_CLOSED_TAG]
    if len(live) < 2:
        return
    routing = c["routing"]
    loops, loop_rules = [], []
    for s in live:
        tag = f"{LOOP_OUT_PREFIX}{s}"
        c.setdefault("outbounds", []).append({"tag": tag, "protocol": "loopback", "settings": {"inboundTag": f"{LOOP_IN_PREFIX}{s}"}})
        loop_rules.append({"type": "field", "inboundTag": [f"{LOOP_IN_PREFIX}{s}"], **slot_target[s]})
        loops.append(tag)
    api_rule = {"type": "field", "inboundTag": [API_TAG], "outboundTag": API_TAG}
    routing["rules"] = [api_rule] + loop_rules + list(routing.get("rules", []))
    for r in routing["rules"]:
        if _is_catch_all(r) and r.get("balancerTag") == GLOBAL_BALANCER:
            r["balancerTag"] = DIRECTOR_BALANCER
    routing["balancers"] = list(routing.get("balancers", [])) + [
        {"tag": DIRECTOR_BALANCER, "selector": loops, "strategy": {"type": "leastPing"}, "fallbackTag": FAIL_CLOSED_TAG}]
    obs = c.get("observatory")
    if isinstance(obs, dict):
        sel = list(obs.get("subjectSelector", []))
        for t in loops:
            if t not in sel:
                sel.append(t)
        obs["subjectSelector"] = sel
    else:
        c["observatory"] = {"subjectSelector": list(loops), "probeUrl": ad.get("probe_url") or "https://1.1.1.1/", "probeInterval": ad.get("probe_interval") or "10s",
                            "enableConcurrency": True}
    c["api"] = {"tag": API_TAG, "services": ["RoutingService"]}
    c["inbounds"] = list(c.get("inbounds", [])) + [
        {"tag": API_TAG, "listen": f"{ad.get('api_socket') or '/run/xray-router-api.sock'},0600", "protocol": "dokodemo-door", "settings": {"address": "127.0.0.1"}}]


def check_invariants(config: dict) -> list[str]:
    """Properties that MUST hold for any produced config (also used by the tests and by verify.sh)."""
    problems = []
    rules = config.get("routing", {}).get("rules", [])
    outs = {o.get("tag") for o in config.get("outbounds", [])}
    bals = {b.get("tag"): b for b in config.get("routing", {}).get("balancers", [])}
    final_idx = max((i for i, r in enumerate(rules) if _is_catch_all(r)), default=None)
    # 1) every RU-direct rule precedes every slot final (RU must stay in RU from every entrance)
    ours = [i for i, r in enumerate(rules) if _is_ours(r) and not (r.get("ip") or r.get("domain"))]
    ru = [i for i, r in enumerate(rules) if r.get("outboundTag") == "direct-ru"]
    if ours and ru and max(ru) > min(ours):
        problems.append("a direct-ru rule appears AFTER a slot final rule")
    # 2) slot finals precede the generic catch-all
    if ours and final_idx is not None and max(ours) > final_idx:
        problems.append("a slot final rule appears after the generic catch-all")
    # 3) fail-closed: every country balancer falls back to the blackhole and to nothing else (a country never borrows another country's node)
    for tag, b in bals.items():
        if tag.startswith("balancer-s") and b.get("fallbackTag") != FAIL_CLOSED_TAG:
            problems.append(f"{tag}: fallbackTag must be '{FAIL_CLOSED_TAG}' to fail closed, got {b.get('fallbackTag')!r}")
    layer_active = any(str(i.get("tag", "")).startswith("from-hiddify-s") for i in config.get("inbounds", []))
    if layer_active and GLOBAL_BALANCER in bals and bals[GLOBAL_BALANCER].get("fallbackTag") != FAIL_CLOSED_TAG:
        problems.append(f"{GLOBAL_BALANCER}: fallbackTag must be '{FAIL_CLOSED_TAG}' (AUTO fails closed when both countries are down), got {bals[GLOBAL_BALANCER].get('fallbackTag')!r}")
    if any(b.get("fallbackTag") == FAIL_CLOSED_TAG for b in bals.values()):
        block = next((o for o in config.get("outbounds", []) if o.get("tag") == FAIL_CLOSED_TAG), None)
        if block is None or block.get("protocol") != "blackhole":
            problems.append(f"outbound '{FAIL_CLOSED_TAG}' is missing or is not a blackhole: the balancers cannot fail closed")
    # 3b) AUTO director: loopback members exist, every loopback pass is routed before the catch-all (otherwise a connection could loop), the catch-all uses the
    #     director, the director fails closed, the API entrance exists and is reachable only from the unix socket
    if DIRECTOR_BALANCER in bals:
        d = bals[DIRECTOR_BALANCER]
        loops = list(d.get("selector", []))
        if d.get("fallbackTag") != FAIL_CLOSED_TAG:
            problems.append(f"{DIRECTOR_BALANCER}: fallbackTag must be '{FAIL_CLOSED_TAG}' (AUTO fails closed when no country answers), got {d.get('fallbackTag')!r}")
        if len(loops) < 2:
            problems.append(f"{DIRECTOR_BALANCER} needs two countries, has {loops}")
        by_tag = {o.get("tag"): o for o in config.get("outbounds", [])}
        loop_idx = []
        for t in loops:
            o = by_tag.get(t)
            if not o or o.get("protocol") != "loopback":
                problems.append(f"{DIRECTOR_BALANCER} member {t} is missing or is not a loopback outbound")
                continue
            tag_in = (o.get("settings") or {}).get("inboundTag")
            idx = [i for i, r in enumerate(rules) if tag_in in (r.get("inboundTag") or []) and (r.get("outboundTag") or r.get("balancerTag"))]
            if not idx:
                problems.append(f"loopback {t}: no rule routes the inbound {tag_in}")
            else:
                loop_idx.append(idx[0])
        all_tags = [o.get("tag") for o in config.get("outbounds", []) if o.get("tag")]
        eff = {t for t in all_tags if any(t.startswith(x) for x in loops)}
        if eff != set(loops):
            problems.append(f"{DIRECTOR_BALANCER}: prefix-matching selector would also pick {sorted(eff - set(loops))}")
        if final_idx is None or any(i > final_idx for i in loop_idx):
            problems.append("a loopback pass rule appears after the generic catch-all (connections could loop)")
        if final_idx is not None and rules[final_idx].get("balancerTag") != DIRECTOR_BALANCER:
            problems.append(f"the generic catch-all must use {DIRECTOR_BALANCER}, uses {rules[final_idx].get('balancerTag') or rules[final_idx].get('outboundTag')!r}")
        if (config.get("api") or {}).get("tag") != API_TAG or "RoutingService" not in ((config.get("api") or {}).get("services") or []):
            problems.append("the director needs the API entrance with RoutingService")
        api_in = [i for i in config.get("inbounds", []) if i.get("tag") == API_TAG]
        if not api_in or not str(api_in[0].get("listen", "")).startswith("/"):
            problems.append("the API entrance must listen on a unix socket (never on a TCP port)")
        if not any(API_TAG in (r.get("inboundTag") or []) and r.get("outboundTag") == API_TAG for r in rules):
            problems.append("no rule routes the API entrance to the API outbound")
        obs_sel = (config.get("observatory") or {}).get("subjectSelector", [])
        for t in loops:
            if not any(t == x or t.startswith(x) for x in obs_sel):
                problems.append(f"{DIRECTOR_BALANCER} member {t} not covered by the observatory")
    # 4) slot rules only reference existing outbounds / balancers
    for r in rules:
        if _is_ours(r):
            if r.get("outboundTag") and r["outboundTag"] not in outs:
                problems.append(f"rule refers to missing outbound {r['outboundTag']}")
            if r.get("balancerTag") and r["balancerTag"] not in bals:
                problems.append(f"rule refers to missing balancer {r['balancerTag']}")
    # 5) Xray matches balancer selectors by PREFIX ("upstream-1" also selects "upstream-10"): the effective membership must equal
    #    the intended membership, otherwise nodes of another country could silently join this country's balancer.
    all_tags = [o.get("tag") for o in config.get("outbounds", []) if o.get("tag")]
    for tag, b in bals.items():
        if tag.startswith("balancer-s"):
            intended = set(b.get("selector", []))
            effective = {t for t in all_tags if any(t.startswith(s) for s in intended)}
            if effective != intended:
                problems.append(f"{tag}: prefix-matching selector would also pick {sorted(effective - intended)}")
    # 6) observatory covers all balancer members
    obs = config.get("observatory")
    for tag, b in bals.items():
        if tag.startswith("balancer-s"):
            sel = (obs or {}).get("subjectSelector", [])
            for m in b.get("selector", []):
                if not any(m == s or m.startswith(s) for s in sel):
                    problems.append(f"{tag} member {m} not covered by observatory")
    return problems


READY_SECONDS = 900     # one xray-router start takes ~4-5 min with the production rule set on 2 vCPU (15k single-entry rules; measured 245-274 s on production,
                        # the owner has seen up to ~8 min under load; a compacted config (router_compact.py) starts in about a second): waiting longer only delays the verdict, a too short limit would roll a good config back


def _port_of(inbound: dict) -> int | None:
    try:
        return int(inbound.get("port"))
    except (TypeError, ValueError):
        return None


def router_ports(config: dict) -> list[int]:
    return sorted({p for p in (_port_of(i) for i in config.get("inbounds", [])) if p})


def port_open(port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def busy_new_ports(old: dict, new: dict) -> list[int]:
    """ports the new config adds that somebody else already listens on (the running router does not own them yet)"""
    mine = set(router_ports(old))
    return [p for p in router_ports(new) if p not in mine and port_open(p)]


def _listening_inodes(port: int) -> set:
    """inodes of the sockets that LISTEN on this local port (IPv4 + IPv6)"""
    out = set()
    for fn in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(fn) as f:
                next(f, None)
                for line in f:
                    parts = line.split()
                    if len(parts) > 9 and parts[3] == "0A" and int(parts[1].rsplit(":", 1)[1], 16) == int(port):
                        out.add(parts[9])
        except OSError:
            pass
    return out


def _socket_inodes_of(pid: int) -> set:
    out = set()
    try:
        for fd in os.listdir(f"/proc/{pid}/fd"):
            try:
                link = os.readlink(f"/proc/{pid}/fd/{fd}")
            except OSError:
                continue
            if link.startswith("socket:["):
                out.add(link[8:-1])
    except OSError:
        pass
    return out


def _router_main_pid() -> int:
    try:
        r = subprocess.run(["systemctl", "show", "-p", "MainPID", "--value", "xray-router"], capture_output=True, text=True, timeout=10)
        return int((r.stdout or "0").strip() or 0)
    except Exception:
        return 0


def entrances_listening(ports) -> bool:
    """every port accepts connections AND the listening socket belongs to xray-router's own main process: a foreign process holding a router
    port (so that xray itself could not start) must not look like a healthy router"""
    pid = _router_main_pid()
    if not pid:
        return False
    mine = _socket_inodes_of(pid)
    return all(port_open(p) and bool(_listening_inodes(p) & mine) for p in ports)


def wait_ready(ports: list[int], seconds: float | None = None) -> tuple[bool, float]:
    """xray-router is Type=simple: `systemctl restart` returns at once and `is-active` says "active" while the process still parses the config
    (~2 min with the production rule set). The entrances only listen once the config was accepted, so waiting for them is the real health check."""
    limit = float(seconds if seconds is not None else (os.environ.get("MULTILOC_ROUTER_READY_SECONDS") or READY_SECONDS))
    t0 = time.monotonic()      # monotonic: an NTP / hypervisor clock step must not end the wait early (spurious rollback) or stretch it
    while True:
        if entrances_listening(ports):
            return True, round(time.monotonic() - t0, 1)
        if time.monotonic() - t0 >= limit:
            return False, round(time.monotonic() - t0, 1)
        time.sleep(0.5)


def _is_bulk(rule: Any) -> bool:
    """untouched legacy bulk rule: exactly one ip / one domain and a plain outbound target (the production router config has ~15k of them)"""
    if not isinstance(rule, dict) or _is_ours(rule):
        return False
    keys = set(rule) - {"type"}
    if keys == {"ip", "outboundTag"}:
        return len(rule["ip"]) == 1
    if keys == {"domain", "outboundTag"}:
        return len(rule["domain"]) == 1
    return False


def slim(config: dict) -> dict:
    """The same config without the bulk rules. `xray -test` time grows with the number of RULES (~5 ms each: 108 s for 15k rules on 2 cores) while
    everything this layer adds (entrances, balancers, final rules, references) is kept, so the slim copy validates our part in a fraction of a second."""
    c = dict(config)
    r = dict(c.get("routing", {}))
    r["rules"] = [x for x in r.get("rules", []) if not _is_bulk(x)]
    c["routing"] = r
    return c


def current_plan() -> dict:
    from hiddifypanel.models import ConfigEnum, get_hconfigs
    h = get_hconfigs()
    plan = build_plan(int(h.get(ConfigEnum.commercial_router_port) or 20808))
    plan["probe_url"] = h.get(ConfigEnum.commercial_router_probe_url)
    plan["probe_interval"] = h.get(ConfigEnum.commercial_router_probe_interval)
    try:                                       # the AUTO director is off unless the controller config says enabled
        ad = json.loads(Path(AUTO_DIRECTOR_CONFIG).read_text())
        if isinstance(ad, dict) and ad.get("enabled"):
            plan["auto_director"] = {"enabled": True, "api_socket": ad.get("api_socket") or "/run/xray-router-api.sock",
                                     "probe_url": plan["probe_url"], "probe_interval": plan["probe_interval"]}
    except (OSError, ValueError):
        pass
    return plan


def missing_nodes(config: dict, plan: dict) -> dict[int, list[int]]:
    """enabled slots whose assigned nodes are not usable outbounds in this router config (node added/enabled in the panel after the last FULL apply)"""
    usable = {o.get("tag") for o in config.get("outbounds", []) if o.get("protocol") != "blackhole"}
    out = {}
    for slot, info in (plan.get("slots") or {}).items():
        miss = [u for u in info.get("upstream_ids", []) if f"upstream-{u}" not in usable]
        if miss and info.get("enabled"):
            out[int(slot)] = miss
    return out


def pending(target_path: str = RUNTIME_PATH) -> dict:
    """does the live router config already reflect the database? (read only)"""
    p = Path(target_path)
    if not p.exists():
        return {"in_sync": None, "reason": "router config missing"}
    live = json.loads(p.read_text())
    plan = current_plan()
    return {"in_sync": transform(live, plan) == live, "missing_nodes": missing_nodes(live, plan)}


def _restore_previous(backup: Path, target: Path, old_config: dict) -> bool:
    """Put the previous router config back, restart xray-router and wait until its entrances listen. Never raises: returns whether the router serves again."""
    try:
        shutil.copy2(backup, target)
    except Exception as exc:
        logger.error(f"multiloc: cannot restore {backup} -> {target}: {exc}")
        return False
    try:
        subprocess.run(["systemctl", "reset-failed", "xray-router"], capture_output=True, text=True, timeout=10, check=False)
        subprocess.run(["systemctl", "restart", "xray-router"], capture_output=True, text=True, timeout=60)
    except Exception as exc:       # a stuck systemctl: the wait below decides whether the router is serving anyway
        logger.error(f"multiloc: restart after restoring the previous router config: {exc}")
    try:
        ok, _ = wait_ready(router_ports(old_config))
        return ok
    except Exception:
        return False


def patch_config(target_path: str = RUNTIME_PATH, restart: bool = True) -> dict:
    """Apply the layer to the router config file. Used as the LAST step of the legacy apply pipeline AND on its own (`multiloc_ctl.py apply`,
    one restart instead of the pipeline's five). Safety: invariants, busy-port pre-check, backup, `xray -test` of the slim copy, atomic replace,
    restart, wait until the entrances really listen, restore + restart + wait on failure. No change -> no restart."""
    import datetime

    if os.environ.get("MULTILOC_DEFER_ROUTER_RESTART") == "1":
        # inert unless the background full apply sets it: the legacy pipeline then runs in a private copy of /etc/xray-router with its restarts deferred and the
        # runner does ONE restart at the end (with the readiness wait and the rollback) - neither the busy-port check nor the restart belong to this step then
        restart = False

    target = Path(target_path)
    if not target.exists():
        raise RuntimeError(f"router config {target} is missing: nothing to apply the layer to (run the full apply once: multiloc_ctl.py apply --full)")
    original_text = target.read_text()
    original = json.loads(original_text)
    plan = current_plan()
    new = transform(original, plan)
    problems = check_invariants(new)
    if problems:
        raise RuntimeError("multiloc router invariants violated: " + "; ".join(problems))
    warnings = [f"slot {s}: nodes {ids} are not usable outbounds in the live router config yet (added or enabled after the last full apply) - "
                f"run the full apply (multiloc_ctl.py apply --full)" for s, ids in sorted(missing_nodes(new, plan).items())]
    if new == original:
        return {"changed": False, "reason": "no change", "warnings": warnings}
    busy = busy_new_ports(original, new)
    if restart and busy:
        raise RuntimeError(f"router ports {busy} are already in use by another process; nothing was changed")

    backup_dir = Path("/root/xray-router-backups")
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup = backup_dir / f"config.json.{stamp}.before-multiloc.bak"
    backup.write_text(original_text)

    tmp = target.with_name(target.name + ".multiloc.tmp.json")
    slim_path = target.with_name(target.name + ".multiloc.slim.json")
    tmp.write_text(json.dumps(new, indent=2, ensure_ascii=False) + "\n")
    json.loads(tmp.read_text())
    xray_bin = shutil.which("xray") or "/usr/bin/xray"
    try:
        slim_path.write_text(json.dumps(slim(new)))
        res = subprocess.run([xray_bin, "run", "-test", "-config", str(slim_path)], capture_output=True, text=True, timeout=120)
    finally:
        slim_path.unlink(missing_ok=True)
    if res.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("multiloc router config failed xray -test:\n" + (res.stdout or "") + (res.stderr or ""))
    os.replace(tmp, target)

    ready = None
    if restart:
        try:
            subprocess.run(["systemctl", "reset-failed", "xray-router"], check=False, timeout=10)
            r = subprocess.run(["systemctl", "restart", "xray-router"], capture_output=True, text=True, timeout=60)
            if r.returncode != 0:
                raise RuntimeError("systemctl restart xray-router failed: " + ((r.stderr or r.stdout or "").strip() or f"rc={r.returncode}"))
            ok, waited = wait_ready(router_ports(new))
            if not ok:
                raise RuntimeError(f"no router entrance listening after {waited}s")
            ready = waited
        except BaseException as exc:            # ANY failure after the replace: a systemctl timeout, an error while waiting, an interrupt ...
            back = _restore_previous(backup, target, original)
            if not isinstance(exc, Exception):
                raise
            raise RuntimeError(f"xray-router did not start serving the multiloc config ({exc}); previous config restored and "
                               + ("serving again" if back else "NOT serving yet - check `systemctl status xray-router`")) from exc
    logger.info(f"multiloc router layer applied backup={backup}")
    return {"changed": True, "backup": str(backup), "ready_seconds": ready, "warnings": warnings,
            "slots": {s: {"enabled": v["enabled"], "nodes": len(v["upstream_ids"])} for s, v in plan["slots"].items()}}
