#!/usr/bin/env python3
"""Compare this server with the golden manifest: for every file of MANIFEST.tsv the (LF-normalised) sha256 of the installed file must equal the one captured from the node.
Parameters that differ on purpose (a new IP, a new domain) change the rendered content: with --params the expected content of a file that has @@NAME@@ placeholders is the overlay file
rendered with THOSE values (so a node with its own parameters verifies too). Prints a summary; exit 1 when something differs or is missing.

usage: verify.py [--root /] [--params FILE]"""
import argparse
import hashlib
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from gpaths import stored  # noqa: E402
ap = argparse.ArgumentParser()
ap.add_argument("--root", default="/")
ap.add_argument("--params", default="")
a = ap.parse_args()
PH = re.compile(r"@@([A-Z0-9_]+)@@")
params = {}
if a.params:
    for line in open(a.params, encoding="utf-8"):
        line = line.rstrip("\n")
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            params[k] = v

rows = [l.rstrip("\n").split("\t") for l in open(os.path.join(HERE, "MANIFEST.tsv"), encoding="utf-8") if l.strip() and not l.startswith("#")]
ok = missing = differ = skipped = 0
bad = []
HAS_KEY = os.path.isfile("/root/.ssh/authorized_keys") and os.path.getsize("/root/.ssh/authorized_keys") > 0
for path, mode, owner, sha, kind in rows:
    if "/ssh/sshd_config.d/" in path and not HAS_KEY and a.root == "/":
        skipped += 1
        continue
    fp = os.path.join(a.root, path.lstrip("/"))
    if not os.path.isfile(fp):
        missing += 1
        bad.append(("MISSING", path))
        continue
    data = open(fp, "rb").read()
    if kind == "text":
        data = data.replace(b"\r\n", b"\n")
        if params:
            src = open(os.path.join(HERE, "overlay", stored(path)), encoding="utf-8").read()
            if PH.search(src) and all(params.get(n) for n in PH.findall(src)):
                sha = hashlib.sha256(PH.sub(lambda m: params[m.group(1)], src).encode("utf-8")).hexdigest()
    if hashlib.sha256(data).hexdigest() == sha:
        ok += 1
        st = os.stat(fp)
        if oct(st.st_mode & 0o7777)[2:] != mode:
            bad.append((f"MODE {oct(st.st_mode & 0o7777)[2:]} != {mode}", path))
    else:
        differ += 1
        bad.append(("DIFFERS", path))
print(f"golden manifest: {len(rows)} files | identical {ok} | differ {differ} | missing {missing}" + (f" | {skipped} SSH-policy file(s) wait for a key in /root/.ssh/authorized_keys" if skipped else ""))
for why, p in bad:
    print(f"  {why:10} {p}")
sys.exit(0 if not bad else 1)
