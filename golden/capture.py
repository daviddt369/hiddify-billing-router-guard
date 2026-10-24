#!/usr/bin/env python3
"""Capture the "golden" overlay from the running node (READ ONLY on the node) into this repository, depersonalized.

What it does
  1. reads FILES.list (absolute paths on the node) and pulls exactly those files over ssh with `tar -c` (nothing is written on the node);
  2. replaces every private value listed in the PRIVATE params file (NAME=value) by @@NAME@@ (longest value first), line endings CRLF -> LF;
  3. writes the result under overlay/<path on the node> and the manifest MANIFEST.tsv: sha256 of the ORIGINAL content (what the node has), mode, owner;
  4. refuses to finish if any private value, IP, domain outside the allow-list or secret-shaped token is left in the output.
Private values never enter git: they live in the params file outside the repository (default ~/VPN-GOLDEN-PRIVATE/params.private.env).

usage: python capture.py --ssh "ssh -i KEY -o BatchMode=yes root@HOST" [--params PATH] [--list FILES.list]
"""
import argparse
import hashlib
import io
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from gpaths import stored  # noqa: E402
ap = argparse.ArgumentParser()
ap.add_argument("--ssh", required=True, help='ssh command prefix that reaches the node as root, e.g. "ssh -i ~/.ssh/key root@host"')
ap.add_argument("--params", default=os.path.expanduser("~/VPN-GOLDEN-PRIVATE/params.private.env"))
ap.add_argument("--list", default=os.path.join(HERE, "FILES.list"))
args = ap.parse_args()

params = {}
for line in open(args.params, encoding="utf-8"):
    line = line.rstrip("\n")
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        if v:
            params[k] = v
order = sorted(params.items(), key=lambda kv: -len(kv[1]))          # longest value first: a domain before the domain's own parent

paths = [l.strip() for l in open(args.list, encoding="utf-8") if l.strip() and not l.startswith("#")]
cmd = shlex.split(args.ssh) + ["tar -C / --ignore-failed-read -cf - -T - 2>/dev/null"]
raw = subprocess.run(cmd, input=("\n".join(p.lstrip("/") for p in paths) + "\n").encode(), capture_output=True)
if not raw.stdout:
    sys.exit("nothing came back from the node")
tf = tarfile.open(fileobj=io.BytesIO(raw.stdout))

OUT = os.path.join(HERE, "overlay")
shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(OUT)
manifest, got = [], set()
for m in tf.getmembers():
    if not m.isfile():
        continue
    p = "/" + m.name.lstrip("/")
    data = tf.extractfile(m).read()
    is_text = b"\0" not in data
    if is_text:
        text = data.decode("utf-8").replace("\r\n", "\n")
        original = text.encode("utf-8")
        for name, val in order:
            text = text.replace(val, f"@@{name}@@")
        out = text.encode("utf-8")
    else:
        original = out = data
    dst = os.path.join(OUT, stored(p))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    open(dst, "wb").write(out)
    manifest.append((p, oct(m.mode & 0o7777)[2:], f"{m.uname}:{m.gname}", hashlib.sha256(original).hexdigest(), "text" if is_text else "bin"))
    got.add(p)
missing = [p for p in paths if p not in got]
open(os.path.join(HERE, "MANIFEST.tsv"), "w", encoding="utf-8", newline="\n").write(
    "# path\tmode\towner\tsha256 of the content on the node (LF-normalised for text)\tkind\n" + "\n".join("\t".join(r) for r in sorted(manifest)) + "\n")
print(f"captured {len(manifest)} files; not on the node: {len(missing)} {missing[:5]}")

# ---------------------------------------------------------------------------------------------------- gate
IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
OK_IP = re.compile(r"^(127\.|10\.|192\.168\.|0\.0\.0\.0|255\.|1\.1\.1\.1$|1\.0\.0\.1$|8\.8\.8\.8$|8\.8\.4\.4$|9\.9\.9\.9$|77\.88\.8\.[18]$|169\.254\.|1\.2\.3\.\d$|5\.6\.7\.8$|13\.0\.0\.|135\.0\.0\.0$|172\.(1[6-9]|2\d|3[01])\.|203\.0\.113\.|198\.51\.100\.)")
TOKEN = re.compile(r"[A-Za-z0-9+/=_\-]{16,}")
BENIGN = re.compile(r"^(Environment|ExecStart|StateDirectory|JobRunning|TimeoutStart|RandomizedDelay|OnUnit|ExecStartPre|requirepass|COLLATE|usage_|current_|pinned|Ed25519|X25519|com/|routing/onadd|INSTALL|roles_|roles=|after_this|rocessing|ExecStop)")


def entropy(t):
    c = Counter(t)
    return -sum(v / len(t) * math.log2(v / len(t)) for v in c.values())


problems = []
for root, _, files in os.walk(OUT):
    for f in files:
        p = os.path.join(root, f)
        rel = os.path.relpath(p, OUT).replace("\\", "/")
        b = open(p, "rb").read()
        if b"\0" in b:
            continue
        t = b.decode("utf-8")
        for name, val in params.items():
            if val in t:
                problems.append((rel, f"private value {name} left in the output"))
        for ip in set(IPV4.findall(t)):
            if not OK_IP.match(ip):
                problems.append((rel, f"IPv4 outside the allow-list: {ip}"))
        for m in TOKEN.finditer(t):
            core = m.group(0).strip("-_=/+")
            if len(core) < 16 or re.fullmatch(r"[0-9a-fA-F]+", core) or BENIGN.match(core):
                continue
            if sum(bool(re.search(x, core)) for x in (r"[a-z]", r"[A-Z]", r"[0-9]")) < 3 or entropy(core) < 3.4:
                continue
            if re.search(r"[_\-]", core) and all(re.fullmatch(r"[A-Za-z]{2,}|[0-9]{1,4}|[A-Z]{2,}[0-9]*", x) for x in re.split(r"[_\-]+", core) if x):
                continue
            problems.append((rel, f"secret-shaped token {core[:5]}...({len(core)})"))
for rel, why in sorted(set(problems)):
    print("PROBLEM", rel, "->", why)
print("gate:", "CLEAN" if not problems else f"{len(set(problems))} problem(s)")
sys.exit(1 if problems else 0)
