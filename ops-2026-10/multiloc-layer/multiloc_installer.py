#!/usr/bin/env python3
"""Multi-location installer: preflight / apply / verify / status / rollback.  Idempotent, hash-checked, fully reversible.

  install.sh preflight            read-only checks, prints the plan, changes nothing
  install.sh apply [--no-restart] copy files (backup first), additive DB migration, restart panel, render xray+haproxy templates,
                                  validate (xray -test / haproxy -c), reload HAProxy, restart hiddify-xray, verify
  install.sh verify               post-conditions only (also used after apply and after rollback)
  install.sh status               per-file state (base / applied / new-absent / UNKNOWN)
  install.sh rollback [<run>] [--force-router]
                                  undo the given run (default: every run that is not rolled back yet, newest first): files, DB (drop the
                                  3 new tables), rendered configs, HAProxy reload, hiddify-xray restart, panel restart, router config restore.
                                  A run counts as rolled back ONLY after the services were restored and verify() found nothing wrong;
                                  until then the same command resumes where the last attempt stopped (exit 3 / 1 = still open).

Safety rules enforced here:
  * a target file whose sha256 is neither the expected BASE nor the NEW version aborts the run (somebody changed it) - nothing is touched
  * every replaced file is copied to <backup>/files/<abs path> before it is overwritten; new files are listed for deletion
  * the DB migration only CREATEs new tables; a full mysqldump is stored before the first change
  * apply never touches xray-router (the router layer runs on the next explicit `multiloc_ctl.py apply`)
  * apply refuses to start while an earlier run is neither finished nor rolled back (an interrupted apply / rollback must be completed first)
  * rollback never throws away something it did not install: a file or the router config that is not what the feature wrote is copied to
    <backup>/diverged-at-rollback/ (files) or <backup>/router-config.at-rollback-<time>.json first; a router config that differs from the
    pre-install copy by more than the feature's own entrances is LEFT AS IS unless --force-router is given
"""
import argparse
import contextlib
import copy
import datetime
import fcntl
import hashlib
import json
import os
import py_compile
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PAYLOAD = HERE / "payload"
MANIFEST = HERE / "MANIFEST.json"
H = Path("/opt/hiddify-manager")
PY = H / ".venv313/bin/python"
BACKUPS = H / "multiloc-installer-backups"
RENDER = [  # templates re-rendered by `apply` (and by `rollback`): only what the feature touches
    "xray/configs/03_routing.json.j2", "xray/configs/05_inbounds_30_locations.json.j2", "xray/configs/06_outbounds.json.j2",
    "haproxy/haproxy.cfg.j2", "haproxy/maps/path_v10.j2",
]
RENDERED_TARGETS = [r[:-3] for r in RENDER]
SERVICES_REQUIRED = ["hiddify-panel", "hiddify-haproxy", "hiddify-xray", "mariadb"]
LOCK = Path("/run/multiloc-installer.lock")       # tmpfs: taking the lock leaves nothing behind


# ------------------------------------------------------------------------------------------------------ helpers
def log(msg):
    print(f"[{datetime.datetime.now():%H:%M:%S}] {msg}", flush=True)


def sh(cmd, check=True, timeout=300, **kw):
    r = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, timeout=timeout, **kw)
    if check and r.returncode != 0:
        raise RuntimeError(f"command failed ({r.returncode}): {cmd}\n{r.stdout[-1500:]}\n{r.stderr[-1500:]}")
    return r


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest():
    return json.loads(MANIFEST.read_text())["files"]


def target_of(rel):
    return Path("/") / rel


def file_state(entry):
    t = target_of(entry["path"])
    if not t.exists():
        return "absent"
    cur = sha(t)
    if cur == entry["new"]:
        return "applied"
    if entry["base"] != "NEW" and cur == entry["base"]:
        return "base"
    return "UNKNOWN"


def inject(step_name):
    """inert test hook: MULTILOC_TEST_FAIL_AT=<step> makes `apply` fail right at the start of that step (used by the rehearsal tests)"""
    if os.environ.get("MULTILOC_TEST_FAIL_AT") == step_name:
        raise RuntimeError(f"injected failure at step {step_name} (test hook)")


def inject_rollback(point):
    """inert test hook: MULTILOC_TEST_FAIL_ROLLBACK_AT=<point> makes the rollback fail at that point (used to prove that a rollback which did
    not finish leaves its run OPEN and that a plain `rollback` afterwards completes it)"""
    if os.environ.get("MULTILOC_TEST_FAIL_ROLLBACK_AT") == point:
        raise RuntimeError(f"injected failure in the rollback at {point} (test hook)")


def crash_point(name):
    """inert test hook: MULTILOC_TEST_CRASH_AT=<name> kills the process right here, like kill -9 or a power loss (nothing after it runs: no rollback,
    no state save). Used to prove that an interrupted apply is still rolled back completely."""
    if os.environ.get("MULTILOC_TEST_CRASH_AT") == name:
        os._exit(137)


