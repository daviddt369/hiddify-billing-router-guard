"""Stand test of the bot changes (no network: the bot object is replaced by a recorder).
usage (inside the panel venv, from any dir): bot_menu_test.py <telegram_id of a test account>"""
import html.parser
import os
import sys
import types as pytypes

os.environ.setdefault("HIDDIFY_CFG_PATH", "/opt/hiddify-manager/hiddify-panel/app.cfg")
os.environ.setdefault("HIDDIFY_CONFIG_PATH", "/opt/hiddify-manager/")
os.chdir("/opt/hiddify-manager/hiddify-panel")
import sqlalchemy  # noqa: E402
import hiddifypanel.panel.init_db as _i  # noqa: E402
_i.text = getattr(_i, "text", sqlalchemy.text)
from hiddifypanel import create_app  # noqa: E402

TG_ID = int(sys.argv[1])
OK = FAIL = 0


def check(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print("  PASS ", name)
    else:
        FAIL += 1
        print("  FAIL ", name, extra)


def _f(b, name):
    return b[name] if isinstance(b, dict) else getattr(b, name, None)


class Rec:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def f(*a, **k):
            self.calls.append((name, a, k))
        return f


class TgHtml(html.parser.HTMLParser):
    ALLOWED = {"b", "i", "u", "s", "a", "code", "pre"}

    def __init__(self):
        super().__init__()
        self.stack, self.bad = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in self.ALLOWED:
            self.bad.append("tag " + tag)
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            self.bad.append("unbalanced " + tag)


app = create_app(app_mode="web")
with app.test_request_context("/"):
    from hiddifypanel.models import User
    from hiddifypanel.panel.commercial.telegrambot import Usage
    user = User.query.filter(User.telegram_id == TG_ID).first()
    check("the test account exists", user is not None)
    rec = Rec()
    Usage.bot = rec

    labels = [_f(b, "text") for row in Usage._user_menu_keyboard().keyboard for b in row]
    print("   menu:", labels)
    check("menu has 'Личный кабинет'", "Личный кабинет" in labels)
    check("menu no longer has 'Моя подписка'", "Моя подписка" not in labels)
    check("menu keeps tariff buttons and the instruction button", {"Мой тариф", "Сменить тариф", "Продлить тариф"} <= set(labels) and Usage._telegram_instruction_button_text() in labels)

    kb = Usage._subscription_link_keyboard(user)
    rows = [[(_f(b, "text"), _f(b, "url")) for b in row] for row in kb.keyboard]
    check("links keyboard: one row, the cabinet button", len(rows) == 1 and rows[0][0][0] == "Открыть личный кабинет", str(rows))
    msg = Usage._subscription_links_message(user)
    print("   links message:\n      " + msg.replace("\n", "\n      "))
    check("links message names the cabinet and shows the link", "личный кабинет" in msg.lower() and rows[0][0][1] in msg)

    for label in ("Личный кабинет", "Моя подписка"):
        rec.calls.clear()
        m = pytypes.SimpleNamespace(text=label, chat=pytypes.SimpleNamespace(id=TG_ID), from_user=pytypes.SimpleNamespace(id=TG_ID))
        handled = Usage._handle_user_menu_action(m)
        names = [c[0] for c in rec.calls]
        sent = [c for c in rec.calls if c[0] == "reply_to"]
        check(f"'{label}' is handled and the links message is sent", handled is True and bool(sent) and rows[0][0][1] in (sent[0][1][1] if len(sent[0][1]) > 1 else ""), str(names))

    rec.calls.clear()
    m = pytypes.SimpleNamespace(text=Usage._telegram_instruction_button_text(), chat=pytypes.SimpleNamespace(id=TG_ID), from_user=pytypes.SimpleNamespace(id=TG_ID))
    handled = Usage._handle_user_menu_action(m)
    sent = [c for c in rec.calls if c[0] == "send_message"]
    text = sent[0][1][1] if sent and len(sent[0][1]) > 1 else ""
    p = TgHtml()
    p.feed(text)
    check("instruction button sends the instruction (HTML mode)", handled is True and bool(sent) and sent[0][2].get("parse_mode") == "HTML")
    check("instruction is valid Telegram HTML (allowed tags, balanced)", not p.bad and not p.stack, str(p.bad))
    check("instruction fits one message (< 4096)", 0 < len(text) < 4096, str(len(text)))
    import re
    hrefs = re.findall(r'href="([^"]+)"', text)
    allowed = ("https://apps.apple.com/", "https://play.google.com/", "https://github.com/hiddify/")
    check("instruction mentions the cabinet, INCY first on iPhone, only app-store links (and the region guide page), no markdown stars", "Личный кабинет" in text and text.index("INCY") < text.index("Hiddify") and "**" not in text and bool(hrefs) and all(h.startswith(allowed) or h.endswith("/#region") for h in hrefs), str(hrefs))
    check("instruction has no template leftovers (@@, [[GUIDE]])", "@@" not in text and "[[" not in text and "]]" not in text)
    w = Usage._telegram_welcome_message()
    check("welcome message points to the cabinet and has no external link", "Личный кабинет" in w and "http" not in w, w[:80])

print(f"\nRESULT: {OK} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
