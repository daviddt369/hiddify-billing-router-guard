"""Runs the Telegram bot's own message / keyboard builders for one account inside a real web-mode panel app (flask-babel needs it).
usage: bot_runner.py <uuid>      prints one line:  JSON:{"message": "...", "keyboard": [[[text, url], ...], ...]}"""
import json
import os
import sys

os.environ.setdefault("HIDDIFY_CFG_PATH", "/opt/hiddify-manager/hiddify-panel/app.cfg")
os.environ.setdefault("HIDDIFY_CONFIG_PATH", "/opt/hiddify-manager/")
os.chdir("/opt/hiddify-manager/hiddify-panel")
import sqlalchemy  # noqa: E402
import hiddifypanel.panel.init_db as _i  # noqa: E402
_i.text = getattr(_i, "text", sqlalchemy.text)
from hiddifypanel import create_app  # noqa: E402

app = create_app(app_mode="web")
with app.test_request_context("/"):
    from hiddifypanel.models import User
    from hiddifypanel.panel.commercial.telegrambot import Usage
    user = User.by_uuid(sys.argv[1])
    kb = Usage._subscription_link_keyboard(user)
    print("JSON:" + json.dumps({"message": Usage._subscription_links_message(user),
                                "keyboard": [[[b.text, b.url] for b in row] for row in kb.keyboard]}, ensure_ascii=False))
