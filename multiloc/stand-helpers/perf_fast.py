#!/usr/bin/env python3
"""Fast-path router apply (`multiloc_ctl.py apply`): wall time, xray-router restarts and availability gaps of the router entrance while it runs.
Toggles the node assignment of slot 2 (DB change -> router change) twice.  usage: perf_fast.py <label>"""
import json
import subprocess
import sys
import threading
import time

label = sys.argv[1] if len(sys.argv) > 1 else "fast"
URL = "http://12.0.0.20:18080/"
VENV = "/opt/hiddify-manager/.venv313/bin/python"
CTL = [VENV, "/opt/hiddify-manager/scripts/multiloc_ctl.py"]


def probe_loop(stop, samples):
    while not stop.is_set():
        t0 = time.time()
        r = subprocess.run(["curl", "-s", "--max-time", "1.0", "--socks5-hostname", "127.0.0.1:20808", "-o", "/dev/null", URL], capture_output=True)
        samples.append((t0, r.returncode == 0))
        time.sleep(0.05)


def restarts_since(t_from):
    out = subprocess.run(["journalctl", "-u", "xray-router", "--no-pager", "--since", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t_from)),
                          "-o", "cat"], capture_output=True, text=True).stdout
    return out.count("Started xray-router")


def ctl(*a):
    return subprocess.run(CTL + list(a), capture_output=True, text=True, cwd="/opt/hiddify-manager/hiddify-panel")


steps = [("unassign", ["unassign", "4"]), ("assign", ["assign", "2", "4"])]
for i, (what, args) in enumerate(steps, 1):
    r = ctl(*args)
    assert r.returncode == 0, r.stderr
    samples, stop = [], threading.Event()
    th = threading.Thread(target=probe_loop, args=(stop, samples), daemon=True)
    th.start()
    time.sleep(0.5)
    t0 = time.time()
    a = ctl("apply")
    dt = time.time() - t0
    time.sleep(1.0)
    stop.set()
    th.join()
    gaps, cur = [], None
    for ts, ok in samples:
        if not ok and cur is None:
            cur = ts
        if ok and cur is not None:
            gaps.append(round(ts - cur, 2))
            cur = None
    try:
        info = json.loads(a.stdout.strip().splitlines()[-1])
    except Exception:
        info = {"raw": a.stdout[-200:], "err": a.stderr[-300:]}
    print(json.dumps({"label": label, "run": i, "change": what, "rc": a.returncode, "apply_seconds": round(dt, 1), "xray_router_restarts": restarts_since(t0 - 1),
                      "ready_seconds": info.get("ready_seconds"), "unavailable_windows_s": gaps, "max_unavailable_s": max(gaps) if gaps else 0,
                      "total_unavailable_s": round(sum(gaps), 2)}), flush=True)
