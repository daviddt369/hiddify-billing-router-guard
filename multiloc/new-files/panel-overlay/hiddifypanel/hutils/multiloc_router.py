"""xray-router layer for multi-location: ONE extra post-processing layer appended after the existing commercial_routing layers.

transform(config, plan) is a PURE function (unit-testable, idempotent):
  * adds one socks entrance per slot  (from-hiddify-s<N>, 127.0.0.1:<router_port>+2+N, same sniffing as the default entrance)
  * adds one balancer per slot with >=2 live-capable nodes (leastPing, NO fallbackTag -> a dead country fails closed)
  * inserts the slot final rules right BEFORE the generic catch-all, i.e. after every RU-direct / DNS / block rule that the
    existing layers produced, so local traffic from any entrance stays local
  * duplicates the "global-upstream-bound" specific rules (e.g. proxy-DNS IPs -> upstream-balancer) per slot, so even those
    connections keep the chosen country
  * makes sure the observatory covers the slot nodes.
Nothing in the existing layers (and not `_is_final_global_rule`) is modified.
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
_SPECIFIC_KEYS = ("domain", "ip", "port", "inboundTag", "protocol", "user", "source", "sourcePort", "attrs")


def _is_catch_all(rule: Any) -> bool:
    return (isinstance(rule, dict) and rule.get("type") == "field" and rule.get("network") == "tcp,udp"
            and not any(rule.get(k) for k in _SPECIFIC_KEYS))


def _targets_global_upstream(rule: dict) -> bool:
    ob = str(rule.get("outboundTag") or "")
    return rule.get("balancerTag") == GLOBAL_BALANCER or ob == "to-de" or ob.startswith("upstream-")


def _is_ours(rule: Any) -> bool:
    return isinstance(rule, dict) and any(str(t).startswith("from-hiddify-s") for t in (rule.get("inboundTag") or []))


def strip(config: dict) -> dict:
    """Remove everything this layer adds (so transform() is idempotent and 'feature off' restores the legacy shape)."""
    c = copy.deepcopy(config)
    c["inbounds"] = [i for i in c.get("inbounds", []) if not str(i.get("tag", "")).startswith("from-hiddify-s")]
    r = c.setdefault("routing", {})
    r["rules"] = [x for x in r.get("rules", []) if not _is_ours(x)]
    if "balancers" in r:
        r["balancers"] = [b for b in r["balancers"] if not str(b.get("tag", "")).startswith("balancer-s")]
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
            new_balancers.append({"tag": bal, "selector": tags, "strategy": copy.deepcopy(strategy)})  # no fallbackTag
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
    return c


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
    # 3) fail-closed: no slot balancer has a fallback
    for tag, b in bals.items():
        if tag.startswith("balancer-s") and b.get("fallbackTag"):
            problems.append(f"{tag} has a fallbackTag (must fail closed)")
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
                        # the owner has seen up to ~8 min under load): waiting longer only delays the verdict, a too short limit would roll a good config back


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