@contextlib.contextmanager
def installer_lock():
    """one apply / rollback at a time (two operators or agents on the same node must not interleave file copies and restarts)"""
    f = open(LOCK, "w")
    try:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise SystemExit(f"another multiloc_installer apply / rollback is running (lock {LOCK}): wait until it finishes")
        yield
    finally:
        f.close()


def active(unit):
    return sh(["systemctl", "is-active", unit], check=False).stdout.strip() == "active"


def get_state(run: Path) -> dict:
    try:
        return json.loads((run / "STATE.json").read_text())
    except ValueError as exc:
        raise RuntimeError(f"{run}/STATE.json is unreadable ({exc}); the backups in {run} are intact - restore from them by hand") from exc


def write_state(run: Path, state: dict):
    """STATE.json decides whether a run is still open: write it atomically (a crash leaves the old or the new file, never half of one)"""
    tmp = run / "STATE.json.tmp"
    with open(tmp, "w") as f:
        f.write(json.dumps(state, indent=1))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, run / "STATE.json")


def update_state(run: Path, **kv) -> dict:
    """read-modify-write of one run's STATE.json (always from disk: several functions of one rollback write to it)"""
    st = get_state(run)
    st.update(kv)
    write_state(run, st)
    return st


def mark_rolled_back(run: Path):
    """the ONLY place that closes a run: called after the data AND the services were restored and verify() found nothing wrong"""
    st = get_state(run)                      # `rollback_done` stays: it records which restarts already happened (an explicit re-run must not repeat them)
    st["rolled_back"] = True
    st["rolled_back_at"] = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    write_state(run, st)


def run_is_open(st: dict) -> bool:
    """a run whose story is not over: an apply that neither finished nor was rolled back (crash, power loss), or a rollback that was started
    and could not complete"""
    if st.get("rolled_back"):
        return False
    return bool(st.get("rollback_started")) or not st.get("finished")


ROUTER_CONFIG = Path("/etc/xray-router/config.json")


def router_ports(config_path):
    try:
        cfg = json.loads(Path(config_path).read_text())
        return sorted({int(i["port"]) for i in cfg.get("inbounds", []) if str(i.get("port", "")).isdigit() or isinstance(i.get("port"), int)})
    except Exception:
        return []


def port_open(port, timeout=0.5):
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def layer_free(cfg: dict) -> dict:
    """a router config WITHOUT what the multi-location layer adds (its entrances, rules, balancers) and without `observatory` (compared separately, see
    observatory_foreign_change). Mirrors hutils/multiloc_router.strip() on purpose: the rollback must not depend on code it is about to remove."""
    c = copy.deepcopy(cfg)
    c["inbounds"] = [i for i in c.get("inbounds", []) if not str(i.get("tag", "")).startswith("from-hiddify-s") and i.get("tag") != "api"]
    r = c.get("routing") or {}

    def _director_rule(x):          # AUTO director (mirrors multiloc_router._is_director_rule): loopback passes `auto-in-s<N>` and the API entrance
        tags = [str(t) for t in ((x.get("inboundTag") or []) if isinstance(x, dict) else [])]
        return bool(tags) and all(t.startswith("auto-in-s") or t == "api" for t in tags)
    r["rules"] = [x for x in r.get("rules", [])
                  if not (isinstance(x, dict) and any(str(t).startswith("from-hiddify-s") for t in (x.get("inboundTag") or [])))
                  and not _director_rule(x)]
    for x in r["rules"]:
        if isinstance(x, dict) and x.get("balancerTag") == "auto-director":
            x["balancerTag"] = "upstream-balancer"
    c["outbounds"] = [o for o in c.get("outbounds", []) if not str(o.get("tag", "")).startswith("auto-via-s")]
    if (c.get("api") or {}).get("tag") == "api":
        del c["api"]
    if "balancers" in r:
        r["balancers"] = [b for b in r["balancers"] if not str(b.get("tag", "")).startswith("balancer-s") and b.get("tag") != "auto-director"]
        for b in r["balancers"]:
            if b.get("tag") == "upstream-balancer" and b.get("fallbackTag") == "block":
                del b["fallbackTag"]                       # the layer makes AUTO fail closed (mirrors multiloc_router.strip())
        if not r["balancers"]:
            del r["balancers"]
    c["routing"] = r
    c.pop("observatory", None)
    return c


_OBSERVATORY_KEYS = {"subjectSelector", "probeUrl", "probeInterval", "enableConcurrency"}


