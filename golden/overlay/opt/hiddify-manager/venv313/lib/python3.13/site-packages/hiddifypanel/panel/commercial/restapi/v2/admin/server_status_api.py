from flask import current_app as app, request
from flask import g
from flask.views import MethodView
from apiflask.fields import Dict
from apiflask import Schema, abort
from hiddifypanel.models.usage import DailyUsage
from hiddifypanel.auth import login_required
from hiddifypanel.models import Role, DailyUsage
from hiddifypanel import hutils


class ServerStatusOutputSchema(Schema):
    stats = Dict(required=True,  metadata={"description": "System stats"})
    usage_history = Dict(required=True,  metadata={"description": "System usage history"})


class AdminServerStatusApi(MethodView):
    decorators = [login_required({Role.super_admin, Role.admin, Role.agent})]

    @staticmethod
    def _empty_stats():
        return {
            'system': {
                'num_cpus': 0,
                'cpu_percent': 0,
                'bytes_sent_cumulative': 0,
                'bytes_recv_cumulative': 0,
                'net_sent_cumulative_GB': 0,
                'ram_used': 0,
                'ram_total': 1,
                'disk_used': 0,
                'disk_total': 1,
                'hiddify_used': 0,
            },
            'top5': {
                'cpu': [["", 0], ["", 0], ["", 0]],
                'ram': [["", 0], ["", 0], ["", 0]],
            },
        }

    @app.output(ServerStatusOutputSchema)  # type: ignore
    def get(self):
        """System: ServerStatus"""
        dto = ServerStatusOutputSchema()
        requested_admin_id = request.args.get("admin_id")

        if g.account.role == Role.super_admin and requested_admin_id:
            admin_id = requested_admin_id
        else:
            admin_id = g.account.id
            if requested_admin_id and str(requested_admin_id) != str(g.account.id):
                abort(403, "Access Denied!")

        if admin_id not in g.account.recursive_sub_admins_ids():
            abort(403, "Access Denied!")

        if g.account.role == Role.super_admin:
            dto.stats = {  # type: ignore
                'system': hutils.system.system_stats(),
                'top5': hutils.system.top_processes()
            }
        else:
            dto.stats = self._empty_stats()  # type: ignore

        dto.usage_history = DailyUsage.get_daily_usage_stats(admin_id)  # type: ignore
        return dto
