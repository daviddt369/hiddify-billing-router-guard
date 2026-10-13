"""The three buttons Happ / INCY show - AUTO, Германия, США - on the wire with a REAL xray client built from the panel's own full-xray JSON:
client-side balancer (leastPing + burstObservatory) -> HAProxy -> hiddify-xray -> router -> exit -> whoami "internet".

Run: /opt/hiddify-manager/.venv313/bin/python -W ignore -m unittest -v test_10_groups_wire   (from /work/tests)"""
import copy
import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stand_lib import *  # noqa: E402,F403

AUTO, DE, US = "🟢 AUTO", "🇩🇪 Германия", "🇺🇸 США"


def groups(user="tester"):
    return {c["remarks"]: c for c in json.loads(sub_text(user, fmt="happ/", ua="Happ/3.5.0"))}      # the dedicated Happ / INCY link


def answers(cfg, n=6, url=FOREIGN, settle=25.0):
    """start a real client for this config; wait until the balancer has a live member, then ask n times. -> list of 'via'"""
    with sc.JsonClient(cfg) as c:
        t0 = time.monotonic()
        first = None
        while time.monotonic() - t0 < settle:
            try:
                st, body = c.get(url, timeout=4)
                if isinstance(body, dict) and body.get("via"):
                    first = body["via"]
                    break
            except Exception:
                pass
            time.sleep(1)
        out = [first]
        for _ in range(n - 1):
            try:
                st, body = c.get(url, timeout=6)
                out.append(body.get("via") if isinstance(body, dict) else None)
            except Exception:
                out.append(None)
        return out


def setUpModule():
    for u in ("stand-exit-a", "stand-exit-b", "stand-shim-a", "stand-shim-b"):
        systemd("start", u)
    set_delay("a", 0)
    set_delay("b", 0)
    reset_locations_to_stand_default()
    apply_router()


def tearDownModule():
    for u in ("stand-exit-a", "stand-exit-b", "stand-shim-a", "stand-shim-b"):
        systemd("start", u)
    set_delay("a", 0)
    set_delay("b", 0)
    reset_locations_to_stand_default()
    apply_router()


class TestGroupsOnTheWire(unittest.TestCase):
    def tearDown(self):
        for u in ("stand-exit-a", "stand-exit-b", "stand-shim-a", "stand-shim-b"):
            systemd("start", u)
        set_delay("a", 0)
        set_delay("b", 0)

    def test_each_country_button_stays_in_its_country_whatever_protocol_the_client_picks(self):
        g = groups()
        self.assertEqual(set(answers(g[DE], 8)), {"exit-a"})
        self.assertEqual(set(answers(g[US], 8)), {"exit-b"})
        for url in (FOREIGN, FOREIGN_IP):
            self.assertEqual(set(answers(g[DE], 3, url)), {"exit-a"}, url)

    def test_auto_button_works_and_follows_the_fastest_exit(self):
        g = groups()
        self.assertTrue(set(answers(g[AUTO], 6)) <= {"exit-a", "exit-b"})
        set_delay("a", 400)
        wait_until(lambda: set(via_many(dict(links("tester"))["RU_GLOBAL tls WS direct vless"], 3)) == {"exit-b"}, timeout=60, step=3, what="router prefers exit-b")
        wait_until(lambda: set(answers(g[AUTO], 5)) == {"exit-b"}, timeout=90, step=2, what="the AUTO button follows the fast exit")
        self.assertEqual(set(answers(g[DE], 5)), {"exit-a"}, "a country does not follow the speed of the other country")

    def test_a_broken_protocol_inside_a_button_is_skipped_by_the_client_balancer(self):
        cfg = copy.deepcopy(groups()[DE])
        first = [o for o in cfg["outbounds"] if o["protocol"] in ("vless", "trojan")][0]
        ss = first["streamSettings"]
        key = next(k for k in ss if k.endswith("Settings") and "path" in ss[k])
        ss[key]["path"] = "/this-path-does-not-exist"                       # the FIRST member (also the balancer's fallback) cannot connect
        res = answers(cfg, 6, settle=40)
        self.assertEqual(set(res), {"exit-a"}, f"the balancer must move to a working protocol: {res}")

    def test_when_the_whole_country_is_down_its_button_fails_and_never_borrows_the_other_country(self):
        g = groups()
        systemd("stop", "stand-exit-a")
        systemd("stop", "stand-shim-a")
        try:
            wait_until(lambda: via(pick(links("tester"), "Германия", "XHTTP")[1], timeout=5) is None, timeout=60, step=3, what="Germany closed")
            res = answers(g[DE], 4, settle=5)
            self.assertEqual([r for r in res if r], [], f"Germany must fail closed, got {res}")
            self.assertEqual(set(answers(g[US], 4)), {"exit-b"}, "the other country is unaffected")
            self.assertTrue(set(answers(g[AUTO], 4)) <= {"exit-b"}, "AUTO still works through the remaining exit")
        finally:
            systemd("start", "stand-exit-a")
            systemd("start", "stand-shim-a")
        wait_until(lambda: via(pick(links("tester"), "Германия", "XHTTP")[1]) == "exit-a", timeout=60, step=3, what="Germany recovered")


if __name__ == "__main__":
    unittest.main(verbosity=2)
