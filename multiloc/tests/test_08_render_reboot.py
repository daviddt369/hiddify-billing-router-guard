"""Render/validation variants (xray -test, haproxy -c, nginx -t), compatibility with the WEEKLY FULL install.sh flow that prod runs after every
reboot (cron @reboot: clean_files + full jinja render + restarts), HAProxy reload smoothness and the unavailability window of a hiddify-xray restart.

Run: /opt/hiddify-manager/.venv313/bin/python -W ignore -m unittest -v test_08_render_reboot   (from /work/tests)"""
import json
import os
import subprocess
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stand_lib import *  # noqa: E402,F403

H = "/opt/hiddify-manager"
XRAY = f"{H}/xray/bin/xray"
RENDERED = ["xray/configs/03_routing.json", "xray/configs/05_inbounds_30_locations.json", "xray/configs/06_outbounds.json",
            "haproxy/haproxy.cfg", "haproxy/maps/path_v10"]


def render_now():
    sh(["bash", "-c", "source /opt/hiddify-manager/common/utils.sh; reload_all_configs >/dev/null"], cwd=H)
    code = ("import importlib.util,sys\n"
            "spec=importlib.util.spec_from_file_location('hjinja','/opt/hiddify-manager/common/jinja.py')\n"
            "m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
            "[m.render('/opt/hiddify-manager/'+t+'.j2') for t in sys.argv[1:]]\n")
    sh([VENV_PY, "-c", code] + RENDERED, cwd=H + "/common")


def validate():
    x = sh([XRAY, "run", "-test", "-confdir", f"{H}/xray/configs/"], check=False)
    h = sh(["/usr/sbin/haproxy", "-f", f"{H}/haproxy/", "-c", "-q"], check=False)
    return x.returncode == 0 and "Configuration OK" in x.stdout + x.stderr, h.returncode == 0, (x.stdout + x.stderr)[-400:], (h.stdout + h.stderr)[-400:]


def read(t):
    return open(f"{H}/{t}", "rb").read()


def setUpModule():
    reset_locations_to_stand_default()
    apply_router()


