"""Subscription tests against the REAL panel over HTTP (all formats / user agents). Needs the canonical stand state
(reset_locations_to_stand_default) - set up in setUpModule. Compares with the golden baseline captured BEFORE the feature existed.

Owner's rule under test:
  * the USUAL subscription link (the one the bot gives today) and every other endpoint stay EXACTLY as they are - for every app, every user, testers
    included, no switching by User-Agent;
  * AUTO / countries exist only on the dedicated link `<user link>/happ/` (Happ / INCY: three buttons) and in the user panel's "All Configs" list.
Run: /opt/hiddify-manager/.venv313/bin/python -m unittest -v test_02_subscription   (from /work/tests)"""
import contextlib
import io
import json
import os
import shutil
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "/work/tools")
import golden  # noqa: E402
from stand_lib import *  # noqa: E402,F403

BASELINE = "/work/out/golden/baseline1"
NEWCAP = "/work/out/golden/test02-now"
COUNTRY_NAMES = ("Германия", "США")
XRAY = "/usr/bin/xray"
UAS = ("Happ/3.5.0", "INCY/1.0", "HiddifyNext/2.0.5 (android) like ClashMeta v2ray sing-box", "v2RayTun/1.0", "v2rayNG/1.8.5", "v2rayNG/1.8.20",
       "ClashMeta/1.18.0 mihomo", "SFA/1.9.0 (239; sing-box 1.9.0)", "Streisand/1.5", "Mozilla/5.0 (Windows NT 10.0) Chrome/135")


def setUpModule():
    reset_locations_to_stand_default()
    ctl("tester", "remove", USERS["expired"])
    ctl("tester", "remove", USERS["overlimit"])
    ctl("tester", "remove", USERS["disabled"])
    flush_cache()
    os.makedirs(NEWCAP, exist_ok=True)


def xray_accepts(cfg):
    """the REAL xray validates one full config"""
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(cfg, f)
    try:
        r = sh([XRAY, "run", "-test", "-config", f.name], check=False)
        return r.returncode == 0 and "Configuration OK" in r.stdout + r.stderr
    finally:
        os.unlink(f.name)


def plain(body):
    """some default answers are a base64 link list: return the text"""
    import base64
    if "://" in body or body.lstrip().startswith(("[", "{")):
        return body
    return base64.b64decode(body + "=" * (-len(body) % 4)).decode("utf-8", "replace")


def happ(user="tester", ua="Happ/3.5.0"):
    return sub_text(user, fmt="happ/", ua=ua)


def cmp_users(users, **kw):
    golden.capture(NEWCAP, only_users=users)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        bad = golden.compare(BASELINE, NEWCAP, users=users, **kw)
    return bad, buf.getvalue()


def load_baseline(user_key, fmt, ua):
    return open(f"{BASELINE}/u-{user_key}__{fmt}__{ua}.body", "rb").read().decode()


def is_country(name):
    return any(c in name for c in COUNTRY_NAMES)


def is_auto(name):
    return "AUTO" in name


class TestUsualLinkNeverChanges(unittest.TestCase):
    """"identical" = after the normalisation of exactly three kinds of volatile text (see tools/golden.py): the clock text of the status line, the random
    numeric suffix of some Clash names and random ids; subscription bodies are base64-decoded before comparing. Headers are compared as well."""

    def test_non_testers_are_identical_after_normalisation_in_all_formats_and_agents(self):
        bad, out = cmp_users(["u-active", "u-expired", "u-overlimit", "u-disabled"])
        self.assertEqual(bad, 0, out)
        self.assertIn("IDENTICAL", out)

    def test_a_tester_too_gets_identical_answers_after_normalisation_in_all_formats_and_agents(self):
        bad, out = cmp_users(["u-tester"])
        self.assertEqual(bad, 0, "the usual link and every other endpoint must be the stock answer even for a tester:\n" + out)
        self.assertIn("IDENTICAL", out)

    def test_inactive_testers_still_get_only_the_stub(self):
        for k in ("expired", "overlimit", "disabled"):
            ctl("tester", "add", USERS[k], "--note", "inactive tester")
        try:
            flush_cache()
            bad, out = cmp_users(["u-expired", "u-overlimit", "u-disabled"])
            self.assertEqual(bad, 0, "inactive users must not receive location entries:\n" + out)
        finally:
            for k in ("expired", "overlimit", "disabled"):
                ctl("tester", "remove", USERS[k])

    def test_no_switching_by_user_agent_on_the_usual_link_or_xray(self):
        for ua in UAS:
            for fmt in ("", "xray/"):
                body = sub_text("tester", fmt=fmt, ua=ua)
                self.assertFalse(any(w in body for w in COUNTRY_NAMES + ("AUTO", "balancers")) or any(w in plain(body) for w in COUNTRY_NAMES + ("AUTO", "balancers")),
                                 f"{ua!r} {fmt!r}: stock expected")

    def test_every_format_and_agent_answers_200_with_content_and_so_does_the_happ_link(self):
        for fmt in golden.FORMATS + ["happ/"]:
            for uan, ua in golden.UAS.items():
                st, hd, body = golden.fetch(USERS["tester"], fmt, ua)
                self.assertEqual(st, 200, (fmt, uan))
                self.assertGreater(len(body), 100, (fmt, uan))


