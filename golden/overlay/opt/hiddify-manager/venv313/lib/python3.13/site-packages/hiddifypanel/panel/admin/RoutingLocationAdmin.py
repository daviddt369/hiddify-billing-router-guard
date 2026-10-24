"""RoutingLocationAdmin - minimal UI for multi-location (country selection).  Installed by the multiloc installer.

Adds /routing-admin/locations/ ... routes as one more layer on top of the existing RoutingAdmin class chain
(BusinessAdmin.RoutingAdmin -> RoutingUpstreamAdmin -> RoutingRuleSourceAdmin -> this), the same way those layers were added.
Everything is super_admin only (inherited decorators). All state lives in the three commercial_routing_location* tables.
"""
from __future__ import annotations


from flask import abort, flash, g, redirect, render_template, request
from flask_classful import route
from hiddifypanel.database import db


def _url() -> str:
    return f"/{g.proxy_path}/admin/routing-admin/locations/"


def _happ_url() -> str:
    return f"/{g.proxy_path}/admin/routing-admin/happ/"


def _routing_admin_url() -> str:
    return f"/{g.proxy_path}/admin/routing-admin/"


def _upstreams_url() -> str:
    return f"/{g.proxy_path}/admin/routing-admin/upstreams/"


def _router_state() -> dict:
    """does the live xray-router config already reflect the database? (read only; never breaks the page)"""
    try:
        from hiddifypanel.hutils import multiloc_router
        return multiloc_router.pending()
    except Exception as e:
        return {"in_sync": None, "reason": str(e)[:150]}


def _back_url() -> str:
    """where the "Apply" button was pressed (the admin pages only: never an address from outside)"""
    try:
        from urllib.parse import urlparse
        ref = urlparse(request.referrer or "")
        if ref.path.startswith(_routing_admin_url()) and not ref.path.rstrip("/").endswith(("/apply", "/apply-accept", "/apply-discard")):
            return ref.path + (("?" + ref.query) if ref.query else "")
    except Exception:
        pass
    return _url()


def _int(v, default=0):
    try:
        return int(str(v).strip())
    except Exception:
        return default


