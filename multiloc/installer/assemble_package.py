#!/usr/bin/env python3
"""Assembles the install package of the multi-location addon from THIS server's own files (read only) and the new files of this repository.

    python3 assemble_package.py --out /root/multiloc-pkg [--stock-root /] [--new-files <repo>/multiloc/new-files] [--tar /root/multiloc-pkg.tgz]

  1. the files that the addon modifies (the keys of build_payload.PATCHES) are read from --stock-root (default "/": the server itself; nothing there is written)
  2. build_payload.py applies the exact, asserted replacements. A replacement that finds no anchor stops the assembly: the server is not the baseline this code
     was written for (the message names the file and the anchor); nothing has been installed at that point
  3. the new files of the addon (new-files/, destinations in new-files/DESTINATIONS.md) are added to the payload
  4. MANIFEST.json: for every file the sha256 it must have on the server BEFORE the install (the installer refuses to touch anything else) and the sha256 it gets
  5. <out>/install.sh  preflight | apply | verify | status | rollback    (multiloc_installer.py: every apply is backed up and reversible)

--out must not exist or must be empty.  --tar additionally writes the deliverable part (installer, delta tool, manifest, payload) as one .tgz that can be copied to another server of the same baseline.
"""
import argparse
import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = ("multiloc_installer.py", "multiloc_delta_deploy.py", "build_payload.py", "build_manifest.py", "make_diffs.py", "install.sh", "formfix_pairs.py", "routercore_pairs.py")
DELIVERED = ("multiloc_installer.py", "multiloc_delta_deploy.py", "MANIFEST.json", "BASE_SHA256.txt", "install.sh", "payload")


def die(msg):
    print("ERROR: " + msg, file=sys.stderr)
    sys.exit(2)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def destinations(table):
    """new-files/DESTINATIONS.md: | `file in new-files/` | `path on the server` |"""
    rows = re.findall(r"^\|\s*`([^`]+)`\s*\|\s*`(/[^`]+)`\s*\|\s*$", table.read_text(encoding="utf-8"), re.M)
    if not rows:
        die(f"no destinations found in {table}")
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default="multiloc-pkg", help="package directory (must not exist or be empty)")
    ap.add_argument("--stock-root", default="/", help="where the server's own files are read from (default: / = this server)")
    ap.add_argument("--new-files", default=str(HERE.parent / "new-files"), help="the repository's new-files/ directory")
    ap.add_argument("--tar", default="", help="also write the deliverable part as this .tgz")
    args = ap.parse_args()

    out, stock, new_files = Path(args.out).resolve(), Path(args.stock_root), Path(args.new_files)
    if out.exists() and any(out.iterdir()):
        die(f"{out} exists and is not empty")
    for name in TOOLS:
        if not (HERE / name).is_file():
            die(f"{name} is missing next to this script")
    rows = destinations(new_files / "DESTINATIONS.md")
    missing_new = [src for src, _ in rows if not (new_files / src).is_file()]
    if missing_new:
        die("files missing in new-files/: " + ", ".join(missing_new))

    out.mkdir(parents=True, exist_ok=True)
    for name in TOOLS:
        shutil.copy2(HERE / name, out / name)

    # 1. which files does the addon modify? (asks build_payload itself; nothing is built yet)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(out))
    spec = importlib.util.spec_from_file_location("build_payload", out / "build_payload.py")
    bp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bp)
    rels = sorted(bp.PATCHES)
    absent = [r for r in rels if not (stock / r).is_file()]
    if absent:
        die("the server lacks files the addon modifies (is the business / routing installer applied?):\n  " + "\n  ".join("/" + r for r in absent))
    for rel in rels:
        dst = out / "base" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes((stock / rel).read_bytes())
    print(f"read {len(rels)} files of the server (read only)")

    # 2. the exact replacements
    r = subprocess.run([sys.executable, "build_payload.py"], cwd=out, capture_output=True, text=True, encoding="utf-8", env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1"))
    if r.returncode:
        print(r.stdout + r.stderr, file=sys.stderr)
        die("a replacement did not find its anchor: this server is not the baseline the code was written for (nothing was installed). See the message above.")
    notes = [l for l in r.stdout.splitlines() if l.startswith("   (")]
    print(f"modified files built: {len(rels)}" + (f"  ({len(notes)} note(s))" if notes else ""))
    for l in notes:
        print(l)

    # 3. new files
    payload = out / "payload"
    for src, dest in rows:
        target = payload / dest.lstrip("/")
        if target.exists():
            die(f"{dest} would be both modified and new")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((new_files / src).read_bytes())
    print(f"new files added: {len(rows)}")

    # 4. checks, manifest, base hashes
    bad = []
    for p in payload.rglob("*.py"):
        try:
            compile(p.read_text(encoding="utf-8"), str(p), "exec")
        except SyntaxError as exc:
            bad.append(f"{p.relative_to(payload)}: {exc}")
    if bad:
        die("python files that do not compile:\n  " + "\n  ".join(bad))
    r = subprocess.run([sys.executable, "build_manifest.py"], cwd=out, capture_output=True, text=True, encoding="utf-8", env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    if r.returncode:
        die("MANIFEST.json could not be built:\n" + r.stdout + r.stderr)
    print(r.stdout.strip())
    (out / "BASE_SHA256.txt").write_text("".join(f"{sha(out / 'base' / rel)}  /{rel}\n" for rel in rels), encoding="utf-8", newline="\n")
    for d in out.rglob("__pycache__"):
        shutil.rmtree(d, ignore_errors=True)
    (out / "install.sh").chmod(0o755)

    # 5. optionally one file to carry to another server
    if args.tar:
        def norm(ti):
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = "root"
            ti.mode = 0o755 if (ti.isdir() or ti.name.endswith(".sh")) else 0o644
            return ti
        with tarfile.open(args.tar, "w:gz") as tar:
            for name in DELIVERED:
                tar.add(out / name, arcname=name, filter=norm)
        print(f"{args.tar}: {sha(args.tar)}")

    print(f"""
package: {out}
  {out}/install.sh preflight    read-only checks and the plan, changes nothing
  {out}/install.sh apply        install (backup first, additive DB migration, restarts the panel; the router layer runs on the first Apply in the panel)
  {out}/install.sh verify       post-conditions
  {out}/install.sh rollback     undo""")


if __name__ == "__main__":
    main()