class TestUserPanelList(unittest.TestCase):
    """the user panel's "All Configs" list: legacy profiles for everybody, the country profiles for testers"""

    def test_a_tester_sees_the_legacy_profiles_then_the_six_country_profiles_and_no_auto(self):
        names = all_names("tester")
        auto = [n for n in names if is_auto(n)]
        ctry = [n for n in names if is_country(n)]
        legacy = [n for n in names if not is_auto(n) and not is_country(n)]
        self.assertEqual(auto, [], "AUTO exists only as a group on the /happ/ link")
        self.assertEqual(ctry, ["🇩🇪 Германия · XHTTP", "🇩🇪 Германия · WS", "🇩🇪 Германия · Trojan",
                                "🇺🇸 США · XHTTP", "🇺🇸 США · WS", "🇺🇸 США · Trojan"])
        self.assertEqual(len(legacy), 15)
        self.assertLess(names.index(legacy[-1]), names.index(ctry[0]), "order: legacy profiles, then Германия, then США")
        shown = [i["name"] for i in all_configs("tester") if is_country(i["name"])]
        self.assertEqual(shown[:3], ["Германия · XHTTP", "Германия · WS", "Германия · Trojan"], "the panel page shows readable names")

    def test_a_regular_user_sees_no_country_profile_and_the_list_has_its_usual_size(self):
        items = all_configs("active")
        self.assertEqual(len(items), 22)
        self.assertFalse(any(is_country(i["name"]) or is_auto(i["name"]) for i in items))
        self.assertFalse(any(i["name"].startswith("Happ") for i in items), "no Happ / INCY entry for an account that is not shown the AUTO block")

    def test_a_tester_gets_the_happ_entry_right_after_auto_and_it_is_the_real_dedicated_link(self):
        items = all_configs("tester")
        self.assertEqual(items[0]["name"], "Auto")
        self.assertEqual(items[1]["name"], "Happ (AUTO + countries)", "right after Auto, as the owner asked")
        self.assertEqual(items[1]["domain"], "ALL")
        link = items[1]["link"]
        self.assertTrue(link.startswith(f"https://ru.stand.test/{PC}/{USERS['tester']}/happ/#"), link)
        self.assertEqual(sum(1 for i in items if i["name"].startswith("Happ")), 1)
        self.assertEqual([i["name"] for i in items[2:4]], [i["name"] for i in all_configs("active")[1:3]], "the rest of the list is the usual one")
        self.assertEqual([c["remarks"] for c in json.loads(happ())], ["🟢 AUTO", "🇩🇪 Германия", "🇺🇸 США"], "and that link serves the three buttons")

    def test_the_happ_entry_disappears_with_the_auto_block_or_the_tester_row(self):
        try:
            ctl("auto", "--visibility", "hidden")
            flush_cache()
            self.assertFalse(any(i["name"].startswith("Happ") for i in all_configs("tester")), "hidden AUTO: no entry")
            ctl("auto", "--visibility", "testers")
            ctl("tester", "remove", USERS["tester"])
            flush_cache()
            self.assertFalse(any(i["name"].startswith("Happ") for i in all_configs("tester")), "not a tester any more: no entry")
        finally:
            reset_locations_to_stand_default()


