"""Router apply paths against the REAL xray-router process: the fast path (`multiloc_ctl.py apply`, only the multi-location layer, one restart)
versus the legacy full pipeline, and the safety nets of the layer: busy ports, `xray -test` of the slim copy, waiting until the entrances really
listen and restoring the previous config when they do not.

Run: /opt/hiddify-manager/.venv313/bin/python -W ignore -m unittest -v test_09_router_apply   (from /work/tests)"""
import json
import os
import subprocess
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("HIDDIFY_CFG_PATH", "/opt/hiddify-manager/hiddify-panel/app.cfg")
os.environ.setdefault("HIDDIFY_CONFIG_PATH", "/opt/hiddify-manager/")
from stand_lib import *  # noqa: E402,F403
from hiddifypanel.hutils import multiloc_router as mr  # noqa: E402

LIVE = "/etc/xray-router/config.json"
SLOT2_PORT = 20808 + 2 + 2


def live_text():
    return open(LIVE).read()


def live_json():
    return json.loads(live_text())


def invocation():
    return sh(["systemctl", "show", "-p", "InvocationID", "--value", "xray-router"]).stdout.strip()


def started_since(t0):
    out = sh(["journalctl", "-u", "xray-router", "--no-pager", "--since", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0 - 1)), "-o", "cat"]).stdout
    return out.count("Started xray-router")


def fast_apply(check=True, env=None):
    r = sh(CTL + ["apply"], check=check, cwd="/opt/hiddify-manager/hiddify-panel", env={**os.environ, **(env or {})})
    return r


def status_text():
    return ctl("status")


def canonical():
    reset_locations_to_stand_default()
    apply_router()


def setUpModule():
    canonical()


def tearDownModule():
    canonical()


class TestFastApply(unittest.TestCase):
    def tearDown(self):
        mysql("DELETE FROM commercial_routing_location_node WHERE upstream_id > 4")
        mysql("DELETE FROM commercial_routing_upstream WHERE name = 'exit-b-extra'")
        ctl("assign", 2, 3, 4)
        fast_apply()

    def test_status_says_in_sync_after_the_canonical_apply(self):
        self.assertIn("router: in sync with the database", status_text())

    def test_nothing_changed_means_no_restart(self):
        inv = invocation()
        r = fast_apply()
        self.assertEqual(json.loads(r.stdout.strip().splitlines()[-1]), {"changed": False, "reason": "no change"})
        self.assertEqual(invocation(), inv, "a no-op apply must not restart xray-router")

    def test_a_database_change_is_pending_then_applied_with_exactly_one_restart_and_traffic_follows(self):
        ctl("unassign", 4)
        self.assertIn("router: PENDING", status_text())
        t0 = time.time()
        r = fast_apply()
        info = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertTrue(info["changed"], info)
        self.assertIsNotNone(info["ready_seconds"])
        self.assertEqual(started_since(t0), 1, "the fast path restarts xray-router exactly once")
        self.assertIn("router: in sync with the database", status_text())
        live = live_json()
        self.assertNotIn("balancer-s2", {b["tag"] for b in live["routing"].get("balancers", [])}, "one node left: the slot points at the outbound")
        self.assertEqual(via(pick(links("tester"), "США", "WS", exclude=("Trojan",))[1]), "exit-b")
        self.assertEqual(via(pick(links("tester"), "Германия", "XHTTP")[1]), "exit-a")
        ctl("assign", 2, 4)
        info = json.loads(fast_apply().stdout.strip().splitlines()[-1])
        self.assertTrue(info["changed"])
        self.assertIn("balancer-s2", {b["tag"] for b in live_json()["routing"]["balancers"]})

    def test_the_fast_result_is_identical_to_what_the_full_pipeline_produces(self):
        ctl("unassign", 4)
        fast_apply()
        fast = live_json()
        ctl("apply", "--full", "--accept-diff")
        router_ready()
        full = live_json()
        self.assertEqual(fast, full, "fast path and full pipeline must end in the same router config")

    def test_second_fast_apply_after_a_full_one_is_a_noop(self):
        ctl("apply", "--full", "--accept-diff")
        router_ready()
        inv = invocation()
        self.assertEqual(json.loads(fast_apply().stdout.strip().splitlines()[-1])["changed"], False)
        self.assertEqual(invocation(), inv)

    def test_a_node_missing_from_the_live_router_config_is_reported_not_silently_dropped(self):
        cols = [c for c in mysql("select column_name from information_schema.columns where table_schema='hiddifypanel' "
                                 "and table_name='commercial_routing_upstream' order by ordinal_position").split() if c not in ("id", "name")]
        cl = ",".join(f"`{c}`" for c in cols)
        mysql(f"INSERT INTO commercial_routing_upstream (name,{cl}) SELECT 'exit-b-extra',{cl} FROM commercial_routing_upstream WHERE id=4")
        new_id = int(mysql("select id from commercial_routing_upstream where name='exit-b-extra'"))
        ctl("assign", 2, new_id)
        inv = invocation()
        r = fast_apply()
        self.assertIn("WARNING", r.stderr)
        self.assertIn(f"[{new_id}]", r.stderr)
        self.assertIn("apply --full", r.stderr)
        self.assertEqual(invocation(), inv, "nothing to change in the live router config")
        st = status_text()
        self.assertIn(f"nodes [{new_id}] are not in the live router config", st)
        ctl("unassign", new_id)

    def test_the_slim_copy_keeps_everything_the_layer_adds_and_drops_only_bulk_rules(self):
        live = live_json()
        bulk = {"type": "field", "ip": ["198.51.100.0/24"], "outboundTag": "direct-ru"}
        cfg = json.loads(json.dumps(live))
        cfg["routing"]["rules"] = [bulk] * 500 + cfg["routing"]["rules"]
        s = mr.slim(cfg)
        self.assertEqual(len(s["routing"]["rules"]), len(live["routing"]["rules"]) - sum(1 for r in live["routing"]["rules"] if mr._is_bulk(r)))
        ours = [r for r in live["routing"]["rules"] if mr._is_ours(r)]
        self.assertTrue(ours)
        for r in ours:
            self.assertIn(r, s["routing"]["rules"])
        self.assertEqual(s["routing"].get("balancers"), live["routing"].get("balancers"))
        self.assertEqual(s["inbounds"], live["inbounds"])


