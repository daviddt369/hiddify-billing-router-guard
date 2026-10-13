"""Data-plane tests with REAL processes only:  real xray client (built from the subscription link) -> HAProxy:443 (TLS) -> hiddify-xray
-> commercial-local-router / country entrance -> xray-router -> exit (real xray) -> whoami "internet".   `via` in the answer says
which exit carried the connection ('direct' = left the RU node directly: RU-direct rule, or a LEAK).

Run: /opt/hiddify-manager/.venv313/bin/python -m unittest -v test_03_dataplane      (from /work/tests; ~6-8 min)"""
import os
import sys
import time
import unittest
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stand_lib import *  # noqa: E402,F403

PROFILES = {}


def profile_links():
    ls = dict(links("tester"))
    out = {}
    # "AUTO <t>" = the global fastest-exit behaviour = the legacy direct entrance of that transport (the AUTO group of the full xray JSON holds exactly these)
    legacy_names = {"XHTTP": "RU_GLOBAL tls_h2 xhttp direct vless dl=h2", "WS": "RU_GLOBAL tls WS direct vless", "Trojan": "RU_GLOBAL tls WS direct trojan"}
    for tname in ("XHTTP", "WS", "Trojan"):
        out[f"AUTO {tname}"] = ls[legacy_names[tname]]
        out[f"DE {tname}"] = ls[f"🇩🇪 Германия · {tname}"]
        out[f"US {tname}"] = ls[f"🇺🇸 США · {tname}"]
    out["LEGACY xhttp"] = ls["RU_GLOBAL tls_h2 xhttp direct vless dl=h2"]
    out["LEGACY ws"] = ls["RU_GLOBAL tls WS direct vless"]
    out["LEGACY trojan"] = ls["RU_GLOBAL tls WS direct trojan"]
    out["LEGACY httpupgrade"] = ls["RU_GLOBAL tls httpupgrade direct vless"]
    out["LEGACY xhttp-dl3"] = ls["RU_GLOBAL tls xhttp direct vless dl=h3"]
    return out


def setUpModule():
    global PROFILES
    for u in ("stand-exit-a", "stand-exit-b", "stand-shim-a", "stand-shim-b"):
        systemd("start", u)
    set_delay("a", 0)
    set_delay("b", 0)
    reset_locations_to_stand_default()
    apply_router()
    PROFILES = profile_links()


def settle_default():
    """back to the canonical state, router applied"""
    for u in ("stand-exit-a", "stand-exit-b", "stand-shim-a", "stand-shim-b"):
        systemd("start", u)
    set_delay("a", 0)
    set_delay("b", 0)
    reset_locations_to_stand_default()
    apply_router()


class TestCountryPinning(unittest.TestCase):
    def test_germany_always_exits_via_exit_a_usa_via_exit_b(self):
        for tname in ("XHTTP", "WS", "Trojan"):
            for url in (FOREIGN, FOREIGN_IP):
                self.assertEqual(set(via_many(PROFILES[f"DE {tname}"], 6, url)), {"exit-a"}, f"DE {tname} {url}")
                self.assertEqual(set(via_many(PROFILES[f"US {tname}"], 6, url)), {"exit-b"}, f"US {tname} {url}")

    def test_ru_destinations_never_leave_through_an_exit(self):
        for name, link in PROFILES.items():
            for url in (RU_DOMAIN, RU_CIDR):
                self.assertEqual(via(link, url), "direct", f"{name} {url}: RU traffic must stay in Russia (direct from the RU node)")

    def test_auto_and_legacy_profiles_use_the_global_balancer(self):
        for name, link in PROFILES.items():
            if name.startswith(("AUTO", "LEGACY")):
                self.assertIn(via(link, FOREIGN), ("exit-a", "exit-b"), name)

    def test_nothing_ever_leaks_direct_for_foreign_destinations(self):
        for name, link in PROFILES.items():
            got = set(via_many(link, 3, FOREIGN))
            self.assertTrue(got <= {"exit-a", "exit-b"}, f"{name}: {got}")


