#!/opt/hiddify-manager/.venv313/bin/python
"""multiloc_ctl - manage multi-location (country selection) without a UI.

  multiloc_ctl.py init                                   create the 3 tables + 4 empty slot rows (idempotent)
  multiloc_ctl.py status [--json]                        slots, assigned nodes, testers, validation problems
  multiloc_ctl.py set <slot 1-4> [--title T] [--iso DE] [--code de] [--flag X] [--order N] [--enabled 0|1]
                                 [--visibility hidden|testers|all]
  multiloc_ctl.py auto [--title AUTO] [--order N] [--enabled 0|1] [--visibility ...]    the AUTO button (slot 0): on the dedicated <user link>/happ/ link (Happ / INCY)
                                                         ONE button holding every other protocol, countries are one button each (3 stable protocols inside); the usual link is unchanged
  multiloc_ctl.py assign <slot 1-4> <upstream_id> [<upstream_id> ...]
  multiloc_ctl.py unassign <upstream_id> [...]
  multiloc_ctl.py tester add <uuid> [--note TEXT] | remove <uuid> | list
  multiloc_ctl.py suggest-iso <upstream_id>              GeoIP guess of the node country (uses the panel's GeoLite2 file)
  multiloc_ctl.py apply [--full] [--accept-diff]         router apply (--accept-diff: apply whatever the pipeline built; without it a config that differs from the running one in
                                                         more than the pending change is only KEPT: rc 4, then --accept-staged / --discard-staged, or the panel's two buttons). Default: ONLY the multi-location layer on the live router config (one xray-router
                                                         restart, waits until the entrances really listen, restores the old config if not).
                                                         --full: the whole legacy pipeline (commercial-routing-apply: re-renders rules/nodes from the DB) - run so that the
                                                         router restarts ONCE at the end (private copy of /etc/xray-router, restarts deferred); --classic: the old way,
                                                         every layer restarts the router itself (~6 restarts)
                                                         Only ONE router apply runs at a time (the panel's buttons start theirs in the background: scripts/multiloc_router_apply.py)
  multiloc_ctl.py roscom on|off|status|lists|empty [--json] [--force]  RoscomVPN routing for the Happ / INCY JSON link (<user link>/happ/): header `routing` + the RoscomVPN rules
                                                         inside every JSON config. Source: the routing landing's copy of the upstream profile
                                                         (/var/lib/routing-links/last-good/{happ,incy}.deeplink). Default OFF; takes effect at once (no restart)
  multiloc_ctl.py apply --auto                           what the panel's single "Apply" button runs: compares the router with the database and does the
                                                         minimum - nothing / only the countries layer (one restart) / the full legacy pipeline
  multiloc_ctl.py baseline                               remember the current inputs of the legacy pipeline as "the router reflects them" (once, after the install)
  multiloc_ctl.py drop-schema --yes                      ROLLBACK: drops only the 3 multiloc tables

Changing title / visibility / enabled / assignment takes effect in subscriptions immediately (cache: <=5 min) ; a changed
node assignment or enable flag needs `apply` (router; `status` shows PENDING until it was done). Nothing here restarts hiddify-xray or reloads HAProxy.
A node that was added/enabled in the panel after the last FULL apply is not in the live router config: use `apply --full` for that.
"""
import argparse
import json
import os
import subprocess
import sys
import time

os.environ.setdefault("HIDDIFY_CFG_PATH", "/opt/hiddify-manager/hiddify-panel/app.cfg")
os.environ.setdefault("HIDDIFY_CONFIG_PATH", "/opt/hiddify-manager/")
os.chdir("/opt/hiddify-manager/hiddify-panel")


def _app():
    import sqlalchemy
    import hiddifypanel.panel.init_db as idb
    idb.text = getattr(idb, "text", sqlalchemy.text)   # keeps CLI usable on a fresh DB (see stand notes); no-op on prod
    from hiddifypanel import create_app
    return create_app(app_mode="cli")


def cmd_init(a, ml, db):
    print(json.dumps(ml.ensure_schema(), indent=1))


