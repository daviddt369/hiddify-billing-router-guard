"""Minimal admin UI ("Страны (локации)") tested through REAL HTTP requests to the running panel via HAProxy+TLS, with a real admin session.
Run: /opt/hiddify-manager/.venv313/bin/python -W ignore -m unittest -v test_07_ui   (from /work/tests)"""
import os
import re
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import requests  # noqa: E402
from stand_lib import *  # noqa: E402,F403

BASE = "https://ru.stand.test"
CA = "/etc/stand/ca.crt"
ADMIN_UUID = None
PP = "stPx0Main1234"
PA = "stAdm0Path12345"
ROUTING = f"{BASE}/{PP}/admin/routing-admin/"
LOC = f"{ROUTING}locations/"


def admin_session():
    global ADMIN_UUID
    ADMIN_UUID = mysql("select uuid from admin_user order by id limit 1")
    s = requests.Session()
    s.verify = CA
    s.get(f"{BASE}/{PA}/{ADMIN_UUID}/", timeout=20)          # landing page answers 500 in stock panel (known), but sets the session
    r = s.get(f"{BASE}/{PP}/admin/routing-admin/", timeout=20)
    assert r.status_code == 200, r.status_code
    return s


def wait_apply(session, timeout=240):
    """poll the common status endpoint of the routing area until the background apply is no longer running; returns the last status (a dict)"""
    t0 = time.monotonic()
    st = {}
    while time.monotonic() - t0 < timeout:
        st = session.get(f"{ROUTING}apply-status/", timeout=60).json()["status"]
        if st.get("state") != "running":
            return st
        time.sleep(1)
    raise AssertionError(f"the background apply did not finish in {timeout} s: {st}")


def setUpModule():
    prepare_apply_tests()                  # sudoers mirror of production (the stand image lacks it), canonical locations, a known 'applied' baseline
    flush_cache()


def tearDownModule():
    reset_locations_to_stand_default()
    apply_router()


