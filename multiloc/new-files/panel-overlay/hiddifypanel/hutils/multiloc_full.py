"""The legacy router pipeline (`commander.py commercial-routing-apply`) with ONE router restart instead of ~6. ROOT only (called by `multiloc_ctl.py apply --full|--auto`).

Why: every layer of the legacy pipeline writes /etc/xray-router/config.json and then restarts xray-router itself. With the production rule set (15k single-entry rules,
2 vCPU) one start takes ~4-5 min (measured 245-274 s), the router is down from the first of those restarts until the last one has loaded the finished file - an outage
about as long as the whole pipeline (stand: 15k rules, 331 s of pipeline + 91 s of final load; production is ~3x slower). Nothing in the layers needs the restart in between -
each one reads the FILE the previous one wrote - so the cycle is run unchanged but:
  * in a private mount namespace where /etc/xray-router is a scratch copy (the live router, which keeps its config in memory anyway, never sees a half-built file),
  * with a `systemctl` shim first in PATH that turns start / restart / stop of xray-router into a logged no-op (and answers is-active), and an `xray` shim that answers the layers' own
    `xray run -test` with a test of the SLIM copy (the full test takes ~110 s on production - the layers' 120 s limit is crossed on a loaded server and the pipeline failed),
  * with MULTILOC_DEFER_ROUTER_RESTART=1, which makes the countries layer (the last step) skip its own busy-port check, restart and wait.
When the pipeline has finished, the finished file is checked (JSON, invariants, `xray -test` of the slim copy, busy ports), swapped in atomically (the old one is kept), and the
router is restarted ONCE; it is waited for until its entrances really listen; on any failure the previous config is put back and the router restarted again.
Before anything is replaced the finished config is COMPARED with the running one (hutils/router_diff.py): what the owner's own change explains is applied; anything else
(a DNS section nobody asked for, another probe interval, other rules ...) stops the cycle - the finished config is KEPT (`staged/`), the router is not touched, and the panel
asks for a decision: «Применить новый конфиг» (accept) or «Не применять» (discard).
The finished config is COMPACTED (hutils/router_compact.py): neighbouring plain lists with the same target become one rule - the same routing (first match), `xray -test` 0.3 s instead
of ~2 min, a router start of 0.6 s instead of 4-5 min on production. The legacy files are not touched. Verified: the finished config equals what the classic 6-restart run produces for the same
database, compacted.
Anything unusual before the pipeline starts (no unshare / not root / no live config) falls back to the classic run.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

LIVE_DIR = "/etc/xray-router"
LIVE = LIVE_DIR + "/config.json"
STATE_DIR = "/var/lib/multiloc-router-apply"
SCRATCH = STATE_DIR + "/scratch-etc"
SHIM_DIR = STATE_DIR + "/shim"
SHIM_LOG = STATE_DIR + "/deferred.log"
BACKUPS = "/root/xray-router-backups"
PY = "/opt/hiddify-manager/.venv313/bin/python"
COMMANDER = "/opt/hiddify-manager/common/commander.py"

SHIM_SCRIPT = r'''#!/bin/bash
# multiloc: runs ONLY inside the private namespace of a full apply. xray-router start / restart / stop is deferred (logged, answered "ok"), is-active xray-router
# answers "active"; everything else - and every other unit - goes to the real systemctl.
verb=""; unit=""
for a in "$@"; do
  case "$a" in
    -*) ;;
    restart|start|stop|try-restart|reload|reload-or-restart|condrestart|is-active) [ -z "$verb" ] && verb="$a";;
    xray-router|xray-router.service) unit="$a";;
  esac
done
if [ -n "$unit" ]; then
  case "$verb" in
    is-active) echo active; exit 0;;
    restart|start|stop|try-restart|reload|reload-or-restart|condrestart)
      echo "$(date +%T) deferred: systemctl $*" >> "${MULTILOC_SHIM_LOG:-/dev/null}"; exit 0;;
  esac
fi
exec /usr/bin/systemctl "$@"
'''
XRAY_SHIM_SCRIPT = r'''#!/bin/bash
# multiloc: runs ONLY inside the private namespace of a full apply. The legacy layers test their finished file with `xray run -test -config FILE`; with the production rule set (15k bulk rules, 2 vCPU)
# one such test takes ~110 s - at the edge of the layers' own 120 s limit, a loaded server crosses it and the whole pipeline fails - and every layer does it. Here that test is run on the SLIM copy
# (single-entry bulk rules cut out: they cannot break the syntax of the rest; the finished file is tested slim again before it is swapped in). Every other xray call goes to the real binary.
self="$(cd "$(dirname "$0")" && pwd)"
real="$(PATH="${PATH//$self:/}" command -v xray)"; real="${real:-/usr/bin/xray}"
cfg=""; prev=""
for a in "$@"; do case "$prev" in -config|-c) cfg="$a";; esac; prev="$a"; done
if [ "$1" = run ] && [ -n "$cfg" ] && [ -f "$cfg" ] && [[ " $* " == *" -test "* ]]; then
  slim="$(mktemp "${MULTILOC_SLIM_DIR:-/tmp}/slim.XXXXXX.json")"
  if python3 - "$cfg" "$slim" <<'PY'
import json, sys
c = json.load(open(sys.argv[1], encoding="utf-8"))
r = dict(c.get("routing") or {})
def bulk(x):
    if not isinstance(x, dict):
        return False
    k = set(x) - {"type"}
    return (k == {"ip", "outboundTag"} and len(x["ip"]) == 1) or (k == {"domain", "outboundTag"} and len(x["domain"]) == 1)
r["rules"] = [x for x in r.get("rules", []) if not bulk(x)]
c["routing"] = r
json.dump(c, open(sys.argv[2], "w", encoding="utf-8"))
PY
  then "$real" run -test -config "$slim"; rc=$?; rm -f "$slim"; exit $rc; fi
  rm -f "$slim"
fi
exec "$real" "$@"
'''
# executed by `unshare -m`: $1 scratch dir, $2 shim dir, $3 python, $4 commander.py
# `nice`: the build is CPU-bound (xray -test of the full config); the live router and the panel keep priority on a 2 vCPU server
CHILD = 'mount --bind "$1" ' + LIVE_DIR + ' || exit 97; export PATH="$2:$PATH"; exec nice -n 10 "$3" "$4" commercial-routing-apply'


def _sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def classic(log=print) -> int:
    """the legacy pipeline as it always ran: every layer restarts the router"""
    p = subprocess.Popen([PY, COMMANDER, "commercial-routing-apply"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                         env=dict(os.environ, PYTHONUNBUFFERED="1"))
    for line in p.stdout:
        log(line.rstrip("\n"))
    return p.wait()


def _namespace_works() -> tuple[bool, str]:
    """can this host run a private mount namespace with a bind mount? Asked with a throw-away pair of directories before anything is built: a container without
    CAP_SYS_ADMIN or a hardened host refuses it, and then the classic pipeline is the way (instead of a failed Apply)."""
    import tempfile
    os.makedirs(STATE_DIR, mode=0o755, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="probe-", dir=STATE_DIR)
    try:
        a, b = os.path.join(tmp, "a"), os.path.join(tmp, "b")
        os.mkdir(a)
        os.mkdir(b)
        r = subprocess.run(["unshare", "-m", "--propagation", "private", "/bin/bash", "-c", 'mount --bind "$1" "$2"', "bash", a, b],
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return False, "a private mount namespace is refused here: " + ((r.stderr or r.stdout or "").strip()[-200:] or f"rc={r.returncode}")
        return True, ""
    except Exception as exc:
        return False, f"cannot probe a private mount namespace: {type(exc).__name__}: {exc}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def possible() -> tuple[bool, str]:
    if os.geteuid() != 0:
        return False, "not root"
    if not shutil.which("unshare") or not shutil.which("mount"):
        return False, "unshare / mount are not available"
    if not Path(LIVE).exists():
        return False, f"{LIVE} does not exist yet (the first apply builds it the classic way)"
    try:
        json.loads(Path(LIVE).read_text())
    except Exception as exc:
        return False, f"{LIVE} is not valid JSON ({exc})"
    return _namespace_works()


def _prepare_scratch() -> None:
    shutil.rmtree(SCRATCH, ignore_errors=True)
    os.makedirs(SCRATCH, mode=0o700)
    os.makedirs(SHIM_DIR, mode=0o755, exist_ok=True)
    for entry in Path(LIVE_DIR).iterdir():          # only what the pipeline reads: the config and the ui mirror - not the pile of old *.bak copies
        n = entry.name
        if entry.is_file() and ".bak" not in n and not n.endswith((".tmp", ".tmp.json")) and ".multiloc." not in n:
            shutil.copy2(entry, Path(SCRATCH) / n)
    shim = Path(SHIM_DIR) / "systemctl"
    shim.write_text(SHIM_SCRIPT)
    shim.chmod(0o755)
    xshim = Path(SHIM_DIR) / "xray"
    xshim.write_text(XRAY_SHIM_SCRIPT)
    xshim.chmod(0o755)
    Path(SHIM_LOG).unlink(missing_ok=True)


def _restarts() -> int:
    try:
        return int(subprocess.run(["systemctl", "show", "-p", "NRestarts", "--value", "xray-router"], capture_output=True, text=True, timeout=10).stdout.strip() or 0)
    except Exception:
        return 0


def _wait_serving(ports, limit: float) -> tuple[bool, float, str]:
    """the real health check of a Type=simple router: its own entrances listen. Fails early when it crash-loops instead of waiting out the whole limit."""
    from hiddifypanel.hutils import multiloc_router as mr
    n0, t0 = _restarts(), time.monotonic()
    while True:
        if mr.entrances_listening(ports):
            return True, round(time.monotonic() - t0, 1), ""
        waited = time.monotonic() - t0
        if _restarts() - n0 >= 2:
            return False, round(waited, 1), "xray-router keeps crashing and restarting"
        if waited >= limit:
            return False, round(waited, 1), f"no router entrance listening after {round(waited)} s"
        time.sleep(0.5)


STAGED_DIR = STATE_DIR + "/staged"            # the finished config that waits for the owner's decision: config.json + meta.json


def staged_meta() -> dict | None:
    try:
        with open(STAGED_DIR + "/meta.json", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def stage(new_text: str, meta: dict) -> None:
    shutil.rmtree(STAGED_DIR, ignore_errors=True)
    os.makedirs(STAGED_DIR, mode=0o755)
    cfg = Path(STAGED_DIR) / "config.json"
    cfg.write_text(new_text)
    cfg.chmod(0o600)                                # the config holds the nodes' credentials: root only
    m = Path(STAGED_DIR) / "meta.json"
    m.write_text(json.dumps(meta, ensure_ascii=False))
    m.chmod(0o644)                                  # the panel shows the differences (no secrets in the meta)


def discard_staged() -> None:
    shutil.rmtree(STAGED_DIR, ignore_errors=True)


def _expected_text(expected) -> str:
    return ", ".join(sorted(expected)) if expected else "nothing (no change is recorded)"


def run(classic_only: bool = False, log=print, expected=None, parts=None, accept_diff: bool = False) -> int:
    """0 = applied (or already up to date), 2 = failed (nothing half-applied: the previous router config is serving), 4 = the finished config differs from the running one
    in ways the change does not explain: kept for the owner's decision, the router was NOT touched, other = the classic pipeline's own code.
    expected = the plan parts that changed (what the owner edited); parts = the inputs of this run (checked again when the staged config is accepted);
    accept_diff = apply whatever the pipeline built (console `--accept-diff`)."""
    try:
        return _run(classic_only, log, set(expected or ()), parts, accept_diff)
    except Exception as exc:
        log(f"full apply: FAILED with an unexpected error: {type(exc).__name__}: {exc}")
        return 2


def _run(classic_only: bool, log, expected: set, parts, accept_diff: bool) -> int:
    t_all = time.monotonic()
    ok, why = possible()
    if classic_only or not ok:
        log("full apply: CLASSIC mode (" + ("requested" if classic_only else why) + "): the router restarts after every layer of the pipeline")
        return classic(log)
    from hiddifypanel.hutils import multiloc_router as mr
    started_hash = _sha(LIVE)
    live_text = Path(LIVE).read_text()
    live_cfg = json.loads(live_text)
    _prepare_scratch()
    env = dict(os.environ, MULTILOC_DEFER_ROUTER_RESTART="1", MULTILOC_SHIM_LOG=SHIM_LOG, MULTILOC_SLIM_DIR=STATE_DIR, PYTHONUNBUFFERED="1")
    try:
        log("full apply: running the legacy pipeline in a private copy of /etc/xray-router; router restarts are deferred to ONE at the end")
        p = subprocess.Popen(["unshare", "-m", "--propagation", "private", "/bin/bash", "-c", CHILD, "bash", SCRATCH, SHIM_DIR, PY, COMMANDER],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=env)
        for line in p.stdout:
            log(line.rstrip("\n"))
        rc = p.wait()
        if rc == 97:                                       # `mount --bind` was refused inside the namespace: this host cannot do it - the classic way works anywhere
            log("full apply: a private mount namespace is not available here (mount refused): falling back to the CLASSIC pipeline")
            shutil.rmtree(SCRATCH, ignore_errors=True)
            return classic(log)
        deferred = Path(SHIM_LOG).read_text().splitlines() if Path(SHIM_LOG).exists() else []
        log(f"full apply: the pipeline finished (rc={rc}) in {round(time.monotonic() - t_all)} s; {len(deferred)} router restart(s) were deferred")
        if rc != 0:
            log("full apply: the pipeline FAILED - the live router and its config were never touched")
            return rc if rc > 0 else 2
        new_text = (Path(SCRATCH) / "config.json").read_text()
        new_cfg = json.loads(new_text)
        from hiddifypanel.hutils import router_compact
        n_rules = len(new_cfg.get("routing", {}).get("rules", []))
        new_cfg = router_compact.compact_config(new_cfg)                  # same routing, the router starts in seconds (see router_compact)
        new_text = json.dumps(new_cfg, indent=2, ensure_ascii=False) + "\n"
        log(f"full apply: the finished config has {n_rules} rules, {len(new_cfg['routing']['rules'])} after joining neighbouring plain lists")
        if _sha(LIVE) != started_hash:
            log("full apply: ABORTED - the live router config changed while the pipeline was running; nothing was swapped (run the apply again)")
            return 2
        if new_text == live_text:
            discard_staged()
            log("full apply: the finished router config equals the running one - no restart needed")
            return 0
        if not accept_diff:
            try:
                from hiddifypanel.hutils import router_diff
                verdict = router_diff.review(live_cfg, new_cfg, expected)
            except Exception as exc:                       # cannot compare = cannot call it safe
                verdict = {"diffs": [], "explained": [], "warnings": [], "unexplained": [
                    {"key": "compare", "title": "Сравнение конфигов не удалось", "lines": [f"{type(exc).__name__}: {exc}"], "parts": []}]}
            lines_un = [f"{d['title']}: {ln}" for d in verdict["unexplained"] for ln in d["lines"]]
            lines_ex = [f"{d['title']}: {ln}" for d in verdict["explained"] for ln in d["lines"]]
            if verdict["unexplained"]:
                stage(new_text, {"built_at": time.time(), "built_text": time.strftime("%Y-%m-%d %H:%M:%S"), "live_sha": started_hash, "new_sha": hashlib.sha256(new_text.encode()).hexdigest(),
                                 "parts": parts, "expected": sorted(expected), "unexplained": lines_un, "explained": lines_ex, "warnings": verdict["warnings"]})
                log(f"full apply: NOT APPLIED - the finished config differs from the running one in ways your change ({_expected_text(expected)}) does not explain:")
                for ln in lines_un:
                    log("   - " + ln)
                for ln in verdict["warnings"]:
                    log("   ! " + ln)
                log("full apply: the finished config is kept for your decision (the panel offers «Применить новый конфиг» / «Не применять»); the router was NOT touched")
                return 4
            if lines_ex:
                log("full apply: the differences are explained by your change (" + _expected_text(expected) + "):")
                for ln in lines_ex:
                    log("   - " + ln)
            for ln in verdict["warnings"]:
                log("   ! " + ln)
        rc = _swap(new_text, new_cfg, live_text, live_cfg, log, t_all)
        if rc == 0:
            discard_staged()                                # a config kept earlier was built against the previous running config: obsolete now
        return rc
    finally:
        shutil.rmtree(SCRATCH, ignore_errors=True)


def _swap(new_text: str, new_cfg: dict, live_text: str, live_cfg: dict, log, t_all: float) -> int:
    """checks -> backups -> atomic replace -> ONE restart -> wait until the entrances listen -> put the previous config back on any failure"""
    from hiddifypanel.hutils import multiloc_router as mr
    problems = mr.check_invariants(new_cfg)
    if problems:
        log("full apply: ABORTED - the finished config violates the router invariants: " + "; ".join(problems))
        return 2
    busy = mr.busy_new_ports(live_cfg, new_cfg)
    if busy:
        log(f"full apply: ABORTED - router ports {busy} are already in use by another process; nothing was swapped")
        return 2
    slim_path = Path(STATE_DIR) / "finished.slim.json"
    try:
        slim_path.write_text(json.dumps(mr.slim(new_cfg)))
        res = subprocess.run([shutil.which("xray") or "/usr/bin/xray", "run", "-test", "-config", str(slim_path)], capture_output=True, text=True, timeout=180)
    finally:
        slim_path.unlink(missing_ok=True)
    if res.returncode != 0:
        log("full apply: ABORTED - the finished config failed `xray -test`:\n" + (res.stdout or "") + (res.stderr or ""))
        return 2
    os.makedirs(BACKUPS, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup = Path(BACKUPS) / f"config.json.{stamp}.before-full-apply.bak"
    backup.write_text(live_text)
    shutil.copy2(LIVE, f"{LIVE}.bak.{stamp}")            # the pile of *.bak copies next to the config is the classic pipeline's own convention
    tmp = Path(LIVE_DIR) / "config.json.multiloc.tmp.json"
    shutil.copy2(LIVE, tmp)
    tmp.write_text(new_text)
    os.replace(tmp, LIVE)
    log("full apply: the finished config is in place; restarting xray-router ONCE")
    try:
        subprocess.run(["systemctl", "reset-failed", "xray-router"], capture_output=True, text=True, timeout=10, check=False)
        r = subprocess.run(["systemctl", "restart", "xray-router"], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise RuntimeError("systemctl restart xray-router failed: " + ((r.stderr or r.stdout or "").strip() or f"rc={r.returncode}"))
        ready, waited, reason = _wait_serving(mr.router_ports(new_cfg), float(os.environ.get("MULTILOC_ROUTER_READY_SECONDS") or mr.READY_SECONDS))
        if not ready:
            raise RuntimeError(reason)
    except BaseException as exc:
        back = mr._restore_previous(backup, Path(LIVE), live_cfg)
        log(f"full apply: FAILED - xray-router did not start serving the finished config ({exc}); the previous config was put back and "
            + ("is serving again" if back else "is NOT serving yet - check `systemctl status xray-router`"))
        if not isinstance(exc, Exception):
            raise
        return 2
    log(f"full apply: done in {round(time.monotonic() - t_all)} s, ONE router restart, entrances listening after {waited} s (backup: {backup})")
    return 0


def accept(parts_now, log=print) -> int:
    """the owner confirmed the staged config: apply EXACTLY what was built - but only if nothing it was built from has changed since (the running config, the rules / nodes /
    settings). 0 = applied, 2 = refused or failed (the running router is serving)."""
    t_all = time.monotonic()
    try:
        meta = staged_meta()
        if not meta:
            log("accept: nothing is waiting for confirmation")
            return 2
        live_bytes = Path(LIVE).read_bytes()
        live_text = live_bytes.decode("utf-8")
        live_cfg = json.loads(live_text)
        if hashlib.sha256(live_bytes).hexdigest() != meta.get("live_sha"):
            log("accept: REFUSED - the running router config changed after the new one was built; the built config is discarded: press «Применить» again")
            discard_staged()
            return 2
        if parts_now is not None and meta.get("parts") not in (None, parts_now):
            log("accept: REFUSED - the rules / nodes / settings changed after the new config was built; it is discarded: press «Применить» again")
            discard_staged()
            return 2
        new_text = (Path(STAGED_DIR) / "config.json").read_text()
        if hashlib.sha256(new_text.encode()).hexdigest() != meta.get("new_sha"):
            log("accept: REFUSED - the kept config is damaged; it is discarded: press «Применить» again")
            discard_staged()
            return 2
        log("accept: applying the config that was built at " + str(meta.get("built_text")))
        rc = _swap(new_text, json.loads(new_text), live_text, live_cfg, log, t_all)
        if rc == 0:
            discard_staged()
        return rc
    except Exception as exc:
        log(f"accept: FAILED with an unexpected error: {type(exc).__name__}: {exc}")
        return 2
