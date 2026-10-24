#!/usr/bin/env python3
"""builds the CP-17 delta (a package over CP-16): only the files whose content differs from the CP-16 package, plus the stock files CP-17 touches for the first time.
usage make_pkg.py <CP-16 pkg dir> <final tree (paths relative to hiddifypanel/)> <stock dir (originals of the NEW stock files)> <out dir>
- a file that CP-16 already carries keeps its manifest entry (`base`), `new` is the CP-17 content;
- a stock file CP-16 did not touch (hutils/utils.py, templates/master.html, panel/admin/templates/view_logs.html) gets base = sha of the original from <stock dir>."""
import hashlib
import json
import shutil
import sys
from pathlib import Path

prev, tree, stock, out = (Path(a) for a in sys.argv[1:5])
PK = "opt/hiddify-manager/.venv313/lib/python3.13/site-packages/hiddifypanel/"
NEW_STOCK = ["hutils/utils.py", "templates/master.html", "panel/admin/templates/view_logs.html"]
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
pm = json.loads((prev / "MANIFEST.json").read_text())
known = {e["path"]: e for e in pm["files"]}
if out.exists():
    shutil.rmtree(out)
entries = []
for p in sorted(x for x in tree.rglob("*") if x.is_file()):
    rel = p.relative_to(tree).as_posix()
    path = PK + rel
    if path in known:
        if known[path]["new"] == sha(p):
            continue                                            # unchanged since CP-16
        e = dict(known[path])
    elif rel in NEW_STOCK:
        e = {"path": path, "base": sha(stock / rel)}
    else:
        raise SystemExit(f"unexpected file outside CP-16 and the stock list: {rel}")
    e.update(new=sha(p), size=p.stat().st_size)
    entries.append(e)
    dst = out / "payload" / path
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(p, dst)
(out / "MANIFEST.json").write_text(json.dumps({"feature": pm.get("feature", "multiloc"), "version": pm.get("version", 1), "files": entries}, indent=1))
for name in ("multiloc_installer.py", "multiloc_delta_deploy.py"):
    shutil.copy2(prev / name, out / name)
print("package:", out, "files:", len(entries))
for e in entries:
    print("  ", e["path"].split("hiddifypanel/")[-1], "(new stock)" if e["path"] not in known else "")
