from __future__ import annotations

from datetime import datetime

from hiddifypanel.database import db


class CommercialRoutingRuleSource(db.Model):
    __tablename__ = "commercial_routing_rule_source"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(64), nullable=False, unique=True)
    label = db.Column(db.String(128), nullable=False, default="")
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    # text | external_url | local_file
    source_type = db.Column(db.String(32), nullable=False, default="text")
    # domain | subnet
    rule_family = db.Column(db.String(32), nullable=False, default="domain")
    # auto | plain_text | sing_box_source_json | sing_box_binary_srs
    source_format = db.Column(db.String(32), nullable=False, default="auto")
    # to_upstream | direct_ru | block
    outbound_policy = db.Column(db.String(32), nullable=False, default="direct_ru")
    content_text = db.Column(db.Text, nullable=False, default="")
    url = db.Column(db.Text, nullable=False, default="")
    local_path = db.Column(db.String(512), nullable=False, default="")
    last_status = db.Column(db.String(32), nullable=False, default="")
    last_error = db.Column(db.Text, nullable=True)
    last_fetched_at = db.Column(db.DateTime, nullable=True)
    last_hash = db.Column(db.String(64), nullable=False, default="")
    rules_count = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    @classmethod
    def get_all_ordered(cls) -> list["CommercialRoutingRuleSource"]:
        return cls.query.order_by(cls.id).all()

    @classmethod
    def get_enabled(cls) -> list["CommercialRoutingRuleSource"]:
        return cls.query.filter_by(enabled=True).order_by(cls.id).all()
