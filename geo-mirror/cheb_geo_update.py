#!/usr/bin/env python3
"""Mirror of the routing geo files (geoip.dat + geosite.dat) of a GitHub release, with a check before anything is published.

Why a mirror: the client app (Happ / INCY) downloads the two .dat files named in the routing profile. They come from our own host (reachable wherever the
subscription itself is) instead of github.com, and a bad release never reaches the phones: the xray inside the app refuses to start when a tag the profile uses
is missing from the file, and the user is left without a working route.

One run (the timer does it a few times a day):
  1. asks GitHub for the latest release tag; nothing to do when it is the one already published
  2. downloads geoip.dat and geosite-compact.dat of THAT tag (published as geosite.dat), checks them against the sha256 that GitHub records for the assets
  3. checks the files: size, every tag the profile uses exists, the big sets did not shrink to less than half of the published ones,
     and the REAL xray loads a config with every rule of the profile (`xray run -test`)
  4. only then replaces the files being served (atomic, one by one) and keeps a copy in last-good/
A rejected or unreachable release changes nothing: the old files keep being served. The super admin gets one Telegram message per day about it
(read from the panel: bot token and admin_user.telegram_id; the token is never printed).

usage: cheb_geo_update.py --repo owner/name --profile profile.json [--force] [--status]
paths and the xray binary have defaults for the node; tests point CGU_API_BASE / CGU_DL_BASE / TG_API_BASE at a local mock."""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

API = os.environ.get("CGU_API_BASE", "https://api.github.com")
DL = os.environ.get("CGU_DL_BASE", "https://github.com")
TG = os.environ.get("TG_API_BASE", "https://api.telegram.org")
SERVE = {"geoip.dat": "geoip.dat", "geosite.dat": "geosite-compact.dat"}     # name served -> asset name in the release
FLOORS = {"RU-DOMAINS": 500, "RU-IPS": 5000, "PROXY-CURATED-DOMAINS": 20000, "PROXY-IPS": 5000}   # a set that is used must have at least that many entries
SIZE = {"geoip.dat": (300_000, 20_000_000), "geosite.dat": (800_000, 20_000_000)}
PROFILE_KEYS = ("DirectSites", "DirectIp", "ProxySites", "ProxyIp", "BlockSites", "BlockIp")