def observatory_foreign_change(live, snap, interval_ok=False):
    """None when `live` differs from the pre-install observatory only the way the layer changes it (created when there was none; selectors APPENDED
    to subjectSelector), else the reason. Every other change - probe URL / interval / concurrency edited, entries removed or reordered, the whole
    observatory removed - is somebody else's."""
    if live == snap:
        return None
    if snap is None:                                   # the layer creates it when a balancer needs one
        if isinstance(live, dict) and set(live) <= _OBSERVATORY_KEYS:
            return None
        return "observatory was created with keys the layer does not write"
    if not isinstance(live, dict) or not isinstance(snap, dict):
        return "observatory was removed or replaced"
    for k in (set(live) | set(snap)) - {"subjectSelector"} - ({"probeInterval"} if interval_ok else set()):     # the AUTO director shortens the probe interval
        if live.get(k) != snap.get(k):
            return f"observatory.{k} was changed"
    ls, ss = list(live.get("subjectSelector") or []), list(snap.get("subjectSelector") or [])
    if ls[:len(ss)] != ss:
        return "observatory.subjectSelector lost or reordered its original entries"
    return None


def foreign_changes(live_cfg: dict, snap_cfg: dict) -> list:
    """what differs between the live router config and the pre-install copy that the multi-location layer does NOT explain (empty = only the layer)"""
    reasons = []
    if layer_free(live_cfg) != layer_free(snap_cfg):
        reasons.append("the config differs outside the feature's entrances, rules and balancers")
    director = any(b.get("tag") == "auto-director" for b in (live_cfg.get("routing") or {}).get("balancers", []))
    why = observatory_foreign_change(live_cfg.get("observatory"), snap_cfg.get("observatory"), interval_ok=director)
    if why:
        reasons.append(why)
    return reasons


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


def wait_router_ready(config_path, seconds=None):
    """xray-router (Type=simple) needs ~2 min to parse the production rule set and `systemctl restart` returns at once: wait until its entrances listen"""
    limit = float(seconds if seconds is not None else (os.environ.get("MULTILOC_ROUTER_READY_SECONDS") or 420))
    ports = router_ports(config_path)
    t0 = time.monotonic()
    while True:
        if ports and entrances_listening(ports):
            return True, round(time.monotonic() - t0, 1)
        if time.monotonic() - t0 >= limit:
            return False, round(time.monotonic() - t0, 1)
        time.sleep(1.0)


# ------------------------------------------------------------------------------------------------------ commands
def list_runs():
    return sorted(p for p in BACKUPS.iterdir() if (p / "STATE.json").exists()) if BACKUPS.exists() else []


def open_runs():
    """[(run dir, state)] of the runs whose story is not over; a run whose STATE.json cannot be read counts as open (state None)"""
    out = []
    for r in list_runs():
        try:
            st = get_state(r)
        except RuntimeError:
            out.append((r, None))
            continue
        if run_is_open(st):
            out.append((r, st))
    return out


def open_run_text(st):
    if st is None:
        return "its STATE.json is unreadable (restore by hand from the backups in that directory)"
    return "its rollback did not finish" if st.get("rollback_started") else "its apply did not finish"


def cmd_status(a):
    worst = 0
    for e in manifest():
        st = file_state(e)
        mark = {"base": "to install", "applied": "applied", "absent": "new file, absent", "UNKNOWN": "UNKNOWN CONTENT"}[st]
        print(f"{st:8} {mark:18} {e['path']}")
        worst |= (st == "UNKNOWN")
    for r, st in open_runs():
        print(f"OPEN    run {r.name}: {open_run_text(st)} -> run `rollback` (it resumes where the last attempt stopped)")
        worst = 1
    return 1 if worst else 0


def preflight():
    problems = []
    if os.geteuid() != 0:
        problems.append("must run as root")
    for u in SERVICES_REQUIRED:
        if not active(u):
            problems.append(f"service {u} is not active")
    for e in manifest():
        st = file_state(e)
        if st == "UNKNOWN":
            problems.append(f"{e['path']}: content is neither the expected base nor the new version (changed by someone else?)")
        elif st == "absent" and e["base"] != "NEW":
            problems.append(f"{e['path']}: a stock file this install modifies is MISSING (this does not look like the expected system)")
    have = sh("mysql -N hiddifypanel -e \"select count(*) from information_schema.tables where table_schema='hiddifypanel' "
              "and table_name like 'commercial_routing_location%'\"", check=False).stdout.strip()
    if have not in ("", "0", "3"):
        problems.append(f"the multi-location tables exist only partially ({have} of 3): clean up by hand before installing")
    for r, st in open_runs():
        problems.append(f"run {r.name} is neither finished nor rolled back ({open_run_text(st)}): run `multiloc_installer.py rollback` first - it resumes and completes the job")
    if not PY.exists():
        problems.append(f"{PY} missing")
    free = shutil.disk_usage("/opt").free
    if free < 1_500_000_000:
        problems.append(f"less than 1.5 GB free in /opt ({free // 1_000_000} MB)")
    r = sh("mysql -N -e 'select 1'", check=False)
    if r.returncode != 0:
        problems.append("cannot connect to MariaDB as root")
    return problems


