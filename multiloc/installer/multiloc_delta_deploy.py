#!/usr/bin/env python3
"""Delta deploy of a newer multi-location package over an installed older one (the installer itself only knows base -> new).

  multiloc_delta_deploy.py --new <new package dir> [--run <installer run dir>] [--backup <dir>] [--apply] [--no-restart]

Without --apply it only prints the plan (nothing is touched). With --apply, for every payload file whose live content is not the new version:
  * the live content must be either what the installer wrote for this run (STATE.json installed_sha) or the stock original (a stock file the older
    package did not touch yet) - anything else aborts BEFORE a single change;
  * the current content is copied to <backup>/<abs path>, the new content replaces it atomically (mode / owner kept), python files are compiled;
  * a stock file touched for the first time is added to the run's STATE.json (backup of the original in <run>/files/, `replaced_files`,
    `installed_sha`) so that `install.sh rollback` puts it back; STATE.json is copied to <backup> first and written atomically;
  * the panel is restarted once (unless --no-restart); on ANY failure everything is restored from <backup> and the panel restarted.
"""
import argparse
import importlib.util
import json
import os
import py_compile
import shutil
import subprocess
import sys
import time
from pathlib import Path


def load_installer(pkg: Path):
    spec = importlib.util.spec_from_file_location("multiloc_installer_pkg", pkg / "multiloc_installer.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--new", required=True)
    ap.add_argument("--run")
    ap.add_argument("--backup")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--no-restart", action="store_true")
    a = ap.parse_args()
    new = Path(a.new).resolve()
    mi = load_installer(new)
    manifest = json.loads((new / "MANIFEST.json").read_text())["files"]
    runs = [r for r in mi.list_runs() if mi.get_state(r).get("finished") and not mi.get_state(r).get("rolled_back")]
    if not runs:
        sys.exit("no finished, not rolled back installer run: nothing to deploy over")
    run = Path(a.run) if a.run else runs[-1]
    state = mi.get_state(run)
    installed = state.get("installed_sha", {})
    plan, problems = [], []
    for e in manifest:
        payload = new / "payload" / e["path"]
        if mi.sha(payload) != e["new"]:
            problems.append(f"{e['path']}: the package payload does not match its own manifest")
            continue
        t = mi.target_of(e["path"])
        cur = mi.sha(t) if t.exists() else None
        if cur == e["new"]:
            continue
        prev = installed.get(e["path"])
        if cur is None and e["path"] in state.get("new_files", []):
            kind = "restore-new"                       # a file this run added and somebody removed
        elif cur is not None and cur == prev:
            kind = "update"
        elif cur is not None and e["base"] != "NEW" and cur == e["base"] and e["path"] not in state.get("replaced_files", []):
            kind = "first-touch"                       # a stock file the older package did not modify
        elif cur is None and e["base"] == "NEW":
            kind = "add-new"
        else:
            problems.append(f"{e['path']}: live content is neither what this run installed nor the stock original - refusing")
            continue
        plan.append((kind, e, t))
    print(f"run: {run.name}; package: {new}; files in the manifest: {len(manifest)}; to change: {len(plan)}")
    for kind, e, t in plan:
        print(f"   {kind:12} {e['path']}")
    if problems:
        for p in problems:
            print("PROBLEM:", p)
        sys.exit(1)
    if not a.apply:
        print("dry run: nothing was changed")
        return
    if not plan:
        print("nothing to do")
        return
    backup = Path(a.backup or f"/root/multiloc-delta-backup-{time.strftime('%Y%m%d-%H%M%S')}")
    backup.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.copy2(run / "STATE.json", backup / "STATE.json.before")
    done = []
    py_changed = False
    try:
        for kind, e, t in plan:
            if t.exists():
                dst = backup / e["path"]
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(t, dst)
            if kind == "first-touch":
                keep = run / "files" / e["path"]
                keep.parent.mkdir(parents=True, exist_ok=True)
                if not keep.exists():
                    shutil.copy2(t, keep)
            mi.copy_with_meta(new / "payload" / e["path"], t)
            done.append((kind, e, t))
            if t.suffix == ".py":
                py_changed = True
                pyc = t.parent / "__pycache__"
                if pyc.is_dir():
                    for f in pyc.glob(t.stem + ".*.pyc"):
                        f.unlink()
                py_compile.compile(str(t), doraise=True)
            assert mi.sha(t) == e["new"], f"verification after copy failed: {t}"
        st = mi.get_state(run)
        for kind, e, t in done:
            if kind == "first-touch" and e["path"] not in st.setdefault("replaced_files", []):
                st["replaced_files"].append(e["path"])
            if kind == "add-new" and e["path"] not in st.setdefault("new_files", []):
                st["new_files"].append(e["path"])
            st.setdefault("installed_sha", {})[e["path"]] = e["new"]
        st.setdefault("delta_deploys", []).append({"at": time.strftime("%Y%m%d-%H%M%S"), "files": [e["path"] for _, e, _ in done], "backup": str(backup)})
        mi.write_state(run, st)
        if py_changed and not a.no_restart:
            subprocess.run(["systemctl", "restart", "hiddify-panel"], check=True)
            if not mi.wait_panel():
                raise RuntimeError("hiddify-panel did not come back after the restart")
    except BaseException as exc:
        print(f"FAILED: {exc!r} -> restoring everything from {backup}")
        for kind, e, t in reversed(done):
            b = backup / e["path"]
            if b.exists():
                mi.copy_with_meta(b, t)
            elif kind in ("add-new", "restore-new") and t.exists():
                t.unlink()
        shutil.copy2(backup / "STATE.json.before", run / "STATE.json")
        if py_changed and not a.no_restart:
            subprocess.run(["systemctl", "restart", "hiddify-panel"], check=False)
            mi.wait_panel()
        sys.exit(2)
    print(f"DELTA DEPLOY OK: {len(done)} file(s), backup in {backup}")


if __name__ == "__main__":
    main()