def log(msg: str) -> None:
    print(f"[cheb-geo {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def get(url: str, limit: int = 30_000_000) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "cheb-geo-mirror", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError("answer is larger than the limit")
    return data


# ---------------------------------------------------------------------------------------------------- the .dat files (protobuf, no dependencies)
def _varint(b: bytes, i: int) -> tuple[int, int]:
    r = s = 0
    while True:
        x = b[i]
        i += 1
        r |= (x & 0x7F) << s
        if not x & 0x80:
            return r, i
        s += 7


def _fields(b: bytes):
    i = 0
    while i < len(b):
        k, i = _varint(b, i)
        f, w = k >> 3, k & 7
        if w == 0:
            v, i = _varint(b, i)
        elif w == 2:
            n, i = _varint(b, i)
            v = b[i:i + n]
            i += n
        elif w == 1:
            v = b[i:i + 8]
            i += 8
        elif w == 5:
            v = b[i:i + 4]
            i += 4
        else:
            raise ValueError(f"wire type {w}")
        yield f, w, v


def dat_tags(data: bytes) -> dict[str, int]:
    """tag (upper case, as in the file) -> number of entries"""
    out: dict[str, int] = {}
    for f, w, v in _fields(data):
        if f != 1 or w != 2:
            continue
        code, n = "", 0
        for f2, w2, v2 in _fields(v):
            if f2 == 1 and w2 == 2:
                code = v2.decode("utf-8", "replace").upper()
            elif f2 == 2 and w2 == 2:
                n += 1
        if code:
            out[code] = n
    return out


# ---------------------------------------------------------------------------------------------------- the profile
def profile_items(path: str) -> tuple[list[str], list[str], dict[str, set[str]]]:
    """(domain items, ip items, {"geosite": tags used, "geoip": tags used}) of the routing profile that will be served"""
    prof = json.load(open(path, encoding="utf-8"))
    sites = [x for k in PROFILE_KEYS if k.endswith("Sites") for x in prof.get(k, [])]
    ips = [x for k in PROFILE_KEYS if k.endswith("Ip") for x in prof.get(k, [])]
    used = {"geosite": set(), "geoip": set()}
    for item in sites + ips:
        for kind in used:
            if item.startswith(kind + ":"):
                used[kind].add(item.split(":", 1)[1].upper())
    return sites, ips, used


def xray_loads(xray: str, asset_dir: str, sites: list[str], ips: list[str]) -> tuple[bool, str]:
    rules = []
    if sites:
        rules.append({"type": "field", "domain": sites, "outboundTag": "x"})
    if ips:
        rules.append({"type": "field", "ip": ips, "outboundTag": "x"})
    cfg = {"log": {"loglevel": "none"}, "outbounds": [{"tag": "x", "protocol": "blackhole"}], "routing": {"rules": rules}}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(cfg, f)
        path = f.name
    try:
        r = subprocess.run([xray, "run", "-test", "-c", path], capture_output=True, text=True, timeout=120, env=dict(os.environ, XRAY_LOCATION_ASSET=asset_dir))
        return r.returncode == 0, (r.stdout + r.stderr).strip().splitlines()[-1][:300] if (r.stdout + r.stderr).strip() else ""
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------------------------------- telegram (the admin gets one message a day)
def _sql(q: str) -> list[str]:
    r = subprocess.run(["mysql", "--default-character-set=utf8mb4", "--raw", "-N", "-B", "hiddifypanel", "-e", q], capture_output=True, text=True, check=True)
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


def tell_admin(text: str) -> bool:
    try:
        token = _sql("select value from str_config where `key`='telegram_bot_token'")[0]
        chat = int(_sql("select telegram_id from admin_user where mode='super_admin' and telegram_id is not null and telegram_id<>'' order by id limit 1")[0])
    except Exception:  # noqa: BLE001
        return False
    delays = [float(x) for x in os.environ.get("CGU_RETRY_SLEEP", "10,30,90").split(",") if x.strip()]
    req = urllib.request.Request(f"{TG}/bot{token}/sendMessage", data=json.dumps({"chat_id": chat, "text": text, "disable_web_page_preview": True}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    for i in range(len(delays) + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode()).get("ok") is True
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504):
                return False
        except Exception:  # noqa: BLE001 - never print the exception: it can carry the URL with the token
            pass
        if i < len(delays):
            time.sleep(delays[i])
    return False


# ---------------------------------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="owner/name of the GitHub repository that publishes the release")
    ap.add_argument("--profile", required=True, help="the routing profile (JSON) whose tags the files must contain")
    ap.add_argument("--www", default="/var/lib/cheb-geo/www")
    ap.add_argument("--state", default="/var/lib/cheb-geo/state.json")
    ap.add_argument("--last-good", default="/var/lib/cheb-geo/last-good")
    ap.add_argument("--xray", default="/opt/hiddify-manager/xray/bin/xray")
    ap.add_argument("--force", action="store_true", help="download and check even when the tag is already published")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    state = json.load(open(a.state)) if os.path.exists(a.state) else {}
    if a.status:
        print(json.dumps(state, indent=1, ensure_ascii=False))
        return 0
    now = time.time()

    def save() -> None:
        os.makedirs(os.path.dirname(a.state), exist_ok=True)
        json.dump(state, open(a.state + ".tmp", "w"), indent=1)
        os.replace(a.state + ".tmp", a.state)

    def alert(key: str, text: str) -> None:
        last = state.get("alert", {})
        if last.get("key") == key and now - last.get("time", 0) < 86400:
            return
        ok = tell_admin(text)
        log(f"telegram to the admin: {'sent' if ok else 'NOT sent'}")
        if ok:
            state["alert"] = {"key": key, "time": now}

    def reject(tag: str, why: str) -> int:
        log(f"REJECTED {tag}: {why}. The files served now stay as they are.")
        state["last_error"] = {"tag": tag, "why": why, "time": now}
        alert(f"reject:{tag}:{why[:60]}", f"Зеркало geo-файлов: новый релиз {tag} не принят ({why}). Раздаётся прежняя версия, клиенты ничего не заметят.")
        save()
        return 0

    # 1. the latest tag
    try:
        rel = json.loads(get(f"{API}/repos/{a.repo}/releases/latest", 5_000_000))
        tag = str(rel["tag_name"])
        digests = {x["name"]: str(x.get("digest") or "") for x in rel.get("assets", [])}
    except Exception as exc:  # noqa: BLE001
        log(f"the release list is not reachable ({type(exc).__name__}): nothing changed")
        state["last_error"] = {"tag": "-", "why": f"api {type(exc).__name__}", "time": now}
        if now - state.get("ok_time", now) > 172800:
            alert("stale", "Зеркало geo-файлов: больше двух суток не получается проверить новый релиз (GitHub недоступен). Раздаются прежние файлы.")
        save()
        return 0
    served = all(os.path.exists(os.path.join(a.www, n)) for n in SERVE)
    if tag == state.get("tag") and served and not a.force:
        log(f"release {tag} is already published: nothing to do")
        state["ok_time"] = now
        save()
        return 0
    log(f"new release {tag} (served now: {state.get('tag', '-')})")

    # 2. download, checksums
    tmp = tempfile.mkdtemp(prefix="cheb-geo-")
    files: dict[str, bytes] = {}
    try:
        for name, asset in SERVE.items():
            files[name] = get(f"{DL}/{a.repo}/releases/download/{tag}/{asset}")
            digest = digests.get(asset, "")
            got = "sha256:" + hashlib.sha256(files[name]).hexdigest()
            if digest and digest != got:
                return reject(tag, f"{asset}: sha256 differs from the one GitHub records")
            if not digest:
                log(f"  {asset}: GitHub gives no digest for the asset, only the content checks below apply")
            with open(os.path.join(tmp, name), "wb") as f:
                f.write(files[name])
    except Exception as exc:  # noqa: BLE001
        log(f"download failed ({type(exc).__name__}): nothing changed")
        state["last_error"] = {"tag": tag, "why": f"download {type(exc).__name__}", "time": now}
        if now - state.get("ok_time", now) > 172800:
            alert("stale", "Зеркало geo-файлов: больше двух суток не получается скачать новый релиз. Раздаются прежние файлы.")
        save()
        return 0

    # 3. content checks
    for name, (lo, hi) in SIZE.items():
        if not lo <= len(files[name]) <= hi:
            return reject(tag, f"{name} has an odd size ({len(files[name])} bytes)")
    try:
        found = {"geosite": dat_tags(files["geosite.dat"]), "geoip": dat_tags(files["geoip.dat"])}
    except Exception as exc:  # noqa: BLE001
        return reject(tag, f"a file is not a geo file ({type(exc).__name__})")
    sites, ips, used = profile_items(a.profile)
    for kind in ("geosite", "geoip"):
        missing = sorted(t for t in used[kind] if t not in found[kind])
        if missing:
            return reject(tag, f"{kind} tags the profile uses are missing: {', '.join(missing)[:150]}")
    old = state.get("counts", {})
    for kind in ("geosite", "geoip"):
        for t in used[kind]:
            n = found[kind][t]
            if t in FLOORS and n < FLOORS[t]:
                return reject(tag, f"{t} has only {n} entries (at least {FLOORS[t]} expected)")
            prev = old.get(f"{kind}:{t}")
            if prev and prev >= 100 and n < prev / 2:
                return reject(tag, f"{t} shrank from {prev} to {n} entries")
    ok, why = xray_loads(a.xray, tmp, sites, ips)
    if not ok:
        return reject(tag, f"the real xray cannot load the profile with these files ({why})")

    # 4. publish
    os.makedirs(a.www, exist_ok=True)
    os.makedirs(a.last_good, exist_ok=True)
    for name in SERVE:
        for d in (a.last_good, a.www):
            part = os.path.join(d, f".{name}.new")
            with open(part, "wb") as f:
                f.write(files[name])
            os.chmod(part, 0o644)
            os.replace(part, os.path.join(d, name))
    state.update({"tag": tag, "published": now, "ok_time": now, "sha256": {n: hashlib.sha256(files[n]).hexdigest() for n in SERVE},
                  "counts": {f"{kind}:{t}": found[kind][t] for kind in ("geosite", "geoip") for t in used[kind]}})
    state.pop("last_error", None)
    save()
    log(f"PUBLISHED {tag}: " + ", ".join(f"{k.split(':')[1]}={v}" for k, v in state["counts"].items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
