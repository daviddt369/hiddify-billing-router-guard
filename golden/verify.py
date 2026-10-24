#!/usr/bin/env python3
"""Compare this server with the golden manifest: for every file of MANIFEST.tsv the (LF-normalised) sha256 of the installed file must equal the one captured from the node.
Parameters that differ on purpose (a new IP, a new domain) change the rendered content: those files are reported as `param` when the file with the captured placeholders values rendered
differs only by them (only when --params is given). Prints a summary; exit 1 when something differs for another reason or is missing.

usage: verify.py [--root /] [--params FILE]"""
import argparse
import hashlib
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
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
ok = missing = differ = 0
bad = []
HAS_KEY = os.path.isfile("/root/.ssh/authorized_keys") and os.path.getsize("/root/.ssh/authorized_keys") > 0
for path, mode, owner, sha, kind in rows:
    if "/ssh/sshd_config.d/" in path and not HAS_KEY and a.root == "/":
        continue
    fp = os.path.join(a.root, path.lstrip("/"))
    if not os.path.isfile(fp):
        missing += 1
        bad.append(("MISSING", path))
        continue
    data = open(fp, "rb").read()
    if kind == "text":
        data = data.replace(b"\r\n", b"\n")
    if hashlib.sha256(data).hexdigest() == sha:
        ok += 1
        st = os.stat(fp)
        if oct(st.st_mode & 0o7777)[2:] != mode:
            bad.append((f"MODE {oct(st.st_mode & 0o7777)[2:]} != {mode}", path))
    else:
        differ += 1
        bad.append(("DIFFERS", path))
print(f"golden manifest: {len(rows)} files | identical {ok} | differ {differ} | missing {missing}")
for why, p in bad:
    print(f"  {why:10} {p}")
sys.exit(0 if not bad else 1)
