#!/usr/bin/env python3
"""Router apply timing + availability gaps of the router entrance while applying (real commander command, real xray-router).
usage: perf_apply.py <label> [runs]      prints one JSON line per run"""
import json, subprocess, sys, threading, time, os

label = sys.argv[1]
runs = int(sys.argv[2]) if len(sys.argv) > 2 else 2
URL = "http://12.0.0.20:18080/"      # RU-cidr target: needs the router only, no exit


def probe_loop(stop, samples):
    while not stop.is_set():
        t0 = time.time()
        r = subprocess.run(["curl", "-s", "--max-time", "1.0", "--socks5-hostname", "127.0.0.1:20808", "-o", "/dev/null", URL], capture_output=True)
        samples.append((t0, r.returncode == 0))
        time.sleep(0.05)


def restarts_between(t_from):
    out = subprocess.run(["journalctl", "-u", "xray-router", "--no-pager", "--since", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t_from)),
                          "-o", "cat"], capture_output=True, text=True).stdout
    return out.count("Started xray-router")


for i in range(runs):
    samples, stop = [], threading.Event()
    th = threading.Thread(target=probe_loop, args=(stop, samples), daemon=True)
    th.start()
    time.sleep(0.5)
    t0 = time.time()
    r = subprocess.run(["/opt/hiddify-manager/.venv313/bin/python", "/opt/hiddify-manager/common/commander.py", "commercial-routing-apply"],
                       capture_output=True, text=True, cwd="/opt/hiddify-manager/hiddify-panel")
    dt = time.time() - t0
    time.sleep(1.0)
    stop.set(); th.join()
    # failed-probe windows
    gaps, cur = [], None
    for ts, ok in samples:
        if not ok and cur is None:
            cur = ts
        if ok and cur is not None:
            gaps.append(round(ts - cur, 2)); cur = None
    size = os.path.getsize("/etc/xray-router/config.json")
    print(json.dumps({"label": label, "run": i + 1, "rc": r.returncode, "apply_seconds": round(dt, 1), "xray_router_restarts": restarts_between(t0 - 1),
                      "router_config_bytes": size, "unavailable_windows_s": gaps, "max_unavailable_s": max(gaps) if gaps else 0,
                      "total_unavailable_s": round(sum(gaps), 2)}))
