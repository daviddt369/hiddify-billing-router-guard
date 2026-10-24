#!/usr/bin/env python3
"""Golden subscription capture / compare for the stand (real panel, real HTTP).

  golden.py capture <outdir> [--label NAME]      fetch every (user x format x user-agent) and store raw bodies + metadata
  golden.py compare <dirA> <dirB> [--show N]     semantic diff; exit 0 only if identical after normalisation
  golden.py volatility <dirA> <dirB>             which fields differ between two captures of the SAME state (to tune normalisation)

Normalisation removes only things that legitimately change between two requests (clock text in the usage pseudo-line,
random reality short-ids / random-case hosts, Date header). Everything else must match byte for byte.
"""
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:9000"
HOST = "ru.stand.test"
PATH_CLIENT = "stCli0Path1234567890abcdef"
USERS = {
    "u-active": "11111111-1111-4111-8111-111111111111",
    "u-tester": "22222222-2222-4222-8222-222222222222",
    "u-expired": "33333333-3333-4333-8333-333333333333",
    "u-overlimit": "44444444-4444-4444-8444-444444444444",
    "u-disabled": "55555555-5555-4555-8555-555555555555",
}
FORMATS = ["", "sub/", "sub64/", "singbox/", "clash/", "clashmeta/", "xray/", "auto/"]
UAS = {
    "hiddify": "Hiddify/2.0.5 (linux)",
    "happ": "Happ/3.5.0",
    "v2rayng": "v2rayNG/1.8.5",
    "clashmeta": "ClashMeta/1.18.0 mihomo",
    "singbox": "sing-box 1.9.0",
    "browser": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0 Safari/537.36",
}
KEEP_HEADERS = ["content-type", "content-disposition", "profile-update-interval", "subscription-userinfo", "profile-title",
                "profile-web-page-url", "support-url", "announce", "routing", "content-length-class"]


def fname(user, fmt, ua):
    f = fmt.strip("/") or "default"
    return f"{user}__{f}__{ua}"


def fetch(uuid, fmt, ua):
    url = f"{BASE}/{PATH_CLIENT}/{uuid}/{fmt}"
    req = urllib.request.Request(url, headers={"Host": HOST, "User-Agent": ua})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read()
            hdrs = {k.lower(): v for k, v in r.getheaders()}
            return r.status, hdrs, body
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()


def capture(outdir, only_users=None):
    os.makedirs(outdir, exist_ok=True)
    meta = {}
    for u, uuid in USERS.items():
        if only_users and u not in only_users:
            continue
        for fmt in FORMATS:
            for uan, ua in UAS.items():
                st, hd, body = fetch(uuid, fmt, ua)
                key = fname(u, fmt, uan)
                with open(os.path.join(outdir, key + ".body"), "wb") as f:
                    f.write(body)
                meta[key] = {"status": st, "headers": {k: hd[k] for k in KEEP_HEADERS if k in hd}, "size": len(body),
                             "sha": hashlib.sha256(body).hexdigest()}
    with open(os.path.join(outdir, "_meta.json"), "w") as f:
        json.dump(meta, f, indent=1, sort_keys=True)
    print(f"captured {len(meta)} responses into {outdir}")


# ------------------------------------------------------------------ normalisation
_TIME_LINK = re.compile(r"trojan://1@[0-9.:\-]*time:\d{4}\?")
_FAKEIP = re.compile(r"\d\d\.\d\d--\d{4}\.\d\d\.\d\d\.time:\d{4}")
_SID = re.compile(r"(sid=|\"short_id\": \"|short-id: )[0-9a-f]{0,16}")


_RND1 = re.compile(r"(&|proxies|--)\d{1,7}(?=[\"&.]|$)")      # the panel appends a random number WITHOUT zero padding: 1..6 digits


_AUTOIP = re.compile(r"(#Hiddify auto ip:.*default:)\w+")


def norm_text(t: str) -> str:
    t = _AUTOIP.sub(r"<R>", t)
    t = _FAKEIP.sub("<TIME>", t)
    t = _SID.sub(r"\1<SID>", t)
    t = _RND1.sub(r"\1<RND>", t)
    return t


def _maybe_b64(t: str) -> str:
    """default/sub64/auto bodies are base64 of the link list (UA dependent): decode when the text looks like base64"""
    import base64
    s = t.strip()
    if s and "://" not in s[:30] and re.fullmatch(r"[A-Za-z0-9+/=\r\n]+", s):
        try:
            return base64.b64decode(s + "=" * (-len(s) % 4)).decode("utf-8", "replace")
        except Exception:
            return t
    return t


def lines_of(body: bytes, fmt: str):
    t = _maybe_b64(body.decode("utf-8", "replace"))
    return [norm_text(x) for x in t.splitlines()]


def compare(a, b, show=15, allow_added=False, users=None, formats=None, quiet=False, skip_uas=None, skip_formats=None):
    ma = json.load(open(os.path.join(a, "_meta.json")))
    mb = json.load(open(os.path.join(b, "_meta.json")))
    keys = sorted(set(ma) | set(mb))
    bad = 0
    added_total = 0
    for k in keys:
        if users and k.split("__")[0] not in users:
            continue
        if formats and k.split("__")[1] not in formats:
            continue
        if skip_uas and k.split("__")[2] in skip_uas:
            continue
        if skip_formats and k.split("__")[1] in skip_formats:
            continue
        if k not in ma or k not in mb:
            print("MISSING in one capture:", k)
            bad += 1
            continue
        if ma[k]["status"] != mb[k]["status"]:
            print(f"STATUS differs {k}: {ma[k]['status']} vs {mb[k]['status']}")
            bad += 1
            continue
        ha, hb = ma[k]["headers"], mb[k]["headers"]
        ha = {x: y for x, y in ha.items() if x not in ("subscription-userinfo",)}
        hb = {x: y for x, y in hb.items() if x not in ("subscription-userinfo",)}
        if ha != hb:
            print(f"HEADERS differ {k}: {ha} vs {hb}")
            bad += 1
        ba = open(os.path.join(a, k + ".body"), "rb").read()
        bb = open(os.path.join(b, k + ".body"), "rb").read()
        fmt = k.split("__")[1]
        la, lb = lines_of(ba, fmt), lines_of(bb, fmt)
        if la == lb:
            continue
        sa, sb = set(la), set(lb)
        removed = [x for x in la if x not in sb]
        added = [x for x in lb if x not in sa]
        if allow_added and not removed and la == [x for x in lb if x in sa]:
            added_total += len(added)
            continue
        bad += 1
        print(f"DIFF {k}: -{len(removed)} +{len(added)} lines (order changed={not removed and not added})")
        for x in removed[:show]:
            print("   -", x[:170])
        for x in added[:show]:
            print("   +", x[:170])
    print(f"compared {len(keys)} responses: {'IDENTICAL' if bad == 0 else str(bad) + ' DIFFER'}"
          + (f" (+{added_total} added lines tolerated)" if allow_added else ""))
    return bad


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "capture":
        capture(sys.argv[2])
    elif cmd in ("compare", "volatility"):
        sys.exit(1 if compare(sys.argv[2], sys.argv[3], allow_added="--allow-added" in sys.argv) else 0)
