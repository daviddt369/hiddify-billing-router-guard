#!/usr/bin/env python3
"""Regenerate work/src/diffs/*.diff (unified diff base -> payload, LF) and diffs/INDEX.md from MANIFEST.json.
base/ holds the byte-identical copies of the production files; NEW files are shown as a full addition.
usage: python3 make_diffs.py"""
import difflib
import json
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "diffs")
manifest = json.load(open(os.path.join(HERE, "MANIFEST.json")))["files"]

shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(OUT)
rows = []
for e in manifest:
    rel = e["path"]
    new = open(os.path.join(HERE, "payload", rel), "rb").read().decode("utf-8").splitlines(keepends=True)
    if e["base"] == "NEW":
        old, label_a = [], "/dev/null"
    else:
        old, label_a = open(os.path.join(HERE, "base", rel), "rb").read().decode("utf-8").splitlines(keepends=True), "a/" + rel
    d = list(difflib.unified_diff(old, new, fromfile=label_a, tofile="b/" + rel, n=3))
    add = sum(1 for x in d if x.startswith("+") and not x.startswith("+++"))
    rem = sum(1 for x in d if x.startswith("-") and not x.startswith("---"))
    with open(os.path.join(OUT, rel.replace("/", "__") + ".diff"), "wb") as f:
        f.write("".join(d).encode("utf-8"))
    rows.append((rel, "new" if e["base"] == "NEW" else "modified", add, rem))

with open(os.path.join(OUT, "INDEX.md"), "wb") as f:
    lines = ["# Diffs (production file -> payload)", "", "| file | kind | + | - |", "|---|---|---:|---:|"]
    for rel, kind, add, rem in rows:
        lines.append(f"| `{rel}` | {kind} | {add} | {rem} |")
    mod = [r for r in rows if r[1] == "modified"]
    lines += ["", f"Modified existing files: {len(mod)} (+{sum(r[2] for r in mod)} / -{sum(r[3] for r in mod)} lines); new files: {len(rows) - len(mod)} "
                  f"(+{sum(r[2] for r in rows if r[1] == 'new')} lines)."]
    f.write(("\n".join(lines) + "\n").encode("utf-8"))
print(f"{len(rows)} diffs written to {OUT}")
for r in rows:
    print(f"  {r[1]:8} +{r[2]:<5} -{r[3]:<4} {r[0].split('site-packages/')[-1]}")