def make_routing_admin(Base):
    class RoutingAdmin(Base):
        """previous RoutingAdmin + /locations/ routes"""

        @route("/locations/", methods=["GET"])
        def location_list(self):
            return self._render_locations()

        def _render_locations(self, **extra):
            from hiddifypanel.hutils import multiloc as ml
            from hiddifypanel.models.commercial_routing_upstream import CommercialRoutingUpstream as Up
            if not ml.schema_present():
                flash("Модуль «Страны» не установлен в БД (нет таблиц). Выполните: multiloc_ctl.py init", "danger")
                return redirect(_routing_admin_url())
            _, _, Tester = ml._models()
            rows = ml.get_rows()
            nodes = ml.nodes_by_slot()
            ups = Up.query.order_by(Up.priority.asc(), Up.id.asc()).all()
            node_slot = {uid: slot for slot, ids in nodes.items() for uid in ids}
            users = {}
            try:
                from hiddifypanel.models import User
                users = {str(u.uuid).lower(): u.name for u in User.query.all()}
            except Exception:
                pass
            testers = [{"uuid": t.user_uuid, "note": t.note, "name": users.get(t.user_uuid, "")}
                       for t in Tester.query.order_by(Tester.created_at).all()]
            return render_template(
                "routing-location.html",
                rows=rows, upstreams=ups, node_slot=node_slot, testers=testers, user_choices=sorted(users.items(), key=lambda kv: kv[1]),
                problems=ml.validate_all(), label_of=ml.label_of, flag_for=ml.flag_for, visibilities=ml.VISIBILITIES,
                locations_url=_url(), routing_admin_url=_routing_admin_url(), upstreams_url=_upstreams_url(),
                slots=range(1, ml.MAX_SLOT + 1), router_state=_router_state(), happ_url=_happ_url(), **extra,
            )

        # ------------------------------------------------------------------------------------------ the Happ / INCY page (profile, own domains, apps outside the VPN)
        @route("/happ/", methods=["GET"])
        def happ_page(self):
            return self._render_happ()

        def _render_happ(self, **extra):
            from hiddifypanel.hutils import multiloc as ml
            return render_template(
                "routing-happ.html", roscom=ml.roscom_ui_state(), routing_admin_url=_routing_admin_url(), locations_url=_url(), happ_url=_happ_url(),
                **{"roscom_text": None, "roscom_error": None, "extra_error": None, "apps_error": None, **extra},
            )

        @route("/happ/domains/", methods=["POST"])
        def happ_domains(self):
            """the owner's own domains that travel with the subscription: added to the direct (from the device) and to the proxy (through the VPN) list of the served profile"""
            from hiddifypanel.hutils import multiloc as ml
            f = request.form
            direct, proxy = f.get("extra_direct") or "", f.get("extra_proxy") or ""
            try:
                ml.roscom_set(extra_direct=direct, extra_proxy=proxy)
            except ValueError as exc:
                flash("Домены не сохранены, ничего не изменено: " + str(exc), "danger")
                return self._render_happ(extra_error=str(exc), extra_direct_text=direct, extra_proxy_text=proxy)
            st = ml.roscom_ui_state()
            flash(f"Домены сохранены: напрямую {len(ml.roscom_extra_sites('direct'))}, через VPN {len(ml.roscom_extra_sites('proxy'))}. "
                  "Действуют сразу, при следующем обновлении подписки в приложении.", "success")
            return redirect(_happ_url())

        @route("/happ/apps/", methods=["POST"])
        def happ_apps(self):
            """Android apps that stay OUTSIDE the VPN (they refuse to work under one) + the Happ provider id; sent as response headers to Happ / INCY"""
            from hiddifypanel.hutils import multiloc as ml
            f = request.form
            apps, pid = f.get("apps") or "", (f.get("provider_id") or "").strip()
            try:
                ml.roscom_set(bypass_apps=apps, provider_id=pid)
            except ValueError as exc:
                flash("Список приложений не сохранён, ничего не изменено: " + str(exc), "danger")
                return self._render_happ(apps_error=str(exc), apps_text=apps, provider_id_text=pid)
            n = len(ml.roscom_app_ids())
            flash(f"Сохранено: приложений вне VPN — {n}" + ("." if n else " (список пуст: заголовки не отправляются).") + " Действует сразу, при следующем обновлении подписки в приложении.", "success")
            return redirect(_happ_url())

        @route("/locations/slot/<int:slot>/", methods=["POST"])
        def location_save(self, slot: int):
            from hiddifypanel.hutils import multiloc as ml
            Loc, _, _ = ml._models()
            row = Loc.query.filter_by(slot=slot).first()
            if row is None:
                if slot != 0:
                    abort(404)
                row = Loc(slot=0, kind="auto", code="auto", title="AUTO", sort_order=10, enabled=False, visibility="testers")
                db.session.add(row)
            f = request.form
            row.title = (f.get("title") or "").strip()
            if row.kind == "country":                    # no silent truncation: over-long / malformed values are refused by validate_all()
                row.country_iso = (f.get("country_iso") or "").strip().upper()
                row.code = (f.get("code") or "").strip().lower()
                row.flag = (f.get("flag") or "").strip()
            row.sort_order = _int(f.get("sort_order"), row.sort_order)
            row.enabled = f.get("enabled") == "1"
            row.visibility = f.get("visibility") or row.visibility
            problems = ml.validate_all()
            if problems:
                db.session.rollback()
                flash("Не сохранено: " + "; ".join(problems), "danger")
            else:
                db.session.commit()
                flash(f"Слот {slot} сохранён. Изменения в подписке видны сразу; для маршрутизации нажмите «Применить».", "success")
            return redirect(_url())

        def _apply_assignments(self, cleared=()) -> int:
            """the node -> country selects of the form (nothing is committed here); a node aimed at a slot that is being cleared in the same save ends up without a country"""
            from hiddifypanel.hutils import multiloc as ml
            from hiddifypanel.models.commercial_routing_upstream import CommercialRoutingUpstream as Up
            Loc, Node, _ = ml._models()
            by_slot = {r.slot: r for r in Loc.query.filter_by(kind="country").all()}
            changed = 0
            for up in Up.query.all():
                if f"slot_{up.id}" not in request.form:                       # a node the form did not carry is left alone
                    continue
                want = _int(request.form.get(f"slot_{up.id}"), 0)
                if want in cleared:
                    want = 0
                node = db.session.get(Node, up.id)
                if want == 0:
                    if node is not None:
                        db.session.delete(node)
                        changed += 1
                elif want in by_slot:
                    if node is None:
                        db.session.add(Node(upstream_id=up.id, location_id=by_slot[want].id))
                        changed += 1
                    elif node.location_id != by_slot[want].id:
                        node.location_id = by_slot[want].id
                        changed += 1
            return changed

        @route("/locations/assign/", methods=["POST"])
        def location_assign(self):
            changed = self._apply_assignments()
            db.session.commit()
            flash(f"Назначения нод сохранены (изменено: {changed}). Нажмите «Применить».", "success")
            return redirect(_url())

        @route("/locations/save-all/", methods=["POST"])
        def location_save_all(self):
            """ONE save for the whole page: every slot row (title, country, code, order, switch, visibility, "free the slot") and the node -> country selects.
            Everything is validated together; one problem refuses the whole save and nothing changes."""
            from hiddifypanel.hutils import multiloc as ml
            Loc, Node, _ = ml._models()
            f = request.form
            rows = {r.slot: r for r in Loc.query.all()}
            cleared = set()
            touched = 0
            for slot in range(0, ml.MAX_SLOT + 1):
                if f"title_{slot}" not in f:
                    continue
                row = rows.get(slot)
                if row is None:
                    if slot != 0:
                        continue
                    row = Loc(slot=0, kind="auto", code="auto", title="AUTO", sort_order=10, enabled=False, visibility="testers")
                    db.session.add(row)
                touched += 1
                if row.kind == "country" and f.get(f"clear_{slot}") == "1":          # "free the slot": the same state as a never used one
                    row.title, row.country_iso, row.code, row.flag, row.enabled, row.visibility = "", "", "", "", False, "testers"
                    cleared.add(slot)
                    for n in Node.query.filter_by(location_id=row.id).all():
                        db.session.delete(n)
                    continue
                row.title = (f.get(f"title_{slot}") or "").strip()
                if row.kind == "country":
                    row.country_iso = (f.get(f"country_iso_{slot}") or "").strip().upper()
                    row.code = (f.get(f"code_{slot}") or "").strip().lower()
                row.sort_order = _int(f.get(f"sort_{slot}"), row.sort_order)
                row.enabled = f.get(f"enabled_{slot}") == "1"
                row.visibility = f.get(f"visibility_{slot}") or row.visibility
            changed = self._apply_assignments(cleared)
            problems = ml.validate_all()
            if problems:
                db.session.rollback()
                flash("Не сохранено, ничего не изменено: " + "; ".join(problems), "danger")
                return redirect(_url())
            db.session.commit()
            flash(f"Сохранено: слотов {touched}" + (f", освобождено {len(cleared)}" if cleared else "") + f", назначений нод изменено {changed}. "
                  "Названия и порядок видны в подписке сразу; для маршрутизации нажмите «Применить».", "success")
            return redirect(_url())

        @route("/locations/tester/add/", methods=["POST"])
        def location_tester_add(self):
            import uuid as _uuid
            from hiddifypanel.hutils import multiloc as ml
            _, _, Tester = ml._models()
            raw = (request.form.get("uuid") or "").strip().lower()
            try:
                u = str(_uuid.UUID(raw))
            except Exception:
                flash("Нужен корректный UUID пользователя.", "danger")
                return redirect(_url())
            if db.session.get(Tester, u) is None:
                db.session.add(Tester(user_uuid=u, note=(request.form.get("note") or "")[:128]))
                db.session.commit()
            flash("Тестовый пользователь добавлен.", "success")
            return redirect(_url())

        @route("/locations/tester/remove/", methods=["POST"])
        def location_tester_remove(self):
            from hiddifypanel.hutils import multiloc as ml
            _, _, Tester = ml._models()
            t = db.session.get(Tester, (request.form.get("uuid") or "").strip().lower())
            if t is not None:
                db.session.delete(t)
                db.session.commit()
            flash("Тестовый пользователь удалён.", "success")
            return redirect(_url())

        @route("/happ/roscom/", methods=["POST"])
        def happ_roscom(self):
            return self.location_roscom()

        @route("/locations/roscom/", methods=["POST"])
        def location_roscom(self):
            """the field for the Happ / INCY routing profile (RoscomVPN): the switch and an optional pasted profile that replaces the automatic copy.
            Only the subscription is affected (it answers with the new profile at once); no router apply is involved."""
            from hiddifypanel.hutils import multiloc as ml
            f = request.form
            enabled = f.get("enabled") == "1"
            header_lists = f.get("header_lists") == "1"
            ru_zone = f.get("ru_zone") == "1"
            if f.get("action") == "clear":
                ml.roscom_set(custom=None)
                flash("Вставленный профиль удалён: снова используется автоматический профиль RoscomVPN.", "success")
                return redirect(_happ_url())
            raw = (f.get("custom") or "").strip()
            if raw:
                try:
                    prof = ml.parse_roscom_input(raw)
                except ValueError as exc:
                    flash("Профиль не принят, ничего не изменено: " + str(exc), "danger")
                    return self._render_happ(roscom_text=raw, roscom_error=str(exc))
                ml.roscom_set(enabled=enabled, custom=raw, header_lists=header_lists, ru_zone=ru_zone)
                flash(f"Сохранено: используется вставленный профиль «{prof.get('Name')}» " + ("(включён)." if enabled else "(маршрутизация сейчас ВЫКЛЮЧЕНА)."), "success")
            else:
                ml.roscom_set(enabled=enabled, custom=None, header_lists=header_lists, ru_zone=ru_zone)
                flash("Сохранено: " + ("маршрутизация включена, используется автоматический профиль RoscomVPN." if enabled else "маршрутизация для Happ / INCY выключена."), "success")
            return redirect(_happ_url())

        def before_request(self, name, **kwargs):
            # the addresses the shared "Apply" bar (routing-apply-bar.html) needs on every page of the routing area
            try:
                g.apply_url = _routing_admin_url() + "apply/"
                g.apply_status_url = _routing_admin_url() + "apply-status/"
                g.apply_accept_url = _routing_admin_url() + "apply-accept/"
                g.apply_discard_url = _routing_admin_url() + "apply-discard/"
            except Exception:
                pass
            parent = getattr(super(), "before_request", None)          # flask_classful has no default hook: call the lower layer's only if there is one
            return parent(name, **kwargs) if parent is not None else None

        @route("/apply/", methods=["POST"])
        def apply_all(self):
            # THE "Apply" of the routing area - the same action behind the button on every page: compares the router with the database and does the minimum
            # (nothing / only the countries layer / the full cycle) in the BACKGROUND as a systemd service; the panel never waits for it
            from hiddifypanel.hutils import multiloc_apply
            pl, res = multiloc_apply.apply_now()
            text, category = multiloc_apply.flash_for(res, pl)
            flash(text, category)
            return redirect(_back_url())

        @route("/apply-accept/", methods=["POST"])
        def apply_accept(self):
            # «Применить новый конфиг»: the owner confirmed the config that was built but kept (it differs from the running one in more than his edit)
            from hiddifypanel.hutils import multiloc_apply
            text, category = multiloc_apply.flash_for_decision(multiloc_apply.start("accept"), "accept")
            flash(text, category)
            return redirect(_back_url())

        @route("/apply-discard/", methods=["POST"])
        def apply_discard(self):
            from hiddifypanel.hutils import multiloc_apply
            text, category = multiloc_apply.flash_for_decision(multiloc_apply.start("discard"), "discard")
            flash(text, category)
            return redirect(_back_url())

        @route("/apply-status/", methods=["GET"])
        def apply_status(self):
            from flask import jsonify
            from hiddifypanel.hutils import multiloc_apply
            return jsonify(multiloc_apply.bar())

    return RoutingAdmin
