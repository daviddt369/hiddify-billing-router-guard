"""The field for the Happ / INCY routing profile on the "Страны / локации" page and the sidebar entry. The owner wants to see the profile source, paste another one when he needs
to replace the routing, and find the Countries page in the left menu.
Run: /opt/hiddify-manager/.venv313/bin/python -W ignore -m unittest -v test_16_roscom_field   (from /work/tests)"""
import base64
import json
import os
import re
import shutil
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import requests  # noqa: E402
from stand_lib import *  # noqa: E402,F403
sys.path.insert(0, "/work/tools")
import golden  # noqa: E402

FIX = "/work/tests/fixtures/roscom"
GEO = FIX + "/geo"
SETTINGS = "/opt/hiddify-manager/hiddify-panel/var/multiloc-roscom.json"
FLAG = "/opt/hiddify-manager/multiloc-roscom-json.on"
LANDING_ROOT = "/var/lib/routing-links"
LANDING = LANDING_ROOT + "/last-good"
BASE = "https://ru.stand.test"
CA = "/etc/stand/ca.crt"
PP = "stPx0Main1234"
PA = "stAdm0Path12345"                                         # the menu links are built with the admin path
ROUTING = f"{BASE}/{PP}/admin/routing-admin/"
LOC = ROUTING + "locations/"


def profile_text(extra_domain=None, name="RoscomVPN", scheme="happ", route_order="block-proxy-direct"):
    raw = open(f"{FIX}/HAPP_DEFAULT.DEEPLINK", encoding="utf-8").read().strip()
    prof = json.loads(base64.b64decode(raw.split("/onadd/")[1]))
    prof["Name"] = name
    prof["RouteOrder"] = route_order
    if extra_domain:
        prof["DirectSites"] = prof["DirectSites"] + [f"domain:{extra_domain}"]
    return f"{scheme}://routing/onadd/" + base64.b64encode(json.dumps(prof).encode()).decode()


def setUpModule():
    prepare_apply_tests()
    os.makedirs(LANDING, exist_ok=True)
    for p in (LANDING_ROOT, LANDING):
        os.chmod(p, 0o755)
    for c in ("happ", "incy"):
        shutil.copy(f"{FIX}/{c.upper()}_DEFAULT.DEEPLINK", f"{LANDING}/{c}.deeplink")
        os.chmod(f"{LANDING}/{c}.deeplink", 0o644)


def tearDownModule():
    for p in (SETTINGS, FLAG):
        if os.path.exists(p):
            os.remove(p)
    shutil.rmtree(LANDING_ROOT, ignore_errors=True)
    reset_locations_to_stand_default()


def admin_session():
    from test_07_ui import admin_session as s07
    return s07()


def happ_json(ua="Happ/3.5.0"):
    st, hd, body = golden.fetch(USERS["tester"], "happ/", ua)
    assert st == 200, st
    return hd, json.loads(body)


