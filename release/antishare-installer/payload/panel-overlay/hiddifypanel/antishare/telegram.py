from __future__ import annotations

import datetime

from loguru import logger
from hiddifypanel.panel.commercial.telegrambot.secrets import telegram_bot_token


def notify_state_change(*, user, new_state: str, extra_ips: list[str], ban_until: datetime.datetime | None) -> bool:
    telegram_id = getattr(user, "telegram_id", None)
    if not telegram_id:
        return False

    try:
        from hiddifypanel.panel.commercial.telegrambot import bot
    except Exception:
        logger.exception("Anti-share: cannot import Telegram bot")
        return False

    if not bot:
        return False

    token = telegram_bot_token()
    if not token:
        logger.error("Anti-share: Telegram bot token is not configured")
        return False
    bot.token = token

    if new_state == "warned":
        text = (
            "Обнаружено превышение лимита IP/устройств.\n\n"
            f"Разрешено по тарифу: {int(user.max_ips or 1)} IP\n"
            f"Сейчас замечены лишние IP: {', '.join(extra_ips) if extra_ips else 'да'}\n\n"
            "Отключите лишние устройства. При нормализации доступа блокировка не потребуется."
        )
    elif new_state == "blocked":
        until_text = ban_until.strftime("%Y-%m-%d %H:%M:%S UTC") if ban_until else "временно"
        text = (
            "Сработала антишаринг-защита.\n\n"
            f"Превышен лимит по тарифу: {int(user.max_ips or 1)} IP\n"
            f"Временно ограничены лишние IP: {', '.join(extra_ips) if extra_ips else 'да'}\n"
            f"Ограничение действует до: {until_text}\n\n"
            "Отключите лишние устройства и не делитесь подпиской."
        )
    else:
        return False

    try:
        bot.send_message(int(telegram_id), text)
        return True
    except Exception:
        logger.exception("Anti-share: failed to send Telegram notification to user {}", user.id)
        return False
