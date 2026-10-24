import datetime

from apiflask import abort
from flask import g, request
from flask_babel import lazy_gettext as _
from wtforms.validators import NumberRange

from hiddifypanel.auth import login_required
from hiddifypanel.models import AdminUser, CommercialSubscription, PaymentProvider, Role, UserMode
from hiddifypanel.panel import custom_widgets

from .adminlte import AdminLTEModelView


class SubscriptionAdmin(AdminLTEModelView):
    column_default_sort = ("id", False)
    column_list = [
        "id",
        "user",
        "plan",
        "status",
        "start_date",
        "end_date",
        "auto_renew",
        "billing_amount",
        "billing_currency",
        "payment_provider",
    ]
    column_searchable_list = ["external_payment_id", "note"]
    column_sortable_list = ["id", "start_date", "end_date", "billing_amount"]
    form_columns = [
        "user",
        "plan",
        "start_date",
        "end_date",
        "auto_renew",
        "usage_limit",
        "package_days",
        "max_ips",
        "mode",
        "billing_amount",
        "billing_currency",
        "payment_provider",
        "external_payment_id",
        "note",
    ]
    form_overrides = {
        "usage_limit": custom_widgets.UsageField,
        "mode": custom_widgets.EnumSelectField,
        "payment_provider": custom_widgets.EnumSelectField,
    }
    form_widget_args = {
        "usage_limit": {"min": "0"},
        "package_days": {"min": "1"},
        "max_ips": {"min": "1", "max": "10"},
        "billing_amount": {"min": "0"},
    }
    form_args = {
        "mode": {"enum": UserMode},
        "payment_provider": {"enum": PaymentProvider},
        "package_days": {"validators": [NumberRange(min=1, max=36500)]},
        "max_ips": {"validators": [NumberRange(min=1, max=10)]},
        "billing_amount": {"validators": [NumberRange(min=0, max=1000000000)]},
    }
    column_labels = {
        "user": _("User"),
        "plan": _("Plan"),
        "status": _("Status"),
        "start_date": _("Start Date"),
        "end_date": _("End Date"),
        "auto_renew": _("Auto renew"),
        "usage_limit": _("user.usage_limit_GB"),
        "package_days": _("Package Days"),
        "max_ips": _("Max IPs"),
        "mode": _("Mode"),
        "billing_amount": _("Price"),
        "billing_currency": _("Currency"),
        "payment_provider": _("Payment Provider"),
        "external_payment_id": _("External Payment Id"),
        "note": _("Note"),
    }
    column_descriptions = {
        "payment_provider": _("Prepared for future payment integrations such as YooKassa."),
        "external_payment_id": _("Will be used later for provider-side payment reconciliation."),
    }

    def search_placeholder(self):
        return "search payment note"

    def is_accessible(self):
        if login_required(roles={Role.super_admin, Role.admin})(lambda: True)() != True:
            return False
        return True

    def _status_formatter(view, context, model, name):
        status = model.status
        color = {
            "active": "success",
            "draft": "secondary",
            "expired": "danger",
            "suspended": "warning",
            "canceled": "dark",
        }.get(str(status), "secondary")
        from markupsafe import Markup
        return Markup(f"<span class='badge badge-{color}'>{status}</span>")

    column_formatters = {
        "status": _status_formatter,
    }

    def on_model_change(self, form, model, is_created):
        model.max_ips = max(1, min(int(model.max_ips or 1), 10))
        model.package_days = max(1, min(int(model.package_days or 1), 36500))
        model.billing_amount = max(0, int(model.billing_amount or 0))
        if not model.created_by:
            model.created_by = g.account.id
        if model.plan and (is_created or not model.usage_limit):
            model.sync_from_plan()
        if model.start_date and not model.end_date and model.package_days:
            model.end_date = model.start_date + datetime.timedelta(days=model.package_days)
        model.apply_to_user()

    def get_query(self):
        query = super().get_query()
        admin_id = int(request.args.get("admin_id") or g.account.id)
        if admin_id not in g.account.recursive_sub_admins_ids():
            abort(403)
        admin = AdminUser.query.filter(AdminUser.id == admin_id).first()
        if not admin:
            abort(403)
        return query.filter(CommercialSubscription.created_by.in_(admin.recursive_sub_admins_ids()))

    def get_count_query(self):
        query = super().get_count_query()
        admin_id = int(request.args.get("admin_id") or g.account.id)
        if admin_id not in g.account.recursive_sub_admins_ids():
            abort(403)
        admin = AdminUser.query.filter(AdminUser.id == admin_id).first()
        if not admin:
            abort(403)
        return query.filter(CommercialSubscription.created_by.in_(admin.recursive_sub_admins_ids()))
