from flask import render_template, request, redirect, g
from . import fix_flaskadmin_babel
import flask_admin
from flask_admin import Admin
from hiddifypanel import Events
from .DomainAdmin import DomainAdmin
from .AdminstratorAdmin import AdminstratorAdmin
import logging


from hiddifypanel.database import db
from hiddifypanel.models import *
from apiflask import APIBlueprint
from flask_adminlte3 import AdminLTE3
from hiddifypanel.panel.commercial import capabilities

flask_bp = APIBlueprint("flask", __name__, template_folder="templates", enable_openapi=False)
admin_bp = APIBlueprint("admin", __name__, template_folder="templates", enable_openapi=False)

flaskadmin = Admin(endpoint="admin", base_template='flaskadmin-layout.html',
                   translations_path="/opt/hiddify-develop/hiddify-panel/src/hiddifypanel/translations/")
logger = logging.getLogger(__name__)
OPTIONAL_ADMIN_MODULE_MESSAGES = {
    "hiddifypanel.panel.admin.PlanAdmin": "Tariffs UI deferred because PlanAdmin runtime dependencies are incomplete",
    "hiddifypanel.panel.admin.AntiShareAdmin": "Anti-share admin views disabled: optional module missing",
}


def init_app(app):
    business_admin_views_enabled = False
    business_settings_view_enabled = False
    plan_admin_view_enabled = False
    routing_admin_view_enabled = False
    antishare_admin_view_enabled = False

    @app.context_processor
    def inject_commercial_capabilities():
        try:
            hconfigs = get_hconfigs()
        except Exception:
            hconfigs = {}
        return {
            "business_capability_enabled": capabilities.business_enabled(hconfigs),
            "routing_capability_enabled": capabilities.routing_enabled(hconfigs),
            "antishare_capability_enabled": capabilities.antishare_enabled(hconfigs),
            "business_admin_views_enabled": business_admin_views_enabled,
            "business_settings_view_enabled": business_settings_view_enabled,
            "plan_admin_view_enabled": plan_admin_view_enabled,
            "routing_admin_view_enabled": routing_admin_view_enabled,
            "antishare_admin_view_enabled": antishare_admin_view_enabled,
        }

    from .UserAdmin import UserAdmin
    # admin_secret=StrConfig.query.filter(StrConfig.key==ConfigEnum.admin_secret).first()
    #
    # return
    # admin = Admin(endpoint="admin",index_view=Dashboard(),base_template='lte-master.html',static_url_path="/static/")
    flaskadmin.template_mode = "bootstrap4"
    flaskadmin.init_app(flask_bp)
    adminlte = AdminLTE3()
    adminlte.init_app(app)

    Events.admin_prehook.notify(flaskadmin=flaskadmin, admin_bp=admin_bp)

    @app.route('/<proxy_path>/admin')
    @app.doc(hide=True)
    def auto_route(proxy_path=None, user_secret=None):
        return redirect(request.url.replace("http://", "https://") + "/")

    try:
        with app.app_context():
            hconfigs = get_hconfigs()
            business_enabled = capabilities.business_enabled(hconfigs)
            routing_enabled = capabilities.routing_enabled(hconfigs)
            antishare_enabled = capabilities.antishare_enabled(hconfigs)
    except Exception:
        business_enabled = False
        routing_enabled = False
        antishare_enabled = False

    PlanAdmin = None
    if business_enabled:
        try:
            from .PlanAdmin import PlanAdmin
        except ModuleNotFoundError as exc:
            if exc.name == "hiddifypanel.panel.admin.PlanAdmin":
                logger.warning(OPTIONAL_ADMIN_MODULE_MESSAGES.get(exc.name, "Business admin views disabled: optional module missing"))
            else:
                logger.exception("Business plan/subscription admin views disabled due to unexpected import failure.")
        except Exception:
            logger.exception("Business plan/subscription admin views disabled due to unexpected import failure.")
        if PlanAdmin and getattr(PlanAdmin, "runtime_ready", True):
            plan_admin_view_enabled = True
            business_admin_views_enabled = True
        elif PlanAdmin:
            logger.warning("Tariffs UI deferred because PlanAdmin runtime dependencies are incomplete")

    flaskadmin.add_view(UserAdmin(User, db.session))
    if business_enabled and plan_admin_view_enabled and PlanAdmin:
        flaskadmin.add_view(PlanAdmin(CommercialPlan, db.session, name="Plans", endpoint="plans"))
    flaskadmin.add_view(DomainAdmin(Domain, db.session))
    flaskadmin.add_view(AdminstratorAdmin(AdminUser, db.session))
    from .NodeAdmin import NodeAdmin
    flaskadmin.add_view(NodeAdmin(Child, db.session))
    from .Dashboard import Dashboard
    from .SettingAdmin import SettingAdmin
    BusinessAdmin = None
    RoutingAdmin = None
    if business_enabled or routing_enabled:
        try:
            from .BusinessAdmin import BusinessAdmin, RoutingAdmin
        except Exception:
            logger.exception("Business routing admin is unavailable in current runtime layout; disabling business startup registration.")
            business_enabled = False
            routing_enabled = False
    try:
        from .AntiShareAdmin import AntiShareAdmin
    except ModuleNotFoundError as exc:
        AntiShareAdmin = None
        antishare_enabled = False
        logger.warning(OPTIONAL_ADMIN_MODULE_MESSAGES.get(exc.name, "Anti-share admin views disabled: optional module missing"))
    except Exception:
        AntiShareAdmin = None
        antishare_enabled = False
    from .commercial_info import CommercialInfo
    from .ProxyAdmin import ProxyAdmin
    from .Actions import Actions
    from .Backup import Backup
    from .QuickSetup import QuickSetup
    Dashboard.register(admin_bp, route_base="/")
    SettingAdmin.register(admin_bp)
    if business_enabled and BusinessAdmin:
        BusinessAdmin.register(admin_bp)
        business_settings_view_enabled = True
    if routing_enabled and RoutingAdmin:
        RoutingAdmin.register(admin_bp)
        routing_admin_view_enabled = True
    if antishare_enabled and AntiShareAdmin:
        AntiShareAdmin.register(admin_bp)
        antishare_admin_view_enabled = True
    ProxyAdmin.register(admin_bp)
    Actions.register(admin_bp)
    CommercialInfo.register(admin_bp)
    QuickSetup.register(admin_bp)
    Backup.register(admin_bp)

    # admin_bp.add_url_rule('/admin/quicksetup/',endpoint="quicksetup",view_func=QuickSetup.index,methods=["GET"])
    # admin_bp.add_url_rule('/admin/quicksetup/',endpoint="quicksetup-save", view_func=QuickSetup.save,methods=["POST"])

    app.add_url_rule("/<proxy_path>/admin/static/<filename>/", endpoint="admin.static")  # fix bug in admin with blueprint

    flask_bp.debug = True
    app.register_blueprint(admin_bp, url_prefix=f"/<proxy_path>/admin/",)
    app.register_blueprint(admin_bp, name=f'child_{admin_bp.name}', url_prefix=f"/<proxy_path>/<int:child_id>/admin/")
    app.register_blueprint(flask_bp, url_prefix=f"/<proxy_path>/")
    app.register_blueprint(flask_bp, name=f'child_{flask_bp.name}', url_prefix=f"/<proxy_path>/<int:child_id>/")
