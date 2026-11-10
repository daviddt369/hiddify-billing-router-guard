"""STAND: build release variants from the real geo files. usage: make_variant.py <src dir with geoip.dat geosite.dat> <out dir> <good|missing_tag|truncated|halved|badmagic>"""
import os
import sys

sys.path.insert(0, "/work/stage2/geo/pkg")
from cheb_geo_update import _fields, _varint  # noqa: E402


def enc(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def ld(field: int, payload: bytes) -> bytes:
    return enc(field << 3 | 2) + enc(len(payload)) + payload


def entries(data: bytes):
    for f, w, v in _fields(data):
        if f == 1 and w == 2:
            code = next((v2.decode() for f2, w2, v2 in _fields(v) if f2 == 1 and w2 == 2), "")
            yield code.upper(), v


src, out, kind = sys.argv[1:4]
os.makedirs(out, exist_ok=True)
geoip, geosite = open(f"{src}/geoip.dat", "rb").read(), open(f"{src}/geosite.dat", "rb").read()
if kind == "missing_tag":
    geosite = b"".join(ld(1, v) for code, v in entries(geosite) if code != "RU-DOMAINS")
elif kind == "truncated":
    geosite = geosite[:500_000]
elif kind == "halved":
    new = []
    for code, v in entries(geoip):
        if code == "RU-IPS":
            subs = [(f, w, x) for f, w, x in _fields(v)]
            cidrs = [x for f, w, x in subs if f == 2]
            keep = cidrs[: len(cidrs) // 3]
            v = b"".join(ld(f, x) for f, w, x in subs if f != 2) + b"".join(ld(2, x) for x in keep)
        new.append(ld(1, v))
    geoip = b"".join(new)
elif kind == "badmagic":
    geoip = b"<html>rate limit exceeded</html>" * 20000
open(f"{out}/geoip.dat", "wb").write(geoip)
open(f"{out}/geosite-compact.dat", "wb").write(geosite)
print(kind, len(geoip), len(geosite))