def cmd_preflight(a):
    problems = preflight()
    todo = [e["path"] for e in manifest() if file_state(e) != "applied"]
    log(f"files to install: {len(todo)} of {len(manifest())}")
    for p in todo:
        print("   +", p)
    if problems:
        for p in problems:
            print("PROBLEM:", p)
        return 1
    log("preflight OK (nothing was changed)")
    return 0


def render_selected(templates):
    """render only the given templates with the SAME jinja environment/filters as common/jinja.py"""
    code = (
        "import importlib.util, sys\n"
        "spec = importlib.util.spec_from_file_location('hjinja', '/opt/hiddify-manager/common/jinja.py')\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "for t in sys.argv[1:]:\n"
        "    m.render(t)\n"
    )
    r = sh([str(PY), "-c", code] + [str(H / t) for t in templates], cwd=str(H / "common"))
    if "Error rendering" in (r.stdout + r.stderr):
        raise RuntimeError("template rendering error:\n" + (r.stdout + r.stderr)[-2000:])


def snapshot_rendered():
    out = {}
    for t in RENDERED_TARGETS:
        p = H / t
        out[t] = p.read_bytes() if p.exists() else None
    return out


def reload_current_json():
    """regenerate current.json (the context of every template) with the freshly installed panel code"""
    code = ("source /opt/hiddify-manager/common/utils.sh; reload_all_configs >/dev/null; echo rc=$?")
    r = sh(["bash", "-c", code], cwd=str(H))
    if "rc=0" not in r.stdout:
        raise RuntimeError("reload_all_configs failed: " + r.stdout + r.stderr)
    data = json.loads((H / "current.json").read_text())
    if "locations" not in data:
        raise RuntimeError("current.json has no `locations` key - panel code not loaded?")
    return data


def validate_configs():
    r = sh([str(H / "xray/bin/xray"), "run", "-test", "-confdir", str(H / "xray/configs/")], check=False)
    if r.returncode != 0 or "Configuration OK" not in r.stdout + r.stderr:
        raise RuntimeError("xray -test failed:\n" + (r.stdout + r.stderr)[-2000:])
    r = sh(["/usr/sbin/haproxy", "-f", str(H / "haproxy") + "/", "-c", "-q"], check=False)
    if r.returncode != 0:
        raise RuntimeError("haproxy -c failed:\n" + (r.stdout + r.stderr)[-2000:])


def wait_active(unit, seconds=40):
    for _ in range(seconds):
        if active(unit):
            return True
        time.sleep(1)
    return False


def wait_panel(seconds=90):
    for _ in range(seconds):
        r = sh("curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:9000/", check=False)
        if r.stdout.strip() in ("400", "200", "302", "404"):
            return True
        time.sleep(1)
    return False


def copy_with_meta(src, dst):
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        st = dst.stat()
        mode, uid, gid = st.st_mode & 0o7777, st.st_uid, st.st_gid
    else:
        ref = next((p for p in dst.parent.iterdir() if p.is_file() and p != dst), None)
        pst = (ref or dst.parent).stat()
        mode = (pst.st_mode & 0o7777) if ref else 0o644
        if dst.suffix == ".py" and "scripts" in dst.parts:
            mode = 0o755
        uid, gid = pst.st_uid, pst.st_gid
    tmp = dst.with_name(dst.name + ".multiloc-new")
    shutil.copyfile(src, tmp)
    os.chmod(tmp, mode)
    os.chown(tmp, uid, gid)
    os.replace(tmp, dst)