class TestHappLink(unittest.TestCase):
    """`<user link>/happ/`: Happ / INCY see ONE button per location: AUTO, Германия, США"""

    def tearDown(self):
        mysql("update bool_config set value=1 where `key` in ('trojan_enable','ws_enable','xhttp_enable')")
        reset_locations_to_stand_default()

    def test_any_app_gets_three_group_buttons_without_the_usage_line(self):
        for ua in ("Happ/3.5.0", "INCY/1.0", "Mozilla/5.0 Chrome/135"):
            cfgs = json.loads(happ(ua=ua))
            self.assertEqual([c["remarks"] for c in cfgs], ["🟢 AUTO", "🇩🇪 Германия", "🇺🇸 США"], ua)
            for c in cfgs:
                self.assertTrue(xray_accepts(c), (ua, c["remarks"]))
        st, hd, _ = golden.fetch(USERS["tester"], "happ/", "Happ/3.5.0")
        self.assertEqual(hd.get("content-type", "").split(";")[0], "application/json")
        self.assertIn("subscription-userinfo", hd, "the usage reaches Happ through the header instead of an extra button")

    def test_the_site_button_of_the_apps_opens_the_personal_cabinet_not_the_json(self):
        _, hd_happ, _ = golden.fetch(USERS["tester"], "happ/", "Happ/3.5.0")
        _, hd_usual, _ = golden.fetch(USERS["tester"], "", "Happ/3.5.0")
        _, hd_xray, _ = golden.fetch(USERS["tester"], "xray/", "Happ/3.5.0")
        cabinet = hd_usual["profile-web-page-url"]
        self.assertTrue(cabinet.endswith(f"/{USERS['tester']}/"), cabinet)
        self.assertEqual(hd_happ["profile-web-page-url"], cabinet, "/happ/ -> the personal cabinet (Happ / INCY open this from their Site button)")
        self.assertTrue(hd_xray["profile-web-page-url"].endswith("/xray/"), "the stock /xray/ endpoint is untouched")
        _, hd_reg, _ = golden.fetch(USERS["active"], "happ/", "Happ/3.5.0")
        self.assertTrue(hd_reg["profile-web-page-url"].endswith(f"/{USERS['active']}/"), "the same for an account without countries")

    def test_the_happ_link_is_one_group_per_location_and_legacy_transports_sit_inside_auto(self):
        base = json.loads(load_baseline("tester", "xray", "happ"))        # the stock list for the same kind of client (v2rayNG 1.8.5 drops httpupgrade)
        new = json.loads(happ())
        self.assertEqual([c.get("remarks") for c in new], ["🟢 AUTO", "🇩🇪 Германия", "🇺🇸 США"])
        by = {c["remarks"]: c for c in new}
        legacy_out = [c["outbounds"][0] for c in base if c["remarks"].startswith("RU_GLOBAL")]

        def strip(o):
            return {k: v for k, v in o.items() if k != "tag"}
        legacy_stripped = [strip(o) for o in legacy_out]
        members = [o for o in by["🟢 AUTO"]["outbounds"] if o["protocol"] in ("vless", "trojan")]
        self.assertGreaterEqual(len(members), 8)
        for o in members:
            self.assertIn(strip(o), legacy_stripped, "every AUTO member is an unchanged legacy transport")
        self.assertEqual(members[0]["tag"], "🟢 AUTO")
        for label, cfg in by.items():
            bal = cfg["routing"]["balancers"]
            self.assertEqual(len(bal), 1, label)
            self.assertEqual(bal[0]["strategy"], {"type": "leastPing"})
            self.assertEqual(bal[0]["selector"], [label])
            self.assertEqual(bal[0]["fallbackTag"], label)
            self.assertEqual(cfg["burstObservatory"]["subjectSelector"], [label])
            self.assertEqual([r for r in cfg["routing"]["rules"] if r.get("balancerTag")][-1]["port"], "0-65535", "the catch-all goes to the balancer")
            self.assertFalse([r for r in cfg["routing"]["rules"] if r.get("outboundTag") == "proxy"], "no dangling 'proxy' target")
            tags = [o["tag"] for o in cfg["outbounds"]]
            self.assertEqual(sum(1 for t in tags if t.startswith(label)), len(cfg["outbounds"]) - 3, "the selector prefix matches the members only")
        for label in ("🇩🇪 Германия", "🇺🇸 США"):
            self.assertEqual([o["protocol"] for o in by[label]["outbounds"] if o["protocol"] in ("vless", "trojan")], ["vless", "vless", "trojan"])

    def test_a_regular_user_on_the_happ_link_gets_the_stock_list(self):
        body = happ("active")
        self.assertNotIn("balancers", body)
        self.assertFalse(any(w in body for w in COUNTRY_NAMES + ("AUTO",)))
        self.assertTrue(isinstance(json.loads(body), list))

    def test_group_members_follow_the_enabled_transports(self):
        mysql("update bool_config set value=0 where `key`='trojan_enable'")
        flush_cache()
        cfgs = {c["remarks"]: c for c in json.loads(happ())}
        for label in ("🇩🇪 Германия", "🇺🇸 США"):
            self.assertEqual([o["protocol"] for o in cfgs[label]["outbounds"] if o["protocol"] in ("vless", "trojan")], ["vless", "vless"])
            self.assertTrue(xray_accepts(cfgs[label]), label)
        self.assertFalse([o for o in cfgs["🟢 AUTO"]["outbounds"] if o["protocol"] == "trojan"], "no trojan transport left in AUTO")

    def test_a_label_that_would_match_a_service_outbound_by_prefix_never_reaches_a_balancer(self):
        mysql("update commercial_routing_location set title='direct', flag='', country_iso='' where slot=2")
        flush_cache()
        cfgs = json.loads(happ())
        for c in cfgs:
            for b in c.get("routing", {}).get("balancers", []):
                self.assertNotIn("direct", b["selector"], "a balancer selecting by the prefix 'direct' would leak traffic around the tunnel")
        self.assertEqual(cfgs, json.loads(sub_text("tester", fmt="xray/", ua="Happ/3.5.0")),
                         "a grouping error must give EXACTLY the stock /xray/ answer - not a list with the country profiles added")
        self.assertFalse(any(is_country(c["remarks"]) for c in cfgs))

    def stock_xray(self, user="tester"):
        return json.loads(sub_text(user, fmt="xray/", ua="Happ/3.5.0"))

    def assert_exactly_the_stock_answer(self, why):
        got = json.loads(happ())
        self.assertEqual(got, self.stock_xray(), why + ": the /happ/ answer must be EXACTLY the stock /xray/ list")
        self.assertFalse(any(is_country(c["remarks"]) or is_auto(c["remarks"]) for c in got), why + ": no country / AUTO config may appear")
        self.assertFalse(any("balancers" in c.get("routing", {}) for c in got), why)

    def test_with_a_hidden_auto_block_but_visible_countries_the_happ_link_is_exactly_the_stock_xray_answer(self):
        ctl("auto", "--visibility", "hidden")
        flush_cache()
        self.assertTrue(any(is_country(n) for n in all_names("tester")), "the countries ARE visible to this tester (panel list)")
        self.assert_exactly_the_stock_answer("AUTO hidden")

    def test_with_the_auto_block_switched_off_the_happ_link_is_exactly_the_stock_xray_answer(self):
        ctl("auto", "--enabled", 0)
        flush_cache()
        self.assert_exactly_the_stock_answer("AUTO disabled")

    def test_a_removed_tester_and_a_regular_user_get_exactly_the_stock_xray_answer_on_the_happ_link(self):
        ctl("tester", "remove", USERS["tester"])
        flush_cache()
        self.assert_exactly_the_stock_answer("removed tester")
        self.assertEqual(len(all_names("tester")), 15, "and no country profiles in the panel list")
        self.assertEqual(json.loads(happ("active")), self.stock_xray("active"))


