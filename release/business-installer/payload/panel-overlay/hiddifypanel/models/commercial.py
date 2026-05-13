import datetime

from strenum import StrEnum
from sqlalchemy.orm import validates

from hiddifypanel.commercial_logic import PlanCycle, compute_subscription_status
from hiddifypanel.database import db
from hiddifypanel.models.user import ONE_GIG, UserMode


class PaymentProvider(StrEnum):
    manual = "manual"
    yookassa = "yookassa"


class CommercialPlan(db.Model):
    __tablename__ = "commercial_plan"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(128), nullable=False, unique=True)
    cycle = db.Column(db.Enum(PlanCycle), default=PlanCycle.monthly, nullable=False)
    usage_limit = db.Column(db.BigInteger, default=300 * ONE_GIG, nullable=False)
    package_days = db.Column(db.Integer, default=31, nullable=False)
    max_ips = db.Column(db.Integer, default=1, nullable=False)
    mode = db.Column(db.Enum(UserMode), default=UserMode.monthly, nullable=False)
    enable = db.Column(db.Boolean, default=True, nullable=False)
    is_public = db.Column(db.Boolean, default=True, nullable=False)
    price = db.Column(db.Integer, default=0, nullable=False)
    currency = db.Column(db.String(8), default="RUB", nullable=False)
    payment_provider = db.Column(db.Enum(PaymentProvider), default=PaymentProvider.yookassa, nullable=False)
    sort_order = db.Column(db.Integer, default=100, nullable=False)
    note = db.Column(db.String(512), default="", nullable=False)
    added_by = db.Column(db.Integer, db.ForeignKey("admin_user.id"), default=1, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow,
        nullable=False,
    )

    @property
    def usage_limit_GB(self):
        return (self.usage_limit or 0) / ONE_GIG

    @usage_limit_GB.setter
    def usage_limit_GB(self, value):
        self.usage_limit = max(0, min(1000000 * ONE_GIG, int((value or 0) * ONE_GIG)))

    def to_user_kwargs(self) -> dict:
        return {
            "usage_limit": self.usage_limit,
            "package_days": self.package_days,
            "max_ips": self.max_ips,
            "mode": self.mode,
        }

    def __str__(self) -> str:
        return self.name


class CommercialSubscription(db.Model):
    __tablename__ = "commercial_subscription"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    plan_id = db.Column(db.Integer, db.ForeignKey("commercial_plan.id"), nullable=True, index=True)
    start_date = db.Column(db.Date, nullable=True)
    end_date = db.Column(db.Date, nullable=True)
    suspended_at = db.Column(db.DateTime, nullable=True)
    canceled_at = db.Column(db.DateTime, nullable=True)
    auto_renew = db.Column(db.Boolean, default=False, nullable=False)
    usage_limit = db.Column(db.BigInteger, default=0, nullable=False)
    package_days = db.Column(db.Integer, default=0, nullable=False)
    max_ips = db.Column(db.Integer, default=1, nullable=False)
    mode = db.Column(db.Enum(UserMode), default=UserMode.no_reset, nullable=False)
    billing_amount = db.Column(db.Integer, default=0, nullable=False)
    billing_currency = db.Column(db.String(8), default="RUB", nullable=False)
    payment_provider = db.Column(db.Enum(PaymentProvider), default=PaymentProvider.yookassa, nullable=False)
    external_payment_id = db.Column(db.String(128), unique=True, nullable=True)
    note = db.Column(db.String(512), default="", nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("admin_user.id"), default=1, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow,
        nullable=False,
    )

    user = db.relationship("User", backref="subscriptions")
    plan = db.relationship("CommercialPlan", backref="subscriptions")
    creator = db.relationship("AdminUser", backref="commercial_subscriptions")

    @property
    def usage_limit_GB(self):
        return (self.usage_limit or 0) / ONE_GIG

    @usage_limit_GB.setter
    def usage_limit_GB(self, value):
        self.usage_limit = max(0, min(1000000 * ONE_GIG, int((value or 0) * ONE_GIG)))

    @validates("external_payment_id")
    def _normalize_external_payment_id(self, key, value):
        normalized = (value or "").strip()
        return normalized or None

    @property
    def status(self):
        return compute_subscription_status(
            start_date=self.start_date,
            end_date=self.end_date,
            canceled_at=self.canceled_at,
            suspended_at=self.suspended_at,
        )

    def sync_from_plan(self):
        if not self.plan:
            return
        self.usage_limit = self.plan.usage_limit
        self.package_days = self.plan.package_days
        self.max_ips = self.plan.max_ips
        self.mode = self.plan.mode
        self.billing_amount = self.plan.price
        self.billing_currency = self.plan.currency
        self.payment_provider = self.plan.payment_provider

    def apply_to_user(self):
        if not self.user:
            return
        if self.plan:
            self.user.plan = self.plan
        if self.usage_limit:
            self.user.usage_limit = self.usage_limit
        if self.package_days:
            self.user.package_days = self.package_days
        if self.max_ips:
            self.user.max_ips = self.max_ips
        if self.mode:
            self.user.mode = self.mode
        if self.start_date is not None:
            self.user.start_date = self.start_date

    def __str__(self) -> str:
        suffix = self.plan.name if self.plan else self.user.name
        return f"Subscription #{self.id or 'new'} - {suffix}"