def cmd_apply(a):
    problems = preflight()
    if problems:
        for p in problems:
            print("PROBLEM:", p)
        return 1
    if all(file_state(e) == "applied" for e in manifest()):
        sh([str(PY), str(H / "scripts/multiloc_ctl.py"), "init"])      # idempotent: no-op when the tables exist
        vp = verify(expect_loc_tags=False)
        log("already applied - nothing to do" + (f" (verify problems: {vp})" if vp else ""))
        return 1 if vp else 0
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    run = BACKUPS / ts
    (run / "files").mkdir(parents=True, exist_ok=True)
    os.chmod(BACKUPS, 0o700)
    state = {"run": ts, "started": ts, "steps": [], "new_files": [], "replaced_files": [], "db_created": False}

    def save():
        write_state(run, state)

    def step(name):
        state["steps"].append(name)
        save()
        log(f"STEP {name}")
        inject(name)

    try:
        step("db-dump")
        dump = run / "db-before.sql.gz"
        # pipefail: in a plain `mysqldump | gzip` a failing mysqldump is hidden by gzip's exit code and the "backup" would be an empty file
        sh(f"set -o pipefail; mysqldump --single-transaction --quick --routines hiddifypanel | gzip > {dump}", executable="/bin/bash", timeout=1800)
        if sh(["gzip", "-t", str(dump)], check=False).returncode != 0 or dump.stat().st_size < 200:
            raise RuntimeError("the database dump taken before the install is empty or damaged: nothing was changed")
        if (Path("/etc/xray-router/config.json")).exists():
            shutil.copy2("/etc/xray-router/config.json", run / "router-config.json")
        if (H / "current.json").exists():
            shutil.copy2(H / "current.json", run / "current.json")
        for t in RENDERED_TARGETS:
            p = H / t
            if p.exists():
                (run / "rendered" / Path(t).parent).mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, run / "rendered" / t)
        os.chmod(run, 0o700)

        step("copy-files")
        changed_py = False
        n_files = 0
        for e in manifest():
            st = file_state(e)
            t = target_of(e["path"])
            if st == "applied":
                continue
            n_files += 1
            if t.exists():
                b = run / "files" / e["path"]
                b.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(t, b)
                state["replaced_files"].append(e["path"])
            else:
                state["new_files"].append(e["path"])
            save()                                   # write-ahead: the intent is on disk BEFORE the file is touched, so a crash right after the copy is rolled back too
            copy_with_meta(PAYLOAD / e["path"], t)
            crash_point(f"file-replaced:{n_files}")
            state.setdefault("installed_sha", {})[e["path"]] = e["new"]      # what the rollback must find there (anything else is somebody's later change)
            save()
            if t.suffix == ".py":
                changed_py = True
                pyc_dir = t.parent / "__pycache__"
                if pyc_dir.is_dir():
                    for pyc in pyc_dir.glob(t.stem + ".*.pyc"):
                        pyc.unlink()
                py_compile.compile(str(t), doraise=True)
            assert sha(t) == e["new"], f"verification after copy failed: {t}"

        step("db-migrate")
        before = sh("mysql -N hiddifypanel -e \"select count(*) from information_schema.tables where table_schema='hiddifypanel' "
                    "and table_name like 'commercial_routing_location%'\"").stdout.strip()
        state["db_created"] = before == "0"      # written BEFORE the tables are created: a failure in the middle must still drop what exists
        save()
        r = sh([str(PY), str(H / "scripts/multiloc_ctl.py"), "init"])
        log("schema: " + r.stdout.replace("\n", " ")[:200])

        step("restart-panel")
        if changed_py or not wait_panel(3):
            state["restarted_panel"] = True          # the restart flags are write-ahead too: "may have been restarted" is all the rollback needs to know
            save()
            sh(["systemctl", "restart", "hiddify-panel"])
            if not wait_panel():
                raise RuntimeError("panel did not come back after restart")
            sh(["systemctl", "restart", "hiddify-panel-background-tasks"], check=False)

        step("render")
        data = reload_current_json()
        log(f"current.json: locations={len(data['locations'])} slots")
        pre = snapshot_rendered()
        state["rendered"] = True
        save()
        render_selected(RENDER)
        post = snapshot_rendered()
        changed = {t for t in RENDERED_TARGETS if pre[t] != post[t]}
        log("rendered files that changed: " + (", ".join(sorted(changed)) or "none"))

        step("validate")
        validate_configs()

        haproxy_changed = any(t.startswith("haproxy/") for t in changed)
        xray_changed = any(t.startswith("xray/") for t in changed)
        if haproxy_changed:
            step("reload-haproxy")
            state["reloaded_haproxy"] = True
            save()
            sh(["systemctl", "reload", "hiddify-haproxy"])
            time.sleep(2)
            if not wait_active("hiddify-haproxy", 20):
                raise RuntimeError("hiddify-haproxy not active after reload")
        if xray_changed:
            step("restart-xray")
            state["restarted_xray"] = True
            save()
            sh(["systemctl", "restart", "hiddify-xray"])
            crash_point("xray-restarted")
            if not wait_active("hiddify-xray", 30):
                raise RuntimeError("hiddify-xray not active after restart")
            time.sleep(2)
        step("verify")
        vp = verify(expect_loc_tags=True)
        if vp:
            raise RuntimeError("verification failed: " + "; ".join(vp))
        state["finished"] = True
        save()
        log(f"APPLY OK (backup: {run})")
        return 0
    except Exception as exc:
        log(f"APPLY FAILED: {exc}")
        log("rolling back this run ...")
        state["failed"] = str(exc)[:500]
        save()
        try:
            do_rollback(run)
        except Exception as exc2:
            log(f"ROLLBACK ALSO FAILED: {exc2}  -- manual intervention needed, backup is in {run}. The run stays OPEN: remove the cause and "
                "run `rollback` (it resumes); apply refuses to start until then")
            return 3
        return 2