ROSCOM_FIX = "/work/tests/fixtures/roscom"
ROSCOM_GEO = ROSCOM_FIX + "/geo"                                # the REAL geoip.dat / geosite.dat of the RoscomVPN release the profile points at
STAND_GEO = "/opt/hiddify-manager/xray/bin"                     # what a client without the profile's geo files has
ROSCOM_FLAG = "/opt/hiddify-manager/multiloc-roscom-json.on"                       # the v3 switch: counts only while there is no settings file
ROSCOM_SETTINGS = "/opt/hiddify-manager/hiddify-panel/var/multiloc-roscom.json"    # the v4 switch + the profile pasted on the panel page (test_16 covers the page)
ROSCOM_ROOT = "/var/lib/routing-links"                          # the routing landing's data dir (absent on the stand until a test creates it)
ROSCOM_LANDING = ROSCOM_ROOT + "/last-good"
UPSTREAM_LISTS = ("DirectSites", "DirectIp", "ProxySites", "ProxyIp", "BlockSites", "BlockIp")
RU_ZONE = ["regexp:.*\\.ru$", "regexp:.*\\.su$", "regexp:.*\\.xn\\-\\-p1ai$", "domain:sberbank.com", "domain:tbank-online.com", "domain:alfabank.com", "domain:alfa-bank.com",
           "domain:gazprombank.com"]                   # the spec of the forced-direct entries (owner decision 2026-10-13): they come first in the direct list


def xray_check(cfg, geo_dir):
    """(accepted, last line): the REAL xray validates one full config with the geoip.dat / geosite.dat found in geo_dir"""
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(cfg, f)
    try:
        r = sh([XRAY, "run", "-test", "-config", f.name], check=False, env={**os.environ, "XRAY_LOCATION_ASSET": geo_dir})
        out = (r.stdout + r.stderr).strip()
        return r.returncode == 0 and "Configuration OK" in out, (out.splitlines() or [""])[-1][:300]
    finally:
        os.unlink(f.name)


def roscom_switched_on():
    """the switch of the RoscomVPN routing as the console and the panel see it: the settings file; the v3 flag file only while there are no settings"""
    try:
        with open(ROSCOM_SETTINGS, encoding="utf-8") as f:
            return bool(json.load(f).get("enabled"))
    except (OSError, ValueError):
        return os.path.exists(ROSCOM_FLAG)


def roscom_payload(header):
    import base64
    scheme, payload = header.split("://routing/onadd/")
    return scheme, json.loads(base64.b64decode(payload))


