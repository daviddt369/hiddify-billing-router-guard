import asyncio
import time
from flask import current_app as app, request, Response
from flask import g
from flask.views import MethodView
from apiflask.fields import Dict
from apiflask import Schema
from hiddifypanel.models.usage import DailyUsage
from hiddifypanel.auth import login_required
from hiddifypanel.models import Role, DailyUsage
from hiddifypanel.panel import hiddify, usage
from hiddifypanel import hutils
import json


class UpdateUserUsageApi(MethodView):
    decorators = [login_required({Role.super_admin})]

    def get(self):
        """System: Update User Usage admin/debug only"""
        forwarded_for = request.headers.get("X-Forwarded-For", "")
        remote_addr = forwarded_for.split(",")[0].strip() if forwarded_for else (request.remote_addr or "-")

        app.logger.warning(
            "UpdateUserUsageApi accessed by super_admin account_id=%s role=%s from ip=%s",
            getattr(g.account, "id", "-"),
            getattr(getattr(g.account, "role", None), "name", getattr(g.account, "role", "-")),
            remote_addr,
        )

        response = Response(
            json.dumps(usage.update_local_usage_not_lock(), indent=2),
            mimetype="application/json",
        )
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response


class AllConfigsApi(MethodView):
    decorators = [login_required({Role.super_admin})]

    def get(self):
        """System: All Configs debug/export only"""
        forwarded_for = request.headers.get("X-Forwarded-For", "")
        remote_addr = forwarded_for.split(",")[0].strip() if forwarded_for else (request.remote_addr or "-")

        app.logger.warning(
            "AllConfigsApi accessed by super_admin account_id=%s role=%s from ip=%s",
            getattr(g.account, "id", "-"),
            getattr(getattr(g.account, "role", None), "name", getattr(g.account, "role", "-")),
            remote_addr,
        )

        response = Response(
            json.dumps(hiddify.all_configs_for_cli(), indent=2),
            mimetype="application/json",
        )
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
