#!/usr/bin/env python3
"""Render the golden overlay (placeholders @@NAME@@ -> values of the params file) and install it on this server.
Every file that exists is first copied to <backup>/<path>; the list of new files goes to <backup>/NEW_FILES so rollback.sh can remove them again.

usage: overlay_apply.py --params FILE --backup DIR [--root /] [--dry-run]
exit 0 = installed, 2 = a placeholder has no value (nothing is written in that case)"""
import argparse
import hashlib
import os
import pwd
import grp
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from gpaths import stored  # noqa: E402
ap = argparse.ArgumentParser()
ap.add_argument("--params", required=True)
ap.add_argument("--backup", required=True)
ap.add_argument("--root", default="/")
ap.add_argument("--dry-run", action="store_true")
a = ap.parse_args()

params = {}
for line in open(a.params, encoding="utf-8"):
    line = line.rstrip("\n")
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        params[k] = v

rows = [l.rstrip("\n").split("\t") for l in open(os.path.join(HERE, "MANIFEST.tsv"), encoding="utf-8") if l.strip() and not l.startswith("#")]
PH = re.compile(r"@@([A-Z0-9_]+)@@")

# 1) every placeholder must have a value BEFORE anything is written
need = {}
for path, mode, owner, sha, kind in rows:
    if kind != "text":
        continue
    for name in PH.findall(open(os.path.join(HERE, "overlay", stored(path)), encoding="utf-8").read()):
        need.setdefault(name, []).append(path)
missing = {n: p for n, p in need.items() if not params.get(n)}
if missing:
    for n, p in sorted(missing.items()):
        print(f"missing parameter {n}  (used by {len(p)} file(s), e.g. {p[0]})")
    sys.exit(2)

if a.dry_run:
    print(f"dry run: {len(rows)} files, parameters in use: {sorted(need)}")
    sys.exit(0)

# the SSH policy of the node is key-only: on a server whose root has no authorized key it would lock the owner out. Those files wait until a key is there.
HAS_KEY = os.path.isfile("/root/.ssh/authorized_keys") and os.path.getsize("/root/.ssh/authorized_keys") > 0
rows_all, rows = rows, []
for r in rows_all:
    if "/ssh/sshd_config.d/" in r[0] and not HAS_KEY and a.root == "/":
        print(f"skipped {r[0]}: /root/.ssh/authorized_keys is empty (key-only SSH would lock you out); install a key and run golden/install.sh --stages overlay again")
        continue
    rows.append(r)

os.makedirs(a.backup, exist_ok=True)
new_files = []
by_params = []   # files whose content differs from the captured node only because the parameters (IP, domain, name...) are this node's own
done = 0
for path, mode, owner, sha, kind in rows:
    src = os.path.join(HERE, "overlay", stored(path))
    dst = os.path.join(a.root, path.lstrip("/"))
    data = open(src, "rb").read()
    if kind == "text":
        text = data.decode("utf-8")
        data = PH.sub(lambda m: params[m.group(1)], text).encode("utf-8")
        if hashlib.sha256(data).hexdigest() != sha:
            by_params.append(path)
    if os.path.exists(dst):
        b = os.path.join(a.backup, path.lstrip("/"))
        os.makedirs(os.path.dirname(b), exist_ok=True)
        if not os.path.exists(b):
            shutil.copy2(dst, b)
    else:
        new_files.append(path)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".golden-tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.chmod(tmp, int(mode, 8))
    u, g = owner.split(":")
    try:
        os.chown(tmp, pwd.getpwnam(u).pw_uid, grp.getgrnam(g).gr_gid)
    except KeyError:
        print(f"note: user/group {owner} does not exist yet for {path}: left as root")
    os.replace(tmp, dst)
    done += 1
if by_params:
    print(f"note: {len(by_params)} file(s) carry this node's own parameters, not the captured ones (expected for a new node): " + ", ".join(os.path.basename(p) for p in by_params))
open(os.path.join(a.backup, "NEW_FILES"), "w").write("\n".join(new_files) + ("\n" if new_files else ""))
print(f"installed {done} files ({len(new_files)} new); backup of the replaced ones: {a.backup}")
