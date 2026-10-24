#!/usr/bin/env python3
"""Resolve current INCY download URLs from the public GitHub releases API.

Writes a JSON map {dynamic_key: url}. Only URLs under the official INCY release path
are ever accepted. On any problem the previous file is left untouched.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.request

API = "https://api.github.com/repos/INCY-DEV/incy-platforms/releases?per_page=20"
PREFIX = "https://github.com/INCY-DEV/incy-platforms/releases/download/"
WANT = {
    "desktop-v": {
        "incy_windows_setup": "incy-windows-setup.exe",
        "incy_windows_portable": "incy-windows-portable.zip",
        "incy_macos_arm64": "incy-macos-arm64.dmg",
        "incy_macos_intel": "incy-macos-intel.dmg",
    },
    "android-v": {"incy_android_apk": "Incy.apk"},
}


def fetch(url: str) -> list:
    req = urllib.request.Request(url, headers={"User-Agent": "routing-links-updater", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 - fixed https URL
        return json.load(r)


def resolve(releases: list) -> dict:
    out: dict = {}
    for tag_prefix, mapping in WANT.items():
        cands = [r for r in releases if str(r.get("tag_name", "")).startswith(tag_prefix) and not r.get("prerelease") and not r.get("draft")]
        cands.sort(key=lambda r: r.get("published_at") or "", reverse=True)
        for rel in cands:
            assets = {a.get("name"): a.get("browser_download_url", "") for a in rel.get("assets", [])}
            found = {k: assets[v] for k, v in mapping.items() if v in assets and assets[v].startswith(PREFIX)}
            if found:
                out.update(found)
                break
    return out


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: resolve_links.py OUT.json", file=sys.stderr)
        return 64
    dest = sys.argv[1]
    src = os.environ.get("INCY_RELEASES_JSON")  # test hook: read a local file instead of the network
    try:
        if src:
            with open(src, "r", encoding="utf-8") as f:
                releases = json.load(f)
        else:
            releases = fetch(API)
        links = resolve(releases)
    except Exception as exc:  # noqa: BLE001
        print(f"resolve_links: keeping previous links ({exc})", file=sys.stderr)
        return 1
    if not links:
        print("resolve_links: nothing resolved, keeping previous links", file=sys.stderr)
        return 1
    fd, tmp = tempfile.mkstemp(prefix=".dyn-", dir=os.path.dirname(os.path.abspath(dest)))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(links, f, indent=1, sort_keys=True)
    os.chmod(tmp, 0o644)
    os.replace(tmp, dest)
    print(f"resolve_links: {len(links)} links -> {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
