import telebot
from flask import request, Response, current_app
from apiflask import HTTPError
from flask_restful import Resource
from werkzeug.exceptions import HTTPException
import time
import os
import hmac
import threading
from concurrent.futures import ThreadPoolExecutor

from hiddifypanel.models import *
from hiddifypanel import Events
from hiddifypanel.cache import cache
try:
    from hiddifypanel.panel.commercial.telegrambot.runtime import configure_telegram_api_proxy, prepare_telebot
except Exception:
    def configure_telegram_api_proxy():
        telebot.apihelper.proxy = None
        return None

    def prepare_telebot(instance):
        return instance

try:
    from hiddifypanel.panel.commercial.telegrambot.secrets import telegram_bot_token
except Exception:
    def telegram_bot_token():
        return (hconfig(ConfigEnum.telegram_bot_token) or "").strip()
logger = telebot.logger
_WEBHOOK_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tg-webhook")
_UPDATE_LOCK = threading.Lock()
_RECENT_UPDATE_IDS: dict[int, float] = {}
_UPDATE_TTL_SECONDS = 900


class ExceptionHandler(telebot.ExceptionHandler):
    def handle(self, exception):
        """Improved error handling for Telegram bot exceptions"""
        error_msg = str(exception)
        logger.error(f"Telegram bot error: {error_msg}")

        try:
            # Attempt recovery based on error type
            if "webhook" in error_msg.lower():
                if hasattr(bot, 'remove_webhook'):
                    bot.remove_webhook()
                    logger.info("Removed webhook due to error")
            elif "connection" in error_msg.lower():
                # Wait and retry for connection issues
                time.sleep(5)
                return True  # Indicates retry
        except Exception as e:
            logger.error(f"Error during recovery attempt: {str(e)}")

        return False  # Don't retry for unknown errors


bot = prepare_telebot(telebot.TeleBot("1:2", parse_mode="HTML", threaded=False, exception_handler=ExceptionHandler()))
bot.username = ''


