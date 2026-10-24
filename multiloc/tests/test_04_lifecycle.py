"""User lifecycle against the REAL running xray (hot add / remove through the xray gRPC API, exactly like the panel's periodic task does),
independent of inbound statistics, plus usage accounting and anti-share log compatibility for the new inbounds.

Run: /opt/hiddify-manager/.venv313/bin/python -W ignore -m unittest -v test_04_lifecycle   (from /work/tests)"""
import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stand_lib import *  # noqa: E402,F403

XRAY = "/opt/hiddify-manager/xray/bin/xray"
NEW_UUID = "66666666-6666-4666-8666-666666666666"
NEW2_UUID = "77777777-7777-4777-8777-777777777777"


def all_inbound_tags():
    r = sh([XRAY, "api", "lsi", "--server=127.0.0.1:10085"])
    return sorted(i["tag"] for i in json.loads(r.stdout)["inbounds"] if i.get("tag") and i["tag"] != "api")


def loc_tags():
    return [t for t in all_inbound_tags() if "-loc" in t]


def legacy_tags():
    return [t for t in all_inbound_tags() if "-loc" not in t]


def inbound_users(tag):
    r = sh([XRAY, "api", "inbounduser", "--server=127.0.0.1:10085", f"-tag={tag}"], check=False)
    try:
        return {u["email"].split("@")[0] for u in json.loads(r.stdout).get("users", []) if u.get("email")}
    except Exception:
        return set()


def membership(uuid, tags):
    return {t: (uuid in inbound_users(t)) for t in tags}


def create_user(name, uuid, **extra):
    panel_python(f"""
import datetime
from hiddifypanel.models import User, UserMode
from hiddifypanel.database import db
u = User.query.filter(User.uuid=={uuid!r}).first()
if u is None:
    u = User(name={name!r}, uuid={uuid!r}, usage_limit_GB=50, package_days=30, mode=UserMode.no_reset, added_by=1, max_ips=5)
    db.session.add(u)
u.start_date = datetime.date.today(); u.enable = True; u.current_usage = 0; u.usage_limit_GB = 50; u.package_days = 30
db.session.commit()
print('created', u.id)
""")


def delete_user_via_model(uuid):
    panel_python(f"""
from hiddifypanel.models import User
u = User.query.filter(User.uuid=={uuid!r}).first()
if u: u.remove()
print('removed')
""")


def set_user(uuid, **fields):
    sets = ";".join(f"u.{k}={v!r}" for k, v in fields.items())
    panel_python(f"""
from hiddifypanel.models import User
from hiddifypanel.database import db
u = User.query.filter(User.uuid=={uuid!r}).first()
{sets}
db.session.commit()
""")


def usage_cycle():
    """the function Celery beat runs every minute (compares xray vs DB, hot-adds / removes users, accounts usage)"""
    return panel_python("from hiddifypanel.panel import usage; r = usage.update_local_usage_not_lock(); print('cycle', r.get('status'))", timeout=240)


def user_link(uuid, needle, host="ru.stand.test"):
    return pick(links(uuid=uuid), *needle if isinstance(needle, tuple) else (needle,))[1]


def setUpModule():
    reset_locations_to_stand_default()
    apply_router()
    for u in (NEW_UUID, NEW2_UUID):
        delete_user_via_model(u)
    usage_cycle()


def tearDownModule():
    for u in (NEW_UUID, NEW2_UUID):
        delete_user_via_model(u)
    usage_cycle()


def clean_new_users():
    for u in (NEW_UUID, NEW2_UUID):
        delete_user_via_model(u)
    usage_cycle()


