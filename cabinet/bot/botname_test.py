"""Stand test of the bot username lookup of the cabinet (no network: the bot object is a fake).
usage (panel venv): botname_test.py"""
import os
import sys
import time

os.environ.setdefault("HIDDIFY_CFG_PATH", "/opt/hiddify-manager/hiddify-panel/app.cfg")
os.environ.setdefault("HIDDIFY_CONFIG_PATH", "/opt/hiddify-manager/")
os.chdir("/opt/hiddify-manager/hiddify-panel")
import sqlalchemy  # noqa: E402
import hiddifypanel.panel.init_db as _i  # noqa: E402
_i.text = getattr(_i, "text", sqlalchemy.text)
from hiddifypanel.panel.user import cabinet  # noqa: E402

OK = FAIL = 0


def check(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print("  PASS ", name)
    else:
        FAIL += 1
        print("  FAIL ", name, extra)


class Bot:
    def __init__(self, username="", answer="demo_bot", delay=0.0, fail=False):
        self.username, self.answer, self.delay, self.fail, self.calls = username, answer, delay, fail, 0

    def get_me(self):
        self.calls += 1
        time.sleep(self.delay)
        if self.fail:
            raise OSError("no route to api.telegram.org")
        return type("Me", (), {"username": self.answer})()


def reset():
    cabinet._bot_name.update(name="", tried=0.0)


reset()
check("no bot object -> no name", cabinet._bot_username(None, {}) == "")
check("setting wins (and a leading @ is cut)", cabinet._bot_username(Bot(), {"bot_username": "@my_bot"}) == "my_bot")
check("the bot object's own username is used", cabinet._bot_username(Bot(username="obj_bot"), {}) == "obj_bot")
check("the placeholder \"-\" (no bot named at install time) falls through to the bot object", cabinet._bot_username(Bot(username="obj_bot"), {"bot_username": "-"}) == "obj_bot")
check("an invalid setting is not used as a link part", cabinet._bot_username(Bot(), {"bot_username": "bad name/../x"}) == "")
reset()
b = Bot(delay=0.4)
t0 = time.time()
first = cabinet._bot_username(b, {})
check("empty username: the request does NOT wait for Telegram", first == "" and time.time() - t0 < 0.2, f"{first!r} {time.time() - t0:.2f}s")
time.sleep(0.8)
check("a later visit has the name (asked once)", cabinet._bot_username(b, {}) == "demo_bot" and b.calls == 1, f"calls={b.calls}")
reset()
f = Bot(fail=True)
check("Telegram unreachable: no name, no error", cabinet._bot_username(f, {}) == "")
time.sleep(0.2)
check("and not asked again within 10 minutes", cabinet._bot_username(f, {}) == "" and f.calls == 1, f"calls={f.calls}")
reset()
w = Bot(answer="bad name!")
cabinet._bot_username(w, {})
time.sleep(0.2)
check("an answer that is not a username is dropped", cabinet._bot_username(w, {}) == "")
print(f"\nRESULT: {OK} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
