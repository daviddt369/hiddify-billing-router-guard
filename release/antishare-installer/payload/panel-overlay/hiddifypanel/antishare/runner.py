from __future__ import annotations

import argparse
import datetime
import json

from loguru import logger

from hiddifypanel import create_app
from hiddifypanel.accesslog import collect_recent_ips
from hiddifypanel.antishare.config import AntiShareSettings
from hiddifypanel.antishare.models import AntiShareConfig, AntiShareEvent, AntiShareIPProfile, AntiShareState, AntiShareUserOverride
from hiddifypanel.antishare.nftables import NftBanBackend
from hiddifypanel.antishare.scoring import age_trust, derive_state, pick_allowed_ips, record_seen, score_bump_for_excess
from hiddifypanel.antishare.telegram import notify_state_change
from hiddifypanel.antishare.traffic import compute_cycle_usage_delta, compute_traffic_multiplier
from hiddifypanel.database import db
from hiddifypanel.models import User


def ensure_tables() -> None:
    for table in (
        AntiShareConfig.__table__,
        AntiShareState.__table__,
        AntiShareIPProfile.__table__,
        AntiShareEvent.__table__,
        AntiShareUserOverride.__table__,
    ):
        table.create(bind=db.engine, checkfirst=True)
    if not AntiShareConfig.query.first():
        settings = AntiShareSettings.from_env()
        db.session.add(
            AntiShareConfig(
                enabled=settings.enabled,
                window_seconds=settings.window_seconds,
                learning_days=settings.learning_days,
                retention_days=settings.retention_days,
                trusted_recent_days=settings.trusted_recent_days,
                trust_decay_per_day=settings.trust_decay_per_day,
                score_decay_clean=settings.score_decay_clean,
                score_plus1=settings.score_plus1,
                score_plus2=settings.score_plus2,
                score_plus3=settings.score_plus3,
                suspect_score=settings.suspect_score,
                warn_score=settings.warn_score,
                block_score=settings.block_score,
                severe_new_ip_threshold=settings.severe_new_ip_threshold,
                severe_traffic_ratio=settings.severe_traffic_ratio,
                ban_seconds=settings.ban_seconds,
                telegram_enabled=settings.telegram_enabled,
                nft_enabled=settings.nft_enabled,
                nft_dry_run=settings.nft_dry_run,
                nft_helper=settings.nft_helper,
                scan_limit=settings.scan_limit,
                current_ip_snapshot_limit=settings.current_ip_snapshot_limit,
                service_name=settings.service_name,
            )
        )
        db.session.commit()


def get_or_create_state(user: User, now: datetime.datetime, settings: AntiShareSettings) -> AntiShareState:
    state = AntiShareState.query.filter(AntiShareState.user_id == user.id).first()
    if state:
        return state

    start_date = user.start_date or now.date()
    learning_until = datetime.datetime.combine(
        start_date + datetime.timedelta(days=settings.learning_days),
        datetime.time.min,
    )
    state = AntiShareState(
        user_id=user.id,
        allowed_ip_count=max(1, int(user.max_ips or 1)),
        learning_until=learning_until,
        state="learning",
    )
    db.session.add(state)
    db.session.flush()
    return state


def load_profiles(user_id: int) -> dict[str, AntiShareIPProfile]:
    return {
        row.ip: row
        for row in AntiShareIPProfile.query.filter(AntiShareIPProfile.user_id == user_id).all()
    }


def prune_profiles(profiles: dict[str, AntiShareIPProfile], now: datetime.datetime, settings: AntiShareSettings) -> None:
    cutoff = now - datetime.timedelta(days=settings.retention_days)
    for profile in list(profiles.values()):
        if profile.last_seen_at and profile.last_seen_at < cutoff:
            db.session.delete(profile)


def severe_learning_anomaly(excess: int, traffic_multiplier: float, settings: AntiShareSettings) -> bool:
    return excess >= settings.severe_new_ip_threshold and traffic_multiplier >= settings.severe_traffic_ratio


def append_event(user_id: int, event_type: str, score_before: float, score_after: float, state_before: str, state_after: str, ip: str | None = None, payload: dict | None = None) -> None:
    db.session.add(
        AntiShareEvent(
            user_id=user_id,
            event_type=event_type,
            ip=ip,
            score_before=score_before,
            score_after=score_after,
            state_before=state_before,
            state_after=state_after,
            payload=json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
        )
    )


