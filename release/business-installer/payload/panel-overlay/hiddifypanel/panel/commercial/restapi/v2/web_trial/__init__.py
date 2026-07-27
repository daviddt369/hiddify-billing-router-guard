from apiflask import APIBlueprint
from flask_restful import Api

from .trial import WebTrialResource

bp = APIBlueprint(
    "api_v2_web_trial",
    __name__,
    url_prefix="/<proxy_path>/api/v2/",
    enable_openapi=False,
)
api = Api(bp)


def init_app(app):
    api.add_resource(WebTrialResource, "trial/")
    app.register_blueprint(bp)
