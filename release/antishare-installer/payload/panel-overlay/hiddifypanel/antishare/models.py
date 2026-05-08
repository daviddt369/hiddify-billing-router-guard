from __future__ import annotations

import datetime

from sqlalchemy import UniqueConstraint

from hiddifypanel.database import db


class AntiShareState(db.Model):
    __tablename__ = "anti_share_state"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True, unique=True)
    allowed_ip_count = db.Column(db.Integer, default=1, nullable=False)
    current_ip_count = db.Column(db.Integer, default=0, nullable=False)
    score = db.Column(db.Float, default=0.0, nullable=False)
    state = db.Column(db.String(16), default="learning", nullable=False)
    learning_until = db.Column(db.DateTime, nullable=True)
    violation_started_at = db.Column(db.DateTime, nullable=True)
    warned_at = db.Column(db.DateTime, nullable=True)
    blocked_at = db.Column(db.DateTime, nullable=True)
    ban_until = db.Column(db.DateTime, nullable=True)
    traffic_multiplier = db.Column(db.Float, default=1.0, nullable=False)
    last_cycle_usage = db.Column(db.BigInteger, default=0, nullable=False)
    last_ips_snapshot = db.Column(db.Text, default="[]", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow,
        nullable=False,
    )


class AntiShareConfig(db.Model):
    __tablename__ = "anti_share_config"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    enabled = db.Column(db.Boolean, default=True, nullable=False)
    window_seconds = db.Column(db.Integer, default=120, nullable=False)
    learning_days = db.Column(db.Integer, default=7, nullable=False)
    retention_days = db.Column(db.Integer, default=45, nullable=False)
    trusted_recent_days = db.Column(db.Integer, default=7, nullable=False)
    trust_decay_per_day = db.Column(db.Float, default=0.15, nullable=False)
    score_decay_clean = db.Column(db.Float, default=0.25, nullable=False)
    score_plus1 = db.Column(db.Float, default=0.25, nullable=False)
    score_plus2 = db.Column(db.Float, default=0.50, nullable=False)
    score_plus3 = db.Column(db.Float, default=1.00, nullable=False)
    suspect_score = db.Column(db.Float, default=0.50, nullable=False)
    warn_score = db.Column(db.Float, default=0.75, nullable=False)
    block_score = db.Column(db.Float, default=1.00, nullable=False)
    severe_new_ip_threshold = db.Column(db.Integer, default=3, nullable=False)
    severe_traffic_ratio = db.Column(db.Float, default=5.0, nullable=False)
    ban_seconds = db.Column(db.Integer, default=3600, nullable=False)
    telegram_enabled = db.Column(db.Boolean, default=True, nullable=False)
    nft_enabled = db.Column(db.Boolean, default=True, nullable=False)
    nft_dry_run = db.Column(db.Boolean, default=False, nullable=False)
    nft_helper = db.Column(db.String(512), default="/opt/hiddify-manager/common/hiddify-antishare-nft.sh", nullable=False)
    scan_limit = db.Column(db.Integer, default=1000, nullable=False)
    current_ip_snapshot_limit = db.Column(db.Integer, default=32, nullable=False)
    service_name = db.Column(db.String(128), default="hiddify-anti-share", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow,
        nullable=False,
    )


class AntiShareIPProfile(db.Model):
    __tablename__ = "anti_share_ip_profile"
    __table_args__ = (
        UniqueConstraint("user_id", "ip", name="uq_anti_share_user_ip"),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    ip = db.Column(db.String(64), nullable=False, index=True)
    first_seen_at = db.Column(db.DateTime, nullable=False, default=datetime.datetime.utcnow)
    last_seen_at = db.Column(db.DateTime, nullable=False, default=datetime.datetime.utcnow)
    seen_cycles = db.Column(db.Integer, default=0, nullable=False)
    seen_days = db.Column(db.Integer, default=0, nullable=False)
    total_hits = db.Column(db.Integer, default=0, nullable=False)
    trust_score = db.Column(db.Float, default=0.0, nullable=False)
    is_trusted = db.Column(db.Boolean, default=False, nullable=False)
    last_banned_at = db.Column(db.DateTime, nullable=True)
    last_ban_until = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow,
        nullable=False,
    )


class AntiShareEvent(db.Model):
    __tablename__ = "anti_share_event"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    event_type = db.Column(db.String(64), nullable=False, index=True)
    ip = db.Column(db.String(64), nullable=True, index=True)
    score_before = db.Column(db.Float, default=0.0, nullable=False)
    score_after = db.Column(db.Float, default=0.0, nullable=False)
    state_before = db.Column(db.String(16), default="", nullable=False)
    state_after = db.Column(db.String(16), default="", nullable=False)
    payload = db.Column(db.Text, default="{}", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow, nullable=False)


class AntiShareUserOverride(db.Model):
    __tablename__ = "anti_share_user_override"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True, unique=True)
    disabled = db.Column(db.Boolean, default=False, nullable=False)
    note = db.Column(db.String(512), default="", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow,
        nullable=False,
    )