class TestRenderVariants(unittest.TestCase):
    def tearDown(self):
        mysql("update bool_config set value=1 where `key` in ('trojan_enable','ws_enable','xhttp_enable','vless_enable')")
        mysql("update str_config set value='xray' where `key`='core_type'")
        flush_cache()
        render_now()

    def inbound_tags_rendered(self):
        return sorted(i["tag"] for i in json.load(open(f"{H}/xray/configs/05_inbounds_30_locations.json"))["inbounds"])

    def test_default_render_is_consistent_with_the_database(self):
        render_now()
        ok_x, ok_h, ox, oh = validate()
        self.assertTrue(ok_x, ox)
        self.assertTrue(ok_h, oh)
        tags = self.inbound_tags_rendered()
        self.assertEqual(len(tags), 12)
        rows = mysql("select slot,path_xhttp,path_ws,path_trojan from commercial_routing_location where kind='country' order by slot").splitlines()
        cfg = read("haproxy/haproxy.cfg").decode()
        mp = read("haproxy/maps/path_v10").decode()
        for r in rows:
            slot, px, pw, pt = r.split("\t")
            self.assertIn(f"use_backend v10-vless-xhttp-loc{slot} if {{ path_beg /{px} }}", cfg)
            self.assertIn(f"use_backend v10-vless-ws-loc{slot} if {{ path_beg /{pw} }}", cfg)
            self.assertIn(f"use_backend v10-trojan-ws-loc{slot} if {{ path_beg /{pt} }}", cfg)
            for p, t in ((px, "v10-vless-xhttp"), (pw, "v10-vless-ws"), (pt, "v10-trojan-ws")):
                self.assertIn(f"/{p} {t}-loc{slot}-http", mp)
        inb = {i["tag"]: i for i in json.load(open(f"{H}/xray/configs/05_inbounds_30_locations.json"))["inbounds"]}
        for r in rows:
            slot, px, pw, pt = r.split("\t")
            self.assertEqual(inb[f"v10-vless-xhttp-loc{slot}"]["streamSettings"]["xhttpSettings"]["path"], "/" + px)
            self.assertEqual(inb[f"v10-vless-ws-loc{slot}"]["streamSettings"]["wsSettings"]["path"], "/" + pw)
            self.assertEqual(inb[f"v10-trojan-ws-loc{slot}"]["streamSettings"]["wsSettings"]["path"], "/" + pt)
        users = {c["id"] for c in inb["v10-vless-ws-loc1"]["settings"]["clients"]}
        self.assertEqual(users, {USERS["active"], USERS["tester"]}, "only active users are rendered as clients")

    def test_each_transport_toggle_removes_exactly_its_inbounds_and_configs_stay_valid(self):
        cases = {"trojan_enable": {"v10-trojan-ws-loc%d" % s for s in range(1, 5)},
                 "xhttp_enable": {"v10-vless-xhttp-loc%d" % s for s in range(1, 5)},
                 "ws_enable": {"v10-vless-ws-loc%d" % s for s in range(1, 5)} | {"v10-trojan-ws-loc%d" % s for s in range(1, 5)},
                 "vless_enable": {"v10-vless-xhttp-loc%d" % s for s in range(1, 5)} | {"v10-vless-ws-loc%d" % s for s in range(1, 5)}}
        for key, gone in cases.items():
            with self.subTest(disabled=key):
                mysql(f"update bool_config set value=0 where `key`='{key}'")
                flush_cache()
                render_now()
                ok_x, ok_h, ox, oh = validate()
                self.assertTrue(ok_x, ox)
                self.assertTrue(ok_h, oh)
                all12 = {f"v10-{p}-{s}-loc{n}" for n in range(1, 5) for p, s in (("vless", "xhttp"), ("vless", "ws"), ("trojan", "ws"))}
                self.assertEqual(set(self.inbound_tags_rendered()), all12 - gone)
                cfg = read("haproxy/haproxy.cfg").decode()
                for t in gone:
                    self.assertNotIn(f"backend {t}\n", cfg)
                    self.assertNotIn(f"use_backend {t} ", cfg)
                for t in all12 - gone:
                    self.assertIn(f"backend {t}\n", cfg)
                    self.assertIn(f"use_backend {t} ", cfg)
                mp = read("haproxy/maps/path_v10").decode()
                for t in gone:
                    self.assertNotIn(f"{t}-http", mp)
                mysql(f"update bool_config set value=1 where `key`='{key}'")

    def test_all_transports_off_and_singbox_core_still_render_valid_configs(self):
        mysql("update bool_config set value=0 where `key` in ('trojan_enable','ws_enable','xhttp_enable')")
        flush_cache()
        render_now()
        ok_x, ok_h, ox, oh = validate()
        self.assertTrue(ok_x and ok_h, (ox, oh))
        self.assertEqual(self.inbound_tags_rendered(), [])
        mysql("update bool_config set value=1 where `key` in ('trojan_enable','ws_enable','xhttp_enable')")
        mysql("update str_config set value='singbox' where `key`='core_type'")
        flush_cache()
        render_now()
        ok_x, ok_h, ox, oh = validate()
        self.assertEqual(self.inbound_tags_rendered(), [], "location inbounds exist for the xray core only")
        self.assertNotIn("use_backend v10-vless-ws-loc1", read("haproxy/haproxy.cfg").decode())
        self.assertTrue(ok_h, oh)

    def test_nginx_config_is_untouched_and_valid(self):
        os.makedirs(f"{H}/nginx/run", exist_ok=True)
        r = sh(["/usr/sbin/nginx", "-t", "-c", f"{H}/nginx/nginx.conf"], check=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class TestRuleOrderOfTheCountryEntrances(unittest.TestCase):
    """The exact rule order of the first-tier xray (hiddify-xray) for the country inbounds (audit checklist B3), read from the EFFECTIVE merged config
    (`xray run -dump -confdir`), not from a template:
      stock head -> country copies of the geosite rule -> the stock geosite rule -> api -> block private -> block BitTorrent -> country finals -> catch-all.
    The geosite copies sit exactly where the stock geosite rule sits (before the blocks): for those domains a country behaves like every ordinary profile.
    The country FINAL rule (everything else) sits after both blocks."""

    @classmethod
    def setUpClass(cls):
        render_now()
        r = sh([XRAY, "run", "-dump", "-confdir", f"{H}/xray/configs/"])
        cls.rules = json.loads(r.stdout)["routing"]["rules"]

    def find(self, pred, what):
        hits = [i for i, r in enumerate(self.rules) if pred(r)]
        self.assertEqual(len(hits), 1, f"{what}: expected exactly one rule, found {len(hits)}")
        return hits[0]

    def test_geosite_copies_precede_the_stock_geosite_rule_and_the_blocks_while_the_country_final_follows_them(self):
        rules = self.rules
        is_geo = lambda r: "geosite:google" in (r.get("domain") or [])           # noqa: E731
        stock_geo = self.find(lambda r: is_geo(r) and not r.get("inboundTag"), "stock geosite rule")
        private = self.find(lambda r: r.get("ip") == ["geoip:private"], "block private")
        torrent = self.find(lambda r: r.get("protocol") == ["bittorrent"], "block BitTorrent")
        catch_all = self.find(lambda r: r.get("port") == "0-65535" and not r.get("inboundTag"), "catch-all")
        self.assertLess(stock_geo, private)
        self.assertLess(private, torrent)
        self.assertLess(torrent, catch_all)
        slots = sorted({int(t.rsplit("loc", 1)[1]) for r in rules for t in (r.get("inboundTag") or []) if "-loc" in t})
        self.assertEqual(slots, [1, 2, 3, 4], "all four country slots are rendered")
        for slot in slots:
            tags = [f"v10-vless-xhttp-loc{slot}", f"v10-vless-ws-loc{slot}", f"v10-trojan-ws-loc{slot}"]
            geo = self.find(lambda r: r.get("inboundTag") == tags and is_geo(r), f"slot {slot} geosite copy")
            final = self.find(lambda r: r.get("inboundTag") == tags and not r.get("domain"), f"slot {slot} final")
            self.assertLess(geo, stock_geo, f"slot {slot}: the geosite copy must win over the stock rule for the country's inbounds")
            self.assertLess(geo, private, f"slot {slot}: the geosite copy sits where the stock rule sits - before the blocks")
            self.assertGreater(final, torrent, f"slot {slot}: the country final comes AFTER the private / BitTorrent blocks")
            self.assertLess(final, catch_all, f"slot {slot}: ... and before the generic catch-all")
            self.assertEqual(rules[geo]["outboundTag"], rules[final]["outboundTag"], f"slot {slot}: same router entrance for the copy and the final")
            self.assertNotEqual(rules[final]["outboundTag"], "commercial-local-router", f"slot {slot}: never the default router entrance")
            self.assertEqual(rules[geo]["domain"], rules[stock_geo]["domain"], f"slot {slot}: the copy covers exactly the stock rule's domains")

    def test_no_other_rule_is_inserted_between_the_blocks_and_the_catch_all(self):
        rules = self.rules
        torrent = next(i for i, r in enumerate(rules) if r.get("protocol") == ["bittorrent"])
        catch_all = next(i for i, r in enumerate(rules) if r.get("port") == "0-65535" and not r.get("inboundTag"))
        between = rules[torrent + 1:catch_all]
        self.assertEqual(len(between), 4, between)
        for r in between:
            self.assertEqual(sorted(set(r) - {"type"}), ["inboundTag", "outboundTag"], "only the plain per-country finals live there")


class TestWeeklyFullInstallFlow(unittest.TestCase):
    """prod: cron @reboot -> install.sh (clean_files; jinja FULL render; run.sh of every component). Emulated without apt/downloads."""

    def test_clean_files_then_full_render_reproduces_the_same_configs(self):
        before = {f: open(f, "rb").read() for f in
                  [f"{H}/{t}" for t in RENDERED] + [f"{H}/xray/configs/{n}" for n in os.listdir(f"{H}/xray/configs") if n.endswith(".json")]}
        # clean_files() of install.sh
        sh(f"rm -rf {H}/xray/configs/*.json {H}/singbox/configs/*.json {H}/haproxy/*.cfg", check=True)
        sh(["bash", "/work/tools/stand_render.sh"], check=False)            # = reload_all_configs + replace_variables.sh (FULL jinja render)
        after = {f: open(f, "rb").read() for f in before if os.path.exists(f)}
        self.assertEqual(set(before), set(after), "a rendered file disappeared")
        changed = [f for f in before if before[f] != after[f]]
        self.assertEqual(changed, [], f"rendering from scratch gives different files: {changed}")

    def test_services_restarted_in_install_order_serve_the_locations_again(self):
        for u in ("hiddify-nginx",):
            systemd("restart", u)
        sh(["systemctl", "stop", "hiddify-haproxy"])
        systemd("start", "hiddify-haproxy")                                  # haproxy/run.sh: stop + start
        systemd("restart", "hiddify-xray")
        wait_until(lambda: via(pick(links("tester"), "Германия", "XHTTP")[1]) == "exit-a", timeout=40, step=2, what="Germany after restarts")
        self.assertEqual(via(pick(links("tester"), "США", "Trojan")[1]), "exit-b")
        self.assertEqual(via(pick(links("tester"), "Германия", "WS", exclude=("Trojan",))[1], RU_DOMAIN), "direct")

    def test_one_minute_apply_users_job_keeps_the_location_files(self):
        before = read("xray/configs/05_inbounds_30_locations.json")
        sh(["bash", "/work/tools/stand_render.sh", "apply_users"], check=False)
        self.assertEqual(read("xray/configs/05_inbounds_30_locations.json"), before)
        self.assertTrue(os.path.exists(f"{H}/xray/configs/05_inbounds_30_locations.json.j2"), "template must survive")
        r = sh([XRAY, "run", "-test", "-confdir", f"{H}/xray/configs/"], check=False)
        self.assertIn("Configuration OK", r.stdout + r.stderr)


class TestServiceDisruption(unittest.TestCase):
    def test_haproxy_reload_does_not_cut_a_running_transfer(self):
        link = pick(links("tester"), "Германия", "WS")[1]
        res = {}

        def slow():
            try:
                with sc.Client(link) as c:
                    t0 = time.monotonic()
                    st, body = c.get("http://13.0.0.20:18080/slow?ms=6000", timeout=20)
                    res["ok"] = (st == 200 and isinstance(body, dict) and body.get("via") == "exit-a")
                    res["dt"] = time.monotonic() - t0
            except Exception as e:
                res["err"] = str(e)
        th = threading.Thread(target=slow)
        th.start()
        time.sleep(2.0)
        sh(["systemctl", "reload", "hiddify-haproxy"])
        th.join(30)
        self.assertTrue(res.get("ok"), res)
        self.assertGreater(res.get("dt", 0), 5.5)

    def test_hiddify_xray_restart_window_is_short(self):
        link = pick(links("tester"), "США", "XHTTP")[1]
        samples = []
        stop = threading.Event()

        def probe():
            while not stop.is_set():
                t0 = time.monotonic()
                samples.append((t0, via(link, FOREIGN, timeout=2.5) == "exit-b"))
        th = threading.Thread(target=probe)
        th.start()
        time.sleep(1.5)
        t0 = time.monotonic()
        systemd("restart", "hiddify-xray")
        t_restart = time.monotonic() - t0
        time.sleep(6)
        stop.set()
        th.join(20)
        bad = [ts for ts, ok in samples if not ok]
        window = (max(bad) - min(bad) + 2.5) if bad else 0
        print(f"\n   hiddify-xray restart: systemctl returned in {t_restart:.1f}s, failed/unavailable window ~{window:.1f}s ({len(bad)} failed probes)")
        self.assertLess(window, 15, "restart window")
        self.assertTrue(samples[-1][1], "service is back")


if __name__ == "__main__":
    unittest.main(verbosity=2)