def upstream(client="happ"):
    with open(f"{ROSCOM_FIX}/{client.upper()}_DEFAULT.DEEPLINK", encoding="utf-8") as f:
        return roscom_payload(f.read().strip())[1]


class TestRoscomRoutingInTheJson(unittest.TestCase):
    """RoscomVPN routing for the JSON profiles of the dedicated /happ/ link: the real panel over HTTP, the real xray on the answers, the real RoscomVPN geo
    files. Happ / INCY run a JSON subscription in restricted mode: the profile (DNS + geo files, NO rules) travels in the `routing` header, the rules sit
    inside every JSON config. Default OFF; the usual link and every other endpoint never get any of it."""

    @classmethod
    def setUpClass(cls):
        cls.created_root = not os.path.exists(ROSCOM_ROOT)

    @classmethod
    def tearDownClass(cls):
        ctl("roscom", "off")
        for p in (ROSCOM_SETTINGS, ROSCOM_FLAG):                 # leave the stand as it was: no switch file at all (= off)
            if os.path.exists(p):
                os.remove(p)
        if cls.created_root:
            shutil.rmtree(ROSCOM_ROOT, ignore_errors=True)

    def setUp(self):
        os.makedirs(ROSCOM_LANDING, exist_ok=True)
        for p in (ROSCOM_ROOT, ROSCOM_LANDING):
            os.chmod(p, 0o755)
        for c in ("happ", "incy"):
            shutil.copy(f"{ROSCOM_FIX}/{c.upper()}_DEFAULT.DEEPLINK", f"{ROSCOM_LANDING}/{c}.deeplink")
            os.chmod(f"{ROSCOM_LANDING}/{c}.deeplink", 0o644)
        ctl("roscom", "on")

    def tearDown(self):
        ctl("roscom", "off")
        reset_locations_to_stand_default()

    def fetch(self, user="tester", fmt="happ/", ua="Happ/3.5.0"):
        st, hd, body = golden.fetch(USERS[user], fmt, ua)
        self.assertEqual(st, 200, (user, fmt, ua))
        return hd, body.decode("utf-8", "replace")

    def assert_stock_rules(self, cfgs, why):
        for c in cfgs:
            self.assertEqual(c["routing"]["domainStrategy"], "AsIs", why + ": " + c["remarks"])
            self.assertTrue(any("geosite:category-ru" in r.get("domain", []) for r in c["routing"]["rules"]), why + ": the stock RU bypass rule is there")
            self.assertFalse(any("geosite:whitelist" in r.get("domain", []) for r in c["routing"]["rules"]), why)

    # ------------------------------------------------------------------------------------------------ the answer for the two apps
    def test_happ_gets_the_header_and_every_location_carries_the_roscom_rules(self):
        hd, body = self.fetch()
        self.assertIn("routing", hd)
        scheme, prof = roscom_payload(hd["routing"])
        self.assertEqual(scheme, "happ")
        self.assertEqual(prof["Name"], "RoscomVPN JSON")
        self.assertEqual({k: prof[k] for k in UPSTREAM_LISTS}, {k: [] for k in UPSTREAM_LISTS}, "the header profile has NO rules - they sit inside the JSON")
        up = upstream("happ")
        for k in ("Geoipurl", "Geositeurl", "LastUpdated", "DnsHosts", "RemoteDNSDomain", "DomesticDNSDomain", "DomainStrategy", "RouteOrder"):
            self.assertEqual(prof[k], up[k], k)
        self.assertEqual(prof["UseChunkFiles"], "false", "no chunked geo files: the profile has no rules to chunk by")
        cfgs = json.loads(body)
        self.assertEqual([c["remarks"] for c in cfgs], ["🟢 AUTO", "🇩🇪 Германия", "🇺🇸 США"])
        for c in cfgs:
            bal = c["routing"]["balancers"][0]["tag"]
            rules = c["routing"]["rules"]
            self.assertEqual(c["routing"]["domainStrategy"], "IPIfNonMatch")
            self.assertEqual([(("domain" if "domain" in r else "ip" if "ip" in r else r["port"]), r.get("outboundTag") or r.get("balancerTag")) for r in rules],
                             [("domain", "block"), ("domain", bal), ("domain", "direct"), ("ip", "direct"), ("0-65535", bal)], c["remarks"])
            self.assertEqual(rules[0]["domain"], up["BlockSites"])
            self.assertEqual(rules[1]["domain"], up["ProxySites"])
            self.assertEqual(rules[2]["domain"], RU_ZONE + up["DirectSites"])
            self.assertEqual(rules[3]["ip"], up["DirectIp"])
            self.assertEqual(c["routing"]["balancers"][0]["strategy"], {"type": "leastPing"}, "the ping choice and the switching keep working")
            self.assertEqual(c["burstObservatory"]["subjectSelector"], [c["remarks"]])
            self.assertFalse([r for r in rules if r.get("outboundTag") == "proxy"], "no dangling 'proxy' target")
        self.assertIn("subscription-userinfo", hd)
        self.assertTrue(hd["profile-web-page-url"].endswith(f"/{USERS['tester']}/"), "the Site button still opens the personal cabinet")

    def test_incy_gets_its_own_scheme_and_its_own_profile_but_the_same_rules(self):
        hd, body = self.fetch(ua="INCY/2.4.5")
        scheme, prof = roscom_payload(hd["routing"])
        self.assertEqual(scheme, "incy")
        self.assertEqual(prof["Name"], "RoscomVPN JSON")
        self.assertEqual(prof["Geoipurl"], upstream("incy")["Geoipurl"], "INCY's own profile (its geo addresses are not pinned to a release)")
        self.assertEqual({k: prof[k] for k in UPSTREAM_LISTS}, {k: [] for k in UPSTREAM_LISTS})
        happ_rules = [c["routing"]["rules"] for c in json.loads(self.fetch()[1])]
        self.assertEqual([c["routing"]["rules"] for c in json.loads(body)], happ_rules)

    def test_the_real_xray_loads_every_config_with_the_roscom_geo_files_and_refuses_it_without_them(self):
        cfgs = json.loads(self.fetch()[1])
        for c in cfgs:
            ok, line = xray_check(c, ROSCOM_GEO)
            self.assertTrue(ok, f"{c['remarks']}: the real xray must load it with the RoscomVPN geo files: {line}")
        ok, line = xray_check(cfgs[0], STAND_GEO)
        self.assertFalse(ok, "control: without the profile's geo files xray cannot start this config (that is why the header carries the profile)")
        self.assertIn("geosite", line)

    # ------------------------------------------------------------------------------------------------ everybody and everything else stays as it was
    def test_other_apps_get_the_old_answer_and_nobody_gets_the_header_on_other_endpoints(self):
        for ua in ("HiddifyNext/2.0.5 (android) like ClashMeta v2ray sing-box", "v2rayNG/1.8.5", "v2RayTun/1.0", "Mozilla/5.0 Chrome/135", ""):
            hd, body = self.fetch(ua=ua)
            self.assertNotIn("routing", hd, ua)
            self.assert_stock_rules(json.loads(body), repr(ua))
        for fmt in ("", "xray/", "sub/", "sub64/", "auto/", "clash/", "singbox/"):
            for ua in ("Happ/3.5.0", "INCY/1.0"):
                st, hd, body = golden.fetch(USERS["tester"], fmt, ua)
                self.assertNotIn("routing", hd, f"{fmt!r} {ua}: only /happ/")
        hd, body = self.fetch(fmt="xray/")
        self.assert_stock_rules(json.loads(body), "/xray/")

    def test_an_account_without_the_layout_gets_the_stock_list_and_no_header(self):
        hd, body = self.fetch("active")
        self.assertNotIn("routing", hd)
        self.assertNotIn("balancers", body)
        self.assert_stock_rules(json.loads(body), "a regular user")
        ctl("auto", "--visibility", "hidden")
        hd, body = self.fetch()
        self.assertNotIn("routing", hd, "AUTO hidden -> /happ/ is the stock list -> there is nothing for the rules to sit in -> no header either")
        self.assertEqual(json.loads(body), json.loads(self.fetch(fmt="xray/")[1]))

    # ------------------------------------------------------------------------------------------------ fail safe
    def test_an_unusable_landing_copy_means_the_old_answer_for_that_app_only(self):
        for bad in ("broken", "", "happ://routing/add/" + "QUJD", fixture_text("INCY_DEFAULT.DEEPLINK")):
            with open(f"{ROSCOM_LANDING}/happ.deeplink", "w") as f:
                f.write(bad)
            hd, body = self.fetch()
            self.assertNotIn("routing", hd, repr(bad[:30]))
            self.assert_stock_rules(json.loads(body), repr(bad[:30]))
            self.assertIn("routing", self.fetch(ua="INCY/1.0")[0], "INCY's own copy is fine")
        os.remove(f"{ROSCOM_LANDING}/happ.deeplink")
        hd, body = self.fetch()
        self.assertNotIn("routing", hd, "missing file")
        self.assert_stock_rules(json.loads(body), "missing file")
        shutil.rmtree(ROSCOM_ROOT)
        hd, body = self.fetch(ua="INCY/1.0")
        self.assertNotIn("routing", hd, "the landing is gone altogether")
        self.assert_stock_rules(json.loads(body), "no landing")

    def test_the_switch_takes_effect_at_once_without_a_restart(self):
        self.assertIn("routing", self.fetch()[0])
        ctl("roscom", "off")
        hd, body = self.fetch()
        self.assertNotIn("routing", hd)
        self.assert_stock_rules(json.loads(body), "switched off")
        ctl("roscom", "on")
        self.assertIn("routing", self.fetch()[0])

    def test_a_new_upstream_release_reaches_the_answer_with_the_next_request(self):
        prof = upstream("happ")
        prof["LastUpdated"] = "1999999999"
        prof["DirectSites"] = prof["DirectSites"] + ["domain:example.org"]
        import base64
        with open(f"{ROSCOM_LANDING}/happ.deeplink", "w") as f:
            f.write("happ://routing/onadd/" + base64.b64encode(json.dumps(prof).encode()).decode() + "\n")
        hd, body = self.fetch()
        self.assertEqual(roscom_payload(hd["routing"])[1]["LastUpdated"], "1999999999")
        self.assertEqual(json.loads(body)[0]["routing"]["rules"][2]["domain"][-1], "domain:example.org")

    # ------------------------------------------------------------------------------------------------ the console switch
    def test_the_console_refuses_to_switch_on_with_an_unusable_copy_and_status_tells_why(self):
        ctl("roscom", "off")
        os.remove(f"{ROSCOM_LANDING}/incy.deeplink")
        r = ctl("roscom", "on", check=False)
        self.assertFalse(roscom_switched_on(), "refused: the switch stays off")
        st = json.loads(ctl("roscom", "status", "--json"))
        self.assertFalse(st["enabled"])
        self.assertTrue(st["clients"]["happ"]["ok"])
        self.assertFalse(st["clients"]["incy"]["ok"])
        self.assertIn("No such file", st["clients"]["incy"]["error"])
        ctl("roscom", "on", "--force")
        self.assertTrue(roscom_switched_on(), "--force: the operator knows better")
        self.assertIn("routing", self.fetch()[0], "happ works, incy has no copy -> only incy falls back")
        self.assertNotIn("routing", self.fetch(ua="INCY/1.0")[0])

    def test_status_is_readable_and_changes_nothing(self):
        out = ctl("roscom", "status")
        self.assertIn("ON", out)
        self.assertIn("RoscomVPN JSON", out)
        self.assertTrue(roscom_switched_on())
        st = json.loads(ctl("roscom", "status", "--json"))
        self.assertEqual(st["clients"]["happ"]["rules"], {"BlockSites": 3, "BlockIp": 0, "ProxySites": 5, "ProxyIp": 0, "DirectSites": 12, "DirectIp": 2})
        self.assertTrue(st["clients"]["happ"]["panel_can_read"])