def _webhook_secret() -> str:
    secret = (
        os.environ.get("HIDDIFY_TELEGRAM_WEBHOOK_SECRET", "")
        or os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
    ).strip()
    if secret:
        return secret
    secrets_file = "/etc/hiddify-panel/panel-secrets.env"
    try:
        with open(secrets_file, "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("HIDDIFY_TELEGRAM_WEBHOOK_SECRET="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def _config_value(name: str) -> str:
    key = getattr(ConfigEnum, name, None)
    if key is None:
        return ""
    try:
        return (hconfig(key) or "").strip()
    except RuntimeError:
        return ""


def _set_bot_token():
    token = telegram_bot_token()
    if token:
        bot.token = token
    else:
        logger.error("Telegram bot token is not configured.")
    return token


def _webhook_domain_override() -> str:
    domain = (
        _config_value("telegram_webhook_domain")
        or
        os.environ.get("HIDDIFY_TELEGRAM_WEBHOOK_DOMAIN", "")
        or os.environ.get("TELEGRAM_WEBHOOK_DOMAIN", "")
    ).strip().lower()
    if not domain:
        return ""
    if domain.startswith("http://"):
        domain = domain[len("http://"):]
    elif domain.startswith("https://"):
        domain = domain[len("https://"):]
    return domain.split("/", 1)[0].strip()


def _webhook_secret_is_valid(request) -> bool:
    secret = _webhook_secret()
    if not secret:
        logger.error(
            "Telegram webhook rejected: webhook secret is not configured. path=%s remote=%s",
            request.path,
            request.remote_addr,
        )
        return False
    received = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    return bool(received) and hmac.compare_digest(received, secret)


def _prune_recent_update_ids(now_ts: float) -> None:
    expired = [update_id for update_id, ts in _RECENT_UPDATE_IDS.items() if now_ts - ts > _UPDATE_TTL_SECONDS]
    for update_id in expired:
        _RECENT_UPDATE_IDS.pop(update_id, None)


def _mark_update_seen(update_id: int | None) -> bool:
    if update_id is None:
        return True
    now_ts = time.time()
    with _UPDATE_LOCK:
        _prune_recent_update_ids(now_ts)
        if update_id in _RECENT_UPDATE_IDS:
            return False
        _RECENT_UPDATE_IDS[update_id] = now_ts
    return True


def _unmark_update(update_id: int | None) -> None:
    if update_id is None:
        return
    with _UPDATE_LOCK:
        _RECENT_UPDATE_IDS.pop(update_id, None)


def _process_update_async(app, environ, update, update_id: int | None):
    try:
        with app.request_context(environ):
            if not _set_bot_token():
                logger.error("Telegram webhook update skipped because bot token is not configured. update_id=%s", update_id)
                return
            bot.process_new_updates([update])
    except Exception as exc:
        logger.exception("Telegram webhook background processing failed for update_id=%s: %s", update_id, exc)


@cache.cache(1000)
def register_bot_cached(set_hook=False, remove_hook=False):
    return register_bot(set_hook, remove_hook)


def register_bot(set_hook=False, remove_hook=False):
    try:
        global bot
        token = _set_bot_token()
        if token:
            configure_telegram_api_proxy()
            try:
                bot.username = bot.get_me().username
            except BaseException:
                pass
            if remove_hook:
                bot.remove_webhook()
            domain = _webhook_domain_override() or Domain.get_panel_link()
            if not domain:
                raise Exception('Cannot get valid domain for setting telegram bot webhook')

            admin_proxy_path = hconfig(ConfigEnum.proxy_path_admin)

            user_secret = AdminUser.get_super_admin_uuid()
            if set_hook:
                kwargs = {}
                secret = _webhook_secret()
                if not secret:
                    logger.error(
                        "Telegram webhook registration skipped: webhook secret is not configured."
                    )
                    return
                kwargs["secret_token"] = secret
                bot.set_webhook(
                    url=f"https://{domain}/{admin_proxy_path}/{user_secret}/api/v1/tgbot/",
                    **kwargs,
                )
    except Exception as e:
        logger.error(e)



def init_app(app):
    with app.app_context():
        global bot
        token = _set_bot_token()
        if token:
            configure_telegram_api_proxy()
            try:
                bot.username = bot.get_me().username
            except BaseException:
                pass


class TGBotResource(Resource):
    def post(self):
        try:
            if not _webhook_secret_is_valid(request):
                logger.error(
                    "Telegram webhook rejected: invalid secret header. path=%s remote=%s",
                    request.path,
                    request.remote_addr,
                )
                return Response("", status=403)
            content_type = (request.headers.get('content-type') or '').lower()
            if not content_type.startswith('application/json'):
                logger.error(
                    "Telegram webhook rejected: invalid content-type=%s",
                    request.headers.get('content-type'),
                )
                return Response("", status=403)
            if not _set_bot_token():
                logger.error("Telegram webhook rejected: bot token is not configured.")
                return Response("", status=503)
            json_string = request.get_data().decode('utf-8')
            logger.info(
                "Telegram webhook received: path=%s remote=%s bytes=%s",
                request.path,
                request.remote_addr,
                len(json_string),
            )
            update = telebot.types.Update.de_json(json_string)
            update_id = getattr(update, "update_id", None)
            if not _mark_update_seen(update_id):
                logger.info("Telegram webhook duplicate update skipped: update_id=%s", update_id)
                return Response("", status=200)

            app = current_app._get_current_object()
            environ = dict(request.environ)
            try:
                _WEBHOOK_EXECUTOR.submit(_process_update_async, app, environ, update, update_id)
            except Exception as exc:
                _unmark_update(update_id)
                logger.exception("Telegram webhook executor submit failed for update_id=%s: %s", update_id, exc)
            return Response("", status=200)
        except (HTTPError, HTTPException):
            raise
        except Exception as e:
            logger.exception("Telegram webhook request validation failed: %s", e)
            return Response("", status=200)
