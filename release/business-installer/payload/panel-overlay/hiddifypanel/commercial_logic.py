import datetime
from enum import auto

from strenum import StrEnum


class PlanCycle(StrEnum):
    daily = auto()
    weekly = auto()
    monthly = auto()
    quarterly = auto()
    semiannual = auto()
    yearly = auto()
    lifetime = auto()


class SubscriptionStatus(StrEnum):
    draft = auto()
    active = auto()
    suspended = auto()
    expired = auto()
    canceled = auto()


PLAN_CYCLE_DAYS = {
    PlanCycle.daily: 1,
    PlanCycle.weekly: 7,
    PlanCycle.monthly: 30,
    PlanCycle.quarterly: 90,
    PlanCycle.semiannual: 180,
    PlanCycle.yearly: 365,
    PlanCycle.lifetime: 36500,
}


def cycle_to_days(cycle: PlanCycle) -> int:
    return PLAN_CYCLE_DAYS[cycle]


def package_end_date(start_date: datetime.date, package_days: int) -> datetime.date:
    normalized_days = max(1, int(package_days or 1))
    return start_date + datetime.timedelta(days=normalized_days - 1)


def build_plan_snapshot(
    *,
    usage_limit: int,
    package_days: int,
    max_ips: int,
    mode,
):
    return {
        "usage_limit": max(0, int(usage_limit or 0)),
        "package_days": max(0, int(package_days or 0)),
        "max_ips": max(1, min(10, int(max_ips or 1))),
        "mode": mode,
    }


def apply_package_renewal(
    user,
    plan,
    *,
    start_date: datetime.date | None = None,
):
    start_date = start_date or datetime.date.today()
    user.plan = plan
    user.usage_limit = max(0, int(plan.usage_limit or 0))
    user.package_days = max(1, int(plan.package_days or 1))
    user.max_ips = max(1, min(10, int(plan.max_ips or 1)))
    user.mode = plan.mode
    user.start_date = start_date
    user.last_reset_time = start_date
    user.current_usage = 0
    user.enable = True
    return start_date


def renew_user_package(
    user,
    plan,
    *,
    start_date: datetime.date | None = None,
    created_by: int | None = None,
    note: str = "",
):
    from hiddifypanel.database import db
    from hiddifypanel.models import CommercialSubscription, UserDetail

    start_date = apply_package_renewal(user, plan, start_date=start_date)

    for detail in UserDetail.query.filter(UserDetail.user_id == user.id):
        detail.current_usage = 0
        detail.connected_devices = ""

    subscription = CommercialSubscription(
        user=user,
        plan=plan,
        start_date=start_date,
        end_date=package_end_date(start_date, plan.package_days),
        auto_renew=False,
        usage_limit=plan.usage_limit,
        package_days=plan.package_days,
        max_ips=plan.max_ips,
        mode=plan.mode,
        billing_amount=plan.price,
        billing_currency=plan.currency,
        payment_provider=plan.payment_provider,
        note=note,
        created_by=created_by or getattr(user, "added_by", 1) or 1,
    )
    db.session.add(subscription)
    return subscription


def compute_subscription_status(
    *,
    start_date: datetime.date | None,
    end_date: datetime.date | None,
    canceled_at: datetime.datetime | None = None,
    suspended_at: datetime.datetime | None = None,
    today: datetime.date | None = None,
) -> SubscriptionStatus:
    today = today or datetime.date.today()

    if canceled_at is not None:
        return SubscriptionStatus.canceled
    if suspended_at is not None:
        return SubscriptionStatus.suspended
    if start_date is None:
        return SubscriptionStatus.draft
    if end_date is not None and end_date < today:
        return SubscriptionStatus.expired
    return SubscriptionStatus.active
