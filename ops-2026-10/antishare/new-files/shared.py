"""Shared infrastructure addresses (CDN edges) are not clients.

A user who connects through a CDN shows up in the Xray access log with the address of the CDN edge, the same for every user of that CDN. Counting it as "one more
device" gives false warnings, and banning it would cut every other user behind the same edge. These addresses are therefore ignored by the anti-share cycle:
they never count towards a user's IP limit and are never banned. Real sharing of an account is still caught by the client addresses that are visible.

The list is a JSON file (path: HIDDIFY_ANTI_SHARE_SHARED_FILE, default /etc/hiddify-panel/antishare-shared-ips.json). Accepted shapes:
  {"cidrs": ["198.51.100.0/24", ...]}                              (our own format)
  {"addresses": [...], "addresses_v6": [...]}                     (the published G-Core CDN list, as downloaded)
A missing or broken file means "no shared addresses": the cycle then behaves exactly as before.
"""
from __future__ import annotations

import ipaddress
import json
import os

SHARED_FILE = os.environ.get("HIDDIFY_ANTI_SHARE_SHARED_FILE", "/etc/hiddify-panel/antishare-shared-ips.json")
_cache: dict = {"path": None, "mtime": None, "nets": []}


def parse_networks(data) -> list:
    raw = []
    if isinstance(data, dict):
        for key in ("cidrs", "addresses", "addresses_v6"):
            raw += [x for x in (data.get(key) or []) if isinstance(x, str)]
    nets = []
    for x in raw:
        try:
            nets.append(ipaddress.ip_network(x.strip(), strict=False))
        except ValueError:
            continue
    return nets


def load_networks(path: str | None = None) -> list:
    path = path or SHARED_FILE
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        return []
    if _cache["path"] == path and _cache["mtime"] == mtime:
        return _cache["nets"]
    try:
        with open(path, encoding="utf-8") as f:
            nets = parse_networks(json.load(f))
    except (OSError, ValueError):
        nets = []
    _cache.update(path=path, mtime=mtime, nets=nets)
    return nets


def is_shared(ip: str, nets: list) -> bool:
    try:
        addr = ipaddress.ip_address(ip.strip("[]"))
    except ValueError:
        return False
    return any(addr in n for n in nets if n.version == addr.version)


def split_shared(ips: list, nets: list) -> tuple[list, list]:
    """-> (client addresses, shared addresses), order kept"""
    clients, shared = [], []
    for ip in ips:
        (shared if is_shared(ip, nets) else clients).append(ip)
    return clients, shared


def db_networks() -> list:
    """the addresses added in the admin page (table anti_share_shared_ip); any problem with the table means "none" - the cycle must never fail because of this list"""
    try:
        from hiddifypanel.antishare.models import AntiShareSharedIP
        return parse_networks({"cidrs": [r.cidr for r in AntiShareSharedIP.query.all()]})
    except Exception:
        return []


def all_networks() -> list:
    return load_networks() + db_networks()