def _status(ml, db):
    from hiddifypanel.models.commercial_routing_upstream import CommercialRoutingUpstream as Up
    if not ml.schema_present():
        return {"installed": False}
    ups = {u.id: u for u in Up.query.all()}
    nodes = ml.nodes_by_slot()
    rows = []
    for r in ml.get_rows():
        rows.append({
            "slot": r.slot, "kind": r.kind, "code": r.code, "title": r.title, "iso": r.country_iso,
            "flag": r.flag or ml.flag_for(r.country_iso), "order": r.sort_order, "enabled": bool(r.enabled),
            "visibility": r.visibility,
            "nodes": [{"id": i, "name": ups[i].name if i in ups else "?", "enabled": bool(ups[i].enabled) if i in ups else None}
                      for i in nodes.get(int(r.slot), [])],
            "inbounds": {k: ml.inbound_tag(r.slot, k) for k in ml.INBOUNDS} if r.kind == "country" else {},
        })
    unassigned = [{"id": u.id, "name": u.name} for u in ups.values() if not any(u.id in v for v in nodes.values())]
    _, _, Tester = ml._models()
    return {"installed": True, "locations": rows, "unassigned_nodes": unassigned,
            "testers": [t.user_uuid for t in Tester.query.order_by(Tester.created_at).all()],
            "problems": ml.validate_all(), "router": _router_state()}


def _router_state():
    try:
        from hiddifypanel.hutils import multiloc_router
        return multiloc_router.pending()
    except Exception as e:      # status must stay usable when the router config is unreadable
        return {"in_sync": None, "reason": str(e)[:200]}


def cmd_status(a, ml, db):
    st = _status(ml, db)
    if a.json:
        print(json.dumps(st, indent=1, ensure_ascii=False))
        return
    if not st["installed"]:
        print("multiloc: NOT installed (tables missing) - run: multiloc_ctl.py init")
        return
    for r in st["locations"]:
        flag = r["flag"] or " "
        nodes = ",".join(f"{n['id']}:{n['name']}" for n in r["nodes"]) or "-"
        print(f"slot {r['slot']} [{r['kind']:7}] {'ON ' if r['enabled'] else 'off'} vis={r['visibility']:8} order={r['order']:<4} "
              f"{flag} {r['title'] or '(unnamed)'} code={r['code'] or '-'} nodes={nodes}")
    print("unassigned nodes:", ", ".join(f"{n['id']}:{n['name']}" for n in st["unassigned_nodes"]) or "-")
    print("testers:", ", ".join(st["testers"]) or "-")
    print("problems:", "; ".join(st["problems"]) or "none")
    rt = st.get("router") or {}
    if rt.get("in_sync") is True:
        print("router: in sync with the database")
    elif rt.get("in_sync") is False:
        print("router: PENDING - run: multiloc_ctl.py apply")
    else:
        print("router: unknown (" + str(rt.get("reason")) + ")")
    for slot, ids in sorted((rt.get("missing_nodes") or {}).items()):
        print(f"router: slot {slot}: nodes {ids} are not in the live router config - run: multiloc_ctl.py apply --full")
    try:                                                    # the panel's background apply (buttons): what it did last / is doing now
        from hiddifypanel.hutils import multiloc_apply
        ba = multiloc_apply.status(tail=0)
        if ba.get("state") != "idle":
            print(f"background apply: {ba.get('state_text')} ({ba.get('mode_text')}), started {ba.get('started_text', '-')}"
                  + (f", {ba.get('summary')}" if ba.get("summary") and ba.get("state") != "running" else ""))
    except Exception:
        pass


def _row(ml, slot, kind="country"):
    Loc, _, _ = ml._models()
    return Loc.query.filter_by(slot=slot).first()


def _commit_checked(ml, db):
    problems = ml.validate_all()
    if problems:
        db.session.rollback()
        print("REFUSED:", "; ".join(problems), file=sys.stderr)
        sys.exit(2)
    db.session.commit()


def cmd_set(a, ml, db):
    r = _row(ml, a.slot)
    if r is None or r.kind != "country":
        sys.exit(f"slot {a.slot} does not exist - run init first")
    if a.title is not None:
        r.title = a.title
    if a.iso is not None:
        r.country_iso = a.iso.upper()
    if a.code is not None:
        r.code = a.code.lower()
    if a.flag is not None:
        r.flag = a.flag
    if a.order is not None:
        r.sort_order = a.order
    if a.visibility is not None:
        r.visibility = a.visibility
    if a.enabled is not None:
        r.enabled = bool(a.enabled)
    _commit_checked(ml, db)
    print("ok:", ml.label_of(r), "enabled" if r.enabled else "disabled", r.visibility)