def fixture_text(name):
    with open(f"{ROSCOM_FIX}/{name}", encoding="utf-8") as f:
        return f.read()


class TestLinkCorrectness(unittest.TestCase):
    def setUp(self):
        self.st = json.loads(ctl("status", "--json"))
        self.rows = {r["slot"]: r for r in self.st["locations"]}

    def _db_paths(self):
        out = mysql("select slot, path_xhttp, path_ws, path_trojan from commercial_routing_location where kind='country'")
        return {int(l.split("\t")[0]): l.split("\t")[1:] for l in out.splitlines()}

    def test_paths_ports_uuid_and_params_match_db_and_legacy_source(self):
        import urllib.parse
        ls = dict(links("tester"))
        paths = self._db_paths()
        legacy = {"xhttp": ls["RU_GLOBAL tls_h2 xhttp direct vless dl=h2"], "ws": ls["RU_GLOBAL tls WS direct vless"],
                  "trojan": ls["RU_GLOBAL tls WS direct trojan"]}

        def parts(link):
            u = urllib.parse.urlsplit(link)
            return u, {k: v[0] for k, v in urllib.parse.parse_qs(u.query, keep_blank_values=True).items()}
        for slot, label in ((1, "🇩🇪 Германия"), (2, "🇺🇸 США")):
            for idx, (kind, tname) in enumerate((("xhttp", "XHTTP"), ("ws", "WS"), ("trojan", "Trojan"))):
                u, q = parts(ls[f"{label} · {tname}"])
                lu, lq = parts(legacy[kind])
                self.assertEqual(q["path"], "/" + paths[slot][idx])
                self.assertEqual((u.scheme, u.hostname, u.port, u.username), (lu.scheme, lu.hostname, lu.port, lu.username))
                self.assertEqual(u.username, USERS["tester"])
                for k in lq:
                    if k != "path":
                        self.assertEqual(q.get(k), lq[k], f"{label} {tname}: param {k} differs from the legacy source")

    def test_all_paths_unique_and_not_prefixes_of_each_other(self):
        allp = [p for v in self._db_paths().values() for p in v]
        self.assertEqual(len(allp), 12)
        self.assertEqual(ml_check(allp), [])