def process_user(user: User, recent_ips: dict[str, list[str]], settings: AntiShareSettings, backend: NftBanBackend, now: datetime.datetime) -> None:
    state = get_or_create_state(user, now, settings)
    user_override = AntiShareUserOverride.query.filter(AntiShareUserOverride.user_id == user.id).first()
    if user_override and user_override.disabled:
        state.allowed_ip_count = max(1, int(user.max_ips or 1))
        state.current_ip_count = len(recent_ips.get(user.uuid.lower(), []))
        state.score = 0.0
        state.state = "normal"
        state.ban_until = None
        state.violation_started_at = None
        state.last_ips_snapshot = json.dumps(recent_ips.get(user.uuid.lower(), [])[: settings.current_ip_snapshot_limit], ensure_ascii=False)
        append_event(
            user.id,
            "cycle_skipped_disabled",
            0.0,
            0.0,
            "disabled",
            "disabled",
            payload={"reason": "user_override"},
        )
        return
    profiles = load_profiles(user.id)
    current_ips = [ip for ip in recent_ips.get(user.uuid.lower(), []) if ip]
    allowed_count = max(1, int(user.max_ips or 1))

    for profile in profiles.values():
        age_trust(profile, now, settings)

    for ip in current_ips:
        profile = profiles.get(ip)
        if profile is None:
            profile = AntiShareIPProfile(
                user_id=user.id,
                ip=ip,
                first_seen_at=now,
                last_seen_at=now,
                seen_days=0,
                seen_cycles=0,
                total_hits=0,
                trust_score=0.0,
            )
            db.session.add(profile)
            profiles[ip] = profile
        record_seen(profile, now)

    prune_profiles(profiles, now, settings)

    allowed_ips, extra_ips = pick_allowed_ips(current_ips, profiles, allowed_count, now, settings)
    for profile in profiles.values():
        profile.is_trusted = profile.ip in allowed_ips

    previous_cycle_usage = int(state.last_cycle_usage or 0)
    current_delta_usage = compute_cycle_usage_delta(user.current_usage, previous_cycle_usage)
    traffic_multiplier = compute_traffic_multiplier(current_delta_usage, previous_cycle_usage)
    excess = max(0, len(current_ips) - allowed_count)

    score_before = float(state.score or 0.0)
    state_before = str(state.state or "learning")
    score_after = score_before
    learning = bool(state.learning_until and now < state.learning_until)
    anomaly = severe_learning_anomaly(excess, traffic_multiplier, settings)

    if learning and not anomaly:
        score_after = max(0.0, score_before - settings.score_decay_clean)
        state_after = "learning"
    else:
        if excess == 0:
            score_after = max(0.0, score_before - settings.score_decay_clean)
        else:
            score_after += score_bump_for_excess(excess, settings)
            if traffic_multiplier >= 2.0:
                score_after += min(1.0, (traffic_multiplier - 1.0) * 0.15)
        state_after = derive_state(score_after, settings)

    ban_until = None
    if state_after == "blocked" and extra_ips:
        backend.ban_ips(extra_ips, settings.ban_seconds, f"user:{user.id}")
        ban_until = now + datetime.timedelta(seconds=settings.ban_seconds)
        for ip in extra_ips:
            profile = profiles.get(ip)
            if profile:
                profile.last_banned_at = now
                profile.last_ban_until = ban_until
            append_event(
                user.id,
                "ban_ip",
                score_before,
                score_after,
                state_before,
                state_after,
                ip=ip,
                payload={"ban_until": ban_until.isoformat()},
            )

    if state_before != state_after:
        if state_after == "warned":
            state.warned_at = now
        if state_after == "blocked":
            state.blocked_at = now
        append_event(
            user.id,
            "state_transition",
            score_before,
            score_after,
            state_before,
            state_after,
            payload={
                "allowed_ips": allowed_ips,
                "extra_ips": extra_ips,
                "traffic_multiplier": traffic_multiplier,
                "current_delta_usage": current_delta_usage,
            },
        )
        if settings.telegram_enabled and state_after in {"warned", "blocked"}:
            notify_state_change(user=user, new_state=state_after, extra_ips=extra_ips, ban_until=ban_until)

    append_event(
        user.id,
        "cycle_summary",
        score_before,
        score_after,
        state_before,
        state_after,
        payload={
            "current_ip_count": len(current_ips),
            "allowed_ip_count": allowed_count,
            "allowed_ips": allowed_ips,
            "extra_ips": extra_ips,
            "traffic_multiplier": traffic_multiplier,
            "current_delta_usage": current_delta_usage,
            "learning": learning,
        },
    )

    if excess > 0 and state.violation_started_at is None:
        state.violation_started_at = now
    if excess == 0:
        state.violation_started_at = None

    state.allowed_ip_count = allowed_count
    state.current_ip_count = len(current_ips)
    state.score = round(score_after, 4)
    state.state = state_after
    state.ban_until = ban_until
    state.traffic_multiplier = traffic_multiplier
    state.last_cycle_usage = current_delta_usage
    state.last_ips_snapshot = json.dumps(current_ips[: settings.current_ip_snapshot_limit], ensure_ascii=False)


def run_cycle(check_only: bool = False) -> int:
    app = create_app(app_mode="cli")
    with app.app_context():
        ensure_tables()
        settings = AntiShareSettings.load()
        if not settings.enabled:
            logger.info("Anti-share addon is disabled")
            return 0
        backend = NftBanBackend(
            helper_path=settings.nft_helper,
            enabled=settings.nft_enabled,
            dry_run=settings.nft_dry_run,
        )
        backend.ensure()

        if check_only:
            logger.info("Anti-share check completed: tables and nft helper are ready")
            return 0

        recent_ips = collect_recent_ips(ttl_seconds=settings.window_seconds)
        now = datetime.datetime.utcnow()
        users = (
            User.query.filter(User.enable == True)  # noqa: E712
            .order_by(User.id.asc())
            .limit(settings.scan_limit)
            .all()
        )

        processed = 0
        for user in users:
            try:
                process_user(user, recent_ips, settings, backend, now)
                db.session.commit()
                processed += 1
            except Exception:
                db.session.rollback()
                logger.exception("Anti-share processing failed for user {}", user.id)
        logger.info("Anti-share cycle done: processed={} recent_users={}", processed, len(recent_ips))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Hiddify anti-share cycle")
    parser.add_argument("--check", action="store_true", help="Only validate setup and exit")
    args = parser.parse_args()
    return run_cycle(check_only=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
