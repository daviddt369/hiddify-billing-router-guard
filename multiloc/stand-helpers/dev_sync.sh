#!/bin/bash
# DEV shortcut on the stand: copy payload files whose content changed since the last install straight over the live files (no rollback/apply, no restarts),
# so the live tree equals the CURRENT manifest. Only for files that already exist on the stand. Prints what it did; restart services yourself if needed.
set -uo pipefail
cd /work/src
python3 build_payload.py >/dev/null && python3 build_manifest.py
python3 - <<'PYEOF'
import hashlib, json, os, shutil, time
m = json.load(open("/work/src/MANIFEST.json"))["files"]
bk = "/work/out/devsync-backups/" + time.strftime("%Y%m%d-%H%M%S")
n = 0
for e in m:
    live = "/" + e["path"]
    src = "/work/src/payload/" + e["path"]
    h = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
    if os.path.exists(live) and h(live) == e["new"]:
        continue
    if not os.path.exists(live):
        print("skip (absent on the stand, use the installer):", e["path"])
        continue
    os.makedirs(os.path.dirname(os.path.join(bk, e["path"])), exist_ok=True)
    shutil.copy2(live, os.path.join(bk, e["path"]))
    with open(src, "rb") as f, open(live, "r+b") as g:
        g.seek(0); g.truncate(); g.write(f.read())
    print("synced:", e["path"])
    n += 1
print(f"{n} file(s) synced; backups in {bk}" if n else "nothing to sync")
PYEOF
python3 /work/src/multiloc_installer.py status | awk '{print $1}' | sort | uniq -c
