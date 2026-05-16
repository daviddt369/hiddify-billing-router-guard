from apiflask import APIBlueprint
from flask_restful import Api

from .tgbot import (
    bot,
    register_bot,
    register_bot_cached,
    TGBotResource,
)

# No <uuid:secret_uuid> in the URL prefix: auth.auth_before_request() only blocks
# requests when g.uuid is set. Without a UUID in the path, unauthenticated requests
# (like Telegram webhook POSTs) pass through, and TGBotResource validates the
# X-Telegram-Bot-Api-Secret-Token header directly.
bp = APIBlueprint(
    "api_v2_tgbot",
    __name__,
    url_prefix="/<proxy_path>/api/v2/",
    enable_openapi=False,
)
api = Api(bp)


def init_app(app):
    api.add_resource(TGBotResource, "tgbot/")
    app.register_blueprint(bp)
