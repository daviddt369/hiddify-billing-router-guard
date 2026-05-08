from __future__ import annotations

import datetime

from hiddifypanel.antishare.config import AntiShareSettings


def age_trust(profile, now: datetime.datetime, settings: AntiShareSettings) -> None:
    if not profile.last_seen_at:
        profile.trust_score = 0.0
        profile.is_trusted = False
        return

    days_stale = max(0, (now.date() - profile.last_seen_at.date()).days)
    if days_stale > 0:
        profile.trust_score = max(0.0, float(profile.trust_score or 0.0) - days_stale * settings.trust_decay_per_day)
    if days_stale > settings.trusted_recent_days:
        profile.is_trusted = False


def record_seen(profile, now: datetime.datetime) -> None:
    is_new_day = not profile.last_seen_at or profile.last_seen_at.date() != now.date()
    if is_new_day:
        profile.seen_days = int(profile.seen_days or 0) + 1
    profile.seen_cycles = int(profile.seen_cycles or 0) + 1
    profile.total_hits = int(profile.total_hits or 0) + 1
    profile.last_seen_at = now
    profile.trust_score = min(5.0, float(profile.trust_score or 0.0) + 0.40)


def current_ip_sort_key(profile, now: datetime.datetime, settings: AntiShareSettings):
    last_seen = profile.last_seen_at or datetime.datetime.min
    recent = 1 if (now.date() - last_seen.date()).days <= settings.trusted_recent_days else 0
    return (
        recent,
        float(profile.trust_score or 0.0),
        int(profile.seen_days or 0),
        int(profile.seen_cycles or 0),
        int(profile.total_hits or 0),
        last_seen.timestamp() if last_seen != datetime.datetime.min else 0.0,
    )


def pick_allowed_ips(current_ips: list[str], profiles_by_ip: dict[str, object], allowed_count: int, now: datetime.datetime, settings: AntiShareSettings) -> tuple[list[str], list[str]]:
    if len(current_ips) <= allowed_count:
        return list(current_ips), []

    ranked = sorted(
        current_ips,
        key=lambda ip: current_ip_sort_key(profiles_by_ip[ip], now, settings),
        reverse=True,
    )
    allowed = ranked[:allowed_count]
    extra = [ip for ip in ranked if ip not in allowed]
    return allowed, extra


def score_bump_for_excess(excess: int, settings: AntiShareSettings) -> float:
    if excess <= 0:
        return 0.0
    if excess == 1:
        return settings.score_plus1
    if excess == 2:
        return settings.score_plus2
    return settings.score_plus3


def derive_state(score: float, settings: AntiShareSettings) -> str:
    if score >= settings.block_score:
        return "blocked"
    if score >= settings.warn_score:
        return "warned"
    if score >= settings.suspect_score:
        return "suspect"
    return "normal"
