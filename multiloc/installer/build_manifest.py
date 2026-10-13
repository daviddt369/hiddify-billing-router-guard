#!/usr/bin/env python3
"""MANIFEST.json = for every payload file: expected BASE sha256 (prod-identical original, or "NEW") and the NEW sha256."""
import hashlib, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
P, B = os.path.join(HERE, "payload"), os.path.join(HERE, "base")

def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()

files = []
for root, _, fs in os.walk(P):
    if "__pycache__" in root:
        continue                      # compiler leftovers are never part of the payload
    for f in sorted(fs):
        if f.endswith((".pyc", ".pyo")):
            continue
        full = os.path.join(root, f)
        rel = os.path.relpath(full, P).replace(os.sep, "/")
        bp = os.path.join(B, rel)
        files.append({"path": rel, "base": sha(bp) if os.path.exists(bp) else "NEW", "new": sha(full),
                      "size": os.path.getsize(full)})
files.sort(key=lambda e: e["path"])
json.dump({"feature": "multiloc", "version": 1, "files": files}, open(os.path.join(HERE, "MANIFEST.json"), "w"), indent=1)
print(f"MANIFEST.json: {len(files)} files ({sum(1 for e in files if e['base']=='NEW')} new, {sum(1 for e in files if e['base']!='NEW')} modified)")
