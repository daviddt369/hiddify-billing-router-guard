from __future__ import annotations

from dataclasses import dataclass
import os


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return float(value)
    except Exception:
        return default


@dataclass(frozen=True)
class AntiShareSettings:
    enabled: bool
    window_seconds: int
    learning_days: int
    retention_days: int
    trusted_recent_days: int
    trust_decay_per_day: float
    score_decay_clean: float
    score_plus1: float
    score_plus2: float
    score_plus3: float
    suspect_score: float
    warn_score: float
    block_score: float
    severe_new_ip_threshold: int
    severe_traffic_ratio: float
    ban_seconds: int
    telegram_enabled: bool
    nft_enabled: bool
    nft_dry_run: bool
    nft_helper: str
    scan_limit: int
    current_ip_snapshot_limit: int
    service_name: str

    @classmethod
    def from_env(cls) -> "AntiShareSettings":
        return cls(
            enabled=_env_bool("HIDDIFY_ANTI_SHARE_ENABLED", True),
            window_seconds=_env_int("HIDDIFY_ANTI_SHARE_WINDOW_SECONDS", 120),
            learning_days=_env_int("HIDDIFY_ANTI_SHARE_LEARNING_DAYS", 7),
            retention_days=_env_int("HIDDIFY_ANTI_SHARE_RETENTION_DAYS", 45),
            trusted_recent_days=_env_int("HIDDIFY_ANTI_SHARE_TRUSTED_RECENT_DAYS", 7),
            trust_decay_per_day=_env_float("HIDDIFY_ANTI_SHARE_TRUST_DECAY_PER_DAY", 0.15),
            score_decay_clean=_env_float("HIDDIFY_ANTI_SHARE_SCORE_DECAY_CLEAN", 0.25),
            score_plus1=_env_float("HIDDIFY_ANTI_SHARE_SCORE_PLUS1", 0.25),
            score_plus2=_env_float("HIDDIFY_ANTI_SHARE_SCORE_PLUS2", 0.50),
            score_plus3=_env_float("HIDDIFY_ANTI_SHARE_SCORE_PLUS3", 1.00),
            suspect_score=_env_float("HIDDIFY_ANTI_SHARE_SUSPECT_SCORE", 0.50),
            warn_score=_env_float("HIDDIFY_ANTI_SHARE_WARN_SCORE", 0.75),
            block_score=_env_float("HIDDIFY_ANTI_SHARE_BLOCK_SCORE", 1.00),
            severe_new_ip_threshold=_env_int("HIDDIFY_ANTI_SHARE_SEVERE_NEW_IP_THRESHOLD", 3),
            severe_traffic_ratio=_env_float("HIDDIFY_ANTI_SHARE_SEVERE_TRAFFIC_RATIO", 5.0),
            ban_seconds=_env_int("HIDDIFY_ANTI_SHARE_BAN_SECONDS", 3600),
            telegram_enabled=_env_bool("HIDDIFY_ANTI_SHARE_TELEGRAM_ENABLED", True),
            nft_enabled=_env_bool("HIDDIFY_ANTI_SHARE_NFT_ENABLED", True),
            nft_dry_run=_env_bool("HIDDIFY_ANTI_SHARE_NFT_DRY_RUN", False),
            nft_helper=os.environ.get(
                "HIDDIFY_ANTI_SHARE_NFT_HELPER",
                "/opt/hiddify-manager/common/hiddify-antishare-nft.sh",
            ),
            scan_limit=_env_int("HIDDIFY_ANTI_SHARE_SCAN_LIMIT", 1000),
            current_ip_snapshot_limit=_env_int("HIDDIFY_ANTI_SHARE_SNAPSHOT_LIMIT", 32),
            service_name=os.environ.get("HIDDIFY_ANTI_SHARE_SERVICE_NAME", "hiddify-anti-share"),
        )

    @classmethod
    def load(cls) -> "AntiShareSettings":
        settings = cls.from_env()
        try:
            from sqlalchemy import inspect
            from hiddifypanel.database import db
            from hiddifypanel.antishare.models import AntiShareConfig

            inspector = inspect(db.engine)
            if "anti_share_config" not in inspector.get_table_names():
                return settings

            row = AntiShareConfig.query.order_by(AntiShareConfig.id.asc()).first()
            if not row:
                return settings

            return cls(
                enabled=bool(row.enabled),
                window_seconds=int(row.window_seconds),
                learning_days=int(row.learning_days),
                retention_days=int(row.retention_days),
                trusted_recent_days=int(row.trusted_recent_days),
                trust_decay_per_day=float(row.trust_decay_per_day),
                score_decay_clean=float(row.score_decay_clean),
                score_plus1=float(row.score_plus1),
                score_plus2=float(row.score_plus2),
                score_plus3=float(row.score_plus3),
                suspect_score=float(row.suspect_score),
                warn_score=float(row.warn_score),
                block_score=float(row.block_score),
                severe_new_ip_threshold=int(row.severe_new_ip_threshold),
                severe_traffic_ratio=float(row.severe_traffic_ratio),
                ban_seconds=int(row.ban_seconds),
                telegram_enabled=bool(row.telegram_enabled),
                nft_enabled=bool(row.nft_enabled),
                nft_dry_run=bool(row.nft_dry_run),
                nft_helper=row.nft_helper,
                scan_limit=int(row.scan_limit),
                current_ip_snapshot_limit=int(row.current_ip_snapshot_limit),
                service_name=row.service_name,
            )
        except Exception:
            return settings
