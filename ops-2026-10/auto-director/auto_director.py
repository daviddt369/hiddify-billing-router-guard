#!/usr/bin/env python3
"""auto-director: chooses the AUTO exit COUNTRY for xray-router. Python 3 standard library + curl + the xray binary (its `api bo/bi` client).

Why: the router's own leastPing for AUTO compares single NODES by their last probe, so the exit address flips with every ping wobble. Here the router only
provides the mechanism (balancer `auto-director` over one loopback outbound per country, see hutils/multiloc_router.py) and THIS controller makes the decision:

  * both countries are measured all the time, over the FULL route (the router's own country entrances: RU -> exit -> Internet), several probes per cycle
  * smoothing (EWMA) of the measurements; the current country is kept unless the other one is better by more than `margin_ms` for `confirm_cycles` cycles
    in a row AND the last switch is older than `min_dwell_s`
  * a country counts as down after `fail_cycles` failed cycles in a row (one lost probe never switches anything); then AUTO moves to the other country at once
  * both down -> AUTO is pinned to the blackhole (fail closed); back as soon as a country answers twice in a row
  * the choice is applied with `xray api bo` (balancer override): no restart, only NEW connections follow, open ones are not touched
  * if this process dies, systemd removes the override and the router falls back to its own country-level leastPing: AUTO keeps working

  auto_director.py run            the daemon (systemd)
  auto_director.py once           one measurement + decision cycle, printed (state is saved, the override is applied)
  auto_director.py status         print the last status
  auto_director.py pin DE|NL      keep AUTO on this country while it is healthy      auto_director.py unpin
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import signal
import statistics
import subprocess
import sys
import time

CONF_PATH = os.environ.get("AUTO_DIRECTOR_CONF", "/etc/auto-director/config.json")
STATE_DIR = os.environ.get("AUTO_DIRECTOR_STATE", "/var/lib/auto-director")
NONE = "NONE"

DEFAULTS = {
    "enabled": True,
    "xray": "/usr/bin/xray",
    "api_socket": "/run/xray-router-api.sock",
    "balancer": "auto-director",
    "block_outbound": "block",
    "countries": [{"name": "DE", "entrance": 20811, "outbound": "auto-via-s1"}, {"name": "NL", "entrance": 20813, "outbound": "auto-via-s3"}],
    "probe_url": "http://cp.cloudflare.com/generate_204",
    "probe_ok_codes": [200, 204],
    "interval_s": 10, "probes_per_cycle": 2, "probe_timeout_s": 5,
    "margin_ms": 20, "confirm_cycles": 3, "fail_cycles": 3, "min_dwell_s": 300, "ewma_alpha": 0.3,
    "recover_cycles": 2,
}


def load_conf(path=CONF_PATH):
    c = dict(DEFAULTS)
    try:
        with open(path) as f:
            c.update(json.load(f))
    except (OSError, ValueError):
        pass
    return c


# ------------------------------------------------------------------------------------------------------------------ the decision (pure, unit-tested)
class Decider:
    """step(now, results) -> (target, reason). target is a country name, NONE (both down: fail closed) or None (no decision yet: leave the router alone).
    results = {country: latency_ms or None}"""

    def __init__(self, names, p, state=None):
        self.names, self.p = list(names), p
        s = state or {}
        self.cur = s.get("cur")
        self.ewma = {n: (s.get("ewma") or {}).get(n) for n in self.names}
        self.fails = {n: int((s.get("fails") or {}).get(n, 0)) for n in self.names}
        self.ok_streak = {n: int((s.get("ok_streak") or {}).get(n, 0)) for n in self.names}
        self.better_run = int(s.get("better_run", 0))
        self.last_switch = float(s.get("last_switch", 0))
        self.pin = None

    def snapshot(self):
        return {"cur": self.cur, "ewma": self.ewma, "fails": self.fails, "ok_streak": self.ok_streak, "better_run": self.better_run, "last_switch": self.last_switch}

    def healthy(self, n):
        return self.fails[n] < self.p["fail_cycles"] and self.ewma[n] is not None

    def _other(self, n):
        return [m for m in self.names if m != n][0]

    def _switch(self, now, target, reason):
        self.cur, self.last_switch, self.better_run = target, now, 0
        return target, reason

    def _first(self, now, target, reason):
        """the very first choice after a start is a guess from one sample: it must not hold the dwell time against the first real correction"""
        t, r = self._switch(now, target, reason)
        self.last_switch = 0.0
        return t, r

    def step(self, now, results):
        a = float(self.p["ewma_alpha"])
        for n in self.names:
            lat = results.get(n)
            if lat is None:
                self.fails[n] += 1
                self.ok_streak[n] = 0
            else:
                self.fails[n] = 0
                self.ok_streak[n] += 1
                self.ewma[n] = lat if self.ewma[n] is None else a * lat + (1 - a) * self.ewma[n]
        up = [n for n in self.names if self.healthy(n)]
        pin = self.pin if self.pin in self.names and self.pin in up else None

        if self.cur is None:                                    # start: take the best country that answered, no waiting
            ready = [n for n in self.names if self.ok_streak[n] >= 1]
            if pin:
                return self._first(now, pin, "start: pinned by the operator")
            if ready:
                best = min(ready, key=lambda n: self.ewma[n])
                return self._first(now, best, f"start: {best} answers first/fastest ({self.ewma[best]:.0f} ms)")
            if all(self.fails[n] >= self.p["fail_cycles"] for n in self.names):
                return self._first(now, NONE, "start: no country answers")
            return None, None

        if self.cur == NONE:                                    # closed: reopen when a country answered `recover_cycles` times in a row
            back = [n for n in self.names if self.ok_streak[n] >= self.p["recover_cycles"] and self.healthy(n)]
            if pin and pin in back:
                return self._switch(now, pin, f"{pin} is back (pinned)")
            if back:
                best = min(back, key=lambda n: self.ewma[n])
                return self._switch(now, best, f"{best} is back ({self.ewma[best]:.0f} ms)")
            return NONE, None

        cur = self.cur
        if pin and pin != cur:
            return self._switch(now, pin, f"pinned by the operator: {pin}")
        if not self.healthy(cur):                               # current country is down: leave at once (the confirmation was the failed cycles)
            other = self._other(cur)
            if self.healthy(other):
                return self._switch(now, other, f"{cur} is down ({self.fails[cur]} failed cycles in a row), {other} answers")
            return self._switch(now, NONE, f"{cur} and {other} are down: AUTO is closed")
        other = self._other(cur)
        if pin == cur:
            self.better_run = 0
            return cur, None
        if self.healthy(other) and self.ewma[cur] - self.ewma[other] > self.p["margin_ms"]:
            self.better_run += 1
        else:
            self.better_run = 0
        if self.better_run >= self.p["confirm_cycles"] and now - self.last_switch >= self.p["min_dwell_s"]:
            gain = self.ewma[cur] - self.ewma[other]
            return self._switch(now, other, f"{other} is faster by {gain:.0f} ms for {self.p['confirm_cycles']} cycles in a row ({self.ewma[other]:.0f} vs {self.ewma[cur]:.0f} ms)")
        return cur, None


# ------------------------------------------------------------------------------------------------------------------ measuring and applying
def probe_once(entrance, conf):
    """one request through the router entrance of a country: returns latency in ms (time to the first byte of the answer) or None"""
    cmd = ["curl", "-s", "-o", "/dev/null", "-m", str(conf["probe_timeout_s"]), "--socks5-hostname", f"127.0.0.1:{entrance}",
           "-w", "%{http_code} %{time_starttransfer}", conf["probe_url"]]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=conf["probe_timeout_s"] + 3)
        code, t = r.stdout.split()[:2]
        if int(code) in conf["probe_ok_codes"] and float(t) > 0:
            return float(t) * 1000.0
    except Exception:
        pass
    return None


def measure(conf, probe=probe_once):
    """both countries in parallel, `probes_per_cycle` sequential probes each; the cycle value is the median of the answers (None if none answered)"""
    def one(c):
        vals = [v for v in (probe(c["entrance"], conf) for _ in range(int(conf["probes_per_cycle"]))) if v is not None]
        return c["name"], (statistics.median(vals) if vals else None)
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(conf["countries"])) as ex:
        return dict(ex.map(one, conf["countries"]))


def xray_api(conf, *args):
    cmd = [conf["xray"], "api", *args[:1], f"--server=unix://{conf['api_socket']}", *args[1:]]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    return r.returncode, (r.stdout + r.stderr).strip()


def current_override(conf):
    """the outbound tag the router currently pins the director balancer to, '' when none, None when the API does not answer"""
    try:
        rc, out = xray_api(conf, "bi", conf["balancer"])
    except Exception:
        return None
    if rc != 0:
        return None
    if "Selecting Override:" not in out:
        return ""
    # `xray api bi` prints the override as "- Selecting Override:  <count> <tag>" and the normal choice after "- Selects:" - on one line or on several, depending on the build
    parts = out.split("Selecting Override:")[1].split("Selects:")[0].split()
    parts = [x for x in parts if x != "-"]
    if not parts or parts[0] == "0":
        return ""
    return parts[1] if len(parts) >= 2 else ""


def apply_target(conf, target):
    """pin the director balancer to the target's loopback outbound (or to the blackhole). Returns True when the router now says so."""
    if target is None:
        return True
    tag = conf["block_outbound"] if target == NONE else next(c["outbound"] for c in conf["countries"] if c["name"] == target)
    if current_override(conf) == tag:
        return True
    try:
        xray_api(conf, "bo", "-b", conf["balancer"], tag)
    except Exception:
        return False
    return current_override(conf) == tag