def verify(expect_loc_tags=True):
    problems = []
    for u in SERVICES_REQUIRED:
        if not active(u):
            problems.append(f"{u} not active")
    if not wait_panel(3):
        problems.append("panel does not answer")
    last = list_runs()[-1:]
    router_known = ROUTER_CONFIG.exists() or any((r / "router-config.json").exists() for r in last)      # our own record: it existed when we installed
    if router_known:
        if not ROUTER_CONFIG.exists():
            problems.append(f"the xray-router config {ROUTER_CONFIG} is missing")
        elif not active("xray-router"):
            problems.append("xray-router not active")
        elif not entrances_listening(router_ports(ROUTER_CONFIG)):
            problems.append("xray-router is active but its own entrances do not listen")
    cur = json.loads((H / "current.json").read_text()) if (H / "current.json").exists() else {}
    locs = cur.get("locations", [])
    if expect_loc_tags and locs:
        r = sh([str(H / "xray/bin/xray"), "api", "lsi", "--server=127.0.0.1:10085"], check=False)
        try:
            tags = {i.get("tag") for i in json.loads(r.stdout).get("inbounds", [])}
        except Exception:
            tags = set()
        for loc in locs:
            for k, t in loc["tags"].items():
                if t not in tags and (H / "xray/configs/05_inbounds_30_locations.json").exists():
                    problems.append(f"inbound {t} not loaded in running xray")
    return problems


def verify_settled(expect_loc_tags=True, seconds=None):
    """verify(), repeated for a short while: right after a restart a service may need a moment to answer"""
    limit = float(seconds if seconds is not None else (os.environ.get("MULTILOC_VERIFY_SETTLE_SECONDS") or 30))
    t0 = time.monotonic()
    while True:
        vp = verify(expect_loc_tags=expect_loc_tags)
        if not vp or time.monotonic() - t0 >= limit:
            return vp
        time.sleep(2)


def keep_diverged(run: Path, rel: str, current: Path):
    """`current` is not what this install wrote: somebody changed it later. The rollback still puts the original back, but never without a copy"""
    dst = run / "diverged-at-rollback" / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(current, dst)
    log(f"WARNING {current} is not the version this install wrote (changed later?): the current content is kept in {dst}")


def rollback_run_data(run: Path) -> bool:
    """undo ONE run's data changes (DB tables it created, files it replaced/added). Returns True if files were restored.
    Safe to repeat (a table that is gone and a file that already equals its backup are skipped). It does NOT close the run:
    that happens in mark_rolled_back(), after the services were restored and verify() is clean."""
    state = get_state(run)
    state["rollback_started"] = True
    state["rollback_attempts"] = int(state.get("rollback_attempts", 0)) + 1
    write_state(run, state)
    steps = state.get("steps", [])
    log(f"ROLLBACK run {state['run']} (steps done: {', '.join(steps)}; attempt {state['rollback_attempts']})")
    # DB first, with raw SQL (the new python code is about to be removed): keep a dump of the feature's data, then drop the 3 tables
    if state.get("db_created"):
        tabs = ["commercial_routing_location", "commercial_routing_location_node", "commercial_routing_location_tester"]
        have = [t for t in tabs if sh(f"mysql -N hiddifypanel -e \"show tables like '{t}'\"", check=False).stdout.strip()]
        if have:
            dump = run / "feature-tables-at-rollback.sql"
            r = sh(f"set -o pipefail; mysqldump --single-transaction hiddifypanel {' '.join(have)} > {dump}", check=False, executable="/bin/bash")
            if r.returncode != 0 or not dump.exists() or "Dump completed" not in dump.read_text(errors="replace")[-400:]:
                raise RuntimeError("cannot save a dump of the feature tables, so they are NOT dropped (nothing is thrown away without a copy): "
                                   + ((r.stderr or r.stdout or "").strip()[-300:] or f"rc={r.returncode}"))
            sh("mysql hiddifypanel -e 'DROP TABLE IF EXISTS commercial_routing_location_tester, commercial_routing_location_node, "
               "commercial_routing_location'")
            log("dropped feature tables (data dump kept in the backup dir)")
    restored = "copy-files" in steps
    if restored:
        try:
            installed = {e["path"]: e["new"] for e in manifest()}      # fallback for a file whose hash was not recorded before a crash
        except Exception:
            installed = {}
        installed.update(state.get("installed_sha", {}))
        for rel in state.get("replaced_files", []):
            b = run / "files" / rel
            if not b.exists():
                continue
            t = target_of(rel)
            cur = sha(t) if t.exists() else None
            if cur == sha(b):
                continue                                         # a repeated rollback: this file is already back
            if cur is not None and installed.get(rel) and cur != installed[rel]:
                keep_diverged(run, rel, t)
            copy_with_meta(b, t)
            if t.suffix == ".py" and (t.parent / "__pycache__").is_dir():
                for pyc in (t.parent / "__pycache__").glob(t.stem + ".*.pyc"):
                    pyc.unlink()
        for rel in state.get("new_files", []):
            t = target_of(rel)
            if t.exists():
                if installed.get(rel) and sha(t) != installed[rel]:
                    keep_diverged(run, rel, t)
                t.unlink()
            if (t.parent / "__pycache__").is_dir():
                for pyc in (t.parent / "__pycache__").glob(t.stem + ".*.pyc"):
                    pyc.unlink()
    return restored