class TestSafetyNets(unittest.TestCase):
    def tearDown(self):
        sh("pkill -f mltest-hold-port", check=False)
        canonical()

    def run_in_panel(self, code, env=None):
        """run code inside the panel's app context (real DB + the real module); extra environment only for that child process"""
        body = "import socket, json, os\nfrom hiddifypanel.hutils import multiloc_router as mr\n" + code
        old = dict(os.environ)
        os.environ.update(env or {})
        try:
            return panel_python(body)
        finally:
            os.environ.clear()
            os.environ.update(old)

    def test_a_port_that_another_process_holds_is_refused_before_anything_changes(self):
        before, inv = live_text(), invocation()
        out = self.run_in_panel(
            "live = json.load(open('/etc/xray-router/config.json'))\n"
            "json.dump(mr.strip(live), open('/tmp/mltest-router.json', 'w'))\n"
            "try:\n"
            "    mr.patch_config('/tmp/mltest-router.json')\n"
            "    print('RESULT no-error')\n"
            "except RuntimeError as e:\n"
            "    print('RESULT', e)\n")
        self.assertIn("already in use by another process; nothing was changed", out)
        self.assertEqual(live_text(), before)
        self.assertEqual(invocation(), inv, "refused before any restart")

    def test_a_config_xray_does_not_accept_is_caught_by_the_slim_test_and_nothing_is_replaced(self):
        before, inv = live_text(), invocation()
        out = self.run_in_panel(
            "orig = mr.transform\n"
            "def bad(cfg, plan):\n"
            "    c = orig(cfg, plan)\n"
            "    for b in c.get('routing', {}).get('balancers', []):\n"
            "        if b['tag'].startswith('balancer-s'):\n"
            "            b['strategy'] = {'type': 'notAStrategy'}\n"
            "    return c\n"
            "mr.transform = bad\n"
            "try:\n"
            "    mr.patch_config()\n"
            "    print('RESULT no-error')\n"
            "except RuntimeError as e:\n"
            "    print('RESULT', str(e)[:300])\n")
        self.assertIn("failed xray -test", out)
        self.assertEqual(live_text(), before, "the live config must not be replaced")
        self.assertEqual(invocation(), inv, "no restart")
        for leftover in (LIVE + ".multiloc.tmp.json", LIVE + ".multiloc.slim.json"):
            self.assertFalse(os.path.exists(leftover), f"{leftover} must be cleaned up")

    def test_when_the_router_does_not_come_up_the_previous_config_is_restored_and_serves_again(self):
        # state: legacy shape (slot ports free), router restarted on it
        stripped = mr.strip(live_json())
        open(LIVE, "w").write(json.dumps(stripped, indent=2) + "\n")
        sh(["systemctl", "restart", "xray-router"])
        router_ready()
        legacy_text = live_text()
        holder = subprocess.Popen([VENV_PY, "-c", "import socket,time\ns=socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
                                                 f"s.bind(('127.0.0.1', {SLOT2_PORT})); s.listen(); time.sleep(120)  # mltest-hold-port"])
        try:
            time.sleep(1.0)
            out = self.run_in_panel(
                "mr.busy_new_ports = lambda old, new: []          # simulate the race: the port was free when we checked\n"
                "try:\n"
                "    info = mr.patch_config()\n"
                "    print('RESULT no-error', info)\n"
                "except RuntimeError as e:\n"
                "    print('RESULT', str(e)[:400])\n",
                env={"MULTILOC_ROUTER_READY_SECONDS": "12"})
            self.assertIn("did not start serving", out, out)
            self.assertIn("previous config restored and serving again", out, out)
            self.assertEqual(live_text(), legacy_text, "the previous config must be back on disk")
            router_ready(30)
            self.assertIn(router_direct(FOREIGN_IP), ("exit-a", "exit-b"), "legacy router serves again")
        finally:
            holder.kill()
            holder.wait()

    def test_the_ui_button_applies_in_the_background_and_the_badge_follows(self):
        from test_07_ui import admin_session, LOC, ROUTING, wait_apply
        ensure_stand_sudoers()
        s = admin_session()
        self.assertIn("router: синхронизирован", s.get(LOC, timeout=20).text)
        ctl("unassign", 4)
        self.assertIn("router: ждёт применения", s.get(LOC, timeout=20).text)
        before, inv = live_text(), invocation()
        r = s.post(f"{ROUTING}apply/", headers={"Referer": LOC}, timeout=30)
        self.assertEqual(r.status_code, 200)
        self.assertIn("Применение запущено", r.text)
        st = wait_apply(s)
        self.assertEqual((st["state"], st["rc"]), ("ok", 0), st)
        self.assertNotEqual(live_text(), before, "the live router config was rebuilt")
        self.assertNotEqual(invocation(), inv, "by exactly one restart of the router")
        s = admin_session()
        self.assertIn("router: синхронизирован", s.get(LOC, timeout=20).text)
        fast_apply()


