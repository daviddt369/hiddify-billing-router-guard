"""Admin V2 dashboard widget: are the exits (countries / external nodes) reachable. GET /api/v2/admin/nodes-status/ (super admin only).

Per location (country) group: the external nodes assigned to it, for each node what the routing health check last saw (online / offline, when, why) and, for the TCP based
transports (vless / trojan), a live TCP connect to the node's host and port with the time it took. WireGuard nodes are UDP: only the health check's verdict is shown.
Results of the live probe are cached for 15 seconds so that several open dashboards do not multiply the connects.
"""
from __future__ import annotations

import concurrent.futures
import datetime
import socket
import threading
import time
from urllib.parse import urlsplit

from flask import jsonify
from flask.views import MethodView

from hiddifypanel.auth import login_required
from hiddifypanel.models import Role

_CACHE: dict = {"t": 0.0, "data": None}
_LOCK = threading.Lock()
_STALE_AFTER_S = 15 * 60


def _endpoint(up) -> tuple[str | None, int | None, str]:
    """(host, port, kind) of a node: kind 'tcp' (probe-able) or 'udp' (wireguard)"""
    try:
        if (up.tunnel_type or "").lower() == "wireguard":
            ep = (up.wg_endpoint or "").strip()
            host, _, port = ep.rpartition(":")
            return (host.strip("[]") or None), (int(port) if port.isdigit() else None), "udp"
        uri = (up.vless_uri if (up.tunnel_type or "").lower() == "vless" else up.trojan_uri) or up.vless_uri or up.trojan_uri or ""
        parts = urlsplit(uri)
        return parts.hostname, (parts.port or 443), "tcp"
    except Exception:
        return None, None, "tcp"


def _tcp_ping(host: str, port: int, timeout: float = 2.0) -> float | None:
    t0 = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return round((time.monotonic() - t0) * 1000, 1)
    except OSError:
        return None


def _collect() -> dict:
    from hiddifypanel.hutils import multiloc as ml
    from hiddifypanel.models.commercial_routing_upstream import CommercialRoutingUpstream as Up

    rows = ml.get_rows()
    by_slot = ml.nodes_by_slot()
    ups = {u.id: u for u in Up.query.order_by(Up.priority.asc(), Up.id.asc()).all()}
    assigned = {uid for ids in by_slot.values() for uid in ids}

    probe_targets = {}
    ends = {}
    for uid, up in ups.items():
        host, port, kind = _endpoint(up)
        ends[uid] = (host, port, kind)
        if kind == "tcp" and host and port:
            probe_targets[(host, port)] = None
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(_tcp_ping, h, p): (h, p) for (h, p) in probe_targets}
        for f in concurrent.futures.as_completed(futs):
            probe_targets[futs[f]] = f.result()

    now = datetime.datetime.utcnow()

    def node_dict(uid):
        up = ups[uid]
        host, port, kind = ends[uid]
        ping = probe_targets.get((host, port)) if kind == "tcp" else None
        status = (up.last_status or "").lower() or "unknown"
        checked = up.last_checked_at
        stale = bool(checked and (now - checked).total_seconds() > _STALE_AFTER_S)
        if not up.enabled:
            state = "off"
        elif kind == "tcp" and ping is None and host:
            state = "down"                              # the live connect failed
        elif status in ("online", "ok", "up"):
            state = "ok"
        elif status in ("offline", "down", "error", "failed"):
            state = "down"
        else:
            state = "unknown"
        return {"id": uid, "name": up.label or up.name, "type": up.tunnel_type, "enabled": bool(up.enabled), "state": state, "ping_ms": ping, "host": host, "port": port,
                "checked_at": checked.isoformat() + "Z" if checked else None, "stale": stale, "error": (up.last_error or "")[:160] or None}

    def group_state(nodes):
        live = [n for n in nodes if n["state"] != "off"]
        if not live:
            return "off"
        ok = [n for n in live if n["state"] == "ok"]
        down = [n for n in live if n["state"] == "down"]
        if len(ok) == len(live):
            return "ok"
        if not ok and down:
            return "down"
        return "partial" if ok else "unknown"

    groups = []
    for r in rows:
        if r.kind != "country":
            continue
        nodes = [node_dict(uid) for uid in by_slot.get(r.slot, []) if uid in ups]
        if not nodes and not (r.title and r.enabled):
            continue                                    # a free slot is not shown
        pings = [n["ping_ms"] for n in nodes if n["ping_ms"] is not None]
        groups.append({"slot": r.slot, "title": ml.label_of(r), "enabled": bool(r.enabled), "state": group_state(nodes), "ping_ms": min(pings) if pings else None,
                       "ok": sum(1 for n in nodes if n["state"] == "ok"), "total": sum(1 for n in nodes if n["state"] != "off"), "nodes": nodes})
    free = [node_dict(uid) for uid in ups if uid not in assigned]
    return {"generated_at": datetime.datetime.utcnow().isoformat() + "Z", "groups": groups, "unassigned": free}


class AdminNodesStatusApi(MethodView):
    decorators = [login_required(roles={Role.super_admin})]

    def get(self):
        with _LOCK:
            if _CACHE["data"] is None or time.monotonic() - _CACHE["t"] > 15:
                try:
                    _CACHE["data"] = _collect()
                    _CACHE["t"] = time.monotonic()
                except Exception as exc:                   # never break the dashboard because of this card
                    return jsonify({"error": str(exc)[:200], "groups": [], "unassigned": []})
            return jsonify(_CACHE["data"])