def restore_router(first_run: Path, force: bool = False):
    """Put the router config of the moment BEFORE the first install back - but only what the feature itself changed:
      * live == pre-install copy and no restore was interrupted        -> nothing to do (no restart);
      * live differs only by the feature's entrances                   -> restore the copy, restart xray-router, wait until it really serves;
      * live differs by more (changed after the install by someone / something else, e.g. a later routing apply)
                                                                       -> a copy of the live file is kept and the file is LEFT AS IS (the
                                                                          feature's entrances in it are inert), unless --force-router is given.
    A restore that was started and not confirmed ("pending") is resumed even though the file already equals the copy."""
    snap, live = first_run / "router-config.json", ROUTER_CONFIG
    if not snap.exists():
        return                                           # there was no router config before the install: nothing to put back
    pending = get_state(first_run).get("router_restore") == "pending"
    snap_cfg = json.loads(snap.read_text())
    if not live.exists():
        # the router config existed before the install and is gone now: there is nothing to keep, put the pre-install copy back (a rollback must
        # never end "successfully" without it)
        log("the router config is MISSING -> restoring the pre-install copy and restarting xray-router")
        update_state(first_run, router_restore="pending", router_left_as_is=False)
        live.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(snap, live)
    else:
        try:
            live_cfg = json.loads(live.read_text())
        except Exception:
            live_cfg = None                              # unreadable: nothing to preserve, the copy is the better file
        if live_cfg is not None and live_cfg == snap_cfg:
            if not pending:
                return
            log("resuming an interrupted router restore: the pre-install config is already in place, restarting xray-router")
        else:
            keep = first_run / f"router-config.at-rollback-{datetime.datetime.now():%Y%m%d-%H%M%S}.json"
            shutil.copy2(live, keep)                     # never overwrite the live file without a separate copy of it
            reasons = [] if live_cfg is None else foreign_changes(live_cfg, snap_cfg)
            if reasons and not force:
                log("WARNING: the router config differs from the pre-install copy by MORE than the multi-location layer (" + "; ".join(reasons)
                    + f") - LEFT AS IS. Current copy: {keep}; pre-install copy: {snap}. To put the pre-install copy back anyway: "
                    f"`multiloc_installer.py rollback {first_run.name} --force-router`")
                update_state(first_run, router_left_as_is=True)
                return
            log("router config differs from the pre-install copy -> restoring it and restarting xray-router"
                + (" (--force-router)" if reasons else "") + f"; the file it replaces is kept in {keep}")
            update_state(first_run, router_restore="pending", router_left_as_is=False)
            shutil.copy2(snap, live)
    sh(["systemctl", "reset-failed", "xray-router"], check=False)
    r = sh(["systemctl", "restart", "xray-router"], check=False)
    if r.returncode != 0:
        raise RuntimeError("systemctl restart xray-router failed during the rollback: " + (r.stderr or r.stdout or f"rc={r.returncode}").strip())
    ok, waited = wait_router_ready(live)
    if not ok:
        raise RuntimeError(f"xray-router is NOT serving {waited}s after the rollback restored its previous config - check `systemctl status xray-router`")
    update_state(first_run, router_restore="done")
    log(f"xray-router serving again after {waited}s")


