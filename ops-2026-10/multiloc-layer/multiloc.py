"""Multi-location (country selection through the RU node) - core logic.

Design (see PLAN):
  * A *slot* (1..4) is infrastructure: 3 xray inbounds (vless-xhttp, vless-ws, trojan-ws), one router entrance, one balancer.
    Slot title / country / visibility are labels only, so enabling or re-labelling a country never needs a hiddify-xray restart
    or an HAProxy reload - only a router apply.
  * Slot 0 is the virtual AUTO block, no infrastructure. The ONLY place AUTO / countries appear is a SEPARATE link, `<user link>/happ/` (given to
    Happ / INCY users by the Telegram bot): the full xray JSON with ONE button per location (AUTO = every other xray-capable transport of the node,
    a country = its 3 stable transports; the client itself picks the fastest member by ping: leastPing + burstObservatory). The usual
    subscription link and every other endpoint (including `/xray/`) stay EXACTLY as they are, for every client and every user, testers included -
    no switching by User-Agent. The user panel's "All Configs" list also shows the country profiles (owner's decision).
  * Everything here fails SAFE: any problem in the hooks leaves the legacy subscription / legacy routing untouched.
"""
from __future__ import annotations

import base64
import copy
import ipaddress
import json
import os
import re
import secrets
import shutil
import string
import time
from typing import Any

from loguru import logger

MAX_SLOT = 4
ROUTER_PORT_OFFSET = 2          # slot N listens on <commercial_router_port> + 2 + N   (20808 -> 20811..20814)
VISIBILITIES = ("hidden", "testers", "all")
_ALNUM = string.ascii_letters + string.digits
_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,15}$")

# the three inbounds a slot owns: kind -> (xray protocol, stream, DB path column, label shown in the profile name)
INBOUNDS = {
    "xhttp": {"proto": "vless", "stream": "xhttp", "path_field": "path_xhttp", "label": "XHTTP"},
    "ws": {"proto": "vless", "stream": "ws", "path_field": "path_ws", "label": "WS"},
    "trojan": {"proto": "trojan", "stream": "ws", "path_field": "path_trojan", "label": "Trojan"},
}


# ----------------------------------------------------------------------------------------------- naming
def inbound_tag(slot: int, kind: str) -> str:
    spec = INBOUNDS[kind]
    return f"v10-{spec['proto']}-{spec['stream']}-loc{slot}"


def router_inbound_tag(slot: int) -> str:
    return f"from-hiddify-s{slot}"


def router_outbound_tag(slot: int) -> str:
    return f"router-s{slot}"


def balancer_tag(slot: int) -> str:
    return f"balancer-s{slot}"


def router_port(slot: int, base_port: int = 20808) -> int:
    return int(base_port) + ROUTER_PORT_OFFSET + int(slot)


def flag_for(country_iso: str) -> str:
    iso = (country_iso or "").strip().upper()
    if len(iso) == 2 and iso.isalpha() and iso.isascii():
        return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in iso)
    return ""


def new_path() -> str:
    """22 random alphanumerics (>130 bit): secret path of one inbound."""
    return "".join(secrets.choice(_ALNUM) for _ in range(22))


