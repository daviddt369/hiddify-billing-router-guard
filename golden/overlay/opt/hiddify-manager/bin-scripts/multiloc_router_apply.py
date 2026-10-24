#!/opt/hiddify-manager/.venv313/bin/python
"""multiloc_router_apply - run a router apply in the BACKGROUND (outside the web panel) and report on it.

  multiloc_router_apply.py start --mode auto|countries|full|accept|discard  start it as the transient systemd service `multiloc-router-apply` (ONE at a time); returns at once
  multiloc_router_apply.py status [--json] [--tail N]     state of the running / last apply and the end of its log
  multiloc_router_apply.py run --mode countries|full      INTERNAL: what that service executes (lock, status file, the job itself)

  auto       `multiloc_ctl.py apply --auto`: what the panel's single "Apply" button starts. Compares the router with the database and does the minimum:
             nothing (no restart) / only the countries layer / the full pipeline.
  countries  `multiloc_ctl.py apply`: only the multi-location layer on the live router config. One xray-router restart (about a second; the connections through
             the router are reset). Waits until the entrances listen and restores the old config if they do not.
  full       `multiloc_ctl.py apply --full` = the legacy pipeline `commander.py commercial-routing-apply` (rules, nodes, observatory ... the countries layer last):
             built in the background in a private copy of /etc/xray-router (the live router keeps serving), then ONE router restart; the inputs it now reflects
             are remembered (applied.json). With the rule compaction the preparation takes seconds (no outage), then the one restart.

Why: the panel used to wait 60-90 s for that pipeline and then killed it half way - the router had restarted once, but the later layers and the country
entrances were never applied. A transient service has no such timeout and lives outside the panel's cgroup: a panel restart / reload / deploy does not touch it.
Files: status /var/lib/multiloc-router-apply/status.json and log /var/log/multiloc-router-apply.log (both readable by everybody), lock /run/multiloc-router-apply.lock
(`multiloc_ctl.py apply` takes the same lock, so a console apply and a panel apply can never overlap).
  accept / discard  the owner's decision about the finished config that was KEPT because it differs from the running one in more than his edit (state needs-confirm).
Exit codes: 0 ok / started, 2 could not start or the job failed, 3 another apply is running.
"""
import argparse
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import time

UNIT = "multiloc-router-apply"
STATE_DIR = "/var/lib/multiloc-router-apply"
STATE_FILE = STATE_DIR + "/status.json"
LOG_FILE = "/var/log/multiloc-router-apply.log"
LOCK_FILE = "/run/multiloc-router-apply.lock"
PY = "/opt/hiddify-manager/.venv313/bin/python"
HERE = "/opt/hiddify-manager/scripts/multiloc_router_apply.py"
CTL = "/opt/hiddify-manager/scripts/multiloc_ctl.py"
JOBS = {
    "auto": [PY, CTL, "apply", "--auto"],              # the panel's single "Apply": decides by itself - nothing / countries / full
    "countries": [PY, CTL, "apply"],
    "full": [PY, CTL, "apply", "--full"],              # = the legacy pipeline (commander.py commercial-routing-apply) + remembers what the router now reflects
    "accept": [PY, CTL, "apply", "--accept-staged"],   # the owner confirmed the finished config that was kept (it differs from the running one in more than his edit)
    "discard": [PY, CTL, "apply", "--discard-staged"],
}
LIVE_STATES = ("active", "activating", "deactivating", "reloading")
LOG_KEEP_BYTES = 1_000_000


def now() -> float:
    return time.time()


def stamp(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts if ts is not None else now()))


def read_status() -> dict | None:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            st = json.load(f)
        return st if isinstance(st, dict) else None
    except (OSError, ValueError):
        return None


def write_status(st: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    os.chmod(STATE_DIR, 0o755)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)
    os.chmod(tmp, 0o644)
    os.replace(tmp, STATE_FILE)                    # atomic: a reader never sees half a file


def unit_state() -> str:
    try:
        r = subprocess.run(["systemctl", "show", UNIT, "-p", "ActiveState", "--value"], capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or "inactive"
    except Exception:
        return "unknown"


def lock_is_held() -> bool:
    """is somebody (this service or a console `multiloc_ctl.py apply`) holding the apply lock right now?"""
    try:
        fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o644)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except BlockingIOError:
        return True
    finally:
        os.close(fd)


def tail_of(path: str, n: int) -> list[str]:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 65536))
            data = f.read().decode("utf-8", "replace")
        lines = data.splitlines()
        return lines[-n:] if n > 0 else []
    except OSError:
        return []


def current() -> dict:
    """the status as everybody should see it: a recorded 'running' with no live service behind it is reported as aborted"""
    st = read_status() or {"state": "idle"}
    live = unit_state() in LIVE_STATES
    if st.get("state") == "running" and not live and not lock_is_held():
        st = dict(st, state="aborted", summary=st.get("summary") or "the process is gone (killed, service stopped or the server restarted)")
    st["unit_live"] = live
    return st


