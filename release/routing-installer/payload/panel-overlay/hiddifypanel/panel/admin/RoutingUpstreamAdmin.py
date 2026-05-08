"""
RoutingUpstreamAdmin — upstream CRUD routes for routing-admin.
Installed by routing-installer Stage 2B.
Subclasses RoutingAdmin from BusinessAdmin and adds /upstreams/* routes.
__init__.py is patched by install-routing.sh to import this class instead
of the base RoutingAdmin.
"""
from __future__ import annotations

import re

from flask import abort, flash, g, redirect, render_template, request
from flask_classful import route
from hiddifypanel.auth import login_required
from hiddifypanel.database import db
from hiddifypanel.models import Role

from .BusinessAdmin import RoutingAdmin as _BaseRoutingAdmin

_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}$")
_VALID_TUNNEL_TYPES = {"test_blackhole", "vless", "trojan", "wireguard"}


def _validate_upstream(name: str, tunnel_type: str, vless_uri: str,
                        trojan_uri: str, wg_endpoint: str, wg_public_key: str) -> str | None:
    if not name or not _NAME_RE.match(name):
        return "Имя upstream: только латинские буквы, цифры, дефис и подчёркивание (1–64 символа), должно начинаться с буквы или цифры."
    if tunnel_type not in _VALID_TUNNEL_TYPES:
        return "Недопустимый тип туннеля."
    if tunnel_type == "vless" and not vless_uri.strip():
        return "Для типа VLESS укажите VLESS URI (начинается с vless://)."
    if tunnel_type == "trojan" and not trojan_uri.strip():
        return "Для типа Trojan укажите Trojan URI (начинается с trojan://)."
    if tunnel_type == "wireguard" and not wg_endpoint.strip():
        return "Для WireGuard укажите Endpoint (host:port)."
    if tunnel_type == "wireguard" and not wg_public_key.strip():
        return "Для WireGuard укажите публичный ключ узла."
    return None


def _parse_form() -> dict:
    return {
        "name":               request.form.get("name", "").strip(),
        "label":              request.form.get("label", "").strip(),
        "enabled":            request.form.get("enabled") == "1",
        "priority":           max(0, int(request.form.get("priority") or 0)),
        "tunnel_type":        request.form.get("tunnel_type", "test_blackhole").strip(),
        "wg_endpoint":        request.form.get("wg_endpoint", "").strip(),
        "wg_public_key":      request.form.get("wg_public_key", "").strip(),
        "wg_private_key_ref": request.form.get("wg_private_key_ref", "").strip(),
        "wg_addresses":       request.form.get("wg_addresses", "").strip(),
        "wg_mtu":             max(576, min(9000, int(request.form.get("wg_mtu") or 1280))),
        "vless_uri":          request.form.get("vless_uri", "").strip(),
        "trojan_uri":         request.form.get("trojan_uri", "").strip(),
    }


def _upstreams_url() -> str:
    return f"/{g.proxy_path}/admin/routing-admin/upstreams/"


def _routing_admin_url() -> str:
    return f"/{g.proxy_path}/admin/routing-admin/"