class TestSettingsAndParsing(unittest.TestCase):
    """pure functions with the real panel code (an application context via panel_python)"""

    def py(self, code):
        pre = "import json\nfrom hiddifypanel.hutils import multiloc as ml\n"
        return json.loads(panel_python(pre + code).splitlines()[-1])

    def test_every_way_to_paste_a_profile_is_understood_and_rubbish_is_refused_with_a_reason(self):
        good = profile_text()
        b64 = good.split("/onadd/")[1]
        raw_json = base64.b64decode(b64).decode()
        out = self.py(f"""
good = {good!r}
b64 = good.split('/onadd/')[1]
raw = {raw_json!r}
res = {{}}
for label, text in (('happ', good), ('incy scheme', good.replace('happ://', 'incy://')), ('add', good.replace('/onadd/', '/add/')), ('any scheme', good.replace('happ://', 'foo://')),
                    ('bare base64', b64), ('base64 with newlines', b64[:50] + chr(10) + b64[50:]), ('json', raw), ('urlsafe base64', b64.replace('+', '-').replace('/', '_')),
                    ('trailing newline', good + chr(10))):
    try:
        res[label] = ml.parse_roscom_input(text)['Name']
    except ValueError as exc:
        res[label] = 'ERR ' + str(exc)
bad = {{}}
for label, text in (('empty', ''), ('off command', 'happ://routing/off'), ('text', 'hello world, this is not a profile'), ('broken base64', 'happ://routing/onadd/@@@'),
                    ('not json', 'happ://routing/onadd/' + 'QUJD'), ('json without rules', '{{"Name": "x"}}'), ('huge', 'A' * 30000),
                    ('http link', 'https://example.com/profile')):
    try:
        ml.parse_roscom_input(text)
        bad[label] = 'ACCEPTED'
    except ValueError as exc:
        bad[label] = str(exc)[:90]
print(json.dumps({{'good': res, 'bad': bad}}))
""")
        for label, name in out["good"].items():
            self.assertEqual(name, "RoscomVPN", f"{label}: {name}")
        for label, why in out["bad"].items():
            self.assertNotEqual(why, "ACCEPTED", label)
            self.assertTrue(why, label)

    def test_the_switch_is_the_settings_file_and_the_old_flag_file_only_when_there_is_no_settings(self):
        for p in (SETTINGS, FLAG):
            if os.path.exists(p):
                os.remove(p)
        self.assertFalse(self.py("print(json.dumps(ml.roscom_enabled()))"))
        open(FLAG, "w").close()
        self.assertTrue(self.py("print(json.dumps(ml.roscom_enabled()))"), "v3 compatibility: no settings file -> the flag file decides")
        self.py("ml.roscom_set(enabled=False)\nprint(json.dumps(1))")
        self.assertFalse(self.py("print(json.dumps(ml.roscom_enabled()))"), "the settings file wins over a stale flag file")
        self.py("ml.roscom_set(enabled=True)\nprint(json.dumps(1))")
        self.assertTrue(self.py("print(json.dumps(ml.roscom_enabled()))"))
        os.remove(FLAG)
        os.remove(SETTINGS)

    def test_settings_are_written_atomically_readable_by_everybody_and_replaceable_by_the_panel_user(self):
        self.py("ml.roscom_set(enabled=True, custom='x')\nprint(json.dumps(1))")
        st = os.stat(SETTINGS)
        self.assertEqual(oct(st.st_mode & 0o777), "0o644")
        r = sh(["sudo", "-u", "hiddify-panel", "test", "-w", "/opt/hiddify-manager/hiddify-panel/var"], check=False)
        self.assertEqual(r.returncode, 0, "the panel user can replace the file (the directory is its own)")
        self.assertFalse(os.path.exists(SETTINGS + ".tmp"))
        os.remove(SETTINGS)