# ------------------------------------------------------------------------------------------------ start
def cmd_start(a) -> int:
    mode = a.mode
    cur = current()
    if cur.get("state") == "running" or cur.get("unit_live") or lock_is_held():
        print(json.dumps({"started": False, "reason": "busy", "mode": cur.get("mode"), "since": cur.get("started"),
                          "detail": "another router apply is running"}, ensure_ascii=False))
        return 3
    if not os.path.exists(CTL):
        print(json.dumps({"started": False, "reason": "not-installed", "detail": "multiloc_ctl.py is missing"}))
        return 2
    t0 = now()
    cmd = ["systemd-run", "--quiet", "--collect", f"--unit={UNIT}", f"--description=multiloc router apply ({mode})", "--setenv=HOME=/root",
           f"--property=StandardOutput=append:{LOG_FILE}", f"--property=StandardError=append:{LOG_FILE}",
           "--", PY, HERE, "run", "--mode", mode]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except Exception as exc:
        print(json.dumps({"started": False, "reason": "systemd-run", "detail": str(exc)[:300]}))
        return 2
    if r.returncode != 0:
        if "already" in (r.stderr or "").lower():           # a double click: the other request created the service a moment earlier
            print(json.dumps({"started": False, "reason": "busy", "detail": "another router apply was started a moment ago"}))
            return 3
        print(json.dumps({"started": False, "reason": "systemd-run", "detail": (r.stderr or r.stdout).strip()[-300:]}, ensure_ascii=False))
        return 2
    for _ in range(80):                            # the service confirms by writing its status; wait up to 8 s
        st = read_status() or {}
        if st.get("mode") == mode and (st.get("started") or 0) >= t0 - 1 and st.get("state") in ("running", "ok", "failed", "needs-confirm"):
            break
        time.sleep(0.1)
    else:
        print(json.dumps({"started": False, "reason": "no-confirmation", "detail": "the service was queued but did not report; see " + LOG_FILE}))
        return 2
    print(json.dumps({"started": True, "unit": UNIT, "mode": mode}))
    return 0


# ------------------------------------------------------------------------------------------------ run (inside the service)
def trim_log() -> None:
    try:
        if os.path.getsize(LOG_FILE) > LOG_KEEP_BYTES:
            keep = tail_of(LOG_FILE, 300)
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.write("\n".join(keep) + "\n")
    except OSError:
        pass


_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def log(msg: str) -> None:
    # the pipeline's logger colours its lines: keep the log (and the page that shows it) plain text
    sys.stdout.write(f"[{time.strftime('%H:%M:%S')}] {_ANSI.sub('', msg)}\n")
    sys.stdout.flush()


def cmd_run(a) -> int:
    mode = a.mode
    os.makedirs(STATE_DIR, exist_ok=True)
    lock = open(LOCK_FILE, "a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log(f"mode={mode}: another router apply holds the lock - nothing started")
        return 3
    trim_log()
    started = now()
    st = {"state": "running", "mode": mode, "started": started, "started_text": stamp(started), "finished": None, "rc": None, "pid": os.getpid(), "summary": ""}
    write_status(st)
    log(f"=== router apply started: mode={mode}")
    child = {"p": None}

    def on_term(signum, _frame):
        if child["p"] is not None:
            try:
                child["p"].terminate()
            except Exception:
                pass
        write_status(dict(st, state="aborted", finished=now(), rc=None, summary="stopped by a signal (systemctl stop / shutdown)"))
        log(f"=== router apply STOPPED by signal {signum}")
        os._exit(143)

    signal.signal(signal.SIGTERM, on_term)
    env = dict(os.environ, MULTILOC_APPLY_LOCK_HELD="1", PYTHONUNBUFFERED="1")
    last = ""
    rc = 2
    try:
        p = subprocess.Popen(JOBS[mode], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=env, cwd="/opt/hiddify-manager/hiddify-panel")
        child["p"] = p
        for line in p.stdout:
            line = line.rstrip("\n")
            if line.strip():
                last = _ANSI.sub("", line).strip()
            log(line)
        rc = p.wait()
    except Exception as exc:
        last = f"could not run the job: {exc}"
        log(last)
    if rc == 4:                                    # the finished config differs from the running one in more than the owner's edit: kept, NOT applied - his decision
        write_status(dict(st, state="needs-confirm", finished=now(), finished_text=stamp(), rc=rc,
                          summary="новый конфиг собран, но отличается от рабочего не только вашими правками - ждёт вашего решения; router не тронут"))
        log(f"=== router apply finished: mode={mode} rc={rc} (waiting for the owner's decision) in {int(now() - started)} s")
        return 0
    write_status(dict(st, state="ok" if rc == 0 else "failed", finished=now(), finished_text=stamp(), rc=rc, summary=last[:300]))
    log(f"=== router apply finished: mode={mode} rc={rc} in {int(now() - started)} s")
    return 0 if rc == 0 else 2


# ------------------------------------------------------------------------------------------------ status
def cmd_status(a) -> int:
    st = current()
    st["log_tail"] = tail_of(LOG_FILE, a.tail)
    if a.json:
        print(json.dumps(st, ensure_ascii=False))
        return 0
    print(f"state: {st.get('state')}  mode: {st.get('mode', '-')}  started: {st.get('started_text', '-')}  finished: {st.get('finished_text', '-')}  rc: {st.get('rc')}")
    if st.get("summary"):
        print("summary:", st["summary"])
    for line in st["log_tail"]:
        print("  |", line)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    for name, f in (("start", cmd_start), ("run", cmd_run)):
        s = sp.add_parser(name)
        s.add_argument("--mode", choices=sorted(JOBS), required=True)
        s.set_defaults(f=f)
    s = sp.add_parser("status")
    s.add_argument("--json", action="store_true")
    s.add_argument("--tail", type=int, default=30)
    s.set_defaults(f=cmd_status)
    a = ap.parse_args()
    return a.f(a)


if __name__ == "__main__":
    sys.exit(main())
