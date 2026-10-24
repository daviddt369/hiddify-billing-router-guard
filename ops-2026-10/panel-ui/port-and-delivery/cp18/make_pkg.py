#!/usr/bin/env python3
"""builds the CP-18 delta (a package over CP-17): instruction panels, folded router log, editable Telegram bot texts.
usage make_pkg.py <CP-17 pkg dir> <full layer MANIFEST.json> <final tree (paths relative to hiddifypanel/)> <stock dir (originals of the files the manifests do not know)> <out dir>
- a file CP-17 / CP-16 already carries keeps its entry (`base`); a file only the full layer manifest knows keeps that entry (base = the real stock original);
- anything else that exists in <stock dir> gets base = sha of that original; the rest are new files (base NEW). Only files whose content changed are put in."""
import hashlib
import json
import shutil
import sys
from pathlib import Path

prev, fullm, tree, stock, out = (Path(a) for a in sys.argv[1:6])
PK = "opt/hiddify-manager/.venv313/lib/python3.13/site-packages/hiddifypanel/"
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
pm = json.loads((prev / "MANIFEST.json").read_text())
known = {e["path"]: e for e in pm["files"]}
cp16 = {e["path"]: e for e in json.loads((prev.parent.parent / "CP-16-admin-v2-trial/pkg/MANIFEST.json").read_text())["files"]}
layer = {e["path"]: e for e in json.loads(fullm.read_text())["files"]}
cp15 = {e["path"]: e for e in json.loads((prev.parent.parent / "CP-15-happ-ui/pkg/MANIFEST.json").read_text())["files"]}
if out.exists():
    shutil.rmtree(out)
entries = []
for p in sorted(x for x in tree.rglob("*") if x.is_file()):
    rel = p.relative_to(tree).as_posix()
    path = PK + rel
    if path in known:
        e, how = dict(known[path]), "CP-17"
    elif path in cp16:
        e, how = dict(cp16[path]), "CP-16"
    elif path in cp15:
        e, how = dict(cp15[path]), "CP-15"
    elif path in layer:
        e, how = dict(layer[path]), "layer manifest"
    elif (stock / rel).exists():
        e, how = {"path": path, "base": sha(stock / rel)}, "stock original"
    else:
        e, how = {"path": path, "base": "NEW"}, "new file"
    if e.get("new") == sha(p):
        continue                                                        # unchanged
    e.update(new=sha(p), size=p.stat().st_size)
    e["_how"] = how
    entries.append(e)
    dst = out / "payload" / path
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(p, dst)
for e in entries:
    print("  ", e["path"].split("hiddifypanel/")[-1], "<-", e.pop("_how"))
(out / "MANIFEST.json").write_text(json.dumps({"feature": pm.get("feature", "multiloc"), "version": pm.get("version", 1), "files": entries}, indent=1))
for name in ("multiloc_installer.py", "multiloc_delta_deploy.py"):
    shutil.copy2(prev / name, out / name)
print("package:", out, "files:", len(entries))