class TestTheField(unittest.TestCase):

    def setUp(self):
        for p in (SETTINGS, FLAG):
            if os.path.exists(p):
                os.remove(p)
        flush_cache()
        self.s = admin_session()

    def tearDown(self):
        for p in (SETTINGS, FLAG):
            if os.path.exists(p):
                os.remove(p)

    def page(self):
        r = self.s.get(LOC, timeout=60)
        self.assertEqual(r.status_code, 200)
        return r.text

    def save(self, text, enabled=True, **extra):
        data = {"custom": text}
        if enabled:
            data["enabled"] = "1"
        data.update(extra)
        return self.s.post(LOC + "roscom/", data=data, timeout=60)

    def test_the_card_shows_the_state_the_source_and_the_profile(self):
        h = self.page()
        self.assertIn('id="roscom-card"', h)
        self.assertIn("Маршрутизация RoscomVPN для Happ / INCY", h)
        self.assertIn("выключена", h)
        self.assertIn("автоматический (копия профиля RoscomVPN с лендинга", h)
        self.assertIn("Профиль «RoscomVPN»", h)
        self.assertIn("правил: напрямую 14, через VPN 5, блок 3", h)
        self.assertIn('name="custom"', h)
        self.assertNotIn("Вернуть автоматический", h, "only when a profile of your own is pasted")

    def test_switching_on_and_off_from_the_page_takes_effect_at_once_without_any_restart(self):
        hd, _ = happ_json()
        self.assertNotIn("routing", hd)
        r = self.save("", enabled=True)
        self.assertIn("маршрутизация включена", r.text)
        hd, cfgs = happ_json()
        self.assertIn("routing", hd)
        self.assertEqual(cfgs[0]["routing"]["domainStrategy"], "IPIfNonMatch")
        r = self.save("", enabled=False)
        self.assertIn("выключена", r.text)
        hd, cfgs = happ_json()
        self.assertNotIn("routing", hd)
        self.assertEqual(cfgs[0]["routing"]["domainStrategy"], "AsIs")

    def test_the_header_with_the_lists_is_a_checkbox_and_a_console_command_and_the_json_keeps_its_rules(self):
        self.save("", enabled=True)
        hd, cfgs = happ_json()
        sub = json.loads(base64.b64decode(hd["routing"].split("/onadd/")[1]))
        self.assertEqual((sub["Name"], sub["DirectSites"], sub["BlockSites"]), ("RoscomVPN JSON", [], []), "default: the empty JSONSUB variant")
        self.assertNotIn("roscom-header-lists\" name=\"header_lists\" value=\"1\" checked", self.page())
        r = self.save("", enabled=True, header_lists="1")
        self.assertIn("маршрутизация включена", r.text)
        self.assertIn('name="header_lists" value="1" checked', self.page(), "the checkbox shows the state")
        for ua, scheme in (("Happ/3.5.0", "happ"), ("INCY/2.4.5", "incy")):
            hd, cfgs = happ_json(ua)
            sub = json.loads(base64.b64decode(hd["routing"].split("/onadd/")[1]))
            self.assertEqual(sub["Name"], "RoscomVPN JSON", ua)
            self.assertIn("geosite:category-ru", sub["DirectSites"], f"{ua}: the lists are in the header")
            self.assertIn("geosite:telegram", sub["ProxySites"])
            self.assertIn("geosite:category-ads", sub["BlockSites"])
            domains = [d for rule in cfgs[0]["routing"]["rules"] for d in rule.get("domain", [])]
            self.assertIn("geosite:category-ru", domains, f"{ua}: and the same rules stay inside every JSON config")
        out = ctl("roscom", "status")
        self.assertIn("header profile: WITH the lists", out)
        ctl("roscom", "empty")
        hd, _ = happ_json()
        self.assertEqual(json.loads(base64.b64decode(hd["routing"].split("/onadd/")[1]))["DirectSites"], [])
        self.assertIn("empty JSONSUB variant", ctl("roscom", "status"))
        self.assertTrue(json.loads(ctl("roscom", "status", "--json"))["enabled"], "the shape does not touch the switch")
        ctl("roscom", "lists")
        self.assertTrue(json.loads(ctl("roscom", "status", "--json"))["header_lists"])
        self.assertIn("geosite:category-ru", json.loads(base64.b64decode(happ_json()[0]["routing"].split("/onadd/")[1]))["DirectSites"])

    def test_the_ru_zones_and_banks_are_forced_direct_in_the_header_and_every_json_and_the_checkbox_and_the_console_switch_them(self):
        musts = ["regexp:.*\\.ru$", "regexp:.*\\.su$", "regexp:.*\\.xn\\-\\-p1ai$", "domain:sberbank.com", "domain:tbank-online.com"]
        self.save("", enabled=True, header_lists="1", ru_zone="1")
        self.assertIn('name="ru_zone" value="1" checked', self.page(), "the checkbox shows the state")
        hd, cfgs = happ_json()
        sub = json.loads(base64.b64decode(hd["routing"].split("/onadd/")[1]))
        baked = [d for rule in cfgs[0]["routing"]["rules"] for d in rule.get("domain", [])]
        for m in musts:
            self.assertIn(m, sub["DirectSites"], f"header: {m}")
            self.assertIn(m, baked, f"inside the JSON: {m}")
        self.assertIn("geosite:category-ru", sub["DirectSites"], "the profile's own entries are still there")
        self.assertIn("правил: напрямую 14", self.page(), "the card shows the profile as pasted / published, not the added entries")
        self.save("", enabled=True, header_lists="1")                                       # the box is unticked
        self.assertNotIn('name="ru_zone" value="1" checked', self.page())
        hd, cfgs = happ_json()
        sub = json.loads(base64.b64decode(hd["routing"].split("/onadd/")[1]))
        self.assertNotIn("regexp:.*\\.ru$", sub["DirectSites"])
        self.assertNotIn("regexp:.*\\.ru$", [d for rule in cfgs[0]["routing"]["rules"] for d in rule.get("domain", [])])
        self.assertIn("as the profile says", ctl("roscom", "status"))
        ctl("roscom", "zone-on")
        self.assertIn("DIRECT from the device", ctl("roscom", "status"))
        self.assertTrue(json.loads(ctl("roscom", "status", "--json"))["ru_zone"])
        sub = json.loads(base64.b64decode(happ_json()[0]["routing"].split("/onadd/")[1]))
        self.assertIn("regexp:.*\\.su$", sub["DirectSites"])
        ctl("roscom", "zone-off")
        self.assertFalse(json.loads(ctl("roscom", "status", "--json"))["ru_zone"])

    def test_a_pasted_profile_replaces_the_automatic_one_for_both_apps_and_can_be_taken_back(self):
        mine = profile_text(extra_domain="pasted-by-the-owner.example", name="MyRouting")
        r = self.save(mine)
        self.assertIn("используется вставленный профиль «MyRouting»", r.text)
        h = self.page()
        self.assertIn("профиль, вставленный вручную", h)
        self.assertIn("Профиль «MyRouting»", h)
        self.assertIn("Вернуть автоматический", h)
        self.assertIn("happ://routing/onadd/", h, "the pasted text is shown again in the field")
        for ua, scheme in (("Happ/3.5.0", "happ"), ("INCY/2.4.5", "incy")):
            hd, cfgs = happ_json(ua)
            self.assertTrue(hd["routing"].startswith(f"{scheme}://routing/onadd/"), ua)
            sub = json.loads(base64.b64decode(hd["routing"].split("/onadd/")[1]))
            self.assertEqual(sub["Name"], "RoscomVPN JSON", "the header profile keeps its own name whatever the pasted profile was called")
            for c in cfgs:
                domains = [d for rule in c["routing"]["rules"] for d in rule.get("domain", [])]
                self.assertIn("domain:pasted-by-the-owner.example", domains, f"{ua}: the pasted rules are inside every config")
        import tempfile
        for c in cfgs:
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
                json.dump(c, f)
            r = sh(["/usr/bin/xray", "run", "-test", "-config", f.name], check=False, env={**os.environ, "XRAY_LOCATION_ASSET": GEO})
            os.unlink(f.name)
            self.assertIn("Configuration OK", r.stdout + r.stderr, c["remarks"])
        r = self.save("", enabled=True, action="clear")
        self.assertIn("снова используется автоматический", r.text)
        hd, cfgs = happ_json()
        domains = [d for rule in cfgs[0]["routing"]["rules"] for d in rule.get("domain", [])]
        self.assertNotIn("domain:pasted-by-the-owner.example", domains)
        self.assertIn("geosite:whitelist", domains)
        self.assertNotIn("Вернуть автоматический", self.page())

    def test_a_rejected_profile_changes_nothing_and_stays_in_the_field_with_the_reason(self):
        self.save("", enabled=True)
        before = open(SETTINGS).read()
        junk = "happ://routing/onadd/QUJD-not-a-profile"
        r = self.save(junk)
        self.assertEqual(r.status_code, 200)
        self.assertIn("Профиль не принят, ничего не изменено", r.text)
        self.assertIn(junk, r.text, "what was pasted is not lost")
        self.assertEqual(open(SETTINGS).read(), before)
        hd, _ = happ_json()
        self.assertIn("routing", hd, "the working state did not change")
        off = self.save("happ://routing/off")
        self.assertIn("switches routing OFF", off.text)
        self.assertEqual(open(SETTINGS).read(), before)

    def test_a_profile_without_rules_or_with_a_foreign_tag_syntax_is_refused(self):
        bare = json.loads(base64.b64decode(profile_text().split("/onadd/")[1]))
        for k in ("DirectSites", "DirectIp", "ProxySites", "ProxyIp", "BlockSites", "BlockIp"):
            bare[k] = []
        r = self.save("happ://routing/onadd/" + base64.b64encode(json.dumps(bare).encode()).decode())
        self.assertIn("no routing rules", r.text)
        evil = json.loads(base64.b64decode(profile_text().split("/onadd/")[1]))
        evil["DirectSites"] = ["geosite:ok", 'domain:x"; DROP']
        r = self.save("happ://routing/onadd/" + base64.b64encode(json.dumps(evil).encode()).decode())
        self.assertIn("unsupported", r.text)
        self.assertFalse(os.path.exists(SETTINGS))

    def test_the_console_and_the_page_share_one_state(self):
        self.save(profile_text(name="FromThePage"))
        out = ctl("roscom", "status")
        self.assertIn("ON", out)
        self.assertIn("pasted on the panel page", out)
        self.assertIn("FromThePage", out)
        ctl("roscom", "off")
        self.assertIn("выключена", self.page())
        ctl("roscom", "on")
        self.assertIn("включена", self.page())
        self.assertEqual(os.stat(SETTINGS).st_uid, int(sh(["id", "-u", "hiddify-panel"]).stdout), "written by root, owned by the panel user so that the page can rewrite it")

    def test_nobody_but_an_admin_can_change_it(self):
        anon = requests.Session()
        anon.verify = CA
        r = anon.post(LOC + "roscom/", data={"custom": profile_text(), "enabled": "1"}, timeout=20, allow_redirects=False)
        self.assertIn(r.status_code, (301, 302, 303, 307, 401, 403, 404))
        self.assertFalse(os.path.exists(SETTINGS))


