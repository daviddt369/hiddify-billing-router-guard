"""Telegram bot: ONE stock link for everybody who is not shown the AUTO block (message and keyboard byte-for-byte as before),
TWO links for the testers - the usual one, which stays EXACTLY as it is for every app (Hiddify, v2RayTun, ...), and the dedicated Happ / INCY
link `/happ/` (three buttons).
The bot's own builders run in a real web-mode panel app; the links are then fetched from the real panel with the matching app User-Agent.

Run: /opt/hiddify-manager/.venv313/bin/python -W ignore -m unittest -v test_11_bot_links   (from /work/tests)"""
import json
import os
import sys
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stand_lib import *  # noqa: E402,F403

HOST = "ru.stand.test"
STOCK_MESSAGE = ("Вот ваша ссылка на подписку:\n{link}\n\n"
                 "Скопируйте её и вставьте в ваш клиент. Эта же ссылка открывает личный кабинет.")


def bot_view(uuid):
    r = sh([VENV_PY, "-W", "ignore", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_runner.py"), uuid],
           cwd="/opt/hiddify-manager/hiddify-panel", timeout=180)
    line = [ln for ln in r.stdout.splitlines() if ln.startswith("JSON:")][-1]
    return json.loads(line[5:])


def user_link(uuid):
    return f"https://{HOST}/{PC}/{uuid}/"


def fetch(link, ua):
    path = link.split(HOST, 1)[1]
    req = urllib.request.Request("http://127.0.0.1:9000" + path, headers={"Host": HOST, "User-Agent": ua})
    r = urllib.request.urlopen(req, timeout=60)
    return r.read().decode("utf-8", "replace"), {k.lower(): v for k, v in r.getheaders()}


def setUpModule():
    reset_locations_to_stand_default()
    flush_cache()


def tearDownModule():
    reset_locations_to_stand_default()


class TestBotLinks(unittest.TestCase):
    def tearDown(self):
        reset_locations_to_stand_default()

    def test_a_regular_user_gets_exactly_the_stock_message_and_keyboard(self):
        v = bot_view(USERS["active"])
        link = user_link(USERS["active"])
        self.assertEqual(v["message"], STOCK_MESSAGE.format(link=link))
        self.assertEqual(v["keyboard"], [[["Открыть подписку", link]]])

    def test_a_tester_gets_two_links_the_usual_one_first_and_the_happ_incy_one_second(self):
        v = bot_view(USERS["tester"])
        usual, happ = user_link(USERS["tester"]), user_link(USERS["tester"]) + "happ/"
        self.assertIn(usual + "\n", v["message"])
        self.assertIn(happ + "\n", v["message"])
        self.assertLess(v["message"].index(usual), v["message"].index(happ))
        self.assertIn("Hiddify", v["message"])
        self.assertIn("Happ", v["message"])
        self.assertEqual(v["keyboard"], [[["Открыть подписку", usual]], [["Happ / INCY: три кнопки", happ]]])

    def test_the_usual_link_never_changes_and_the_happ_link_gives_the_three_buttons(self):
        usual, happ = user_link(USERS["tester"]), user_link(USERS["tester"]) + "happ/"
        # the usual link: the same stock answer for EVERY app (no switching by User-Agent), tester included
        for ua in ("HiddifyNext/2.0.5 (android) like ClashMeta v2ray sing-box", "v2RayTun/1.0", "v2rayNG/1.8.5", "Happ/3.5.0", "INCY/1.0"):
            body, _ = fetch(usual, ua)
            self.assertFalse(any(w in body for w in ("Германия", "США", "AUTO", "balancers")), f"{ua}: the usual link must stay stock")
        sb = json.loads(fetch(usual, "HiddifyNext/2.0.5 (android) like ClashMeta v2ray sing-box")[0])
        self.assertTrue(any("RU_GLOBAL" in o.get("tag", "") for o in sb["outbounds"]), "Hiddify keeps its own sing-box format with every legacy protocol")
        for ua in ("Happ/3.5.0", "INCY/1.0"):
            body, hd = fetch(happ, ua)
            cfgs = json.loads(body)
            self.assertEqual([c["remarks"] for c in cfgs], ["🟢 AUTO", "🇩🇪 Германия", "🇺🇸 США"], ua)
            self.assertEqual(hd.get("content-type", "").split(";")[0], "application/json")

    def test_without_the_auto_block_or_for_an_inactive_account_there_is_one_link(self):
        link = user_link(USERS["tester"])
        ctl("auto", "--visibility", "hidden")
        flush_cache()
        v = bot_view(USERS["tester"])
        self.assertEqual(v["message"], STOCK_MESSAGE.format(link=link))
        self.assertEqual(v["keyboard"], [[["Открыть подписку", link]]])
        ctl("auto", "--visibility", "testers")
        ctl("tester", "add", USERS["expired"], "--note", "inactive tester")
        flush_cache()
        v = bot_view(USERS["expired"])
        self.assertEqual(v["message"], STOCK_MESSAGE.format(link=user_link(USERS["expired"])), "an expired account keeps the single stock link")

    def test_a_removed_tester_gets_the_single_link_again(self):
        ctl("tester", "remove", USERS["tester"])
        flush_cache()
        v = bot_view(USERS["tester"])
        self.assertEqual(v["message"], STOCK_MESSAGE.format(link=user_link(USERS["tester"])))


if __name__ == "__main__":
    unittest.main(verbosity=2)