class TestHotAdd(unittest.TestCase):
    def setUp(self):
        clean_new_users()

    def test_new_user_reaches_every_inbound_including_location_inbounds_without_xray_restart(self):
        pid_before = sh(["systemctl", "show", "-p", "MainPID", "hiddify-xray"]).stdout
        create_user("u-new", NEW_UUID)
        usage_cycle()
        after = membership(NEW_UUID, all_inbound_tags())
        missing = [t for t, ok in after.items() if not ok and not t.startswith("realityin")]
        self.assertEqual(missing, [], f"new user not added to: {missing}")
        self.assertEqual(len(loc_tags()), 12, loc_tags())
        self.assertEqual(sh(["systemctl", "show", "-p", "MainPID", "hiddify-xray"]).stdout, pid_before, "xray must not have been restarted")

    def test_new_tester_connects_through_countries_right_after_the_cycle(self):
        create_user("u-new", NEW_UUID)
        ctl("tester", "add", NEW_UUID, "--note", "lifecycle")
        try:
            usage_cycle()
            for needle, expect in ((("Германия", "XHTTP"), "exit-a"), (("США", "WS"), "exit-b"), (("Германия", "Trojan"), "exit-a")):
                self.assertEqual(via(user_link(NEW_UUID, needle)), expect, needle)
            self.assertIn(via(user_link(NEW_UUID, ("RU_GLOBAL tls_h2 xhttp direct vless dl=h2",))), ("exit-a", "exit-b"))
            self.assertIn(via(user_link(NEW_UUID, ("RU_GLOBAL tls WS direct vless",))), ("exit-a", "exit-b"))
        finally:
            ctl("tester", "remove", NEW_UUID)

    def test_hot_add_does_not_depend_on_inbound_statistics(self):
        """stats unavailable -> the patched driver still adds the user to every inbound (lsi); the ORIGINAL driver adds nobody"""
        create_user("u-new2", NEW2_UUID)
        code = f"""
import importlib.util, json
from hiddifypanel.models import User
from hiddifypanel.drivers.xray_api import XrayApi
u = User.query.filter(User.uuid=={NEW2_UUID!r}).first()
api = XrayApi(); api.get_xray_client().stats_query = lambda *a, **k: []          # statistics gone
print('patched tags:', len(api.get_inbound_tags()))
api.add_client(u)
# negative control: the pristine prod driver with the same failure
spec = importlib.util.spec_from_file_location('hiddifypanel.drivers.xray_api_base', '/work/src/base/opt/hiddify-manager/.venv313/lib/python3.13/site-packages/hiddifypanel/drivers/xray_api.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
base = m.XrayApi(); base.get_xray_client().stats_query = lambda *a, **k: []
print('base tags:', len(base.get_inbound_tags()))
"""
        out = panel_python(code)
        n_patched = int(out.split("patched tags:")[1].split()[0])
        n_base = int(out.split("base tags:")[1].split()[0])
        self.assertGreaterEqual(n_patched, 20, out)       # 12 legacy-ish + 12 location inbounds
        self.assertEqual(n_base, 0, "the pristine driver depends 100% on stats: with stats gone it sees NO inbound at all")
        after = membership(NEW2_UUID, all_inbound_tags())
        missing = [t for t, ok in after.items() if not ok and not t.startswith("realityin")]
        self.assertEqual(missing, [], f"not added without stats: {missing}")

    def test_unattended_celery_beat_adds_a_user_within_a_minute(self):
        self.assertTrue(is_active("hiddify-panel-background-tasks"))
        create_user("u-new", NEW_UUID)

        def present():
            return all(NEW_UUID in inbound_users(t) for t in ("v10-vless-xhttp-loc1", "v10-trojan-ws-loc1", "v10-trojan-ws"))
        wait_until(present, timeout=150, step=5, what="celery beat hot-add (vless + trojan, location + legacy)")
        self.assertTrue(all(membership(NEW_UUID, loc_tags()).values()))


class TestRemoval(unittest.TestCase):
    def setUp(self):
        clean_new_users()
        create_user("u-new", NEW_UUID)
        ctl("tester", "add", NEW_UUID, "--note", "lifecycle")
        usage_cycle()

    def tearDown(self):
        ctl("tester", "remove", NEW_UUID)
        delete_user_via_model(NEW_UUID)
        usage_cycle()

    def test_disabled_user_loses_every_inbound_and_cannot_connect_anywhere(self):
        links_ = {n: user_link(NEW_UUID, n) for n in (("Германия", "XHTTP"), ("США", "WS"), ("RU_GLOBAL tls WS direct trojan",), "RU_GLOBAL tls WS direct vless")}
        for n, l in links_.items():
            self.assertIn(via(l), ("exit-a", "exit-b"), f"before: {n}")
        set_user(NEW_UUID, enable=False)
        usage_cycle()
        still = [t for t, ok in membership(NEW_UUID, all_inbound_tags()).items() if ok]
        self.assertEqual(still, [], f"a disabled user is still present in: {still}")
        for n, l in links_.items():
            self.assertIsNone(via(l, timeout=6), f"after disable: {n} must fail")
        set_user(NEW_UUID, enable=True)
        usage_cycle()
        for n, l in links_.items():
            self.assertIn(via(l), ("exit-a", "exit-b"), f"after re-enable: {n}")

    def test_user_over_the_limit_is_removed_from_location_inbounds(self):
        set_user(NEW_UUID, current_usage=60 * 1024 ** 3)
        usage_cycle()
        self.assertEqual([t for t, ok in membership(NEW_UUID, loc_tags()).items() if ok], [])

    def test_deleting_a_user_removes_it_everywhere(self):
        delete_user_via_model(NEW_UUID)
        self.assertEqual([t for t, ok in membership(NEW_UUID, all_inbound_tags()).items() if ok], [])

    def test_removal_also_works_when_statistics_are_unavailable(self):
        """the security-critical direction: a disabled user must lose location inbounds even if stats are broken"""
        set_user(NEW_UUID, enable=False)
        panel_python(f"""
from hiddifypanel.models import User
from hiddifypanel.drivers.xray_api import XrayApi
u = User.query.filter(User.uuid=={NEW_UUID!r}).first()
api = XrayApi(); api.get_xray_client().stats_query = lambda *a, **k: []
api.remove_client(u)
""")
        self.assertEqual([t for t, ok in membership(NEW_UUID, all_inbound_tags()).items() if ok], [])
        # negative control: the pristine driver cannot remove anybody without stats (documents the original weakness)
        set_user(NEW_UUID, enable=True)
        usage_cycle()
        self.assertTrue(all(membership(NEW_UUID, loc_tags()).values()))
        out = panel_python(f"""
import importlib.util
from hiddifypanel.models import User
spec = importlib.util.spec_from_file_location('hiddifypanel.drivers.xray_api_base', '/work/src/base/opt/hiddify-manager/.venv313/lib/python3.13/site-packages/hiddifypanel/drivers/xray_api.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
base = m.XrayApi(); base.get_xray_client().stats_query = lambda *a, **k: []
base.remove_client(User.query.filter(User.uuid=={NEW_UUID!r}).first())
print('base remove done')
""")
        self.assertIn("base remove done", out)
        self.assertTrue(all(membership(NEW_UUID, loc_tags()).values()), "pristine driver removes nobody without stats (the weakness the patch fixes)")


