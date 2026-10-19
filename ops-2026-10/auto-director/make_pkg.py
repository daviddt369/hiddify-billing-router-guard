#!/usr/bin/env python3
"""builds pkg/ (MANIFEST + payload + project tools) for the AUTO-director delta: usage make_pkg.py <work/src dir of the multi-location package> <out dir>"""
import hashlib, json, shutil, sys
from pathlib import Path

src, out = Path(sys.argv[1]), Path(sys.argv[2])
H = "opt/hiddify-manager/.venv313/lib/python3.13/site-packages/hiddifypanel/hutils/"
FILES = [H + "multiloc_router.py", H + "multiloc.py"]
full = json.loads((src / "MANIFEST.json").read_text())
entries = []
for rel in FILES:
    e = next(x for x in full["files"] if x["path"] == rel)
    p = src / "payload" / rel
    e = dict(e, new=hashlib.sha256(p.read_bytes()).hexdigest(), size=p.stat().st_size)
    entries.append(e)
    dst = out / "payload" / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(p, dst)
(out / "MANIFEST.json").write_text(json.dumps({"feature": full.get("feature", "multiloc"), "version": full.get("version", 1), "files": entries}, indent=1))
for name in ("multiloc_installer.py", "multiloc_delta_deploy.py"):
    shutil.copy2(src / name, out / name)
print("package:", out, [(e["path"].split("/")[-1], e["base"] if e["base"] == "NEW" else e["base"][:8], e["new"][:16]) for e in entries])