def cmd_auto(a, ml, db):
    Loc, _, _ = ml._models()
    r = Loc.query.filter_by(slot=0).first()
    if r is None:
        r = Loc(slot=0, kind="auto", code="auto", title="AUTO", sort_order=10, enabled=False, visibility="testers")
        db.session.add(r)
    if a.title is not None:
        r.title = a.title
    if a.order is not None:
        r.sort_order = a.order
    if a.visibility is not None:
        r.visibility = a.visibility
    if a.enabled is not None:
        r.enabled = bool(a.enabled)
    _commit_checked(ml, db)
    print("ok: AUTO block", "enabled" if r.enabled else "disabled", r.visibility, "title=", r.title)


def cmd_assign(a, ml, db):
    from hiddifypanel.models.commercial_routing_upstream import CommercialRoutingUpstream as Up
    Loc, Node, _ = ml._models()
    r = _row(ml, a.slot)
    if r is None or r.kind != "country":
        sys.exit(f"slot {a.slot} does not exist")
    for uid in a.upstream_ids:
        if Up.query.get(uid) is None:
            sys.exit(f"upstream {uid} does not exist")
        n = Node.query.get(uid)
        if n is None:
            db.session.add(Node(upstream_id=uid, location_id=r.id))
        else:
            n.location_id = r.id
    db.session.commit()
    print("assigned", a.upstream_ids, "-> slot", a.slot, "(router apply needed)")


def cmd_unassign(a, ml, db):
    _, Node, _ = ml._models()
    for uid in a.upstream_ids:
        n = Node.query.get(uid)
        if n is not None:
            db.session.delete(n)
    db.session.commit()
    print("unassigned", a.upstream_ids, "(router apply needed)")


def cmd_tester(a, ml, db):
    _, _, T = ml._models()
    if a.action == "list":
        for t in T.query.order_by(T.created_at).all():
            print(t.user_uuid, t.note)
        return
    u = a.uuid.strip().lower()
    import uuid as _uuid
    _uuid.UUID(u)  # validates
    t = T.query.get(u)
    if a.action == "add":
        if t is None:
            db.session.add(T(user_uuid=u, note=a.note or ""))
        elif a.note is not None:
            t.note = a.note
    elif a.action == "remove" and t is not None:
        db.session.delete(t)
    db.session.commit()
    print("ok:", a.action, u)


def cmd_suggest_iso(a, ml, db):
    from urllib.parse import urlparse
    from hiddifypanel.models.commercial_routing_upstream import CommercialRoutingUpstream as Up
    u = Up.query.get(a.upstream_id)
    if u is None:
        sys.exit("no such upstream")
    uri = u.vless_uri or u.trojan_uri or ""
    host = urlparse(uri).hostname if uri else (u.wg_endpoint.rsplit(":", 1)[0] if u.wg_endpoint else "")
    try:
        import socket
        ip = socket.gethostbyname(host)
        from hiddifypanel.hutils.network import auto_ip_selector
        print(json.dumps({"host": host, "ip": ip, "iso": auto_ip_selector.get_country(ip)}))
    except Exception as e:
        print(json.dumps({"host": host, "error": str(e)}))


