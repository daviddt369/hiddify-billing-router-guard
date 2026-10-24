"""Creates the trial account exactly as the bot does for a new Telegram user (inside a real web-mode panel app), prints what it got, and deletes it again.
usage: bot_trial_runner.py        prints one line:  JSON:{"usage_limit_gb": .., "package_days": .., "max_ips": .., "text": "<the registration text>"}"""
import json
import os

os.environ.setdefault("HIDDIFY_CFG_PATH", "/opt/hiddify-manager/hiddify-panel/app.cfg")
os.environ.setdefault("HIDDIFY_CONFIG_PATH", "/opt/hiddify-manager/")
os.chdir("/opt/hiddify-manager/hiddify-panel")
import sqlalchemy  # noqa: E402
import hiddifypanel.panel.init_db as _i  # noqa: E402
_i.text = getattr(_i, "text", sqlalchemy.text)
from hiddifypanel import create_app  # noqa: E402

PHONE, TG_ID = "+70000000099", 990000099
app = create_app(app_mode="web")
with app.test_request_context("/"):
    from hiddifypanel.database import db
    from hiddifypanel.models import User
    from hiddifypanel.panel.commercial.telegrambot import Usage
    from hiddifypanel.hutils.tg_texts import trial_settings
    for old in User.query.filter(User.telegram_id == TG_ID).all():
        db.session.delete(old)
    db.session.commit()
    user = Usage._create_user_from_phone(PHONE, TG_ID)
    try:
        t = trial_settings()
        text = Usage._("Добро пожаловать! Вам выдан пробный доступ: %(gb)s ГБ на %(days)s дн.", gb=t["gb_text"], days=t["days"])
        print("JSON:" + json.dumps({"usage_limit_gb": user.usage_limit / Usage.ONE_GIG, "package_days": user.package_days, "max_ips": user.max_ips, "text": text}, ensure_ascii=False))
    finally:
        db.session.delete(user)
        db.session.commit()
