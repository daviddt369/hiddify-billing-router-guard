from apiflask import APIBlueprint
from flask_restful import Api

from .tgbot import (
    bot,
    register_bot,
    register_bot_cached,
    TGBotResource,
)

bp_uuid = APIBlueprint(
    "api_v2_tgbot_uuid",
    __name__,
    url_prefix="/<proxy_path>/<uuid:secret_uuid>/api/v2/",
    enable_openapi=False,
)
api_uuid = Api(bp_uuid)


def init_app(app):
    api_uuid.add_resource(TGBotResource, "tgbot/")
    app.register_blueprint(bp_uuid)
