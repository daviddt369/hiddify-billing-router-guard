"""CP-18: the texts of the Telegram bot are editable on the page «Бизнес → Telegram-бот» (the phrases the bot sends at registration, with the subscription link, at payment ...),
and every page of the routing area carries ONE collapsed «Инструкция» panel.
Run: /opt/hiddify-manager/.venv313/bin/python -W ignore -m unittest -v test_22_bot_texts   (from /work/tests; real panel)"""
import json
import os
import re
import shutil
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stand_lib import *  # noqa: E402,F403
from test_07_ui import admin_session  # noqa: E402
from test_13_apply import form_fields  # noqa: E402

BASE = "https://ru.stand.test"
PP = "stPx0Main1234"
TG = f"{BASE}/{PP}/admin/business-admin/?section=telegram"
UI = "/opt/hiddify-manager/hiddify-panel/var/business-telegram-ui.json"
BAK = "/tmp/test22-business-telegram-ui.json"
DEFAULT_TRIAL = "Добро пожаловать! Вам выдан пробный доступ: %(gb)s ГБ на %(days)s дн."


def setUpModule():
    if os.path.exists(UI):
        shutil.copy2(UI, BAK)
    else:
        open(BAK, "w").write("")


def tearDownModule():
    if os.path.getsize(BAK):
        shutil.copy2(BAK, UI)
    elif os.path.exists(UI):
        os.remove(UI)


def bot_texts():
    try:
        return json.load(open(UI, encoding="utf-8")).get("bot_texts") or {}
    except Exception:
        return {}


def post_texts(s, **changes):
    html = s.get(TG, timeout=60).text
    data = form_fields(html, "/business-admin/")
    for k, v in changes.items():
        data["bot_text__" + k] = v
    return s.post(f"{BASE}/{PP}/admin/business-admin/", data=data, timeout=120, allow_redirects=True)


class TestBotTexts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = admin_session()

    def test_1_page_shows_every_group_with_the_current_texts(self):
        html = self.s.get(TG, timeout=60).text
        for title in ("Подключение бота", "Регистрация и пробный доступ", "Ссылка на подписку", "Тарифы и оплата", "Инструкция и напоминания о продлении", "Ошибки привязки номера", "Подсказки и кнопки"):
            self.assertIn(title, html, title)
        m = re.search(r'name="bot_text__reg_trial_granted"[^>]*>(.*?)</textarea>', html, re.S)
        self.assertTrue(m, "no field for the trial text")
        self.assertIn("{gb}", m.group(1))
        self.assertIn("{days}", m.group(1))
        self.assertNotIn("три кнопки", html)
        self.assertNotIn("Германия, США", html)

    def test_2_custom_text_is_saved_and_used_by_the_bot(self):
        text = "Привет! Тебе выдан тест: {gb} ГБ на {days} дн. Приятного пользования."
        r = post_texts(self.s, reg_trial_granted=text)
        self.assertEqual(r.status_code, 200, r.text[:200])
        self.assertEqual(bot_texts().get("reg_trial_granted"), text)
        # what the bot module does with it: the wrapper replaces the built-in phrase and fills the placeholders
        out = sh(["/opt/hiddify-manager/.venv313/bin/python", "-c",
                  "from hiddifypanel.hutils import tg_texts as t\n"
                  "g = t.make_gettext(lambda m, **k: m % k)\n"
                  "print(g(" + repr(DEFAULT_TRIAL) + ", gb=1, days=2))\n"
                  "print(g('Выберите тариф:'))"], check=False, cwd="/opt/hiddify-manager/hiddify-panel").stdout.strip().splitlines()
        self.assertEqual(out[-2], "Привет! Тебе выдан тест: 1 ГБ на 2 дн. Приятного пользования.")
        self.assertEqual(out[-1], "Выберите тариф:")                       # a text nobody edited stays as it is
        html = self.s.get(TG, timeout=60).text
        self.assertIn("изменён", html)

    def test_3_wrong_placeholder_is_refused_and_nothing_changes(self):
        before = bot_texts()
        r = post_texts(self.s, reg_trial_granted="Тест {foo} и {gb}")
        self.assertIn("нельзя использовать", r.text)
        self.assertIn("{foo}", r.text)
        self.assertEqual(bot_texts(), before)
        r = post_texts(self.s, links_one="ссылка { без закрытия")
        self.assertIn("фигурные скобки", r.text)
        self.assertEqual(bot_texts(), before)

    def test_4_clearing_the_field_restores_the_built_in_text(self):
        post_texts(self.s, reg_trial_granted="Что-то своё {gb}")
        self.assertIn("reg_trial_granted", bot_texts())
        post_texts(self.s, reg_trial_granted="")
        self.assertNotIn("reg_trial_granted", bot_texts())
        html = self.s.get(TG, timeout=60).text
        m = re.search(r'name="bot_text__reg_trial_granted"[^>]*>(.*?)</textarea>', html, re.S)
        self.assertIn("Добро пожаловать", m.group(1))

    def test_5_the_stale_germany_usa_wording_is_gone_from_the_bot(self):
        src = open("/opt/hiddify-manager/.venv313/lib/python3.13/site-packages/hiddifypanel/panel/commercial/telegrambot/Usage.py", encoding="utf-8").read()
        self.assertNotIn("Германия, США", src)
        self.assertNotIn("три кнопки", src)

    def test_6_other_registration_settings_survive_a_save(self):
        mode = mysql("select 1")  # the page still saves: the registration mode is kept
        html = self.s.get(TG, timeout=60).text
        data = form_fields(html, "/business-admin/")
        r = self.s.post(f"{BASE}/{PP}/admin/business-admin/", data=data, timeout=120)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(json.load(open(UI, encoding="utf-8")).get("telegram_registration_mode"), data.get("telegram_registration_mode"))