def _router_apply_lock():
    """ONE router apply at a time: the same lock as the panel's background apply (scripts/multiloc_router_apply.py). The background run itself calls this
    command while holding the lock and says so in its environment - then there is nothing to take."""
    import contextlib
    import fcntl
    if os.environ.get("MULTILOC_APPLY_LOCK_HELD") == "1":
        return contextlib.nullcontext()

    @contextlib.contextmanager
    def lock():
        fd = os.open("/run/multiloc-router-apply.lock", os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            sys.exit("another router apply is running (started from the panel or from another console): wait for it - "
                     "multiloc_router_apply.py status")
        try:
            yield
        finally:
            os.close(fd)                      # closing the descriptor releases the lock
    return lock()


def cmd_apply(a, ml, db):
    with _router_apply_lock():
        _do_apply(a, ml, db)


def _do_apply(a, ml, db):
    from hiddifypanel.hutils import multiloc_apply
    if getattr(a, "discard_staged", False):
        from hiddifypanel.hutils import multiloc_full
        multiloc_full.discard_staged()
        print("the finished config that was waiting for a decision is discarded; the running router was not touched", flush=True)
        return
    if getattr(a, "accept_staged", False):
        from hiddifypanel.hutils import multiloc_full
        parts_now = multiloc_apply.legacy_parts()
        rc = multiloc_full.accept(parts_now, log=lambda m: print(m, flush=True))
        if rc == 0:
            multiloc_apply.record_applied(parts_now, "accepted", multiloc_apply.legacy_meta())
        sys.exit(rc)
    pl = None
    if getattr(a, "auto", False):
        # the panel's "Apply": decide by comparing what the router reflects with the database (exact, see multiloc_apply.plan)
        pl = multiloc_apply.plan()
        print(json.dumps({"plan": pl["action"], "legacy": pl["legacy"], "countries": pl["countries"], "changed": pl["changed"]}, ensure_ascii=False), flush=True)
        if pl["action"] == "none":
            print(json.dumps({"changed": False, "reason": "nothing to apply: the router already reflects the database"}))
            return
        a.full = pl["action"] in ("full", "confirm")
    if a.full:
        from hiddifypanel.hutils import multiloc_full
        parts = multiloc_apply.legacy_parts()            # what the pipeline is about to read: remembered as applied only if the whole run succeeds
        meta = multiloc_apply.legacy_meta()
        if pl is None:
            pl = multiloc_apply.plan()
        expected = set(pl.get("changed") or []) | ({"countries"} if pl.get("countries") not in ("clean", None) else set())      # what the owner edited = what may differ
        rc = multiloc_full.run(classic_only=getattr(a, "classic", False), log=lambda m: print(m, flush=True), expected=expected, parts=parts,
                               accept_diff=getattr(a, "accept_diff", False))      # the legacy pipeline, ONE router restart (or: kept for a decision, rc 4)
        if rc == 0:
            multiloc_apply.record_applied(parts, "full", meta)
        sys.exit(rc)
    from hiddifypanel.hutils import multiloc_router
    try:
        info = multiloc_router.patch_config()
    except Exception as e:
        print("FAILED:", e, file=sys.stderr)
        sys.exit(2)
    for w in info.pop("warnings", []):
        print("WARNING:", w, file=sys.stderr)
    print(json.dumps(info, ensure_ascii=False))


def cmd_baseline(a, ml, db):
    """record the CURRENT inputs of the legacy pipeline as 'the router reflects them' (use once after the install, when the router is known to be in sync)"""
    from hiddifypanel.hutils import multiloc_apply
    parts = multiloc_apply.legacy_parts()
    multiloc_apply.record_applied(parts, "baseline", multiloc_apply.legacy_meta())
    print("recorded as applied:", json.dumps(parts, indent=1))


def _panel_can_read(path):
    """can the panel's own user read the file? (root reads everything, so ask as hiddify-panel; None = could not ask)"""
    try:
        return subprocess.run(["runuser", "-u", "hiddify-panel", "--", "test", "-r", path], capture_output=True, timeout=10).returncode == 0
    except Exception:
        return None


def cmd_roscom(a, ml, db):
    st = ml.roscom_status()
    for info in st["clients"].values():
        info["panel_can_read"] = _panel_can_read(info["file"]) if info.get("ok") else None
    if a.action in ("lists", "empty", "zone-on", "zone-off"):
        if a.action in ("lists", "empty"):
            ml.roscom_set(header_lists=(a.action == "lists"))
        else:
            ml.roscom_set(ru_zone=(a.action == "zone-on"))
        st = ml.roscom_status()
        for info in st["clients"].values():
            info["panel_can_read"] = _panel_can_read(info["file"]) if info.get("ok") else None
    if a.action in ("on", "off"):
        if a.action == "on":
            own = st.get("custom_profile")
            if st["source"] == "custom":
                bad = {"custom profile": own.get("error")} if (own or {}).get("error") else {}
            else:
                bad = {c: (i.get("error") or "the panel user cannot read the file") for c, i in st["clients"].items() if not i.get("ok") or i.get("panel_can_read") is False}
            if bad and not a.force:
                sys.exit("refusing to switch on, unusable profile: " + "; ".join(f"{c}: {e}" for c, e in bad.items()) + "  (--force to override)")
            ml.roscom_set(enabled=True)
        else:
            ml.roscom_set(enabled=False)
        if os.path.exists(ml.ROSCOM_FLAG):                       # the v3 flag file: the settings file decides from now on, a stale flag would only confuse
            os.remove(ml.ROSCOM_FLAG)
        st = ml.roscom_status()
        for info in st["clients"].values():
            info["panel_can_read"] = _panel_can_read(info["file"]) if info.get("ok") else None
    if a.json:
        print(json.dumps(st, indent=1, ensure_ascii=False))
        return
    print(f"RoscomVPN routing in the Happ / INCY JSON link: {'ON' if st['enabled'] else 'off'}  (settings {st['settings']}; also settable on the panel page 'Страны / локации')")
    print("  header profile: " + ("WITH the lists (like the ordinary RoscomVPN profile; the apps cut the traffic by them)" if st.get("header_lists") else "empty JSONSUB variant (no lists)")
          + "  - change: multiloc_ctl.py roscom lists | empty")
    print("  RU zones (.ru .su .рф) + banks: " + ("DIRECT from the device (added to the profile's direct list)" if st.get("ru_zone") else "as the profile says (not forced)") + "  - change: multiloc_ctl.py roscom zone-on | zone-off")
    if st["source"] == "custom":
        cp = st.get("custom_profile") or {}
        print("  SOURCE: the profile pasted on the panel page " + (f"- UNUSABLE: {cp['error']}" if cp.get("error") else
              f"- {cp['name']!r}, updated {cp['last_updated']}, order {cp['route_order']}, rules direct/proxy/block {cp['counts']['direct']}/{cp['counts']['proxy']}/{cp['counts']['block']}"))
    else:
        print("  SOURCE: the routing landing's automatic copy (refreshed there every 6 h)")
    for c, i in st["clients"].items():
        if i.get("ok"):
            print(f"  {c}: landing copy usable, from {i['modified']}; upstream profile {i['profile']!r} LastUpdated={i['last_updated']}; "
                  f"header profile {i['header_profile']!r} ({i['header_bytes']} bytes); rules in total {sum(i['rules'].values())}; "
                  f"the panel user can read it: {i['panel_can_read']}")
        else:
            print(f"  {c}: landing copy NOT usable - {i['error']}")


def cmd_drop(a, ml, db):
    if not a.yes:
        sys.exit("refusing without --yes")
    print("dropped:", ml.drop_schema())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("init").set_defaults(f=cmd_init)
    s = sp.add_parser("status"); s.add_argument("--json", action="store_true"); s.set_defaults(f=cmd_status)
    s = sp.add_parser("set"); s.add_argument("slot", type=int)
    for opt, typ in (("--title", str), ("--iso", str), ("--code", str), ("--flag", str), ("--order", int), ("--enabled", int),
                     ("--visibility", str)):
        s.add_argument(opt, type=typ)
    s.set_defaults(f=cmd_set)
    s = sp.add_parser("auto")
    for opt, typ in (("--title", str), ("--order", int), ("--enabled", int), ("--visibility", str)):
        s.add_argument(opt, type=typ)
    s.set_defaults(f=cmd_auto)
    s = sp.add_parser("assign"); s.add_argument("slot", type=int); s.add_argument("upstream_ids", type=int, nargs="+"); s.set_defaults(f=cmd_assign)
    s = sp.add_parser("unassign"); s.add_argument("upstream_ids", type=int, nargs="+"); s.set_defaults(f=cmd_unassign)
    s = sp.add_parser("tester"); s.add_argument("action", choices=["add", "remove", "list"]); s.add_argument("uuid", nargs="?")
    s.add_argument("--note"); s.set_defaults(f=cmd_tester)
    s = sp.add_parser("suggest-iso"); s.add_argument("upstream_id", type=int); s.set_defaults(f=cmd_suggest_iso)
    s = sp.add_parser("apply"); s.add_argument("--full", action="store_true"); s.add_argument("--auto", action="store_true"); s.add_argument("--classic", action="store_true")
    s.add_argument("--accept-diff", action="store_true"); s.add_argument("--accept-staged", action="store_true"); s.add_argument("--discard-staged", action="store_true"); s.set_defaults(f=cmd_apply)
    sp.add_parser("baseline").set_defaults(f=cmd_baseline)
    s = sp.add_parser("roscom"); s.add_argument("action", choices=["on", "off", "status", "lists", "empty", "zone-on", "zone-off"]); s.add_argument("--json", action="store_true")
    s.add_argument("--force", action="store_true"); s.set_defaults(f=cmd_roscom)
    s = sp.add_parser("drop-schema"); s.add_argument("--yes", action="store_true"); s.set_defaults(f=cmd_drop)
    a = ap.parse_args()

    app = _app()
    with app.app_context():
        from hiddifypanel.database import db
        from hiddifypanel.hutils import multiloc as ml
        if a.cmd not in ("init", "drop-schema", "apply", "status", "roscom", "baseline") and not ml.schema_present():
            sys.exit("multiloc is not installed: run `multiloc_ctl.py init` first")
        a.f(a, ml, db)


if __name__ == "__main__":
    main()