class TestUsageAndAntiShare(unittest.TestCase):
    def test_traffic_through_country_inbounds_is_accounted_to_the_user_and_visible_to_antishare(self):
        t = USERS["tester"]
        set_user(t, current_usage=0)
        usage_cycle()                                                      # reset stat counters
        link = pick(links("tester"), "Германия", "XHTTP")[1]
        for _ in range(3):
            self.assertEqual(via(link, FOREIGN), "exit-a")
        stat = sh([XRAY, "api", "statsquery", "--server=127.0.0.1:10085", "-pattern", f"user>>>{t}@hiddify.com"]).stdout
        total = sum(int(s.get("value", 0)) for s in json.loads(stat).get("stat", []))
        self.assertGreater(total, 0, "xray user counters must grow for traffic through a location inbound")
        usage_cycle()
        used = int(mysql(f"select current_usage from user where uuid='{t}'"))
        self.assertGreater(used, 0, "panel must have accounted the bytes in the DB")
        # access log line (anti-share parser) names the location inbound and the user's email
        log = sh(["tail", "-n", "400", "/opt/hiddify-manager/log/system/xray.access.log"]).stdout
        self.assertRegex(log, r"\[v10-vless-xhttp-loc1 -> router-s1\] email: " + t + r"@hiddify\.com")
        ips = panel_python("from hiddifypanel import accesslog; print(accesslog.collect_recent_ips())")
        self.assertIn(t, ips)

    def test_every_slot_inbound_is_covered_by_the_antishare_log_format(self):
        import re
        pat = re.compile(r"^(?P<ts>\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?) from (?:(?:tcp|udp):)?(?P<ip>\[[^\]]+\]|[^: ]+):\d+ accepted .* email: (?P<uuid>[0-9a-fA-F-]+)@hiddify\.com$")
        for slot_name, link in (("DE WS", pick(links("tester"), "Германия", "WS")[1]), ("US Trojan", pick(links("tester"), "США", "Trojan")[1])):
            self.assertEqual(via(link, RU_DOMAIN), "direct", slot_name)
        lines = sh(["tail", "-n", "60", "/opt/hiddify-manager/log/system/xray.access.log"]).stdout.splitlines()
        seen = {ln.split("[")[1].split(" ->")[0] for ln in lines if "-loc" in ln and pat.match(ln.strip())}
        self.assertTrue({"v10-vless-ws-loc1", "v10-trojan-ws-loc2"} <= seen, seen)


class TestRestartKeepsEverything(unittest.TestCase):
    def test_users_persist_in_location_inbounds_after_a_hiddify_xray_restart(self):
        clean_new_users()
        create_user("u-new", NEW_UUID)
        usage_cycle()
        sh(["bash", "/work/tools/stand_render.sh", "apply_users"], check=False)                 # what the 1-minute job renders
        systemd("restart", "hiddify-xray")
        wait_until(lambda: len(loc_tags()) == 12, timeout=30, what="xray inbounds back")
        time.sleep(2)
        try:
            self.assertTrue(all(membership(NEW_UUID, loc_tags()).values()), "rendered config must carry the user into every slot inbound")
            self.assertEqual(via(pick(links("tester"), "США", "XHTTP")[1]), "exit-b")
        finally:
            delete_user_via_model(NEW_UUID)
            usage_cycle()


if __name__ == "__main__":
    unittest.main(verbosity=2)