def release_override(conf):
    try:
        xray_api(conf, "bo", "-b", conf["balancer"], "-r")
    except Exception:
        pass


# ------------------------------------------------------------------------------------------------------------------ state, status, daemon
def _load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


class Director:
    def __init__(self, conf, now=time.time, probe=probe_once, applier=apply_target, state_dir=STATE_DIR):
        self.conf, self.now, self.probe, self.applier, self.dir = conf, now, probe, applier, state_dir
        self.names = [c["name"] for c in conf["countries"]]
        st = _load(os.path.join(state_dir, "state.json"), {})
        self.decider = Decider(self.names, conf, st.get("decider"))
        self.switches = list(st.get("switches", []))
        self.reason = st.get("reason", "")

    def pin_file(self):
        return os.path.join(self.dir, "pin")

    def cycle(self):
        t = self.now()
        try:
            with open(self.pin_file()) as f:
                pin = f.read().strip().upper()
        except OSError:
            pin = ""
        self.decider.pin = pin or None
        res = measure(self.conf, self.probe)
        before = self.decider.cur
        target, why = self.decider.step(t, res)
        if why and target != before:
            self.switches.append({"t": t, "from": before, "to": target, "reason": why})
            self.switches = self.switches[-50:]
            self.reason = why
        applied = self.applier(self.conf, target)
        hour = [s for s in self.switches if t - s["t"] <= 3600 and s["from"] is not None]
        status = {"ts": t, "iso": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)), "cur": self.decider.cur, "target": target, "applied": bool(applied),
                  "measured_ms": {k: (round(v, 1) if v is not None else None) for k, v in res.items()},
                  "ewma_ms": {k: (round(v, 1) if v is not None else None) for k, v in self.decider.ewma.items()},
                  "fails": self.decider.fails, "better_run": self.decider.better_run, "last_switch": self.decider.last_switch,
                  "switches_last_hour": len(hour), "reason": self.reason, "pin": self.decider.pin, "params": {k: self.conf[k] for k in ("margin_ms", "confirm_cycles", "fail_cycles", "min_dwell_s")}}
        _save(os.path.join(self.dir, "status.json"), status)
        _save(os.path.join(self.dir, "state.json"), {"decider": self.decider.snapshot(), "switches": self.switches, "reason": self.reason})
        return status


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "run"
    conf = load_conf()
    os.makedirs(STATE_DIR, exist_ok=True)
    if cmd == "status":
        print(json.dumps(_load(os.path.join(STATE_DIR, "status.json"), {}), indent=1, ensure_ascii=False))
        return 0
    if cmd == "pin":
        name = (argv[1] if len(argv) > 1 else "").upper()
        if name not in [c["name"] for c in conf["countries"]]:
            print("pin DE|NL"); return 2
        open(os.path.join(STATE_DIR, "pin"), "w").write(name + "\n")
        print(f"AUTO is pinned to {name} while it is healthy (auto_director.py unpin to release)")
        return 0
    if cmd == "unpin":
        try:
            os.unlink(os.path.join(STATE_DIR, "pin"))
        except OSError:
            pass
        print("released")
        return 0
    if not conf.get("enabled"):
        print("auto-director is disabled in the config: nothing to do", file=sys.stderr)
        return 0
    d = Director(conf)
    if cmd == "once":
        print(json.dumps(d.cycle(), indent=1, ensure_ascii=False))
        return 0
    stop = {"flag": False}
    signal.signal(signal.SIGTERM, lambda *a: stop.update(flag=True))
    signal.signal(signal.SIGINT, lambda *a: stop.update(flag=True))
    print(f"auto-director started: countries {d.names}, interval {conf['interval_s']} s, margin {conf['margin_ms']} ms, confirm {conf['confirm_cycles']}, "
          f"fail {conf['fail_cycles']}, dwell {conf['min_dwell_s']} s", flush=True)
    last_reason = None
    while not stop["flag"]:
        t0 = time.time()
        try:
            st = d.cycle()
            if st["reason"] != last_reason:
                print(f"{st['iso']} AUTO -> {st['cur']}: {st['reason']}", flush=True)
                last_reason = st["reason"]
        except Exception as exc:                  # never die on a surprise: the next cycle tries again
            print(f"cycle failed: {type(exc).__name__}: {exc}", flush=True)
        left = float(conf["interval_s"]) - (time.time() - t0)
        end = time.time() + max(left, 1.0)
        while time.time() < end and not stop["flag"]:
            time.sleep(0.5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