class TestFastestExitAndFailover(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        settle_default()

    def test_auto_follows_the_fastest_exit_but_a_country_stays_pinned(self):
        set_delay("a", 200)
        set_delay("b", 0)
        wait_until(lambda: set(via_many(PROFILES["AUTO XHTTP"], 4)) == {"exit-b"}, timeout=40, step=2, what="AUTO -> fast exit-b")
        self.assertEqual(set(via_many(PROFILES["DE XHTTP"], 4)), {"exit-a"}, "Germany must stay on its own (slow) exit")
        self.assertEqual(set(via_many(PROFILES["LEGACY ws"], 4)), {"exit-b"}, "legacy profiles keep the old fastest-exit behaviour")
        set_delay("a", 0)
        set_delay("b", 200)
        wait_until(lambda: set(via_many(PROFILES["AUTO WS"], 4)) == {"exit-a"}, timeout=40, step=2, what="AUTO -> fast exit-a")
        self.assertEqual(set(via_many(PROFILES["US XHTTP"], 4)), {"exit-b"}, "USA must stay on its own (slow) exit")
        set_delay("a", 0)
        set_delay("b", 0)

    def test_dead_country_fails_closed_never_borrowing_another_country(self):
        systemd("stop", "stand-exit-a")
        try:
            # observatory needs a couple of probe intervals (3 s) to notice
            wait_until(lambda: all(r is None for r in via_many(PROFILES["DE XHTTP"], 3, timeout=6)), timeout=60, step=3,
                       what="Germany closed while its exit is dead")
            for tname in ("XHTTP", "WS", "Trojan"):
                got = via_many(PROFILES[f"DE {tname}"], 3, timeout=6)
                self.assertEqual(got, [None] * 3, f"DE {tname}: must fail closed, got {got} (a result here would be a cross-country fallback)")
            # others are unaffected
            self.assertEqual(set(via_many(PROFILES["US XHTTP"], 3)), {"exit-b"})
            self.assertEqual(set(via_many(PROFILES["AUTO XHTTP"], 3)), {"exit-b"}, "AUTO keeps its reserve behaviour")
            self.assertEqual(set(via_many(PROFILES["LEGACY xhttp"], 3)), {"exit-b"})
        finally:
            systemd("start", "stand-exit-a")
        wait_until(lambda: set(via_many(PROFILES["DE XHTTP"], 3)) == {"exit-a"}, timeout=60, step=3, what="Germany recovers")

    def test_reserve_inside_a_country_when_one_of_its_nodes_dies(self):
        # give Germany one node on exit A and one on exit B ("two servers in one country"), then kill exit A
        ctl("unassign", 2)
        ctl("assign", 1, 3)        # Germany = node 1 (exit A) + node 3 (exit B, moved over from the USA)
        ctl("assign", 2, 4)        # USA = node 4 (exit B)
        apply_router()
        try:
            seen = set(via_many(PROFILES["DE XHTTP"], 10))
            self.assertTrue(seen <= {"exit-a", "exit-b"} and seen, seen)
            systemd("stop", "stand-exit-a")
            wait_until(lambda: set(via_many(PROFILES["DE WS"], 4, timeout=6)) == {"exit-b"}, timeout=60, step=3,
                       what="Germany reserve node on exit B takes over inside the country")
        finally:
            systemd("start", "stand-exit-a")
            settle_default()

    def test_all_nodes_dead_means_closed_for_every_country_but_not_for_auto_when_a_node_lives(self):
        systemd("stop", "stand-exit-a")
        systemd("stop", "stand-exit-b")
        try:
            wait_until(lambda: via(PROFILES["AUTO XHTTP"], timeout=5) is None, timeout=60, step=3, what="AUTO closed (everything dead)")
            for name in ("DE XHTTP", "US WS", "AUTO Trojan", "LEGACY ws"):
                self.assertIsNone(via(PROFILES[name], timeout=5), name)
            # RU traffic still works without any exit
            self.assertEqual(via(PROFILES["DE XHTTP"], RU_DOMAIN), "direct")
            self.assertEqual(via(PROFILES["US Trojan"], RU_CIDR), "direct")
        finally:
            systemd("start", "stand-exit-a")
            systemd("start", "stand-exit-b")
        wait_until(lambda: via(PROFILES["AUTO XHTTP"]) in ("exit-a", "exit-b"), timeout=60, step=3, what="recovery")


class TestSlotStates(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        settle_default()

    def test_disabled_country_blocks_an_old_profile_and_never_leaks(self):
        old = PROFILES["US XHTTP"]
        self.assertEqual(via(old), "exit-b")
        ctl("set", 2, "--enabled", 0)
        apply_router()
        try:
            self.assertEqual(via_many(old, 3, timeout=6), [None] * 3, "an old profile of a disabled country must be blocked")
            self.assertFalse(any("США" in n for n, _ in links("tester")))
            self.assertEqual(via(old, RU_DOMAIN), "direct", "RU stays direct even for a blocked country")
            self.assertEqual(via(PROFILES["DE XHTTP"]), "exit-a")
        finally:
            ctl("set", 2, "--enabled", 1)
            apply_router()
        self.assertEqual(via(old), "exit-b", "re-enabled -> the same profile works again")

    def test_country_without_nodes_blocks(self):
        ctl("unassign", 3, 4)
        apply_router()
        try:
            self.assertEqual(via_many(PROFILES["US WS"], 3, timeout=6), [None] * 3)
            self.assertEqual(set(via_many(PROFILES["DE WS"], 3)), {"exit-a"})
        finally:
            ctl("assign", 2, 3, 4)
            apply_router()
        self.assertEqual(via(PROFILES["US WS"]), "exit-b")

    def test_unconfigured_slot_is_a_blocking_entrance(self):
        # slot 3 has inbounds but no name/nodes: a client that somehow knew its path must be blocked, not routed to AUTO
        p3 = mysql("select path_xhttp from commercial_routing_location where slot=3")
        u = urllib.parse.urlsplit(PROFILES["LEGACY xhttp"])
        q = dict(urllib.parse.parse_qsl(u.query, keep_blank_values=True))
        q["path"] = "/" + p3
        link = urllib.parse.urlunsplit((u.scheme, u.netloc, u.path, urllib.parse.urlencode(q), "slot3"))
        self.assertEqual(via_many(link, 3, timeout=6), [None] * 3)
        self.assertEqual(via(link, RU_DOMAIN), "direct")

    def test_moving_a_node_between_countries_takes_effect_after_apply(self):
        ctl("unassign", 3)
        ctl("assign", 1, 3)           # exit-b-1 joins Germany
        apply_router()
        try:
            seen = set(via_many(PROFILES["DE XHTTP"], 12))
            self.assertEqual(seen, {"exit-a", "exit-b"} & seen)
            self.assertTrue(seen)
            self.assertEqual(set(via_many(PROFILES["US XHTTP"], 4)), {"exit-b"})
        finally:
            settle_default()


class TestLegacyBehaviourUnchanged(unittest.TestCase):
    def test_every_legacy_transport_still_works_with_locations_enabled(self):
        for name in ("LEGACY xhttp", "LEGACY ws", "LEGACY trojan", "LEGACY httpupgrade", "LEGACY xhttp-dl3"):
            self.assertIn(via(PROFILES[name]), ("exit-a", "exit-b"), name)
            self.assertEqual(via(PROFILES[name], RU_DOMAIN), "direct", name)

    def test_regular_user_connects_exactly_like_before(self):
        ls = dict(links("active"))
        for name in ("RU_GLOBAL tls_h2 xhttp direct vless dl=h2", "RU_GLOBAL tls WS direct vless", "RU_GLOBAL tls WS direct trojan"):
            self.assertIn(via(ls[name]), ("exit-a", "exit-b"), name)

    def test_panel_and_haproxy_served_paths_still_work_over_http1_and_http2(self):
        # HAProxy answers /<client path>/generate_204 itself and routes the panel path to the panel: exercises the front rules and the
        # path_v10 map that the feature extended (the decoy for unknown paths needs real Internet access, so it is not asserted here)
        for proto_flag in ("--http1.1", "--http2"):
            for path, ok in ((f"/{PC}/{USERS['active']}/sub/", "200"), (f"/{PC}/generate_204", "204")):
                r = sh(["curl", "-sS", proto_flag, "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "15", "-A", "v2rayNG/1.8",
                        f"https://ru.stand.test{path}"], check=False)
                self.assertEqual(r.stdout.strip(), ok, f"{proto_flag} {path}: {r.stdout} {r.stderr}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