class TestLocationsUI(unittest.TestCase):
    def setUp(self):
        # panel sessions live in Redis and flush_cache() (used by the state reset) wipes them -> log in again for every test
        self.s = admin_session()

    def tearDown(self):
        reset_locations_to_stand_default()

    def page(self):
        r = self.s.get(LOC, timeout=20)
        self.assertEqual(r.status_code, 200)
        return r.text

    def test_page_renders_slots_nodes_testers_and_has_no_secret_paths(self):
        h = self.page()
        for needle in ("Германия", "США", "exit-a-1", "exit-b-2", USERS["tester"], 'id="apply-bar"', "Сохранить назначения"):
            self.assertIn(needle, h)
        secrets = mysql("select concat(path_xhttp,' ',path_ws,' ',path_trojan) from commercial_routing_location").split()
        for p in secrets:
            self.assertNotIn(p, h, "the secret inbound paths must never be shown in the UI")
        ids = set(re.findall(r'<form id="(f-slot-\d)"', h))
        used = set(re.findall(r'form="(f-slot-\d)"', h))
        self.assertTrue(used <= ids and len(ids) >= 5, (ids, used))                  # every field is attached to a real form

    def test_the_page_is_in_the_left_menu_not_a_button_on_the_nodes_page(self):
        r = self.s.get(f"{ROUTING}upstreams/", timeout=20)
        self.assertIn(f'href="/{PA}/admin/routing-admin/locations/"', r.text)
        self.assertIn("Страны / локации", r.text)

    def test_save_slot_changes_title_flag_order_visibility_and_the_subscription_follows(self):
        r = self.s.post(f"{LOC}slot/2/", data={"title": "Соединённые Штаты", "country_iso": "us", "code": "us", "sort_order": "5",
                                              "enabled": "1", "visibility": "all"}, timeout=20)
        self.assertEqual(r.status_code, 200)
        row = mysql("select title,country_iso,code,sort_order,enabled,visibility from commercial_routing_location where slot=2")
        self.assertEqual(row, "Соединённые Штаты\tUS\tus\t5\t1\tall")
        names = all_names("active")                      # visibility=all -> a regular user sees it
        self.assertTrue(any("🇺🇸 Соединённые Штаты" in n for n in names), names)

    def test_invalid_input_is_refused_and_nothing_changes(self):
        before = mysql("select slot,title,code,visibility from commercial_routing_location order by slot")
        for data in ({"title": "X", "country_iso": "us", "code": "BAD CODE!", "sort_order": "1", "enabled": "1", "visibility": "testers"},
                     {"title": "X", "country_iso": "us", "code": "us", "sort_order": "1", "enabled": "1", "visibility": "everyone"},
                     {"title": "", "country_iso": "us", "code": "us", "sort_order": "1", "enabled": "1", "visibility": "testers"},
                     {"title": "X", "country_iso": "usa", "code": "us", "sort_order": "1", "enabled": "1", "visibility": "testers"}):
            r = self.s.post(f"{LOC}slot/2/", data=data, timeout=20)
            self.assertIn("Не сохранено", r.text, data)
            self.assertEqual(mysql("select slot,title,code,visibility from commercial_routing_location order by slot"), before, data)
        r = self.s.post(f"{LOC}slot/9/", data={"title": "x"}, timeout=20)
        self.assertEqual(r.status_code, 404)

    def test_assign_nodes_form(self):
        data = {f"slot_{u}": "0" for u in range(1, 5)}
        data.update({"slot_1": "1", "slot_2": "0", "slot_3": "2", "slot_4": "2"})
        r = self.s.post(f"{LOC}assign/", data=data, timeout=20)
        self.assertEqual(r.status_code, 200)
        got = mysql("select n.upstream_id, l.slot from commercial_routing_location_node n join commercial_routing_location l "
                    "on l.id=n.location_id order by 1")
        self.assertEqual(got, "1\t1\n3\t2\n4\t2")
        self.assertEqual(mysql("select count(*) from commercial_routing_location_node"), "3")

    def test_tester_add_and_remove(self):
        new = "88888888-8888-4888-8888-888888888888"
        r = self.s.post(f"{LOC}tester/add/", data={"uuid": new.upper(), "note": "ui test"}, timeout=20)
        self.assertEqual(mysql(f"select note from commercial_routing_location_tester where user_uuid='{new}'"), "ui test")
        r = self.s.post(f"{LOC}tester/add/", data={"uuid": "not-a-uuid"}, timeout=20)
        self.assertIn("корректный UUID", r.text)
        self.assertEqual(mysql("select count(*) from commercial_routing_location_tester where user_uuid='not-a-uuid'"), "0")
        self.s.post(f"{LOC}tester/remove/", data={"uuid": new}, timeout=20)
        self.assertEqual(mysql(f"select count(*) from commercial_routing_location_tester where user_uuid='{new}'"), "0")

    def test_the_apply_button_applies_only_what_is_needed_in_the_background_and_the_page_shows_it(self):
        page = self.page()
        path = ROUTING.replace(BASE, "")                    # the page uses root-relative addresses
        self.assertIn('id="apply-bar"', page, "the shared Apply bar")
        self.assertIn(f'action="{path}apply/"', page, "the one common Apply address")
        self.assertIn(f"{path}apply-status/", page, "and polls the status of the background run")
        self.assertRegex(page, r'<button type="submit" id="apply-btn"[^>]*>Применить</button>')
        self.assertTrue("from-hiddify-s1" in open("/etc/xray-router/config.json").read())        # canonical state: entrances already present
        # nothing changed yet: pressing it does nothing
        r = self.s.post(f"{ROUTING}apply/", headers={"Referer": LOC}, timeout=60)
        self.assertIn("Всё уже применено", r.text)
        # take Germany away from its nodes -> a blocked slot after the apply
        data = {f"slot_{u}": "0" for u in range(1, 5)}
        data.update({"slot_3": "2", "slot_4": "2"})
        self.s.post(f"{LOC}assign/", data=data, timeout=20)
        self.assertIn("router: ждёт применения", self.page())
        before = open("/etc/xray-router/config.json").read()
        t0 = time.monotonic()
        r = self.s.post(f"{ROUTING}apply/", headers={"Referer": LOC}, timeout=60)
        self.assertLess(time.monotonic() - t0, 15, "the panel never waits for the apply")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Применение запущено", r.text)
        st = wait_apply(self.s)
        self.assertEqual((st["state"], st["mode"], st["rc"]), ("ok", "auto", 0), st)
        self.assertNotEqual(open("/etc/xray-router/config.json").read(), before, "the router config was rebuilt")
        self.assertIn("router: синхронизирован", self.page())
        self.assertEqual(via_many(pick(links("tester"), "Германия", "WS")[1], 3, timeout=6), [None] * 3, "slot without nodes is closed")
        self.assertEqual(set(via_many(pick(links("tester"), "США", "WS")[1], 3)), {"exit-b"})

    def test_anonymous_and_wrong_role_cannot_use_it(self):
        anon = requests.Session()
        anon.verify = CA
        before = mysql("select slot,title,visibility,enabled from commercial_routing_location order by slot")
        for url in (LOC, f"{LOC}slot/1/", f"{LOC}assign/", f"{LOC}tester/add/", f"{ROUTING}apply/"):
            r = anon.get(url, timeout=20, allow_redirects=False) if url == LOC else anon.post(url, data={"title": "hacked", "uuid": USERS["active"]},
                                                                                          timeout=20, allow_redirects=False)
            self.assertIn(r.status_code, (301, 302, 303, 307, 401, 403, 404), url)
        r = anon.get(f"{ROUTING}apply-status/", timeout=20, allow_redirects=False)
        self.assertIn(r.status_code, (301, 302, 303, 307, 401, 403, 404), "the status of the apply is for admins only")
        self.assertNotIn("state", r.text[:300] if r.headers.get("content-type", "").startswith("application/json") else "")
        user_sess = requests.Session()
        user_sess.verify = CA
        user_sess.get(f"{BASE}/{PP}/{USERS['active']}/", timeout=20)                 # a regular user's own page/session
        r = user_sess.post(f"{LOC}slot/1/", data={"title": "hacked", "enabled": "1", "visibility": "all"}, timeout=20, allow_redirects=False)
        self.assertIn(r.status_code, (301, 302, 303, 307, 401, 403, 404))
        self.assertEqual(mysql("select slot,title,visibility,enabled from commercial_routing_location order by slot"), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
