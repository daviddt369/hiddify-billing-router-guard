#!/usr/bin/env python3
"""Byte-level snapshot of everything the feature may touch, to prove rollback restores the exact pre-install state.

  tree_snapshot.py take <out.json>      sha256 of files + DB schema/rowcount fingerprint + service states
  tree_snapshot.py diff <a.json> <b.json>

Volatile runtime files (logs, caches, pyc, backups) are excluded; rendered configs ARE included on purpose.
"""
import hashlib
import json
import os
import subprocess
import sys

H = "/opt/hiddify-manager"
PKG = H + "/.venv313/lib/python3.13/site-packages/hiddifypanel"
ROOTS = [PKG, H + "/xray/configs", H + "/haproxy", H + "/nginx", H + "/common", H + "/scripts", H + "/singbox/configs",
         H + "/hiddify-panel/var", "/etc/xray-router", "/etc/systemd/system"]
EXCL_DIRS = {"__pycache__", ".claude-backups", "backup", "iplists", "log", "run"}
EXCL_SUFFIX = (".pyc", ".log", ".multiloc-new", ".tmp", ".tmp.json")
EXCL_NAMES = {"config.json.bak", "xray.access.state.json"}


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def files():
    out = {}
    for root in ROOTS:
        for dp, dns, fns in os.walk(root, followlinks=False):
            dns[:] = [d for d in dns if d not in EXCL_DIRS]
            for fn in fns:
                if fn.endswith(EXCL_SUFFIX) or fn in EXCL_NAMES or ".bak" in fn or "multiloc-installer-backups" in dp:
                    continue
                p = os.path.join(dp, fn)
                if os.path.islink(p):
                    out[p] = "link:" + os.readlink(p)
                    continue
                try:
                    st = os.stat(p)
                    out[p] = f"{sha(p)} {oct(st.st_mode & 0o7777)} {st.st_uid}:{st.st_gid}"
                except OSError:
                    pass
    return out


def db():
    def q(sql):
        return subprocess.run(["mysql", "-N", "-B", "hiddifypanel", "-e", sql], capture_output=True, text=True).stdout
    tables = [t for t in q("show tables").split() if t]
    schema = {}
    for t in tables:
        ddl = q(f"show create table `{t}`")
        schema[t] = hashlib.sha256(ddl.encode()).hexdigest()[:16]
    rows = {}
    for t in ("user", "domain", "proxy", "str_config", "bool_config", "admin_user", "commercial_routing_upstream",
              "commercial_routing_custom_rule", "commercial_routing_rule_source"):
        if t in tables:
            rows[t] = int(q(f"select count(*) from `{t}`").strip() or 0)
    return {"schema": schema, "rows": rows}


def take(out):
    snap = {"files": files(), "db": db()}
    json.dump(snap, open(out, "w"), indent=1, sort_keys=True)
    print(f"snapshot: {len(snap['files'])} files, {len(snap['db']['schema'])} tables -> {out}")


def diff(a, b):
    A, B = json.load(open(a)), json.load(open(b))
    bad = 0
    for k in sorted(set(A["files"]) | set(B["files"])):
        if A["files"].get(k) != B["files"].get(k):
            print("FILE", "only in A" if k not in B["files"] else "only in B" if k not in A["files"] else "differs", k)
            bad += 1
    for t in sorted(set(A["db"]["schema"]) | set(B["db"]["schema"])):
        if A["db"]["schema"].get(t) != B["db"]["schema"].get(t):
            print("TABLE", "only in A" if t not in B["db"]["schema"] else "only in B" if t not in A["db"]["schema"] else "differs", t)
            bad += 1
    for t in sorted(set(A["db"]["rows"]) | set(B["db"]["rows"])):
        if A["db"]["rows"].get(t) != B["db"]["rows"].get(t):
            print("ROWS differ", t, A["db"]["rows"].get(t), "->", B["db"]["rows"].get(t))
            bad += 1
    print("IDENTICAL" if bad == 0 else f"{bad} DIFFERENCES")
    return bad


if __name__ == "__main__":
    if sys.argv[1] == "take":
        take(sys.argv[2])
    else:
        sys.exit(1 if diff(sys.argv[2], sys.argv[3]) else 0)