class TestRouterApplyFailures(unittest.TestCase):
    """EVERY failure after the router config was replaced restores the previous config (not only 'the entrances never listened')"""

    def setUp(self):
        ctl("unassign", 4)                       # a pending change: patch_config goes all the way to the restart
        self.before = live_text()

    def tearDown(self):
        canonical()

    def patch_in_panel(self, prelude, target=None):
        body = ("import subprocess, json\nfrom hiddifypanel.hutils import multiloc_router as mr\n" + prelude +
                "\ntry:\n    mr.patch_config(" + (repr(target) if target else "") + ")\n    print('RESULT no-error')\n"
                "except BaseException as e:\n    print('RESULT', type(e).__name__, e)\n")
        return panel_python(body)

    def assert_restored_and_serving(self, out):
        self.assertIn("previous config restored and serving again", out, out)
        self.assertEqual(live_text(), self.before, "the previous config must be back on disk byte for byte")
        router_ready(30)
        self.assertIn(router_direct(FOREIGN_IP), ("exit-a", "exit-b"), "the router serves again")

    def test_a_systemctl_restart_timeout_restores_the_previous_config(self):
        out = self.patch_in_panel(
            "real = subprocess.run\nn = {'c': 0}\n"
            "def fake(cmd, *a, **kw):\n"
            "    if list(cmd[:2]) == ['systemctl', 'restart'] and n['c'] == 0:\n"
            "        n['c'] += 1\n"
            "        raise subprocess.TimeoutExpired(cmd, 60)\n"
            "    return real(cmd, *a, **kw)\n"
            "subprocess.run = fake\n")
        self.assertIn("RESULT RuntimeError", out)
        self.assert_restored_and_serving(out)

    def test_a_failing_systemctl_restart_restores_the_previous_config(self):
        out = self.patch_in_panel(
            "real = subprocess.run\nn = {'c': 0}\n"
            "def fake(cmd, *a, **kw):\n"
            "    if list(cmd[:2]) == ['systemctl', 'restart'] and n['c'] == 0:\n"
            "        n['c'] += 1\n"
            "        return subprocess.CompletedProcess(cmd, 1, '', 'simulated: Job failed')\n"
            "    return real(cmd, *a, **kw)\n"
            "subprocess.run = fake\n")
        self.assertIn("simulated: Job failed", out)
        self.assert_restored_and_serving(out)

    def test_an_exception_while_waiting_for_the_router_restores_the_previous_config(self):
        out = self.patch_in_panel(
            "real_wait = mr.wait_ready\nn = {'c': 0}\n"
            "def bad_wait(ports, seconds=None):\n"
            "    n['c'] += 1\n"
            "    if n['c'] == 1:\n"
            "        raise OSError('simulated failure while waiting')\n"
            "    return real_wait(ports, seconds)\n"
            "mr.wait_ready = bad_wait\n")
        self.assertIn("simulated failure while waiting", out)
        self.assert_restored_and_serving(out)

    def test_an_interrupt_while_waiting_restores_the_previous_config_and_is_not_swallowed(self):
        out = self.patch_in_panel(
            "real_wait = mr.wait_ready\nn = {'c': 0}\n"
            "def bad_wait(ports, seconds=None):\n"
            "    n['c'] += 1\n"
            "    if n['c'] == 1:\n"
            "        raise KeyboardInterrupt()\n"
            "    return real_wait(ports, seconds)\n"
            "mr.wait_ready = bad_wait\n")
        self.assertIn("RESULT KeyboardInterrupt", out, "the interrupt itself must propagate")
        self.assertEqual(live_text(), self.before, "but the previous config is restored first")
        router_ready(30)

    def test_a_missing_router_config_is_an_error_not_a_successful_no_change(self):
        out = self.patch_in_panel("pass", target="/tmp/this-router-config-does-not-exist.json")
        self.assertIn("RESULT RuntimeError", out)
        self.assertIn("is missing", out)
        self.assertNotIn("no-error", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
