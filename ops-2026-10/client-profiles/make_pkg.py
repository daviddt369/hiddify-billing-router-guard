#!/usr/bin/env python3
"""builds pkg/ for the one-file delta (multiloc.py only): usage make_pkg.py <AUTO-director/pkg dir (MANIFEST + installer + delta tool)> <new multiloc.py> <out dir>"""
import hashlib
import json
import shutil
import sys
from pathlib import Path

base_pkg, new_file, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
H = "opt/hiddify-manager/.venv313/lib/python3.13/site-packages/hiddifypanel/hutils/multiloc.py"
full = json.loads((base_pkg / "MANIFEST.json").read_text())
e = next(x for x in full["files"] if x["path"] == H)
e = dict(e, new=hashlib.sha256(new_file.read_bytes()).hexdigest(), size=new_file.stat().st_size)
dst = out / "payload" / H
dst.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(new_file, dst)
(out / "MANIFEST.json").write_text(json.dumps({"feature": full.get("feature", "multiloc"), "version": full.get("version", 1), "files": [e]}, indent=1))
for name in ("multiloc_installer.py", "multiloc_delta_deploy.py"):
    shutil.copy2(base_pkg / name, out / name)
print("package:", out, e["path"].split("/")[-1], e["new"][:16])
