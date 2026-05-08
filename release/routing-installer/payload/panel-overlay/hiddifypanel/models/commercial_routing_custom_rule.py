from datetime import datetime

from hiddifypanel.database import db


class CommercialRoutingCustomRule(db.Model):
    __tablename__ = "commercial_routing_custom_rule"

    id = db.Column(db.Integer, primary_key=True)
    rule_type = db.Column(db.String(32), nullable=False, index=True)
    value = db.Column(db.Text, nullable=False)
    normalized_value = db.Column(db.Text, nullable=False, index=True)
    outbound_policy = db.Column(db.String(32), nullable=False, default="direct_ru")
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    comment = db.Column(db.Text, nullable=True)
    # NULL = добавлено вручную; non-NULL = импортировано из source
    source_id = db.Column(db.Integer, db.ForeignKey(
        "commercial_routing_rule_source.id", ondelete="SET NULL"
    ), nullable=True, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("rule_type", "normalized_value", name="uq_commercial_routing_rule_unique"),
    )
