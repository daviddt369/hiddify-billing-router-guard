#!/usr/bin/env python3
"""Builds the one-file delta package for the project's own deploy tool (multiloc_delta_deploy.py).
usage: make_delta_pkg.py <work/src dir of the multi-location package> <new multiloc_router.py> <out dir>
The manifest holds ONLY multiloc_router.py (base "NEW" as in the real manifest, new = sha256 of the file); the tool touches nothing else."""
import hashlib, json, shutil, sys
from pathlib import Path

src, newfile, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
REL = "opt/hiddify-manager/.venv313/lib/python3.13/site-packages/hiddifypanel/hutils/multiloc_router.py"
full = json.loads((src / "MANIFEST.json").read_text())
entry = next(e for e in full["files"] if e["path"] == REL)
sha = hashlib.sha256(newfile.read_bytes()).hexdigest()
entry = dict(entry, new=sha, size=newfile.stat().st_size)
if out.exists():
    shutil.rmtree(out)
(out / "payload" / Path(REL).parent).mkdir(parents=True)
shutil.copy2(newfile, out / "payload" / REL)
(out / "MANIFEST.json").write_text(json.dumps({"feature": full.get("feature", "multiloc"), "version": full.get("version", 1), "files": [entry]}, indent=1))
for name in ("multiloc_installer.py", "multiloc_delta_deploy.py"):
    shutil.copy2(src / name, out / name)
print(f"delta package in {out}: 1 file, new sha256 {sha[:16]}…, base {entry['base'] if entry['base']=='NEW' else entry['base'][:16]}")