class TestSidebar(unittest.TestCase):

    def setUp(self):
        flush_cache()
        self.s = admin_session()

    def test_the_countries_page_is_in_the_left_menu_under_routing_on_every_admin_page(self):
        want = f'href="/{PA}/admin/routing-admin/locations/"'
        for name, url in (("routing", ROUTING), ("nodes", ROUTING + "upstreams/"), ("sources", ROUTING + "rule-sources/"), ("countries", LOC)):
            h = self.s.get(url, timeout=60).text
            self.assertIn(want, h, name)
            self.assertIn("Страны / локации", h, name)
            side = h.split('id="routing-sidebar"', 1)[1].split("</div>", 1)[0]
            order = [side.find(t) for t in ("Маршрутизация", "Внешние ноды", "Источники правил", "Страны / локации")]
            self.assertEqual(order, sorted(order), f"{name}: the entry sits after the other three, inside the Routing group")
            self.assertTrue(all(i >= 0 for i in order), name)
        dash = self.s.get(f"{BASE}/{PP}/admin/", timeout=60).text
        self.assertIn(want, dash, "also on the admin start page")

    def test_the_odd_button_on_the_nodes_page_is_gone(self):
        h = self.s.get(ROUTING + "upstreams/", timeout=60).text
        body = h.split('id="routing-sidebar"', 1)[1].split("</aside>", 1)[1]
        self.assertNotIn("Страны (локации)", body, "the entry moved to the menu: no extra button on the nodes page")


if __name__ == "__main__":
    unittest.main(verbosity=2)
