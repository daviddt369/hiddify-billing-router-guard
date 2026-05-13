from __future__ import annotations

from datetime import datetime
from typing import Any

from hiddifypanel.database import db


class CommercialRoutingUpstream(db.Model):
    __tablename__ = "commercial_routing_upstream"

    id                 = db.Column(db.Integer,     primary_key=True, autoincrement=True)
    name               = db.Column(db.String(64),  nullable=False, unique=True)
    label              = db.Column(db.String(128),  nullable=False, default="")
    enabled            = db.Column(db.Boolean,      nullable=False, default=True)
    priority           = db.Column(db.Integer,      nullable=False, default=0)
    tunnel_type        = db.Column(db.String(32),   nullable=False, default="test_blackhole")

    # WireGuard fields
    wg_endpoint        = db.Column(db.String(255),  nullable=False, default="")
    wg_public_key      = db.Column(db.Text,         nullable=False, default="")
    wg_private_key_ref = db.Column(db.String(512),  nullable=False, default="")
    wg_addresses       = db.Column(db.Text,         nullable=False, default="")
    wg_mtu             = db.Column(db.Integer,      nullable=False, default=1280)

    # VLESS / Trojan URI fields
    vless_uri          = db.Column(db.Text,         nullable=False, default="")
    trojan_uri         = db.Column(db.Text,         nullable=False, default="")

    # Status fields (for future health-check / failover UI)
    last_status        = db.Column(db.String(32),   nullable=False, default="")
    last_error         = db.Column(db.Text,         nullable=True)
    last_checked_at    = db.Column(db.DateTime,     nullable=True)

    created_at         = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at         = db.Column(db.DateTime, nullable=False, default=datetime.utcnow,
                                   onupdate=datetime.utcnow)

    __table_args__ = (
        db.Index("ix_upstream_enabled",  "enabled"),
        db.Index("ix_upstream_priority", "priority"),
    )

    @classmethod
    def get_active_ordered(cls) -> list["CommercialRoutingUpstream"]:
        """Return enabled upstreams ordered by priority ASC, id ASC."""
        return (
            cls.query
            .filter_by(enabled=True)
            .order_by(cls.priority.asc(), cls.id.asc())
            .all()
        )


    def __repr__(self) -> str:
        return (
            f"<CommercialRoutingUpstream id={self.id} name={self.name!r} "
            f"tunnel={self.tunnel_type} enabled={self.enabled} priority={self.priority}>"
        )