class TestInstructionPanels(unittest.TestCase):
    PAGES = ["admin/routing-admin/", "admin/routing-admin/upstreams/", "admin/routing-admin/locations/", "admin/routing-admin/rule-sources/", "admin/routing-admin/happ/",
             "admin/anti-share-admin/", "admin/business-admin/?section=telegram", "admin/business-admin/?section=yookassa"]

    @classmethod
    def setUpClass(cls):
        cls.s = admin_session()

    def test_each_page_has_one_collapsed_instruction(self):
        for p in self.PAGES:
            html = self.s.get(f"{BASE}/{PP}/{p}", timeout=60).text
            self.assertEqual(html.count('class="pt-faq"'), 1, p)
            self.assertNotIn('<details class="pt-faq" open', html, p)

    def test_old_scattered_instructions_are_gone(self):
        html = self.s.get(f"{BASE}/{PP}/admin/routing-admin/", timeout=60).text
        for old in ("Внешняя нода = upstream / global", "Для полного split-routing", "первая включённая нода по приоритету"):
            self.assertNotIn(old, html, old)
        html = self.s.get(f"{BASE}/{PP}/admin/routing-admin/upstreams/", timeout=60).text
        self.assertNotIn("первая включённая нода по приоритету", html)

    def test_router_log_is_folded_away(self):
        html = self.s.get(f"{BASE}/{PP}/admin/routing-admin/locations/", timeout=60).text
        self.assertIn('id="apply-log-box"', html)
        self.assertIn("Журнал применения (для диагностики)", html)
        self.assertNotIn('<details id="apply-log-box" class="mt-2" open', html)


def trial_user():
    r = sh(["/opt/hiddify-manager/.venv313/bin/python", "-W", "ignore", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_trial_runner.py")],
           cwd="/opt/hiddify-manager/hiddify-panel", timeout=300, check=False)
    line = [ln for ln in r.stdout.splitlines() if ln.startswith("JSON:")]
    assert line, (r.stdout[-600:], r.stderr[-600:])
    return json.loads(line[-1][5:])


class TestTrialSettings(unittest.TestCase):
    """the numbers of the trial the bot gives are set on the page and are the REAL conditions of the account it creates (not only words in the text)"""
    @classmethod
    def setUpClass(cls):
        cls.s = admin_session()

    def test_1_defaults_are_one_gb_two_days_one_device(self):
        post_texts(self.s, reg_trial_granted="")
        data = json.load(open(UI, encoding="utf-8")) if os.path.exists(UI) else {}
        data.pop("trial", None)
        json.dump(data, open(UI, "w", encoding="utf-8"), ensure_ascii=False)
        html = self.s.get(TG, timeout=60).text
        self.assertRegex(html, r'name="trial_gb"[^>]*value="1"')
        self.assertRegex(html, r'name="trial_days"[^>]*value="2"')
        self.assertRegex(html, r'name="trial_max_ips"[^>]*value="1"')
        got = trial_user()
        self.assertEqual((got["usage_limit_gb"], got["package_days"], got["max_ips"]), (1.0, 2, 1))
        self.assertIn("1 ГБ на 2 дн.", got["text"])

    def test_2_changed_numbers_reach_the_account_and_the_text(self):
        html = self.s.get(TG, timeout=60).text
        data = form_fields(html, "/business-admin/")
        data.update(trial_gb="10", trial_days="5", trial_max_ips="2")
        r = self.s.post(f"{BASE}/{PP}/admin/business-admin/", data=data, timeout=120)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(json.load(open(UI, encoding="utf-8"))["trial"], {"gb": 10.0, "days": 5, "max_ips": 2})
        got = trial_user()
        self.assertEqual((got["usage_limit_gb"], got["package_days"], got["max_ips"]), (10.0, 5, 2))
        self.assertIn("10 ГБ на 5 дн.", got["text"])
        html = self.s.get(TG, timeout=60).text
        self.assertRegex(html, r'name="trial_gb"[^>]*value="10"')

    def test_3_decimal_gb_with_a_comma_is_accepted(self):
        data = form_fields(self.s.get(TG, timeout=60).text, "/business-admin/")
        data.update(trial_gb="0,5", trial_days="3", trial_max_ips="1")
        self.s.post(f"{BASE}/{PP}/admin/business-admin/", data=data, timeout=120)
        got = trial_user()
        self.assertEqual((got["usage_limit_gb"], got["package_days"]), (0.5, 3))
        self.assertIn("0.5 ГБ на 3 дн.", got["text"])

    def test_4_wrong_numbers_are_refused_and_nothing_changes(self):
        before = json.load(open(UI, encoding="utf-8")).get("trial")
        for bad in ({"trial_gb": "0"}, {"trial_gb": "abc"}, {"trial_days": "0"}, {"trial_days": "1.5"}, {"trial_max_ips": "0"}, {"trial_max_ips": "99"}):
            data = form_fields(self.s.get(TG, timeout=60).text, "/business-admin/")
            data.update(bad)
            r = self.s.post(f"{BASE}/{PP}/admin/business-admin/", data=data, timeout=120)
            self.assertIn("неверное значение", r.text, bad)
            self.assertEqual(json.load(open(UI, encoding="utf-8")).get("trial"), before, bad)

    def test_5_the_admin_panel_never_offers_to_install_itself(self):
        for path in ("admin/routing-admin/", "admin/user/", "admin/"):
            html = self.s.get(f"{BASE}/{PP}/{path}", timeout=60).text
            self.assertNotIn("beforeinstallprompt", html, path)
            self.assertEqual(html.count("show_prompt()"), 1, path + ": only the definition remains, nothing calls it")


if __name__ == "__main__":
    unittest.main()