def ml_check(paths):
    from hiddifypanel.hutils import multiloc
    return multiloc.check_paths(paths)


class TestVisibility(unittest.TestCase):
    def tearDown(self):
        reset_locations_to_stand_default()

    def names(self, user):
        return all_names(user)

    def test_visibility_matrix(self):
        # testers: tester yes, regular no
        self.assertTrue(any(is_country(n) for n in self.names("tester")))
        self.assertFalse(any(is_country(n) or is_auto(n) for n in self.names("active")))
        # all: everybody active sees it
        ctl("set", 2, "--visibility", "all")
        self.assertTrue(any("США" in n for n in self.names("active")))
        self.assertFalse(any("Германия" in n for n in self.names("active")))
        # hidden: nobody (not even the tester)
        ctl("set", 2, "--visibility", "hidden")
        self.assertFalse(any("США" in n for n in self.names("tester")))
        # disabled: nobody
        ctl("set", 2, "--visibility", "all", "--enabled", 0)
        self.assertFalse(any("США" in n for n in self.names("active")))
        self.assertFalse(any("США" in n for n in self.names("tester")))

    def test_unnamed_slot_never_appears_even_if_enabled_flag_is_forced(self):
        mysql("update commercial_routing_location set enabled=1, visibility='all' where slot=3")
        self.assertFalse(any(is_country(n) for n in self.names("active")))
        self.assertEqual(len([n for n in self.names("tester") if "·" in n]), 6)

    def test_removing_a_tester_hides_everything_immediately(self):
        ctl("tester", "remove", USERS["tester"])
        n = all_names("tester")
        self.assertFalse(any(is_country(x) or is_auto(x) for x in n))
        self.assertEqual(len(n), 15)

    def test_title_change_is_visible_without_cache_flush_and_order_is_configurable(self):
        ctl("set", 1, "--title", "Германия-2")
        self.assertTrue(any("Германия-2" in n for n in self.names("tester")))
        ctl("set", 2, "--order", 20)                      # USA before Germany now
        ctry = [n for n in self.names("tester") if is_country(n) and "XHTTP" in n]
        self.assertTrue("США" in ctry[0] and "Германия" in ctry[1], ctry)
        ctl("auto", "--enabled", 0)
        flush_cache()
        self.assertNotIn("balancers", happ(), "AUTO block off -> the Happ link serves the stock list")
        self.assertTrue(any(is_country(n) for n in self.names("tester")), "the country profiles stay in the panel list")

    def test_slot_with_missing_transport_source_skips_only_that_transport(self):
        # trojan disabled globally -> no Trojan source entry -> only XHTTP + WS cloned
        mysql("update bool_config set value=0 where `key`='trojan_enable'")
        flush_cache()
        try:
            n = [x for x in self.names("tester") if is_country(x)]
            self.assertEqual(len(n), 4, n)
            self.assertFalse(any("Trojan" in x for x in n))
        finally:
            mysql("update bool_config set value=1 where `key`='trojan_enable'")
            flush_cache()


