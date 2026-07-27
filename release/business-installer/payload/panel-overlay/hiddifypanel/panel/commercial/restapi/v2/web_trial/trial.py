from __future__ import annotations
import datetime
import logging
import time

from flask import request
from flask_restful import Resource

from hiddifypanel.panel import hiddify
from hiddifypanel.models import User, Domain, ConfigEnum, UserMode, AdminUser, hconfig
from hiddifypanel.database import db

logger = logging.getLogger(__name__)

ONE_GIG = 1024 ** 3
TRIAL_USAGE_LIMIT_GB = 1
TRIAL_PACKAGE_DAYS = 2
TRIAL_MAX_IPS = 1

import os
_CORS_ORIGIN = os.environ.get("WEB_TRIAL_CORS_ORIGIN", "*")

# in-memory rate limit: max 2 registrations per IP per hour
_rate_store: dict = {}
_RATE_MAX = 2
_RATE_WINDOW = 3600


def _cors():
    return {
        "Access-Control-Allow-Origin": _CORS_ORIGIN,
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }


def _normalize_phone(value):
    raw = (value or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return ""
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    if not digits.startswith("7") and len(digits) == 10:
        digits = "7" + digits
    if not digits.startswith("7") or len(digits) != 11:
        return ""
    return f"+{digits}"


def _find_user(phone: str):
    return User.query.filter(
        (User.name == phone) | (User.username == phone)
    ).order_by(User.id.desc()).first()


def _default_added_by():
    admin = AdminUser.query.order_by(AdminUser.id.asc()).first()
    return admin.id if admin else 1


def _sub_url(user: User) -> str:
    domain = Domain.get_domains()[0]
    proxy_path = hconfig(ConfigEnum.proxy_path_client)
    return f"https://{domain.domain}/{proxy_path}/{user.uuid}/"


def _create_trial_user(phone: str) -> User:
    user = User(
        name=phone,
        username=phone,
        telegram_id=None,
        added_by=_default_added_by(),
        enable=True,
        usage_limit=TRIAL_USAGE_LIMIT_GB * ONE_GIG,
        package_days=TRIAL_PACKAGE_DAYS,
        max_ips=TRIAL_MAX_IPS,
        mode=UserMode.no_reset,
        start_date=datetime.date.today(),
        last_reset_time=datetime.date.today(),
        comment="Web trial signup",
    )
    db.session.add(user)
    db.session.commit()
    db.session.refresh(user)
    hiddify.quick_apply_users()
    return user


def _client_ip() -> str:
    return (
        request.headers.get("X-Real-IP")
        or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or request.remote_addr
        or "unknown"
    )


def _rate_ok(ip: str) -> bool:
    now = time.time()
    cutoff = now - _RATE_WINDOW
    prev = [t for t in _rate_store.get(ip, []) if t > cutoff]
    if len(prev) >= _RATE_MAX:
        _rate_store[ip] = prev
        return False
    prev.append(now)
    _rate_store[ip] = prev
    return True


class WebTrialResource(Resource):
    def options(self, **_):
        return {}, 200, _cors()

    def post(self, **_):
        headers = _cors()
        try:
            if not _rate_ok(_client_ip()):
                return {
                    "status": "error",
                    "message": "Слишком много запросов. Попробуйте через час.",
                }, 429, headers

            data = request.get_json(silent=True) or {}
            phone = _normalize_phone(data.get("phone", ""))
            if not phone:
                return {
                    "status": "error",
                    "message": "Введите корректный номер в формате +7XXXXXXXXXX",
                }, 400, headers

            existing = _find_user(phone)
            if existing:
                return {
                    "status": "exists",
                    "sub_url": _sub_url(existing),
                    "message": "Подписка уже существует",
                }, 200, headers

            user = _create_trial_user(phone)
            return {
                "status": "created",
                "sub_url": _sub_url(user),
                "message": (
                    f"Пробная подписка создана на {TRIAL_PACKAGE_DAYS} дня, "
                    f"лимит {TRIAL_USAGE_LIMIT_GB} ГБ"
                ),
            }, 201, headers

        except Exception as exc:
            logger.exception("Web trial signup failed: %s", exc)
            return {
                "status": "error",
                "message": "Внутренняя ошибка. Попробуйте позже.",
            }, 500, headers