class RoutingAdmin(_BaseRoutingAdmin):
    """RoutingAdmin extended with upstream CRUD — installed by routing-installer Stage 2B."""

    route_base = "/routing-admin"
    decorators = [login_required(roles={Role.super_admin})]

    def _default_section(self):
        return "routing"

    # --- Upstream list ---

    @route("/upstreams/", methods=["GET"])
    def upstream_list(self):
        from hiddifypanel.models.commercial_routing_upstream import CommercialRoutingUpstream
        upstreams = (
            CommercialRoutingUpstream.query
            .order_by(CommercialRoutingUpstream.priority.asc(), CommercialRoutingUpstream.id.asc())
            .all()
        )
        return render_template(
            "routing-upstream.html",
            upstreams=upstreams,
            upstream=None,
            upstream_id=None,
            action="list",
            routing_admin_url=_routing_admin_url(),
            upstreams_url=_upstreams_url(),
        )

    # --- Add ---

    @route("/upstreams/add/", methods=["GET", "POST"])
    def upstream_add(self):
        from hiddifypanel.models.commercial_routing_upstream import CommercialRoutingUpstream
        if request.method == "POST":
            data = _parse_form()
            err = _validate_upstream(
                data["name"], data["tunnel_type"],
                data["vless_uri"], data["trojan_uri"],
                data["wg_endpoint"], data["wg_public_key"],
            )
            if err:
                flash(err, "danger")
                return render_template(
                    "routing-upstream.html",
                    upstreams=None, upstream=data, upstream_id=None, action="add",
                    routing_admin_url=_routing_admin_url(), upstreams_url=_upstreams_url(),
                )
            if CommercialRoutingUpstream.query.filter_by(name=data["name"]).first():
                flash(f"Upstream с именем '{data['name']}' уже существует.", "danger")
                return render_template(
                    "routing-upstream.html",
                    upstreams=None, upstream=data, upstream_id=None, action="add",
                    routing_admin_url=_routing_admin_url(), upstreams_url=_upstreams_url(),
                )
            up = CommercialRoutingUpstream(**data)
            db.session.add(up)
            db.session.commit()
            flash(f"Upstream «{up.label or up.name}» добавлен.", "success")
            return redirect(_upstreams_url())

        return render_template(
            "routing-upstream.html",
            upstreams=None, upstream=None, upstream_id=None, action="add",
            routing_admin_url=_routing_admin_url(), upstreams_url=_upstreams_url(),
        )

    # --- Edit ---

    @route("/upstreams/<int:upstream_id>/edit/", methods=["GET", "POST"])
    def upstream_edit(self, upstream_id: int):
        from hiddifypanel.models.commercial_routing_upstream import CommercialRoutingUpstream
        up = db.session.get(CommercialRoutingUpstream, upstream_id)
        if up is None:
            abort(404)

        if request.method == "POST":
            data = _parse_form()
            err = _validate_upstream(
                data["name"], data["tunnel_type"],
                data["vless_uri"], data["trojan_uri"],
                data["wg_endpoint"], data["wg_public_key"],
            )
            if err:
                flash(err, "danger")
                return render_template(
                    "routing-upstream.html",
                    upstreams=None, upstream=data, upstream_id=upstream_id, action="edit",
                    routing_admin_url=_routing_admin_url(), upstreams_url=_upstreams_url(),
                )
            conflict = CommercialRoutingUpstream.query.filter(
                CommercialRoutingUpstream.name == data["name"],
                CommercialRoutingUpstream.id != upstream_id,
            ).first()
            if conflict:
                flash(f"Upstream с именем '{data['name']}' уже существует.", "danger")
                return render_template(
                    "routing-upstream.html",
                    upstreams=None, upstream=data, upstream_id=upstream_id, action="edit",
                    routing_admin_url=_routing_admin_url(), upstreams_url=_upstreams_url(),
                )
            for k, v in data.items():
                setattr(up, k, v)
            db.session.commit()
            flash(f"Upstream «{up.label or up.name}» сохранён.", "success")
            return redirect(_upstreams_url())

        return render_template(
            "routing-upstream.html",
            upstreams=None, upstream=up, upstream_id=upstream_id, action="edit",
            routing_admin_url=_routing_admin_url(), upstreams_url=_upstreams_url(),
        )

    # --- Delete ---

    @route("/upstreams/<int:upstream_id>/delete/", methods=["POST"])
    def upstream_delete(self, upstream_id: int):
        from hiddifypanel.models.commercial_routing_upstream import CommercialRoutingUpstream
        up = db.session.get(CommercialRoutingUpstream, upstream_id)
        if up is None:
            abort(404)
        label = up.label or up.name
        db.session.delete(up)
        db.session.commit()
        flash(f"Upstream «{label}» удалён.", "success")
        return redirect(_upstreams_url())

    # --- Toggle enabled ---

    @route("/upstreams/<int:upstream_id>/toggle/", methods=["POST"])
    def upstream_toggle(self, upstream_id: int):
        from hiddifypanel.models.commercial_routing_upstream import CommercialRoutingUpstream
        up = db.session.get(CommercialRoutingUpstream, upstream_id)
        if up is None:
            abort(404)
        up.enabled = not up.enabled
        db.session.commit()
        state = "включён" if up.enabled else "выключен"
        flash(f"Upstream «{up.label or up.name}» {state}.", "success")
        return redirect(_upstreams_url())

    # --- Priority: move up (lower number) ---

    @route("/upstreams/<int:upstream_id>/move-up/", methods=["POST"])
    def upstream_move_up(self, upstream_id: int):
        from hiddifypanel.models.commercial_routing_upstream import CommercialRoutingUpstream
        up = db.session.get(CommercialRoutingUpstream, upstream_id)
        if up is None:
            abort(404)
        if up.priority > 0:
            up.priority -= 1
            db.session.commit()
        return redirect(_upstreams_url())

    # --- Priority: move down (higher number) ---

    @route("/upstreams/<int:upstream_id>/move-down/", methods=["POST"])
    def upstream_move_down(self, upstream_id: int):
        from hiddifypanel.models.commercial_routing_upstream import CommercialRoutingUpstream
        up = db.session.get(CommercialRoutingUpstream, upstream_id)
        if up is None:
            abort(404)
        up.priority += 1
        db.session.commit()
        return redirect(_upstreams_url())