# ----------------------------------------------------------------------------------------------- schema
_DDL = [
    """CREATE TABLE IF NOT EXISTS commercial_routing_location (
  id INT NOT NULL AUTO_INCREMENT,
  slot INT NOT NULL,
  kind VARCHAR(16) NOT NULL DEFAULT 'country',
  code VARCHAR(16) NOT NULL DEFAULT '',
  title VARCHAR(64) NOT NULL DEFAULT '',
  country_iso VARCHAR(2) NOT NULL DEFAULT '',
  flag VARCHAR(16) NOT NULL DEFAULT '',
  sort_order INT NOT NULL DEFAULT 100,
  enabled TINYINT(1) NOT NULL DEFAULT 0,
  visibility VARCHAR(16) NOT NULL DEFAULT 'testers',
  path_xhttp VARCHAR(64) NOT NULL DEFAULT '',
  path_ws VARCHAR(64) NOT NULL DEFAULT '',
  path_trojan VARCHAR(64) NOT NULL DEFAULT '',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY ux_crl_slot (slot)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS commercial_routing_location_node (
  upstream_id INT NOT NULL,
  location_id INT NOT NULL,
  PRIMARY KEY (upstream_id),
  KEY ix_crln_location (location_id),
  CONSTRAINT fk_crln_upstream FOREIGN KEY (upstream_id) REFERENCES commercial_routing_upstream (id) ON DELETE CASCADE,
  CONSTRAINT fk_crln_location FOREIGN KEY (location_id) REFERENCES commercial_routing_location (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS commercial_routing_location_tester (
  user_uuid VARCHAR(36) NOT NULL,
  note VARCHAR(128) NOT NULL DEFAULT '',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (user_uuid)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
]
TABLES = ("commercial_routing_location_tester", "commercial_routing_location_node", "commercial_routing_location")  # drop order


_FK_RE = re.compile(
    r",\s*CONSTRAINT\s+\w+\s+FOREIGN\s+KEY\s*\([^)]*\)\s*REFERENCES\s+\w+\s*\([^)]*\)(?:\s+ON\s+DELETE\s+\w+(?:\s+\w+)?)?", re.I)


def strip_foreign_keys(ddl: str) -> str:
    """the same CREATE TABLE without its FOREIGN KEY constraints (fallback for engines / installs that cannot create them)"""
    return _FK_RE.sub("", ddl)


def _models():
    from hiddifypanel.models.commercial_routing_location import (
        CommercialRoutingLocation, CommercialRoutingLocationNode, CommercialRoutingLocationTester)
    return CommercialRoutingLocation, CommercialRoutingLocationNode, CommercialRoutingLocationTester


def ensure_schema(create_slots: bool = True) -> dict:
    """Idempotent, additive: creates the three tables if missing and the four empty slot rows. Returns what it did."""
    from sqlalchemy import text
    from hiddifypanel.database import db

    done = {"created_tables": [], "slots_created": []}
    existing = {r[0] for r in db.session.execute(text("SHOW TABLES")).fetchall()}
    for ddl in _DDL:
        name = re.search(r"EXISTS (\w+)", ddl).group(1)
        if name in existing:
            continue
        try:
            db.session.execute(text(ddl))
        except Exception as exc:  # engine without FK support etc.: retry without constraints (integrity is also checked in code)
            logger.warning(f"multiloc: DDL for {name} failed ({exc}); retrying without foreign keys")
            db.session.rollback()
            db.session.execute(text(strip_foreign_keys(ddl)))
        db.session.commit()
        done["created_tables"].append(name)
        if os.environ.get("MULTILOC_TEST_FAIL_AFTER_TABLES") == str(len(done["created_tables"])):    # inert test hook (rehearsal tests only)
            raise RuntimeError(f"injected failure after {len(done['created_tables'])} created table(s) (test hook)")
    if create_slots:
        done["slots_created"] = ensure_slots()
    return done


def ensure_slots() -> list[int]:
    """Create the empty slot rows 1..MAX_SLOT (no titles, disabled, random secret paths). Never touches existing rows' labels."""
    from hiddifypanel.database import db
    Location, _, _ = _models()
    created = []
    for slot in range(1, MAX_SLOT + 1):
        row = Location.query.filter_by(slot=slot).first()
        if row is None:
            row = Location(slot=slot, kind="country", enabled=False, visibility="testers", sort_order=100 + slot * 10,
                           path_xhttp=new_path(), path_ws=new_path(), path_trojan=new_path())
            db.session.add(row)
            created.append(slot)
        else:
            for f in ("path_xhttp", "path_ws", "path_trojan"):
                if not getattr(row, f):
                    setattr(row, f, new_path())
    db.session.commit()
    return created


def drop_schema() -> list[str]:
    """Rollback helper (used by the rollback script and by tests): drops ONLY the three multiloc tables."""
    from sqlalchemy import text
    from hiddifypanel.database import db
    dropped = []
    existing = {r[0] for r in db.session.execute(text("SHOW TABLES")).fetchall()}
    for t in TABLES:
        if t in existing:
            db.session.execute(text(f"DROP TABLE {t}"))
            dropped.append(t)
    db.session.commit()
    return dropped


def schema_present() -> bool:
    from sqlalchemy import text
    from hiddifypanel.database import db
    try:
        existing = {r[0] for r in db.session.execute(text("SHOW TABLES")).fetchall()}
    except Exception:
        return False
    return all(t in existing for t in TABLES)


# ----------------------------------------------------------------------------------------------- validation
MAP_TEMPLATE = "/opt/hiddify-manager/haproxy/maps/path_v10.j2"


def literal_map_paths(template: str = MAP_TEMPLATE) -> list[str]:
    """The literal paths written into the HAProxy map template itself (e.g. the secret path of a CDN inbound): `/<12-64 chars> <backend>`. Lines that are built
    from settings (`/{{ ... }}`) are not literal. Read from the template so that no such path is ever written into this code."""
    try:
        with open(template, encoding="utf-8") as f:
            return re.findall(r"^/([A-Za-z0-9_-]{12,64})[ \t]+\S+", f.read(), re.M)
    except OSError:
        return []


def reserved_paths(hconfigs: dict | None = None) -> list[str]:
    """Every path prefix already used for something else (panel paths, stock proxy paths, the literal paths of the map template such as a CDN inbound)."""
    try:
        from hiddifypanel.models import ConfigEnum, get_hconfigs
        h = hconfigs or get_hconfigs()
        g = lambda k: str(h.get(getattr(ConfigEnum, k), h.get(k, "")) or "")  # noqa: E731
        out = [g("proxy_path"), g("proxy_path_client"), g("proxy_path_admin")]
        for proto in ("vless", "vmess", "trojan", "ss"):
            for stream in ("tcp", "ws", "grpc", "httpupgrade", "xhttp"):
                out.append(g("path_" + proto) + g("path_" + stream))
        out += literal_map_paths()
        return [p for p in out if p]
    except Exception:
        return []


def check_paths(paths: list[str], reserved: list[str] | None = None) -> list[str]:
    """Return a list of problems; HAProxy uses prefix matching (path_beg / map_beg) so NO path may be a prefix of another."""
    problems = []
    allp = [p.strip("/") for p in paths if p]
    for i, a in enumerate(allp):
        if not re.fullmatch(r"[A-Za-z0-9_-]{12,64}", a):
            problems.append(f"path {a!r}: must be 12-64 chars of [A-Za-z0-9_-]")
        for j, b in enumerate(allp):
            if i != j and (a == b or b.startswith(a)):
                problems.append(f"path {a!r} is a prefix of / equal to {b!r}")
        for r in reserved or []:
            r = r.strip("/")
            if r and (a.startswith(r) or r.startswith(a)):
                problems.append(f"path {a!r} collides with reserved path {r!r}")
    return problems


def validate_location(row: Any) -> list[str]:
    problems = []
    if row.kind == "country" and not (1 <= int(row.slot) <= MAX_SLOT):
        problems.append(f"slot {row.slot} out of range 1..{MAX_SLOT}")
    if row.kind == "auto" and int(row.slot) != 0:
        problems.append("the AUTO block must use slot 0")
    if row.visibility not in VISIBILITIES:
        problems.append(f"visibility {row.visibility!r} not in {VISIBILITIES}")
    if row.code and not _CODE_RE.match(row.code):
        problems.append(f"code {row.code!r} must match [a-z0-9][a-z0-9-]{{0,15}}")
    if row.country_iso and not re.fullmatch(r"[A-Za-z]{2}", row.country_iso):
        problems.append(f"country_iso {row.country_iso!r} must be 2 letters")
    if row.enabled and not (row.title or "").strip():
        problems.append("an enabled location needs a title")
    if len(row.title or "") > 64:
        problems.append("title is longer than 64 characters")
    if len(getattr(row, "flag", "") or "") > 16:
        problems.append("flag is longer than 16 characters")
    return problems


def validate_all() -> list[str]:
    """Validate every row INCLUDING uncommitted in-memory edits. no_autoflush: an invalid pending value (e.g. a 3-letter country code
    for VARCHAR(2)) must be reported here, not flushed to the database by the query below (strict mode would answer with an error)."""
    from hiddifypanel.database import db
    Location, _, _ = _models()
    with db.session.no_autoflush:
        rows = Location.query.order_by(Location.slot).all()
        problems: list[str] = []
        paths = []
        for r in rows:
            problems += [f"slot {r.slot}: {p}" for p in validate_location(r)]
            if r.kind == "country":
                paths += [r.path_xhttp, r.path_ws, r.path_trojan]
        problems += check_paths(paths, reserved_paths())
    return problems


# ----------------------------------------------------------------------------------------------- data access
def label_of(row: Any) -> str:
    flag = row.flag or flag_for(row.country_iso) or ("🟢" if row.kind == "auto" else "")
    return f"{flag} {row.title}".strip()


def get_rows(kind: str | None = None):
    Location, _, _ = _models()
    q = Location.query
    if kind:
        q = q.filter_by(kind=kind)
    return q.order_by(Location.sort_order, Location.slot).all()


def nodes_by_slot() -> dict[int, list[int]]:
    """slot -> [upstream ids] (all rows, enabled or not)"""
    Location, Node, _ = _models()
    out: dict[int, list[int]] = {}
    for loc in Location.query.filter_by(kind="country").all():
        out[int(loc.slot)] = sorted(n.upstream_id for n in Node.query.filter_by(location_id=loc.id).all())
    return out


def is_tester(user_uuid: str) -> bool:
    _, _, Tester = _models()
    return Tester.query.filter_by(user_uuid=str(user_uuid).strip().lower()).first() is not None


def visible_rows_for(user_uuid: str | None) -> list:
    """Locations (AUTO + enabled countries) this user may see, ordered by sort_order. [] when nothing applies."""
    if not user_uuid:
        return []
    rows = [r for r in get_rows() if r.enabled and (r.title or "").strip()]
    tester = None
    out = []
    for r in rows:
        if r.visibility == "all":
            out.append(r)
        elif r.visibility == "testers":
            if tester is None:
                tester = is_tester(user_uuid)
            if tester:
                out.append(r)
    return out


# ----------------------------------------------------------------------------------------------- template context
def template_context(hconfigs: dict | None = None) -> list[dict]:
    """`locations` variable for the jinja templates (xray inbounds / routing / outbounds, haproxy).
    All four country slots are always emitted once the schema exists (disabled slots are routed to a blocking router entrance,
    so an old profile of a disabled country can never leak into the global balancer). Returns [] when the feature isn't installed."""
    try:
        if not schema_present():
            return []
        from hiddifypanel.models import ConfigEnum, get_hconfigs
        h = hconfigs or get_hconfigs()
        base_port = int(h.get(ConfigEnum.commercial_router_port) or 20808)
        out = []
        for r in get_rows("country"):
            if not (r.path_xhttp and r.path_ws and r.path_trojan):
                continue
            out.append({
                "slot": int(r.slot),
                "enabled": bool(r.enabled),
                "code": r.code,
                "title": r.title,
                "paths": {k: getattr(r, INBOUNDS[k]["path_field"]) for k in INBOUNDS},
                "tags": {k: inbound_tag(r.slot, k) for k in INBOUNDS},
                "protos": {k: INBOUNDS[k]["proto"] for k in INBOUNDS},
                "streams": {k: INBOUNDS[k]["stream"] for k in INBOUNDS},
                "router_outbound": router_outbound_tag(r.slot),
                "router_port": router_port(r.slot, base_port),
            })
        return out
    except Exception as exc:  # never break config generation
        logger.warning(f"multiloc.template_context failed: {exc}")
        return []


# ----------------------------------------------------------------------------------------------- subscription
_SOURCE_RANK = {"tls_h2": 0, "tls": 1}


def _find_sources(entries: list[dict]) -> dict[str, dict]:
    """Pick, per transport kind, the existing direct TLS entry that new entries are cloned from."""
    best: dict[str, tuple[int, int, dict]] = {}
    for idx, e in enumerate(entries):
        try:
            if e.get("cdn") or "reality" in str(e.get("l3")) or "tls" not in str(e.get("l3")) or "quic" in str(e.get("l3")):
                continue
            if "h3" in str(e.get("l3")):
                continue
            proto, tr = str(e.get("proto")), str(e.get("transport")).lower()
            kind = None
            if proto == "vless" and tr == "xhttp":
                kind = "xhttp"
            elif proto == "vless" and tr == "ws":
                kind = "ws"
            elif proto == "trojan" and tr == "ws":
                kind = "trojan"
            if not kind:
                continue
            rank = _SOURCE_RANK.get(str(e.get("l3")), 9)
            if kind not in best or (rank, idx) < best[kind][:2]:
                best[kind] = (rank, idx, e)
        except Exception:
            continue
    return {k: v[2] for k, v in best.items()}


def _clone_entry(src: dict, row: Any, kind: str) -> dict:
    """a country profile = the legacy direct entry of that transport, pointed at the slot's secret path"""
    e = dict(src)
    for k in ("params", "download"):
        if k in e and e[k] is not None:
            e[k] = copy.deepcopy(e[k])
    path = "/" + (getattr(row, INBOUNDS[kind]["path_field"]) or "").lstrip("/")
    e["path"] = path
    if isinstance(e.get("download"), dict) and "path" in e["download"]:
        e["download"]["path"] = path
    # shown as "<flag> <title> · <transport>": the flag goes to extra_info, the rest to name (the user panel's "All Configs" page lists `name` only)
    e["name"] = f"{row.title} · {INBOUNDS[kind]['label']}".strip()
    e["extra_info"] = row.flag or flag_for(row.country_iso) or "🌐"
    e["multiloc"] = {"slot": int(row.slot), "kind": "country", "pinned": True}
    return e


def _row_pins_ok(row: Any, kind: str) -> bool:
    return bool(getattr(row, INBOUNDS[kind]["path_field"]))


_ALL_CONFIGS_API = "/api/v2/user/all-configs"          # the user panel's own "All Configs" list (called by the panel page in the browser)


def request_kind() -> str | None:
    """"happ" for the dedicated Happ / INCY link `<user link>/happ/`, "panel" for the user panel's "All Configs" list, None for every other
    request (the usual link, Hiddify, v2rayNG, sing-box, Clash, `/xray/` ...: the stock answer, testers included). No User-Agent detection."""
    try:
        from flask import has_request_context, request
        if not has_request_context():
            return None
        path = request.path.rstrip("/")
        if path.endswith("/happ"):
            return "happ"
        if path.endswith(_ALL_CONFIGS_API):
            return "panel"
    except Exception:
        pass
    return None


def layout_client() -> bool:
    """True only for the dedicated Happ / INCY link `<user link>/happ/` - the ONLY subscription endpoint that shows AUTO / country buttons."""
    return request_kind() == "happ"


def location_client() -> bool:
    """layout_client() plus the user panel's "All Configs" list."""
    return request_kind() is not None


def append_location_entries(entries: list[dict], user_uuid: str | None = None) -> list[dict]:
    """Hook for hutils.proxy.shared.get_valid_proxies(): the legacy entries (unchanged, same order) + the country profiles this user may see -
    but only for the requests that are allowed to see them (location_client()); every other client gets the stock list.
    AUTO has no entries of its own: it exists as a group in the full xray JSON (see xray_json_layout).
    Returns the ORIGINAL list object untouched whenever nothing applies or anything fails."""
    try:
        kind = request_kind()
        if kind is None:
            return entries
        if user_uuid is None:
            from flask import g
            acct = getattr(g, "account", None)
            if acct is not None and getattr(acct, "is_active", True) is False:
                return entries            # inactive / expired / disabled users never get the new profiles (they could not use them)
            user_uuid = str(getattr(acct, "uuid", "") or "")
        if kind == "happ" and not json_layout_enabled(user_uuid):
            return entries                # /happ/ answers with the stock list unless AUTO is shown: never a half layout with country profiles in it
        rows = [r for r in visible_rows_for(user_uuid) if r.kind == "country"]
        if not rows:
            return entries
        sources = _find_sources(entries)
        if not sources:
            return entries
        country_blocks: list[dict] = []
        for r in rows:
            for kind in INBOUNDS:
                if kind in sources and _row_pins_ok(r, kind):
                    country_blocks.append(_clone_entry(sources[kind], r, kind))
        if not country_blocks:
            return entries
        return list(entries) + country_blocks
    except Exception as exc:
        logger.warning(f"multiloc.append_location_entries failed, serving legacy list: {exc}")
        return entries


# ----------------------------------------------------------------------------------------------- full xray JSON: one group per location
XRAY_JSON_PROTOS = {"vless", "trojan", "vmess", "shadowsocks"}      # what ONE full xray config can run (tuic / hysteria2 / naive / mieru cannot)
DEFAULT_PROBE_URL = "https://www.gstatic.com/generate_204"
DEFAULT_PROBE_INTERVAL = "1m"
# the CLIENT's own burst probes (Happ / INCY pick the fastest TRANSPORT of a group) are decoupled from the router's probe interval: 30 s x 2 samples finds a dead
# transport in 1-2 minutes (3 x 1 m took 3-6). They never decide the exit country - that is the router's job (AUTO director).
CLIENT_PROBE_INTERVAL = "30s"
CLIENT_PROBE_SAMPLING = 2


def json_layout_enabled(user_uuid: str | None) -> bool:
    """is this account shown the AUTO block? (then the full xray JSON is served as one group per location)"""
    try:
        return any(r.kind == "auto" for r in visible_rows_for(user_uuid))
    except Exception as exc:
        logger.warning(f"multiloc.json_layout_enabled failed: {exc}")
        return False


def happ_link_for(user: Any, user_link: str) -> str | None:
    """Second subscription link the Telegram bot hands out for Happ / INCY: `<user link>/happ/` (AUTO / Германия / США as three buttons).
    None when the account is not shown the AUTO block - then the bot sends the single usual link, exactly as before."""
    try:
        if getattr(user, "is_active", True) is False:
            return None
        if not json_layout_enabled(str(getattr(user, "uuid", "") or "")):
            return None
        base, sep, frag = str(user_link).partition("#")
        return base.rstrip("/") + "/happ/" + (sep + frag if sep else "")
    except Exception as exc:
        logger.warning(f"multiloc.happ_link_for failed: {exc}")
        return None


def _probe_settings() -> tuple[str, str]:
    """client-side probe = the same URL / interval the router uses for its own observatory (reachable through the tunnel by construction)"""
    url, interval = DEFAULT_PROBE_URL, DEFAULT_PROBE_INTERVAL
    try:
        from hiddifypanel.models import ConfigEnum, hconfig
        u = str(hconfig(ConfigEnum.commercial_router_probe_url) or "").strip()
        i = str(hconfig(ConfigEnum.commercial_router_probe_interval) or "").strip()
        if re.match(r"^https?://\S+$", u):
            url = u
        if re.match(r"^\d+[smh]$", i):
            interval = i
    except Exception:
        pass
    return url, interval


# ----------------------------------------------------------------------------------------------- RoscomVPN routing inside the Happ / INCY JSON
# Happ / INCY run a JSON subscription in "restricted mode": the routing profile cannot be added by hand, it only travels WITH the subscription (response
# header `routing`). The RoscomVPN author publishes a profile made for exactly this (JSONSUB): DNS + his own geoip / geosite files and NO rules - the rules
# belong inside the JSON configs. So on the dedicated `/happ/` link (1) the header carries that profile (the client downloads the geo files) and (2) every
# location's config gets the RoscomVPN rules (RouteOrder: block -> proxy -> direct; "proxy" = the location's balancer, so the ping choice keeps working).
# The source is the routing landing's validated copy of the upstream DEFAULT profile (refreshed there every 6 h) - ONE source of truth; the header profile is
# derived from it (verified equal to the upstream JSONSUB for Happ and INCY). Default OFF (flag file), Happ / INCY only (User-Agent) and only on `/happ/`;
# anything unusual -> the stock rules and no header, exactly as before.
ROSCOM_FLAG = "/opt/hiddify-manager/multiloc-roscom-json.on"      # the file exists = switched on; `multiloc_ctl.py roscom on|off|status`
ROSCOM_DIR = "/var/lib/routing-links/last-good"                   # <client>.deeplink written by the routing landing's updater
ROSCOM_SETTINGS = "/opt/hiddify-manager/hiddify-panel/var/multiloc-roscom.json"      # written by the panel page (and the console): {"enabled": bool, "custom": "<pasted profile>"}
ROSCOM_JSON_NAME = "RoscomVPN JSON"                               # NOT "RoscomVPN": clients match profiles by name and the user's own profile must not be overwritten
ROSCOM_CLIENTS = ("happ", "incy")
# the national zones + the banks go DIRECT from the device (the RU node has the same masks and bank domains in its direct list). The apps cut the traffic by the lists of the profile
# (block -> proxy -> direct), and the RoscomVPN lists miss a Russian site hosted abroad and the banks' other-zone domains - those would leave through the tunnel with a foreign IP.
# `domain:` entries first: every client matches them in its routing (INCY, measured 2026-10-22, takes the `regexp:` entries into its DNS rules only and sent the .ru sites that
# are not on the whitelist into the tunnel). The `regexp:` entries stay for the clients that read them in the routing too (Happ) and for the DNS rules.
ROSCOM_RU_ZONE = ("domain:ru", "domain:su", "domain:xn--p1ai", "regexp:.*\\.ru$", "regexp:.*\\.su$", "regexp:.*\\.xn\\-\\-p1ai$")
ROSCOM_RU_BANKS = ("domain:sberbank.com", "domain:tbank-online.com", "domain:alfabank.com", "domain:alfa-bank.com", "domain:gazprombank.com")
_ROSCOM_DEEPLINK = {c: re.compile(rf"^{c}://routing/onadd/([A-Za-z0-9+/]+={{0,2}})$") for c in ROSCOM_CLIENTS}
_ROSCOM_GROUPS = (("block", "BlockSites", "BlockIp"), ("proxy", "ProxySites", "ProxyIp"), ("direct", "DirectSites", "DirectIp"))
_ROSCOM_ORDER = re.compile(r"^(block|proxy|direct)-(block|proxy|direct)-(block|proxy|direct)$")
_ROSCOM_STRATEGIES = ("AsIs", "IPIfNonMatch", "IPOnDemand")
_ROSCOM_MAX_ITEMS = 300
_ROSCOM_MAX_TEXT = 20000
_ENV_ROSCOM = "hiddify.multiloc.roscom"                 # per-request keys in the WSGI environ
_ENV_ROSCOM_USED = "hiddify.multiloc.roscom_used"
_GEO_ITEM = re.compile(r"^(?:geosite|geoip):[A-Za-z0-9@!._-]{1,64}$")
_DOMAIN_ITEM = re.compile(r"^(?:domain|full|regexp|keyword):[^\s\"]{1,200}$")      # JSON-encoded later, so a backslash in a regexp is fine
_HOST_ITEM = re.compile(r"^[A-Za-z0-9._-]{1,253}$")


def roscom_client_of(user_agent: str | None) -> str | None:
    """"happ" / "incy" for those two apps' User-Agent, None for everything else (Hiddify, v2rayNG, a browser ...)"""
    ua = (user_agent or "").lower()
    if "incy" in ua:
        return "incy"
    if "happ" in ua:
        return "happ"
    return None


def _roscom_item(item: Any, kind: str) -> str:
    """one rule entry of the profile -> the xray string. kind "site": geosite:tag / domain: full: regexp: keyword: / a bare host (= domain:host);
    kind "ip": geoip:tag / an address / a CIDR. ValueError for everything else (a profile we do not fully understand is not applied at all)."""
    if isinstance(item, str) and item == item.strip():
        if kind == "site":
            if item.startswith("geosite:"):
                if _GEO_ITEM.match(item):
                    return item
            elif _DOMAIN_ITEM.match(item):
                return item
            elif _HOST_ITEM.match(item) and "." in item:
                return "domain:" + item
        elif item.startswith("geoip:"):
            if _GEO_ITEM.match(item):
                return item
        else:
            try:
                ipaddress.ip_network(item, strict=False)
                return item
            except ValueError:
                pass
    raise ValueError(f"unsupported {kind} rule item {item!r}")


def check_roscom_profile(prof: Any) -> None:
    """ValueError unless the profile is exactly what the rules builder understands"""
    if not isinstance(prof, dict):
        raise ValueError("the profile is not an object")
    if not isinstance(prof.get("Name"), str) or not prof["Name"].strip():
        raise ValueError("the profile has no Name")
    order = _ROSCOM_ORDER.match(str(prof.get("RouteOrder", "")))
    if not order or len(set(order.groups())) != 3:
        raise ValueError(f"RouteOrder {prof.get('RouteOrder')!r} is not a permutation of block / proxy / direct")
    if str(prof.get("DomainStrategy", "")) not in _ROSCOM_STRATEGIES:
        raise ValueError(f"DomainStrategy {prof.get('DomainStrategy')!r} is not one of {_ROSCOM_STRATEGIES}")
    for k in ("Geoipurl", "Geositeurl"):
        if not re.fullmatch(r"https://[^\s\"'\\]{1,300}\.dat", str(prof.get(k, ""))):
            raise ValueError(f"{k} is not an https .dat address")
    if str(prof.get("GlobalProxy", "true")).strip().lower() not in ("true", "false"):
        raise ValueError("GlobalProxy is neither true nor false")
    total = 0
    for _, sites_key, ip_key in _ROSCOM_GROUPS:
        for key, kind in ((sites_key, "site"), (ip_key, "ip")):
            items = prof.get(key, [])
            if not isinstance(items, list) or len(items) > _ROSCOM_MAX_ITEMS:
                raise ValueError(f"{key} is not a list of at most {_ROSCOM_MAX_ITEMS} entries")
            for it in items:
                _roscom_item(it, kind)
            total += len(items)
    if not total:
        raise ValueError("the profile has no routing rules")


def parse_roscom_deeplink(text: str, client: str) -> dict:
    """the upstream DEFAULT deeplink `<client>://routing/onadd/<base64 JSON>` -> the checked profile. ValueError on anything unusual."""
    if client not in _ROSCOM_DEEPLINK:
        raise ValueError(f"unknown client {client!r}")
    text = (text or "").strip()
    if len(text) > _ROSCOM_MAX_TEXT:
        raise ValueError("the deeplink is too long")
    m = _ROSCOM_DEEPLINK[client].match(text)
    if not m:
        raise ValueError(f"not a {client}://routing/onadd/<base64> deeplink")
    try:
        prof = json.loads(base64.b64decode(m.group(1), validate=True).decode("utf-8"))
    except Exception as exc:
        raise ValueError(f"the payload is not base64 JSON ({exc})") from None
    check_roscom_profile(prof)
    return prof


def roscom_json_profile(prof: dict) -> dict:
    """the profile the client gets in the `routing` header: the upstream DEFAULT with NO rules, another Name and no chunked geo files = the upstream
    JSONSUB profile (DNS + the custom geo files; the rules live inside the JSON configs)"""
    out = dict(prof)
    out["Name"] = ROSCOM_JSON_NAME
    for _, sites_key, ip_key in _ROSCOM_GROUPS:
        out[sites_key] = []
        out[ip_key] = []
    for k in list(out):
        if k.lower() == "usechunkfiles":
            out[k] = False if isinstance(out[k], bool) else "false"
    return out


def roscom_header_lists() -> bool:
    """the profile in the `routing` header carries the LISTS (like the ordinary RoscomVPN profile) instead of being the empty JSONSUB variant. Why: with the
    empty profile the apps (Happ on Android and on Windows) sent EVERYTHING into the tunnel, Russian services included - measured in the first-hop access log; they cut
    the traffic by the lists of the ACTIVE profile. The rules inside the JSON stay for the clients that read the JSON only. Default OFF (= v3 / v4 behaviour)."""
    return bool(_roscom_settings_read().get("header_lists"))


def roscom_ru_zone() -> bool:
    """the RU zones (.ru .su .рф) and the banks are DIRECT in the client rules. Default ON (owner decision 2026-10-13); the settings file can switch it off."""
    v = _roscom_settings_read().get("ru_zone")
    return True if v is None else bool(v)


def roscom_with_ru_zone(prof: dict) -> dict:
    """the profile with the zone masks and the bank domains at the FRONT of its direct list (a copy; the profile itself is returned when the option is off)"""
    if not roscom_ru_zone():
        return prof
    out = dict(prof)
    have = list(prof.get("DirectSites", []))
    out["DirectSites"] = [x for x in (*ROSCOM_RU_ZONE, *ROSCOM_RU_BANKS) if x not in have] + have
    return out


def roscom_header_profile(prof: dict) -> dict:
    """what the client gets in the `routing` header: the empty JSONSUB variant, or (header_lists) the same profile WITH its lists under the own name"""
    if roscom_header_lists():
        out = dict(prof)
        out["Name"] = ROSCOM_JSON_NAME                 # still NOT "RoscomVPN": the user's own profile of that name must not be overwritten
        return out
    return roscom_json_profile(prof)


def roscom_header_value(client: str, prof: dict) -> str:
    raw = json.dumps(roscom_header_profile(prof), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return f"{client}://routing/onadd/" + base64.b64encode(raw).decode("ascii")


def roscom_rules(prof: dict, balancer: str) -> tuple[list[dict], str]:
    """xray routing rules + domainStrategy from the profile's own lists, groups in its RouteOrder (RoscomVPN: block -> proxy -> direct). "proxy" is the
    location's balancer; what matches nothing follows GlobalProxy (true: the balancer, false: direct)."""
    check_roscom_profile(prof)
    target = {"block": {"outboundTag": "block"}, "direct": {"outboundTag": "direct"}, "proxy": {"balancerTag": balancer}}
    keys = {name: (sites_key, ip_key) for name, sites_key, ip_key in _ROSCOM_GROUPS}
    rules = []
    for name in str(prof["RouteOrder"]).split("-"):
        sites_key, ip_key = keys[name]
        sites = [_roscom_item(i, "site") for i in prof.get(sites_key, [])]
        ips = [_roscom_item(i, "ip") for i in prof.get(ip_key, [])]
        if sites:
            rules.append({"type": "field", "domain": sites, **target[name]})
        if ips:
            rules.append({"type": "field", "ip": ips, **target[name]})
    rules.append({"type": "field", "port": "0-65535", **target["proxy" if str(prof.get("GlobalProxy", "true")).strip().lower() == "true" else "direct"]})
    return rules, str(prof["DomainStrategy"])


_KEEP = object()


def _roscom_settings_read() -> dict:
    try:
        with open(ROSCOM_SETTINGS, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def roscom_enabled() -> bool:
    """the switch: the settings file decides (panel page / console); without it the v3 flag file does (the state before the page existed)"""
    d = _roscom_settings_read()
    if isinstance(d.get("enabled"), bool):
        return d["enabled"]
    return os.path.exists(ROSCOM_FLAG)


_APP_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$")        # an Android package name: com.example.app
_PROVIDER_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def roscom_app_ids() -> list:
    """the Android packages that must stay OUT of the VPN (they refuse to work when the system reports a VPN): the settings file key `bypass_apps`"""
    raw = _roscom_settings_read().get("bypass_apps")
    return list(dict.fromkeys(x for x in (raw if isinstance(raw, list) else []) if isinstance(x, str) and _APP_ID.match(x)))


def roscom_app_headers(client: str | None = None) -> dict:
    """the response headers that keep the listed apps outside the tunnel on Android: `per-app-proxy-mode: bypass` + `per-app-proxy-list: a,b,c`.
    Happ (https://www.happ.su/main/dev-docs/app-management) needs the provider id too; INCY (https://incy.gitbook.io/docs/docs-en/app-management.en) needs
    no provider id but its per-app mode is switched on by `per-app-proxy-enable: 1`. Each client gets only the headers it documents."""
    apps = roscom_app_ids()
    if not apps:
        return {}
    out = {"per-app-proxy-mode": "bypass", "per-app-proxy-list": ",".join(apps)}
    if client == "incy":
        out["per-app-proxy-enable"] = "1"
        return out
    pid = _roscom_settings_read().get("provider_id")
    if isinstance(pid, str) and _PROVIDER_ID.match(pid):
        out["providerid"] = pid
    return out


_EXTRA_MAX = 100


def split_lines(text: Any) -> list:
    """what the panel fields hold: one entry per line (commas and semicolons also separate), blanks and '#' comments dropped, order kept, no duplicates"""
    raw = text if isinstance(text, (list, tuple)) else re.split(r"[\n\r,;]+", str(text or ""))
    return list(dict.fromkeys(x.strip() for x in raw if isinstance(x, str) and x.strip() and not x.strip().startswith("#")))


def normalize_sites(text: Any) -> list:
    """domain entries of a field -> the xray strings (a bare host = domain:host; geosite: / domain: / full: / regexp: / keyword: as they are). ValueError names the bad line."""
    out = []
    for item in split_lines(text):
        try:
            out.append(_roscom_item(item, "site"))
        except ValueError:
            raise ValueError(f"не похоже на домен или правило: {item[:60]}") from None
    out = list(dict.fromkeys(out))
    if len(out) > _EXTRA_MAX:
        raise ValueError(f"слишком много строк (не больше {_EXTRA_MAX})")
    return out


def roscom_extra_sites(kind: str) -> list:
    """the owner's own extra domains kept in the settings file: kind "direct" or "proxy" (broken entries are skipped, never fatal)"""
    raw = _roscom_settings_read().get("extra_" + kind)
    out = []
    for x in (raw if isinstance(raw, list) else []):
        try:
            out.append(_roscom_item(x, "site"))
        except ValueError:
            continue
    return list(dict.fromkeys(out))


def roscom_with_extras(prof: dict) -> dict:
    """the profile with the owner's extra domains at the FRONT of its direct / proxy list (a copy; the profile itself when there are none).
    The profile's order (block -> proxy -> direct) still decides: a domain in the proxy list is not rescued by being listed as direct."""
    d, p = roscom_extra_sites("direct"), roscom_extra_sites("proxy")
    if not d and not p:
        return prof
    out = dict(prof)
    if d:
        have = list(prof.get("DirectSites", []))
        out["DirectSites"] = [x for x in d if x not in have] + have
    if p:
        have = list(prof.get("ProxySites", []))
        out["ProxySites"] = [x for x in p if x not in have] + have
    return out


def roscom_set(enabled: Any = _KEEP, custom: Any = _KEEP, header_lists: Any = _KEEP, ru_zone: Any = _KEEP, bypass_apps: Any = _KEEP, provider_id: Any = _KEEP,
               extra_direct: Any = _KEEP, extra_proxy: Any = _KEEP) -> dict:
    """change the switch, the pasted profile (None / "" = back to the automatic copy) and / or the shape of the header profile. Atomic. Returns the new settings.
    bypass_apps: a list of Android package names (or None / [] = none); provider_id: the Happ provider id (or None);
    extra_direct / extra_proxy: the owner's own domain entries added to the direct / proxy list of the served profile (text or list; [] = none)."""
    d = _roscom_settings_read()
    for key, val in (("extra_direct", extra_direct), ("extra_proxy", extra_proxy)):
        if val is not _KEEP:
            d[key] = normalize_sites(val)
    if bypass_apps is not _KEEP:
        apps = split_lines(bypass_apps or [])
        bad = [x for x in apps if not _APP_ID.match(x)]
        if bad or len(apps) > 100:
            raise ValueError(f"not Android package names (or more than 100): {bad[:3]}")
        d["bypass_apps"] = list(dict.fromkeys(apps))
    if provider_id is not _KEEP:
        pid = str(provider_id).strip() if provider_id else ""
        if pid and not _PROVIDER_ID.match(pid):
            raise ValueError("the provider id has letters, digits, - and _ only (64 at most)")
        d["provider_id"] = pid or None
    if enabled is not _KEEP:
        d["enabled"] = bool(enabled)
    if header_lists is not _KEEP:
        d["header_lists"] = bool(header_lists)
    if ru_zone is not _KEEP:
        d["ru_zone"] = bool(ru_zone)
    if custom is not _KEEP:
        d["custom"] = (str(custom).strip() if custom else None) or None
    d["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    os.makedirs(os.path.dirname(ROSCOM_SETTINGS), exist_ok=True)
    tmp = ROSCOM_SETTINGS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.chmod(tmp, 0o644)
    if os.geteuid() == 0:                                        # written by the console: the panel must be able to replace it later
        try:
            shutil.chown(tmp, "hiddify-panel", "hiddify-panel")
        except Exception:
            pass
    os.replace(tmp, ROSCOM_SETTINGS)
    return d


def parse_roscom_input(text: str) -> dict:
    """what the owner pastes into the panel: `happ://routing/onadd/<base64>` (or incy:// or any scheme, add / onadd), the bare base64, or the profile JSON itself ->
    the checked profile. ValueError (with a reason fit for the page) on anything else."""
    t = (text or "").strip()
    if not t:
        raise ValueError("the field is empty")
    if len(t) > _ROSCOM_MAX_TEXT:
        raise ValueError("the text is too long for a routing profile")
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://routing/off\s*$", t):
        raise ValueError("this is the command that switches routing OFF, not a profile")
    raw = None
    m = re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://routing/(?:add|onadd)/([A-Za-z0-9+/_=-]+)\s*$", t)
    if m:
        b64 = m.group(1)
    elif t.startswith("{"):
        raw = t
    elif re.fullmatch(r"[A-Za-z0-9+/_=\s-]+", t):
        b64 = re.sub(r"\s+", "", t)
    else:
        raise ValueError("this is not a happ://routing/onadd/... link, not base64 and not the profile JSON")
    if raw is None:
        b64 = b64.replace("-", "+").replace("_", "/")
        try:
            raw = base64.b64decode(b64 + "=" * (-len(b64) % 4), validate=True).decode("utf-8")
        except Exception as exc:
            raise ValueError(f"the payload is not valid base64 ({exc})") from None
    try:
        prof = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"the profile is not valid JSON ({exc})") from None
    check_roscom_profile(prof)
    return prof


def roscom_profile_raw(client: str) -> tuple[dict, str]:
    """(profile, source) for this app exactly as it was pasted on the panel page (both apps), otherwise as the routing landing published it for that app"""
    custom = _roscom_settings_read().get("custom")
    if custom:
        return parse_roscom_input(custom), "custom"
    with open(os.path.join(ROSCOM_DIR, f"{client}.deeplink"), encoding="utf-8") as f:
        return parse_roscom_deeplink(f.read(_ROSCOM_MAX_TEXT + 1), client), "landing"


def roscom_profile(client: str) -> tuple[dict, str]:
    """(profile, source) as it is SERVED: the raw profile with the RU zones + banks added to its direct list (option ru_zone)"""
    prof, source = roscom_profile_raw(client)
    return roscom_with_extras(roscom_with_ru_zone(prof)), source


def _profile_summary(prof: dict) -> dict:
    lu = str(prof.get("LastUpdated", ""))
    try:
        lu_text = time.strftime("%Y-%m-%d %H:%M", time.localtime(int(lu)))
    except Exception:
        lu_text = lu
    counts = {"direct": len(prof.get("DirectSites", [])) + len(prof.get("DirectIp", [])), "proxy": len(prof.get("ProxySites", [])) + len(prof.get("ProxyIp", [])),
              "block": len(prof.get("BlockSites", [])) + len(prof.get("BlockIp", []))}
    return {"name": prof.get("Name"), "last_updated": lu_text, "geoip": prof.get("Geoipurl"), "geosite": prof.get("Geositeurl"), "route_order": prof.get("RouteOrder"),
            "domain_strategy": prof.get("DomainStrategy"), "global_proxy": str(prof.get("GlobalProxy", "true")).lower() == "true", "counts": counts}


def roscom_ui_state() -> dict:
    """for the panel card: the switch, where the profile comes from, what it contains, and why it cannot be used (if so). Never raises."""
    s = _roscom_settings_read()
    custom = s.get("custom") or ""
    st: dict = {"enabled": roscom_enabled(), "custom_text": custom, "source": "custom" if custom else "landing", "profile": None, "error": None,
                "header_profile": ROSCOM_JSON_NAME, "header_lists": roscom_header_lists(), "ru_zone": roscom_ru_zone(), "updated": s.get("updated"),
                "extra_direct_text": "\n".join(s.get("extra_direct") or []), "extra_proxy_text": "\n".join(s.get("extra_proxy") or []),
                "apps_text": "\n".join(roscom_app_ids()), "app_count": len(roscom_app_ids()), "provider_id": s.get("provider_id") or ""}
    try:
        prof = parse_roscom_input(custom) if custom else roscom_profile_raw("happ")[0]
        st["profile"] = _profile_summary(prof)
    except Exception as exc:
        st["error"] = str(exc)[:300]
    return st


def roscom_for_request() -> dict | None:
    """RoscomVPN routing for THIS request or None. Decided once per request (kept in the request's WSGI environ - flask.g may outlive a request), so the
    rules inside the JSON and the `routing` header always agree.
    Needs: the dedicated `/happ/` link, the switch on, a Happ / INCY User-Agent and a valid profile (pasted on the panel page, else the landing's copy for that app)."""
    try:
        from flask import has_request_context, request
        if not has_request_context():
            return None
        if _ENV_ROSCOM in request.environ:
            return request.environ[_ENV_ROSCOM]
        found = None
        try:
            if request_kind() == "happ" and roscom_enabled():
                client = roscom_client_of(request.headers.get("User-Agent"))
                logger.info(f"multiloc.roscom: /happ/ asked by User-Agent {str(request.headers.get('User-Agent'))[:120]!r} -> {client or 'not Happ / INCY: stock rules'}")
                if client:
                    prof, source = roscom_profile(client)
                    found = {"client": client, "profile": prof, "source": source, "header": roscom_header_value(client, prof)}
                    extra = roscom_app_headers(client)
                    if extra:                       # added to the response only if it really carries the RoscomVPN rules (same condition as the `routing` header)
                        from flask import after_this_request

                        @after_this_request
                        def _app_control_headers(resp, _extra=extra):
                            try:
                                if roscom_header_used():
                                    for k, v in _extra.items():
                                        resp.headers[k] = v
                            except Exception:
                                pass
                            return resp
        except Exception as exc:
            logger.warning(f"multiloc.roscom_for_request: RoscomVPN routing skipped, the stock rules are served: {exc}")
            found = None
        request.environ[_ENV_ROSCOM] = found
        return found
    except Exception:
        return None


def roscom_header_used() -> str | None:
    """the `routing` header value iff THIS response really carries the RoscomVPN rules (set by xray_json_layout once the layout is complete)"""
    try:
        from flask import has_request_context, request
        if has_request_context():
            return request.environ.get(_ENV_ROSCOM_USED)
    except Exception:
        pass
    return None


def roscom_status() -> dict:
    """for `multiloc_ctl.py roscom status|on`: the switch and, per client, whether the landing copy is usable"""
    custom = _roscom_settings_read().get("custom")
    out = {"bypass_apps": roscom_app_ids(), "provider_id_set": bool(roscom_app_headers().get("providerid")),
           "enabled": roscom_enabled(), "header_lists": roscom_header_lists(), "ru_zone": roscom_ru_zone(), "flag": ROSCOM_FLAG, "settings": ROSCOM_SETTINGS, "dir": ROSCOM_DIR, "source": "custom" if custom else "landing",
           "clients": {}}
    if custom:
        try:
            out["custom_profile"] = _profile_summary(parse_roscom_input(custom))
        except Exception as exc:
            out["custom_profile"] = {"error": str(exc)[:200]}
    for client in ROSCOM_CLIENTS:
        path = os.path.join(ROSCOM_DIR, f"{client}.deeplink")
        info: dict = {"file": path}
        try:
            info["modified"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.stat(path).st_mtime))
            with open(path, encoding="utf-8") as f:
                prof = parse_roscom_deeplink(f.read(_ROSCOM_MAX_TEXT + 1), client)
            info.update(ok=True, profile=prof["Name"], header_profile=ROSCOM_JSON_NAME, last_updated=str(prof.get("LastUpdated", "")),
                        geoip=prof["Geoipurl"], geosite=prof["Geositeurl"], route_order=prof["RouteOrder"], domain_strategy=prof["DomainStrategy"],
                        rules={k: len(prof.get(k, [])) for _, a, b in _ROSCOM_GROUPS for k in (a, b)},
                        header_bytes=len(roscom_header_value(client, prof)),
                        header_rules=sum(len(roscom_header_profile(prof).get(k, [])) for _, a, b in _ROSCOM_GROUPS for k in (a, b)))
        except Exception as exc:
            info.update(ok=False, error=str(exc)[:200])
        out["clients"][client] = info
    return out


def _group_config(base: dict, members: list[dict], label: str, balancer: str, probe: tuple[str, str], roscom: dict | None = None) -> dict:
    """ONE full xray config: the members as outbounds (label, label-2, ...), a leastPing balancer (falling back to the first member until the first
    probe results exist) and a burstObservatory. `base` is the panel's own base config: inbounds, log, fragment/direct/block, RU / private bypass.
    `roscom` (see roscom_for_request) replaces the base routing rules by the RoscomVPN ones."""
    cfg = copy.deepcopy(base)
    outs = []
    for m in members:
        o = copy.deepcopy(m)
        o["tag"] = label if not outs else f"{label}-{len(outs) + 1}"
        outs.append(o)
    cfg["remarks"] = label
    cfg["outbounds"] = outs + list(cfg.get("outbounds", []))
    routing = cfg.setdefault("routing", {})
    if roscom:
        routing["rules"], routing["domainStrategy"] = roscom_rules(roscom["profile"], balancer)
    else:
        rules, final = [], False
        for r in routing.get("rules", []):
            if r.get("outboundTag") == "proxy":            # the base template's catch-all: send it to the balancer instead
                r = {k: v for k, v in r.items() if k != "outboundTag"}
                r["balancerTag"] = balancer
                final = True
            rules.append(r)
        if not final:
            rules.append({"type": "field", "port": "0-65535", "balancerTag": balancer})
        routing["rules"] = rules
    routing["balancers"] = [{"tag": balancer, "selector": [label], "strategy": {"type": "leastPing"}, "fallbackTag": label}]
    cfg["burstObservatory"] = {"subjectSelector": [label],
                               "pingConfig": {"destination": probe[0], "connectivity": "", "interval": CLIENT_PROBE_INTERVAL, "sampling": CLIENT_PROBE_SAMPLING, "timeout": "5s"}}
    return cfg


def xray_json_layout(pairs: list, base_config: dict, user_uuid: str | None) -> list[dict] | None:
    """Hook for hutils.proxy.xrayjson.configs_as_json. pairs = [(proxy dict, xray outbound dict)] in subscription order.
    Returns one full config per visible location, in sort order (AUTO = every legacy transport xray can run, a country = its 3 stable
    transports) - only for the dedicated `/happ/` link - or None to keep the stock list (any other request, feature off, AUTO not shown,
    anything unusual). Happ / INCY read the usage from the subscription headers, so the layout carries no extra "usage" config."""
    try:
        if not layout_client():
            return None                       # every other endpoint keeps the stock list
        rows = visible_rows_for(user_uuid)
        if not any(r.kind == "auto" for r in rows):
            return None
        probe = _probe_settings()
        roscom = roscom_for_request()          # None unless switched on + Happ / INCY + a valid landing copy: then the RoscomVPN rules go into every config
        service_tags = [str(o.get("tag")) for o in base_config.get("outbounds", [])]
        legacy = [o for p, o in pairs if not p.get("multiloc") and o.get("protocol") in XRAY_JSON_PROTOS]
        configs = []
        for r in rows:
            if r.kind == "auto":
                members = legacy
            else:
                members = [o for p, o in pairs if (p.get("multiloc") or {}).get("slot") == int(r.slot) and o.get("protocol") in XRAY_JSON_PROTOS]
            if not members:
                continue
            label = label_of(r)
            if any(t.startswith(label) for t in service_tags):          # the balancer selector matches tags by PREFIX: never let it reach direct/block
                raise ValueError(f"location label {label!r} collides with a service outbound tag")
            configs.append(_group_config(base_config, members, label, f"loc{int(r.slot)}-balancer", probe, roscom))
        if not configs:
            return None
        if roscom:
            from flask import request
            request.environ[_ENV_ROSCOM_USED] = roscom["header"]       # the `routing` header is added only to a response that really carries the rules
        return configs
    except Exception as exc:
        logger.warning(f"multiloc.xray_json_layout failed, serving the stock list: {exc}")
        return None