class TestHookFailureIsSafe(unittest.TestCase):
    def test_missing_tables_serve_the_legacy_subscription(self):
        sh("mysql hiddifypanel -e 'RENAME TABLE commercial_routing_location_tester TO zz_tmp_tester'")
        try:
            flush_cache()
            bad, out = cmp_users(["u-tester", "u-active"])
            self.assertEqual(bad, 0, "with the feature's tables missing the tester must get exactly the legacy subscription:\n" + out)
            self.assertNotIn("balancers", happ(), "and the Happ link serves the stock list")
            self.assertFalse(any(is_country(n) for n in all_names("tester")))
        finally:
            sh("mysql hiddifypanel -e 'RENAME TABLE zz_tmp_tester TO commercial_routing_location_tester'")
            flush_cache()
        self.assertTrue(any("США" in n for n, _ in links("tester")), "feature back after the tables are back")

    def test_broken_row_does_not_break_the_response(self):
        sh("mysql hiddifypanel -e \"update commercial_routing_location set visibility='bogus' where slot=2\"")
        try:
            flush_cache()
            for fmt in ("sub/", "happ/"):
                st, _, body = golden.fetch(USERS["tester"], fmt, "Happ/3.5.0")
                self.assertEqual(st, 200, fmt)
                self.assertGreater(len(body), 1000, fmt)
        finally:
            sh("mysql hiddifypanel -e \"update commercial_routing_location set visibility='testers' where slot=2\"")
            flush_cache()


if __name__ == "__main__":
    unittest.main(verbosity=2)
