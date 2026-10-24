"""AUTO director on the stand: the REAL layer (loopback outbounds, balancer, API on a unix socket), the REAL controller process and the stand's two real exits
(exit-a = "Germany", exit-b = "USA" with controllable delay). Questions: does AUTO follow the better country with hysteresis and without flapping, does it leave a dead
country within a few cycles, does it close when both are down, does RU stay direct, does the router keep working when the controller disappears."""
import json
import os
import random
import signal
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, "/work/tests")
from stand_lib import apply_router, reset_locations_to_stand_default, router_ready, set_delay, systemd, wait_until, sh  # noqa: E402

CONTROLLER = "/work/stage2/AUTO-director/controller/auto_director.py"
CONF_DIR = "/etc/auto-director"
SOCK = "/run/xray-router-api.sock"
XRAY = "/usr/bin/xray"
WHOAMI = "http://13.0.0.20:18080/"
RU_URL = "http://77.88.55.60:18080/"           # a Russian-looking address of the stand (RU direct list)


def client_of(entrance=20808, url=WHOAMI, timeout=6):
    r = subprocess.run(["curl", "-s", "--max-time", str(timeout), "--socks5-hostname", f"127.0.0.1:{entrance}", url], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        return {"127.0.0.191": "A", "127.0.0.231": "B"}.get(json.loads(r.stdout).get("client"), json.loads(r.stdout).get("client"))
    except ValueError:
        return None


def api(*args):
    r = subprocess.run([XRAY, "api", args[0], f"--server=unix://{SOCK}", *args[1:]], capture_output=True, text=True)
    return (r.stdout + r.stderr).strip()


class TestAutoDirector(unittest.TestCase):
    ctl_conf = None
    proc = None

    @classmethod
    def setUpClass(cls):
        for u in ("stand-exit-a", "stand-exit-b", "stand-shim-a", "stand-shim-b"):
            systemd("start", u)
        reset_locations_to_stand_default()
        os.makedirs(CONF_DIR, exist_ok=True)
        with open(f"{CONF_DIR}/config.json", "w") as f:
            json.dump({"enabled": True, "api_socket": SOCK}, f)
        apply_router()                                   # the layer now carries the director (one router restart)
        router_ready(120)
        cls.tmp = tempfile.mkdtemp(prefix="director-")
        cls.conf_path = os.path.join(cls.tmp, "controller.json")
        cls.state = os.path.join(cls.tmp, "state")
        with open(cls.conf_path, "w") as f:
            json.dump({"api_socket": SOCK, "xray": XRAY, "probe_url": WHOAMI, "probe_ok_codes": [200],
                       "countries": [{"name": "DE", "entrance": 20811, "outbound": "auto-via-s1"}, {"name": "US", "entrance": 20812, "outbound": "auto-via-s2"}],
                       "interval_s": 2, "probes_per_cycle": 2, "probe_timeout_s": 4, "margin_ms": 20, "confirm_cycles": 3, "fail_cycles": 3,
                       "min_dwell_s": 12, "ewma_alpha": 0.3, "recover_cycles": 2}, f)

    @classmethod
    def tearDownClass(cls):
        cls.stop_controller()
        api("bo", "-b", "auto-director", "-r")
        try:
            os.unlink(f"{CONF_DIR}/config.json")
        except OSError:
            pass
        for e in ("a", "b"):
            set_delay(e, 0)
        for u in ("stand-exit-a", "stand-exit-b", "stand-shim-a", "stand-shim-b"):
            systemd("start", u)
        apply_router()                                   # the layer without the director again
        router_ready(120)

    def setUp(self):
        for u in ("stand-exit-a", "stand-exit-b", "stand-shim-a", "stand-shim-b"):
            systemd("start", u)
        for e in ("a", "b"):
            set_delay(e, 0)
        self.stop_controller()
        api("bo", "-b", "auto-director", "-r")
        sh(f"rm -rf {self.state}", check=False)

    def tearDown(self):
        self.stop_controller()

    # ---- controller process
    @classmethod
    def start_controller(cls):
        env = dict(os.environ, AUTO_DIRECTOR_CONF=cls.conf_path, AUTO_DIRECTOR_STATE=cls.state)
        cls.proc = subprocess.Popen([sys.executable, CONTROLLER, "run"], env=env, stdout=open(os.path.join(cls.tmp, "ctl.log"), "a"), stderr=subprocess.STDOUT)

    @classmethod
    def stop_controller(cls):
        if cls.proc and cls.proc.poll() is None:
            cls.proc.send_signal(signal.SIGTERM)
            try:
                cls.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                cls.proc.kill()
        cls.proc = None

    def status(self):
        try:
            return json.load(open(os.path.join(self.state, "status.json")))
        except (OSError, ValueError):
            return {}

    def switches(self):
        try:
            return json.load(open(os.path.join(self.state, "state.json"))).get("switches", [])
        except (OSError, ValueError):
            return []

    # ---- tests
    def test_01_the_layer_carries_the_director_and_the_router_serves_without_a_controller(self):
        cfg = json.load(open("/etc/xray-router/config.json"))
        self.assertIn("auto-director", [b["tag"] for b in cfg["routing"]["balancers"]])
        self.assertEqual(cfg["routing"]["rules"][-1].get("balancerTag"), "auto-director")
        self.assertEqual(cfg["api"]["services"], ["RoutingService"])
        self.assertTrue(os.path.exists(SOCK))
        self.assertEqual(oct(os.stat(SOCK).st_mode & 0o777), "0o600")
        # no controller: the router's own country-level leastPing decides, both ways, and still fails over
        set_delay("b", 120)
        wait_until(lambda: client_of() == "A", timeout=40, step=2, what="native AUTO prefers the faster country A")
        set_delay("b", 0); set_delay("a", 120)
        wait_until(lambda: client_of() == "B", timeout=40, step=2, what="native AUTO prefers the faster country B")
        # a country entrance of a pinned profile is unaffected by the director
        self.assertEqual(client_of(20811), "A")
        self.assertEqual(client_of(20812), "B")

    def test_02_the_controller_pins_the_better_country_and_keeps_it_under_jitter(self):
        set_delay("a", 0); set_delay("b", 60)
        self.start_controller()
        wait_until(lambda: self.status().get("cur") == "DE", timeout=30, step=1, what="start on the faster country")
        wait_until(lambda: "auto-via-s1" in api("bi", "auto-director"), timeout=15, step=1, what="override visible in the router")
        self.assertEqual({client_of() for _ in range(6)}, {"A"})
        # strong jitter (+-10 ms around equal routes) for a minute: nothing may move
        rnd = random.Random(5)
        set_delay("b", 0)
        t0 = time.time()
        while time.time() - t0 < 50:
            set_delay("a", rnd.randint(0, 20)); set_delay("b", rnd.randint(0, 20))
            time.sleep(1)
        self.assertEqual([s for s in self.switches() if s["from"] is not None], [], self.switches())
        self.assertEqual({client_of() for _ in range(4)}, {"A"})

    def test_03_a_real_advantage_moves_new_connections_once_and_not_before_the_confirmation(self):
        set_delay("a", 0); set_delay("b", 0)
        self.start_controller()
        wait_until(lambda: self.status().get("cur") in ("DE", "US"), timeout=30, step=1, what="start")
        first = self.status()["cur"]
        better, worse = ("b", "a") if first == "DE" else ("a", "b")
        time.sleep(14)                                                    # past the dwell time
        set_delay(worse, 0); set_delay(better, 0)
        # the OTHER country becomes 150 ms slower than the current one's rival: the current stays; then the current one gets slow -> move
        cur_letter = "a" if first == "DE" else "b"
        other = "b" if first == "DE" else "a"
        t0 = time.time()
        set_delay(cur_letter, 200)
        wait_until(lambda: self.status().get("cur") != first, timeout=60, step=1, what="move to the faster country")
        took = time.time() - t0
        self.assertGreater(took, 3 * 2 - 1, "must not move before the confirmation (3 cycles x 2 s)")
        new_letter = "A" if other == "a" else "B"
        wait_until(lambda: client_of() == new_letter, timeout=15, step=1, what="new connections follow")
        self.assertEqual(len([s for s in self.switches() if s["from"] is not None]), 1)

    def test_04_a_dead_country_is_left_within_a_few_cycles_and_both_dead_closes_ru_stays_direct(self):
        self.start_controller()
        wait_until(lambda: self.status().get("cur") in ("DE", "US"), timeout=30, step=1, what="start")
        cur = self.status()["cur"]
        dead_unit, alive = ("stand-exit-a", "B") if cur == "DE" else ("stand-exit-b", "A")
        t0 = time.time()
        systemd("stop", dead_unit)
        wait_until(lambda: client_of() == alive, timeout=45, step=1, what="AUTO leaves the dead country")
        self.assertLess(time.time() - t0, 30)
        other_unit = "stand-exit-b" if dead_unit == "stand-exit-a" else "stand-exit-a"
        systemd("stop", other_unit)
        wait_until(lambda: self.status().get("cur") == "NONE", timeout=45, step=1, what="both down -> closed")
        wait_until(lambda: client_of() is None, timeout=15, step=1, what="AUTO traffic is blocked")
        self.assertIn("block", api("bi", "auto-director"))
        # Russian destinations never needed an exit
        r = subprocess.run(["curl", "-s", "--max-time", "6", "-o", "/dev/null", "-w", "%{http_code}", "--socks5-hostname", "127.0.0.1:20808", RU_URL], capture_output=True, text=True)
        self.assertNotEqual(r.stdout.strip(), "000", "RU direct must not depend on the exits")
        # recovery
        systemd("start", dead_unit, other_unit)
        systemd("start", "stand-shim-a", "stand-shim-b")
        wait_until(lambda: self.status().get("cur") in ("DE", "US"), timeout=60, step=1, what="reopen when a country answers")
        wait_until(lambda: client_of() in ("A", "B"), timeout=20, step=1, what="AUTO serves again")

    def test_05_the_controller_dying_leaves_a_working_router_and_a_restart_reasserts_the_choice(self):
        set_delay("a", 0); set_delay("b", 80)
        self.start_controller()
        wait_until(lambda: self.status().get("cur") == "DE", timeout=30, step=1, what="start")
        self.proc.kill(); self.proc.wait()                               # SIGKILL: the override stays in the router, AUTO keeps its last choice
        self.proc = None
        self.assertEqual({client_of() for _ in range(4)}, {"A"})
        api("bo", "-b", "auto-director", "-r")                           # what systemd's ExecStopPost does
        set_delay("a", 150); set_delay("b", 0)
        wait_until(lambda: client_of() == "B", timeout=40, step=2, what="the router's own choice takes over")
        # a new controller picks the better country and re-applies the override
        self.start_controller()
        wait_until(lambda: self.status().get("cur") == "US", timeout=30, step=1, what="restart")
        wait_until(lambda: "auto-via-s2" in api("bi", "auto-director"), timeout=15, step=1, what="override re-asserted")

    def test_06_a_router_restart_loses_the_override_and_the_controller_puts_it_back(self):
        set_delay("a", 0); set_delay("b", 80)
        self.start_controller()
        wait_until(lambda: self.status().get("cur") == "DE", timeout=30, step=1, what="start")
        systemd("restart", "xray-router")
        router_ready(120)
        wait_until(lambda: "auto-via-s1" in api("bi", "auto-director"), timeout=40, step=2, what="override re-applied after the router restart")
        self.assertEqual({client_of() for _ in range(4)}, {"A"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
