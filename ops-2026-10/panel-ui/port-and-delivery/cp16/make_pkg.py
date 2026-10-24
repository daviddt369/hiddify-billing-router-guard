#!/usr/bin/env python3
"""builds pkg/ for the CP-16 delta (admin shell V2 + dashboard + node card + theme + grouped menu): 16 python/template/css files and the static/admin-v2 tree.
usage make_pkg.py <src dir of the multi-location package (MANIFEST.json, installer, delta tool)> <final tree (paths relative to hiddifypanel/)> <stock dir (originals of the stock files CP-16 edits)> <out dir>
- a file the multi-location manifest already knows keeps its `base` (the real stock original), `new` is the CP-16 version;
- a stock file the manifest does not know (models/usage.py, the REST admin __init__.py) gets base = sha of the original from <stock dir>;
- everything else is a new file (base NEW)."""
import hashlib
import json
import shutil
import sys
from pathlib import Path

src, tree, stock, out = (Path(a) for a in sys.argv[1:5])
PK = "opt/hiddify-manager/.venv313/lib/python3.13/site-packages/hiddifypanel/"
EDITED = ["models/usage.py", "panel/commercial/restapi/v2/admin/__init__.py", "panel/admin/__init__.py", "templates/admin-layout.html"]
full = json.loads((src / "MANIFEST.json").read_text())
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
if out.exists():
    shutil.rmtree(out)
entries = []
for p in sorted(x for x in tree.rglob("*") if x.is_file()):
    rel = p.relative_to(tree).as_posix()
    path = PK + rel
    known = next((x for x in full["files"] if x["path"] == path), None)
    if known:
        e = dict(known)
    elif rel in EDITED:
        e = {"path": path, "base": sha(stock / rel)}
    else:
        e = {"path": path, "base": "NEW"}
    e.update(new=sha(p), size=p.stat().st_size)
    entries.append(e)
    dst = out / "payload" / path
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(p, dst)
(out / "MANIFEST.json").write_text(json.dumps({"feature": full.get("feature", "multiloc"), "version": full.get("version", 1), "files": entries}, indent=1))
for name in ("multiloc_installer.py", "multiloc_delta_deploy.py"):
    shutil.copy2(src / name, out / name)
print("package:", out, "files:", len(entries), "edited:", [(e["path"].split("hiddifypanel/")[-1], e["base"][:10]) for e in entries if e["path"].split("hiddifypanel/")[-1] in EDITED])