def rollback_services(first_run: Path, restored: bool, flags: dict, force_router: bool = False):
    """after the data/files of all runs are restored: undo exactly what the runs did to the services (nothing more).
    Each piece that went through is noted in the oldest run's STATE.json (`rollback_done`): a repeated rollback does not restart again
    what already came back, and finishes what did not."""
    inject_rollback("services")
    done = dict(get_state(first_run).get("rollback_done", {}))

    def note(name):
        done[name] = True
        update_state(first_run, rollback_done=done)

    if restored and flags.get("restarted_panel") and not done.get("panel"):
        sh(["systemctl", "restart", "hiddify-panel"], check=False)
        if not wait_panel():
            raise RuntimeError("hiddify-panel did not answer after the rollback restarted it")
        sh(["systemctl", "restart", "hiddify-panel-background-tasks"], check=False)
        note("panel")
    if restored and flags.get("rendered") and not done.get("render"):
        retry = bool(done.get("render_started"))          # an earlier attempt got to the rendering but not to the end of this block
        note("render_started")
        reload_current_json_safe()
        pre = snapshot_rendered()
        for t in RENDER:
            if (H / t).exists():
                render_selected([t])
        for t in RENDERED_TARGETS:      # rendered output of a removed template must disappear too
            if not (H / (t + ".j2")).exists() and (H / t).exists():
                (H / t).unlink()
        post = snapshot_rendered()
        changed = {t for t in RENDERED_TARGETS if pre[t] != post[t]}
        log("re-rendered; changed: " + (", ".join(sorted(changed)) or "none"))
        if retry:
            changed = set(RENDERED_TARGETS)               # the files are already rendered, the reload / restart they call for may be missing
        if flags.get("reloaded_haproxy") and any(t.startswith("haproxy/") for t in changed):
            r = sh(["systemctl", "reload", "hiddify-haproxy"], check=False)
            if r.returncode != 0:
                raise RuntimeError("systemctl reload hiddify-haproxy failed during the rollback: " + (r.stderr or r.stdout or f"rc={r.returncode}").strip())
        if flags.get("restarted_xray") and any(t.startswith("xray/") for t in changed):
            r = sh(["systemctl", "restart", "hiddify-xray"], check=False)
            if r.returncode != 0:
                raise RuntimeError("systemctl restart hiddify-xray failed during the rollback: " + (r.stderr or r.stdout or f"rc={r.returncode}").strip())
        note("render")
    restore_router(first_run, force_router)


def do_rollback(run: Path):
    """roll back ONE run (the automatic rollback of a failed apply). The run is closed only after the services came back and verify() is clean:
    otherwise it stays open and a plain `rollback` finishes the job."""
    restored = rollback_run_data(run)
    rollback_services(run, restored, get_state(run))
    vp = verify_settled(expect_loc_tags=False)
    if vp:
        raise RuntimeError("files and services were restored but verification still finds problems: " + "; ".join(vp))
    mark_rolled_back(run)
    log("ROLLBACK done")


def reload_current_json_safe():
    try:
        sh(["bash", "-c", "source /opt/hiddify-manager/common/utils.sh; reload_all_configs >/dev/null"], cwd=str(H))
    except Exception as exc:
        log(f"reload_all_configs after rollback: {exc}")


def cmd_rollback(a):
    try:
        if a.run:
            chosen = [BACKUPS / a.run]
            if not (chosen[0] / "STATE.json").exists():
                print(f"no such run: {a.run}")
                return 1
            get_state(chosen[0])
        else:
            chosen = [r for r in list_runs() if not get_state(r).get("rolled_back")]
            chosen.reverse()                               # newest first; the oldest run restores the pristine originals
    except RuntimeError as exc:                            # an unreadable STATE.json: nothing can be decided automatically
        log(f"ROLLBACK INCOMPLETE: {exc}")
        return 3
    if not chosen:
        print("nothing to roll back")
        return 0
    restored = False
    flags = {}
    try:
        for r in chosen:
            restored |= rollback_run_data(r)
            st = get_state(r)
            for k in ("restarted_panel", "rendered", "reloaded_haproxy", "restarted_xray"):
                flags[k] = flags.get(k, False) or bool(st.get(k))
        rollback_services(chosen[-1], restored, flags, force_router=a.force_router)
    except Exception as exc:
        log(f"ROLLBACK INCOMPLETE: {exc}  -- manual intervention needed. The run(s) stay OPEN: remove the cause and run `rollback` again, "
            "it resumes where this attempt stopped")
        return 3
    vp = verify_settled(expect_loc_tags=False)
    if vp:
        log(f"ROLLBACK INCOMPLETE: post-rollback problems: {vp} -- the run(s) stay OPEN: fix the cause and run `rollback` again")
        return 1
    for r in chosen:
        mark_rolled_back(r)                                # the only place where a run is closed
    log("ROLLBACK done; post-rollback problems: none")
    return 0


def cmd_verify(a):
    vp = verify()
    print("problems:", vp or "none")
    return 1 if vp else 0


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("preflight")
    sp.add_parser("status")
    s = sp.add_parser("apply")
    sp.add_parser("verify")
    s = sp.add_parser("rollback")
    s.add_argument("run", nargs="?")
    s.add_argument("--force-router", action="store_true",
                   help="put the pre-install router config back even if it was changed after the install by more than the feature's entrances "
                        "(the current file is kept next to the backup)")
    a = ap.parse_args()
    handler = {"preflight": cmd_preflight, "status": cmd_status, "apply": cmd_apply, "verify": cmd_verify, "rollback": cmd_rollback}[a.cmd]
    if a.cmd in ("apply", "rollback"):
        with installer_lock():
            return handler(a)
    return handler(a)


if __name__ == "__main__":
    sys.exit(main())
