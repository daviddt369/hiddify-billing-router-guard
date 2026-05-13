from __future__ import annotations
import html
import logging
import re
import os

import telebot.apihelper as tg_apihelper

from hiddifypanel.models import ConfigEnum, hconfig

logger = logging.getLogger(__name__)
_BR_RE = re.compile(r"(?i)<br\s*/?>")
_TAG_RE = re.compile(r"<[^>]+>")


def sanitize_telegram_html(text: str | None) -> str:
    value = "" if text is None else str(text)
    return _BR_RE.sub("\n", value)


def _plain_text_fallback(text: str | None) -> str:
    sanitized = sanitize_telegram_html(text)
    return html.unescape(_TAG_RE.sub("", sanitized))


def _config_value(name: str) -> str:
    key = getattr(ConfigEnum, name, None)
    if key is None:
        return ""
    try:
        value = hconfig(key)
        return "" if value is None else str(value).strip()
    except RuntimeError:
        return ""


def _env_or_file(primary: str, legacy: str = "") -> str:
    value = (os.environ.get(primary, "") or "").strip()
    if value:
        return value
    if legacy:
        value = (os.environ.get(legacy, "") or "").strip()
        if value:
            return value
    try:
        with open("/etc/hiddify-panel/panel-secrets.env", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith(f"{primary}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _proxy_config():
    enabled_value = (
        _config_value("telegram_api_proxy_enable")
        or _env_or_file("HIDDIFY_TELEGRAM_API_PROXY_ENABLE", "TELEGRAM_API_PROXY_ENABLE")
    ).strip().lower()
    url = (
        _config_value("telegram_api_proxy_url")
        or _env_or_file("HIDDIFY_TELEGRAM_API_PROXY_URL", "TELEGRAM_API_PROXY_URL")
    ).strip()
    enabled = enabled_value in {"1", "true", "yes", "on"}
    if not enabled or not url:
        return None
    return {"http": url, "https": url}


def configure_telegram_api_proxy():
    tg_apihelper.proxy = _proxy_config()
    return tg_apihelper.proxy


def _is_entity_parse_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "can't parse entities" in message or "unsupported start tag" in message


def _wrap_text_sender(bot, method_name: str, text_index: int):
    original = getattr(bot, method_name)

    def wrapped(*args, **kwargs):
        configure_telegram_api_proxy()
        args = list(args)
        if len(args) > text_index:
            args[text_index] = sanitize_telegram_html(args[text_index])
        effective_parse_mode = kwargs.get("parse_mode", getattr(bot, "parse_mode", None))
        try:
            return original(*args, **kwargs)
        except Exception as exc:
            if (effective_parse_mode or "").upper() != "HTML" or not _is_entity_parse_error(exc):
                raise
            fallback_args = list(args)
            if len(fallback_args) > text_index:
                fallback_args[text_index] = _plain_text_fallback(fallback_args[text_index])
            fallback_kwargs = dict(kwargs)
            fallback_kwargs["parse_mode"] = None
            return original(*fallback_args, **fallback_kwargs)

    return wrapped


def _wrap_proxy_only(bot, method_name: str):
    original = getattr(bot, method_name)

    def wrapped(*args, **kwargs):
        configure_telegram_api_proxy()
        return original(*args, **kwargs)

    return wrapped


def prepare_telebot(bot):
    if getattr(bot, "_hiddify_runtime_prepared", False):
        return bot
    bot.send_message = _wrap_text_sender(bot, "send_message", 1)
    bot.reply_to = _wrap_text_sender(bot, "reply_to", 1)
    bot.edit_message_text = _wrap_text_sender(bot, "edit_message_text", 0)
    for method_name in (
        "send_invoice",
        "answer_pre_checkout_query",
        "set_webhook",
        "remove_webhook",
        "get_me",
    ):
        setattr(bot, method_name, _wrap_proxy_only(bot, method_name))
    bot._hiddify_runtime_prepared = True
    return bot
