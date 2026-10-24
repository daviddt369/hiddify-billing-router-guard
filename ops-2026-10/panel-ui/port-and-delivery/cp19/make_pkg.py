#!/usr/bin/env python3
"""builds the CP-19 delta (a package over CP-18): readable tables / status log, compact Happ page, bot trial settings, no install prompt in the admin panel.
usage make_pkg.py <final tree (paths relative to hiddifypanel/)> <out dir>
Entries come from the newest earlier package that carries the file (CP-18, 17, 16, 15), else from the full layer manifest; only files whose content changed are put in."""
import hashlib
import json
import shutil
import sys
from pathlib import Path

tree, out = Path(sys.argv[1]), Path(sys.argv[2])
ROOT = Path(__file__).resolve().parent.parent
PK = "opt/hiddify-manager/.venv313/lib/python3.13/site-packages/hiddifypanel/"
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
SOURCES = [("CP-18", ROOT / "CP-18-faq-bot-texts/pkg"), ("CP-17", ROOT / "CP-17-polish/pkg"), ("CP-16", ROOT / "CP-16-admin-v2-trial/pkg"), ("CP-15", ROOT / "CP-15-happ-ui/pkg")]
manifests = [(name, {e["path"]: e for e in json.loads((d / "MANIFEST.json").read_text())["files"]}) for name, d in SOURCES]
meta = json.loads((SOURCES[0][1] / "MANIFEST.json").read_text())
if out.exists():
    shutil.rmtree(out)
entries = []
for p in sorted(x for x in tree.rglob("*") if x.is_file()):
    rel = p.relative_to(tree).as_posix()
    path = PK + rel
    hit = next(((n, m[path]) for n, m in manifests if path in m), None)
    if not hit:
        raise SystemExit(f"{rel}: not in any earlier package")
    how, e = hit[0], dict(hit[1])
    if e.get("new") == sha(p):
        continue                                                        # unchanged since that package
    e.update(new=sha(p), size=p.stat().st_size)
    entries.append(e)
    print("  ", rel, "<-", how)
    dst = out / "payload" / path
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(p, dst)
(out / "MANIFEST.json").write_text(json.dumps({"feature": meta.get("feature", "multiloc"), "version": meta.get("version", 1), "files": entries}, indent=1))
for name in ("multiloc_installer.py", "multiloc_delta_deploy.py"):
    shutil.copy2(SOURCES[0][1] / name, out / name)
print("package:", out, "files:", len(entries))
