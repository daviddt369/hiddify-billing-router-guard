"""The full router apply with ONE restart (hutils/multiloc_full.py). The legacy pipeline restarts the router after every layer (~6 times; production ~25-30 min nearly
without connections). Here the SAME pipeline runs unchanged in a private copy of /etc/xray-router with its restarts deferred, and the router is restarted once at the end.
What must hold: the finished config is byte-identical to the classic result; the live config and router are untouched until the swap; one restart; any failure leaves the
previous config serving.
Run: /opt/hiddify-manager/.venv313/bin/python -W ignore -m unittest -v test_15_full_apply   (from /work/tests; real pipeline, real router)"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stand_lib import *  # noqa: E402,F403
from test_13_apply import LOG, clean_state, start_cli, wait_done  # noqa: E402

LIVE = "/etc/xray-router/config.json"
SCRATCH = "/var/lib/multiloc-router-apply/scratch-etc"
FULL = CTL + ["apply", "--full"]
CLASSIC = CTL + ["apply", "--full", "--classic"]
FULLPY = "/opt/hiddify-manager/.venv313/lib/python3.13/site-packages/hiddifypanel/hutils/multiloc_full.py"


def sha(path=LIVE):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def run_ctl(args, **kw):
    return sh(args, check=False, timeout=1800, cwd="/opt/hiddify-manager/hiddify-panel", **kw)


def mark():
    """a time mark that no earlier router start can share a second with"""
    time.sleep(1.1)
    return now_iso()


def enter_stamp():
    return sh(["systemctl", "show", "xray-router", "-p", "ActiveEnterTimestampMonotonic", "--value"]).stdout.strip()


def setUpModule():
    prepare_apply_tests()


def tearDownModule():
    drop_probe_rules()
    run_ctl(FULL)
    reset_locations_to_stand_default()
    apply_router()
    ensure_baseline(force=True)


class TestShim(unittest.TestCase):
    """the systemctl shim, run for real with bash: only xray-router start / restart / stop is deferred, everything else is the real systemctl"""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        src = open(FULLPY, encoding="utf-8").read()
        script = src.split("SHIM_SCRIPT = r'''", 1)[1].split("'''", 1)[0]
        self.shim = os.path.join(self.d, "systemctl")
        open(self.shim, "w").write(script)
        os.chmod(self.shim, 0o755)
        self.log = os.path.join(self.d, "deferred.log")
        self.env = dict(os.environ, MULTILOC_SHIM_LOG=self.log)

    def call(self, *args):
        return subprocess.run([self.shim, *args], capture_output=True, text=True, env=self.env, timeout=60)

    def deferred(self):
        return open(self.log).read().splitlines() if os.path.exists(self.log) else []

    def test_every_way_to_restart_stop_or_start_the_router_is_deferred_and_does_nothing(self):
        before = enter_stamp()
        for args in (("restart", "xray-router"), ("restart", "xray-router.service"), ("--no-block", "restart", "xray-router"), ("try-restart", "xray-router"),
                     ("stop", "xray-router"), ("start", "xray-router"), ("reload-or-restart", "xray-router"), ("restart", "xray-router", "--no-block")):
            r = self.call(*args)
            self.assertEqual((r.returncode, r.stdout), (0, ""), args)
        self.assertEqual(len(self.deferred()), 8, self.deferred())
        self.assertEqual(enter_stamp(), before, "the real router was not touched")
        self.assertEqual(sh(["systemctl", "is-active", "xray-router"]).stdout.strip(), "active")

    def test_is_active_for_the_router_always_says_active(self):
        r = self.call("is-active", "xray-router")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "active"))
        r = self.call("is-active", "--quiet", "xray-router")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "active"))
        self.assertEqual(self.deferred(), [])

    def test_everything_else_goes_to_the_real_systemctl(self):
        r = self.call("is-active", "hiddify-panel")
        self.assertEqual(r.stdout.strip(), "active")
        r = self.call("is-active", "no-such-unit-xyz")
        self.assertNotEqual(r.returncode, 0)
        r = self.call("restart", "no-such-unit-xyz")
        self.assertNotEqual(r.returncode, 0, "a restart of any OTHER unit is the real thing (and fails here: there is no such unit)")
        self.assertEqual(self.call("reset-failed", "xray-router").returncode, 0, "not a start / stop verb: the real command")
        self.assertIn("xray-router", self.call("cat", "xray-router").stdout)
        self.assertEqual(self.deferred(), [])


class TestOneRestart(unittest.TestCase):

    def setUp(self):
        prepare_apply_tests()
        clean_state()
        self.addCleanup(self.converge)

    @staticmethod
    def converge():
        """the probe rule goes, and the router goes back to what the database says (otherwise the next test would start with the probe inside the running config)"""
        drop_probe_rules()
        run_ctl(FULL + ["--accept-diff"])

    def test_the_finished_config_is_byte_identical_to_the_classic_result_with_one_router_start(self):
        add_probe_rule("one-restart-probe.example")
        t0 = mark()
        r = run_ctl(FULL)
        self.assertEqual(r.returncode, 0, r.stdout[-1500:] + r.stderr[-1500:])
        starts_one = router_starts_since(t0)
        deferred_cfg = open(LIVE, "rb").read()
        self.assertIn("ONE router restart", r.stdout)
        self.assertEqual(starts_one, 1, "exactly one real router start")
        self.assertIn("5 router restart(s) were deferred", r.stdout, "the five restarts of the legacy layers were skipped")
        self.assertIn(b"one-restart-probe.example", deferred_cfg, "the change really went into the router")
        t1 = mark()
        c = run_ctl(CLASSIC)
        self.assertEqual(c.returncode, 0, c.stdout[-800:])
        self.assertGreaterEqual(router_starts_since(t1), 3, "the classic pipeline restarts after every layer")
        from hiddifypanel.hutils import router_compact
        classic_cfg = json.loads(open(LIVE, encoding="utf-8").read())
        packed = (json.dumps(router_compact.compact_config(classic_cfg), indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        self.assertEqual(packed, deferred_cfg, "the classic result, compacted (neighbouring plain lists joined), is the IDENTICAL router config the one-restart cycle produced for the same database")
        self.assertEqual(entrances_ok(), 5)

    def test_nothing_new_means_no_restart_at_all(self):
        self.assertEqual(run_ctl(FULL).returncode, 0)                  # brings the router to what the pipeline would produce
        t0, h0, e0 = mark(), sha(), enter_stamp()
        r = run_ctl(FULL)
        self.assertEqual(r.returncode, 0, r.stdout[-800:])
        self.assertIn("no restart needed", r.stdout)
        self.assertEqual((sha(), enter_stamp(), router_starts_since(t0)), (h0, e0, 0))

    def test_the_live_config_never_shows_an_intermediate_state(self):
        """the pipeline writes its layers one after another - none of that may ever be visible to the running router's file"""
        add_probe_rule("one-restart-probe.example")
        before, seen, stop = sha(), set(), threading.Event()

        def watch():
            while not stop.is_set():
                try:
                    seen.add(sha())
                except OSError:
                    pass
                time.sleep(0.01)
        th = threading.Thread(target=watch)
        th.start()
        try:
            r = run_ctl(FULL)
        finally:
            stop.set()
            th.join()
        self.assertEqual(r.returncode, 0, r.stdout[-800:])
        after = sha()
        self.assertNotEqual(before, after)
        self.assertEqual(seen - {before, after}, set(), "only the old and the finished file were ever visible")
        self.assertFalse(os.path.exists(SCRATCH), "the scratch copy is removed")

    def test_a_failing_pipeline_leaves_the_router_and_its_config_untouched(self):
        add_probe_rule("one-restart-probe.example")
        h0, e0, t0 = sha(), enter_stamp(), mark()
        code = """
import json
from hiddifypanel.hutils import multiloc_full as f
lines = []
f.CHILD = f.CHILD.replace("exec nice", "echo layer 3 broke; exit 7; exec nice")      # the pipeline dies after the mount, as a broken layer would
rc = f.run(log=lines.append)
print(json.dumps({"rc": rc, "log": lines}))
"""
        out = json.loads(panel_python(code).splitlines()[-1])
        self.assertEqual(out["rc"], 7, out)
        self.assertTrue(any("pipeline FAILED" in ln and "never touched" in ln for ln in out["log"]), out)
        self.assertEqual((sha(), enter_stamp(), router_starts_since(t0)), (h0, e0, 0), "the live config and the live router were never touched")
        self.assertFalse(os.path.exists(SCRATCH))
        self.assertEqual(plan_now()["action"], "full", "the change stays pending")

    def test_a_stopped_database_fails_the_apply_before_it_touches_anything(self):
        add_probe_rule("one-restart-probe.example")
        h0, e0, t0 = sha(), enter_stamp(), mark()
        systemd("stop", "mariadb")
        try:
            r = run_ctl(FULL)
        finally:
            systemd("start", "mariadb")
            wait_until(lambda: sh("mysql -N hiddifypanel -e 'select 1'", check=False).returncode == 0, timeout=60, what="database")
            restart_panel()
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual((sha(), enter_stamp(), router_starts_since(t0)), (h0, e0, 0))
        self.assertFalse(os.path.exists(SCRATCH))
        self.assertEqual(plan_now()["action"], "full", "the change stays pending")

    def test_a_router_that_does_not_come_up_gets_the_previous_config_back(self):
        add_probe_rule("one-restart-probe.example")
        old = open(LIVE, "rb").read()
        code = """
import json, os
from hiddifypanel.hutils import multiloc_full as f, multiloc_router as mr
os.environ["MULTILOC_ROUTER_READY_SECONDS"] = "0"
real, calls = mr.entrances_listening, [0]
def blind_once(ports):
    calls[0] += 1
    if calls[0] == 1:
        os.environ.pop("MULTILOC_ROUTER_READY_SECONDS")      # the zero deadline was read already; the restore of the previous config waits the normal way
        return False
    return real(ports)
mr.entrances_listening = blind_once                    # the very first look after the restart finds nothing; with a zero deadline that is a router that never came up
lines = []
rc = f.run(log=lines.append, expected={"rules"})
print(json.dumps({"rc": rc, "log": lines}))
"""
        out = json.loads(panel_python(code).splitlines()[-1])
        log = "\n".join(out["log"])
        self.assertEqual(out["rc"], 2, log[-800:])
        self.assertIn("FAILED", log)
        self.assertIn("previous config was put back and is serving again", log)
        self.assertEqual(open(LIVE, "rb").read(), old, "the previous config is back on disk")
        wait_until(lambda: entrances_ok() == 5, timeout=60, what="router serving the previous config")
        self.assertEqual(plan_now()["action"], "full", "still pending: nothing was recorded as applied")

    def test_a_busy_port_or_a_broken_result_aborts_before_the_swap(self):
        add_probe_rule("one-restart-probe.example")
        code = ("import json\\nfrom hiddifypanel.hutils import multiloc_full as f, multiloc_router as mr\\nlines = []\\n"
                "mr.busy_new_ports = lambda old, new: [20999]\\nrc = f.run(log=lines.append, expected={'rules'})\\n"
                "print(json.dumps({'rc': rc, 'log': lines[-3:]}))")
        h0, e0 = sha(), enter_stamp()
        out = json.loads(panel_python(code.replace("\\n", "\n")).splitlines()[-1])
        self.assertEqual(out["rc"], 2)
        self.assertTrue(any("already in use by another process" in ln for ln in out["log"]), out)
        self.assertEqual((sha(), enter_stamp()), (h0, e0))
        code2 = code.replace("mr.busy_new_ports = lambda old, new: [20999]", "mr.check_invariants = lambda cfg: ['forced problem']")
        out = json.loads(panel_python(code2.replace("\\n", "\n")).splitlines()[-1])
        self.assertEqual(out["rc"], 2)
        self.assertTrue(any("forced problem" in ln for ln in out["log"]), out)
        self.assertEqual((sha(), enter_stamp()), (h0, e0))

    def test_without_the_namespace_tools_the_classic_pipeline_runs(self):
        code = ("import json, shutil\\nfrom hiddifypanel.hutils import multiloc_full as f\\nlines = []\\n"
                "f.shutil.which = lambda name: None\\nf.classic = lambda log=print: (log('classic stub ran'), 0)[1]\\n"
                "rc = f.run(log=lines.append)\\nprint(json.dumps({'rc': rc, 'log': lines}))")
        out = json.loads(panel_python(code.replace("\\n", "\n")).splitlines()[-1])
        self.assertEqual(out["rc"], 0)
        self.assertTrue(any("CLASSIC mode" in ln and "unshare" in ln for ln in out["log"]), out)
        self.assertIn("classic stub ran", out["log"])

    def test_a_host_that_refuses_the_namespace_gets_the_classic_pipeline_instead_of_a_failed_apply(self):
        """`unshare` exists but is refused (a container without CAP_SYS_ADMIN, a hardened host): found by a probe BEFORE the pipeline starts"""
        code = """
import json, os, stat, tempfile
from hiddifypanel.hutils import multiloc_full as f
d = tempfile.mkdtemp()
fake = os.path.join(d, "unshare")
open(fake, "w").write("#!/bin/sh\\necho 'unshare: unshare failed: Operation not permitted' >&2\\nexit 1\\n")
os.chmod(fake, 0o755)
os.environ["PATH"] = d + ":" + os.environ["PATH"]
lines = []
f.classic = lambda log=print: (log("classic stub ran"), 0)[1]
rc = f.run(log=lines.append)
print(json.dumps({"rc": rc, "log": lines, "leftovers": sorted(os.listdir(f.STATE_DIR))}))
"""
        h0, e0 = sha(), enter_stamp()
        out = json.loads(panel_python(code).splitlines()[-1])
        self.assertEqual(out["rc"], 0, out)
        self.assertTrue(any("CLASSIC mode" in ln and "refused" in ln for ln in out["log"]), out)
        self.assertIn("classic stub ran", out["log"])
        self.assertFalse([n for n in out["leftovers"] if n.startswith("probe-")], "the probe cleans up after itself")
        self.assertEqual((sha(), enter_stamp()), (h0, e0))

    def test_the_background_service_runs_it_inside_its_own_unit(self):
        """a mount namespace inside a transient systemd service must work as well (this is how the panel's Apply runs it)"""
        add_probe_rule("one-restart-probe.example")
        t0 = now_iso()
        r = start_cli("full")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        st = wait_done(timeout=600)
        self.assertEqual((st["state"], st["rc"]), ("ok", 0), st)
        log = open(LOG).read()
        self.assertIn("ONE router restart", log)
        self.assertEqual(router_starts_since(t0), 1)
        self.assertEqual(plan_now()["action"], "none")


def entrances_ok():
    return int(sh("ss -Htln | grep -c -E ':208(08|11|12|13|14) ' || true").stdout.strip() or 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
