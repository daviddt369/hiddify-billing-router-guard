"""Installer rehearsal (T6): preflight, idempotency, failure injection at every step with automatic rollback, byte-level proof that
rollback restores the pre-install state (files, rendered configs, DB schema, row counts), rollback after the router layer was applied.
DESTRUCTIVE for the stand's service state (restarts services); always ends in the installed + canonical state.

Run: /opt/hiddify-manager/.venv313/bin/python -W ignore -m unittest -v test_06_installer   (from /work/tests; ~6-9 min)"""
import contextlib
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "/work/tools")
from stand_lib import *  # noqa: E402,F403

INSTALLER = ["python3", os.environ.get("MULTILOC_INSTALLER_UNDER_TEST", "/work/src/multiloc_installer.py")]      # the override is for mutation checks only
SNAP = "/work/out/snap"
PIDS = ("hiddify-xray", "hiddify-haproxy", "hiddify-panel", "xray-router", "hiddify-nginx")


def installer(*args, env=None, timeout=900):
    e = {**os.environ, **(env or {})}
    r = subprocess.run(INSTALLER + list(args), capture_output=True, text=True, timeout=timeout, env=e)
    return r.returncode, r.stdout + r.stderr


def snapshot(name):
    sh(["python3", "/work/tools/tree_snapshot.py", "take", f"{SNAP}/{name}.json"])
    return f"{SNAP}/{name}.json"


def snap_diff(a, b):
    r = sh(["python3", "/work/tools/tree_snapshot.py", "diff", a, b], check=False)
    return r.returncode, r.stdout


def pids():
    return {u: sh(["systemctl", "show", "-p", "MainPID", "-p", "ActiveEnterTimestampMonotonic", u]).stdout.replace("\n", " ") for u in PIDS}


def installed():
    return sh("mysql -N hiddifypanel -e \"show tables like 'commercial_routing_location'\"").stdout.strip() != ""


BACKUPS = "/opt/hiddify-manager/multiloc-installer-backups"
ROUTER = "/etc/xray-router/config.json"


def run_state(name):
    return json.load(open(f"{BACKUPS}/{name}/STATE.json"))


def the_open_run():
    """the one run that is not rolled back (every test starts from the pristine state, so after an apply there is exactly one)"""
    names = [d for d in sorted(os.listdir(BACKUPS)) if os.path.exists(f"{BACKUPS}/{d}/STATE.json") and not run_state(d).get("rolled_back")]
    assert len(names) == 1, names
    return names[0]


def hold_router_port():
    """a foreign process on the router's port: xray-router cannot start while it lives"""
    return subprocess.Popen([VENV_PY, "-c", "import socket,time\ns=socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
                                             "s.bind(('127.0.0.1', 20808)); s.listen(); time.sleep(300)  # mltest-hold-router"])


def release_router_port(holder):
    holder.kill()
    holder.wait()
    sh("pkill -f mltest-hold-router", check=False)


H_XRAY = "/opt/hiddify-manager/xray/bin/xray"


@contextlib.contextmanager
def failing_mysqldump():
    """an environment in which `mysqldump` exists and fails (exit 1, nothing on stdout) - for the 'a backup that did not work must stop the job' tests"""
    d = tempfile.mkdtemp(prefix="mltest-bin-")
    try:
        open(f"{d}/mysqldump", "w").write("#!/bin/sh\necho 'stub: mysqldump failed' >&2\nexit 1\n")
        os.chmod(f"{d}/mysqldump", 0o755)
        yield {"PATH": d + os.pathsep + os.environ.get("PATH", "")}
    finally:
        shutil.rmtree(d, ignore_errors=True)


def to_pristine():
    status = installer("status")[1]
    if installed() or status.count("applied") > 0 or "OPEN" in status:      # OPEN = a run whose apply / rollback did not finish
        installer("rollback")
    time.sleep(2)


def to_canonical():
    rc, out = installer("apply")
    assert rc == 0, out
    reset_locations_to_stand_default()
    apply_router()


def setUpModule():
    os.makedirs(SNAP, exist_ok=True)


def tearDownModule():
    to_pristine()
    to_canonical()


class TestInstaller(unittest.TestCase):
    def setUp(self):
        to_pristine()

    def test_00_payload_integrity(self):
        m = json.load(open("/work/src/MANIFEST.json"))["files"]
        self.assertGreaterEqual(len(m), 18)
        for e in m:
            p = "/work/src/payload/" + e["path"]
            self.assertEqual(hashlib.sha256(open(p, "rb").read()).hexdigest(), e["new"], e["path"])
            if e["base"] != "NEW":
                self.assertEqual(hashlib.sha256(open("/" + e["path"], "rb").read()).hexdigest(), e["base"],
                                 f"{e['path']}: stand file is no longer the pristine prod copy")
        r = sh(["python3", "/work/src/build_payload.py"]).stdout
        out = sh(["python3", "/work/src/build_manifest.py"]).stdout.strip()
        self.assertEqual(out, f"MANIFEST.json: {len(m)} files ({sum(1 for e in m if e['base']=='NEW')} new, {sum(1 for e in m if e['base']!='NEW')} modified)")
        m2 = json.load(open("/work/src/MANIFEST.json"))["files"]
        self.assertEqual(m, m2, "payload build is reproducible")

    def test_preflight_refuses_foreign_changes_and_apply_touches_nothing(self):
        target = "/opt/hiddify-manager/haproxy/maps/path_v10.j2"
        orig = open(target, "rb").read()
        a = snapshot("i1-before")
        try:
            open(target, "ab").write(b"\n# somebody edited this\n")
            snap_tampered = snapshot("i1-tampered")
            rc, out = installer("preflight")
            self.assertNotEqual(rc, 0)
            self.assertIn("neither the expected base nor the new version", out)
            rc, out = installer("apply")
            self.assertNotEqual(rc, 0, out)
            self.assertFalse(installed(), "nothing may be installed after a refused apply")
            self.assertEqual(snap_diff(snap_tampered, snapshot("i1-after-refused"))[0], 0, "refused apply must not change anything")
        finally:
            open(target, "wb").write(orig)
        self.assertEqual(snap_diff(a, snapshot("i1-restored"))[0], 0)

    def test_apply_is_idempotent_second_run_is_a_noop_with_no_restarts(self):
        rc, out = installer("apply")
        self.assertEqual(rc, 0, out)
        before = pids()
        rc, out = installer("apply")
        self.assertEqual(rc, 0, out)
        self.assertIn("already applied", out)
        self.assertEqual(pids(), before, "a no-op apply must not restart anything")
        runs = [d for d in os.listdir("/opt/hiddify-manager/multiloc-installer-backups")]
        rc, out = installer("apply")
        self.assertEqual(len(os.listdir("/opt/hiddify-manager/multiloc-installer-backups")), len(runs), "no backup dir for a no-op")

    def test_rollback_restores_the_exact_pre_install_state_byte_for_byte(self):
        a = snapshot("i2-pristine")
        golden.capture("/work/out/golden/i2-pristine", only_users=["u-tester", "u-active"])
        rc, out = installer("apply")
        self.assertEqual(rc, 0, out)
        reset_locations_to_stand_default()
        apply_router()
        self.assertEqual(via(pick(links("tester"), "Германия", "XHTTP")[1]), "exit-a", "feature works while installed")
        rc, out = installer("rollback")
        self.assertEqual(rc, 0, out)
        self.assertIn("post-rollback problems: none", out)
        time.sleep(3)
        b = snapshot("i2-rolled-back")
        rc, diff = snap_diff(a, b)
        self.assertEqual(rc, 0, "state after rollback differs from the pristine state:\n" + diff)
        golden.capture("/work/out/golden/i2-after", only_users=["u-tester", "u-active"])
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            bad = golden.compare("/work/out/golden/i2-pristine", "/work/out/golden/i2-after", users=["u-tester", "u-active"])
        self.assertEqual(bad, 0, buf.getvalue())
        self.assertFalse(installed())
        for u in ("hiddify-xray", "hiddify-haproxy", "hiddify-panel", "xray-router", "hiddify-nginx", "mariadb"):
            self.assertTrue(is_active(u), u)
        self.assertIn(via(dict(links("active"))["RU_GLOBAL tls WS direct vless"]), ("exit-a", "exit-b"), "legacy profiles work after rollback")

    def test_every_failure_point_rolls_back_automatically_and_disrupts_only_what_it_touched(self):
        a = snapshot("i3-pristine")
        for step_name in ("db-migrate", "restart-panel", "render", "validate", "reload-haproxy", "restart-xray", "verify"):
            with self.subTest(step=step_name):
                before = pids()
                rc, out = installer("apply", env={"MULTILOC_TEST_FAIL_AT": step_name})
                self.assertEqual(rc, 2, f"{step_name}: apply must report a rolled-back failure\n{out[-1500:]}")
                self.assertIn(f"injected failure at step {step_name}", out)
                self.assertIn("ROLLBACK", out)
                self.assertFalse(installed(), f"{step_name}: feature tables must be gone")
                time.sleep(2)
                rc2, diff = snap_diff(a, snapshot(f"i3-after-{step_name}"))
                self.assertEqual(rc2, 0, f"{step_name}: not back to the pristine state:\n{diff}")
                after = pids()
                if step_name in ("db-migrate",):
                    self.assertEqual(before, after, f"{step_name}: nothing was restarted, nothing may be restarted by the rollback")
                if step_name in ("render", "validate"):
                    for u in ("hiddify-xray", "hiddify-haproxy"):
                        self.assertEqual(before[u], after[u], f"{step_name}: {u} was never touched and must not be restarted")
                for u in ("hiddify-xray", "hiddify-haproxy", "hiddify-panel", "xray-router", "mariadb"):
                    self.assertTrue(is_active(u), f"{step_name}: {u}")
                self.assertIn(via(dict(links("active"))["RU_GLOBAL tls_h2 xhttp direct vless dl=h2"]), ("exit-a", "exit-b"), step_name)

    def test_broken_generated_config_is_caught_by_validation_and_rolled_back(self):
        a = snapshot("i4-pristine")
        for where, name, body in (("xray", "/opt/hiddify-manager/xray/configs/99_broken.json", '{"inbounds": [{"tag": "x", "protocol": "nonexistent"}]}'),
                                  ("haproxy", "/opt/hiddify-manager/haproxy/99-broken.cfg", "this is not a valid haproxy directive\n")):
            with self.subTest(where=where):
                open(name, "w").write(body)
                try:
                    rc, out = installer("apply")
                    self.assertEqual(rc, 2, out[-1500:])
                    self.assertIn("rolling back", out)
                    self.assertFalse(installed())
                finally:
                    os.unlink(name)
                self.assertEqual(snap_diff(a, snapshot(f"i4-after-{where}"))[0], 0)

    def test_rollback_after_the_router_layer_was_applied_restores_the_router_config(self):
        legacy_router = open("/etc/xray-router/config.json").read()
        rc, out = installer("apply")
        self.assertEqual(rc, 0, out)
        reset_locations_to_stand_default()
        apply_router()
        self.assertIn("from-hiddify-s1", open("/etc/xray-router/config.json").read())
        rc, out = installer("rollback")
        self.assertEqual(rc, 0, out)
        self.assertIn("restoring it", out)
        router_ready()
        self.assertEqual(json.loads(open("/etc/xray-router/config.json").read()), json.loads(legacy_router))
        self.assertTrue(is_active("xray-router"))
        self.assertIn(via(dict(links("active"))["RU_GLOBAL tls WS direct vless"]), ("exit-a", "exit-b"))

    def test_rollback_when_nothing_is_installed_is_harmless(self):
        a = snapshot("i5-pristine")
        rc, out = installer("rollback")
        self.assertEqual(rc, 0, out)
        self.assertIn("nothing to roll back", out)
        self.assertEqual(snap_diff(a, snapshot("i5-after"))[0], 0)

    def test_a_missing_stock_file_is_refused_by_preflight_and_nothing_changes(self):
        target = "/opt/hiddify-manager/haproxy/maps/path_v10.j2"        # a stock file this install modifies
        keep, st = open(target, "rb").read(), os.stat(target)
        a = snapshot("i6-before")
        os.unlink(target)
        try:
            rc, out = installer("preflight")
            self.assertNotEqual(rc, 0)
            self.assertIn("is MISSING", out)
            rc, out = installer("apply")
            self.assertNotEqual(rc, 0, out)
            self.assertFalse(installed(), "nothing may be installed after a refused apply")
        finally:
            open(target, "wb").write(keep)
            os.chmod(target, st.st_mode & 0o7777)
            os.chown(target, st.st_uid, st.st_gid)
        self.assertEqual(snap_diff(a, snapshot("i6-after"))[0], 0)

    def test_partially_existing_feature_tables_are_refused_by_preflight(self):
        mysql("CREATE TABLE commercial_routing_location (id INT PRIMARY KEY)")
        try:
            rc, out = installer("preflight")
            self.assertNotEqual(rc, 0)
            self.assertIn("only partially", out)
        finally:
            mysql("DROP TABLE IF EXISTS commercial_routing_location")

    def test_a_failure_in_the_middle_of_the_table_creation_rolls_back_the_tables_too(self):
        a = snapshot("i7-pristine")
        rc, out = installer("apply", env={"MULTILOC_TEST_FAIL_AFTER_TABLES": "1"})
        self.assertEqual(rc, 2, out[-1500:])
        self.assertIn("injected failure after 1 created table", out)
        self.assertIn("dropped feature tables", out, "the partially created tables must be dropped by the rollback")
        self.assertFalse(installed())
        self.assertEqual(mysql("show tables like 'commercial_routing_location%'"), "", "no feature table may survive")
        self.assertEqual(snap_diff(a, snapshot("i7-after"))[0], 0)

    def test_a_rollback_that_cannot_bring_the_router_back_says_so(self):
        rc, out = installer("apply")
        self.assertEqual(rc, 0, out)
        reset_locations_to_stand_default()
        apply_router()                                    # the router layer is applied: the pre-install router config differs from the live one
        sh(["systemctl", "stop", "xray-router"])
        holder = subprocess.Popen([VENV_PY, "-c", "import socket,time\ns=socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
                                                 "s.bind(('127.0.0.1', 20808)); s.listen(); time.sleep(300)  # mltest-hold-router"])
        try:
            time.sleep(1.0)
            rc, out = installer("rollback", env={"MULTILOC_ROUTER_READY_SECONDS": "12"})
            self.assertEqual(rc, 3, out[-1500:])
            self.assertIn("ROLLBACK INCOMPLETE", out)
            self.assertIn("NOT serving", out)
        finally:
            holder.kill()
            holder.wait()
            sh("pkill -f mltest-hold-router", check=False)
            sh(["systemctl", "restart", "xray-router"])
            router_ready(120)

    # ---- round-2 audit: a rollback is "done" only when the services are back and verify() is clean; what it cannot account for is never thrown away
    def test_a_rollback_that_could_not_finish_stays_open_and_a_plain_rollback_completes_it(self):
        legacy_router = json.loads(open(ROUTER).read())
        rc, out = installer("apply")
        self.assertEqual(rc, 0, out)
        reset_locations_to_stand_default()
        apply_router()                                    # the router layer is applied: the rollback has to restore the router too
        run = the_open_run()
        sh(["systemctl", "stop", "xray-router"])
        holder = hold_router_port()
        try:
            time.sleep(1.0)
            rc, out = installer("rollback", env={"MULTILOC_ROUTER_READY_SECONDS": "12"})
            self.assertEqual(rc, 3, out[-1500:])
            self.assertIn("ROLLBACK INCOMPLETE", out)
            st = run_state(run)
            self.assertFalse(st.get("rolled_back"), "a rollback that did not finish must not close its run")
            self.assertTrue(st.get("rollback_started"))
            self.assertEqual(st.get("router_restore"), "pending")
            self.assertFalse(installed(), "the data part of the rollback went through")
            n_runs = len(os.listdir(BACKUPS))
            rc, out = installer("apply")
            self.assertNotEqual(rc, 0, "apply must refuse while a rollback is open")
            self.assertIn("neither finished nor rolled back", out)
            self.assertEqual(len(os.listdir(BACKUPS)), n_runs, "a refused apply creates no run")
            rc, out = installer("status")
            self.assertIn("OPEN", out)
        finally:
            release_router_port(holder)
        before = pids()
        rc, out = installer("rollback")                   # plain: no run name, no options - it has to finish the job
        self.assertEqual(rc, 0, out[-2000:])
        self.assertIn("resuming an interrupted router restore", out)
        self.assertIn("post-rollback problems: none", out)
        st = run_state(run)
        self.assertTrue(st.get("rolled_back"))
        self.assertEqual(st.get("router_restore"), "done")
        after = pids()
        for u in ("hiddify-panel", "hiddify-xray", "hiddify-haproxy"):
            self.assertEqual(before[u], after[u], f"the repeated rollback must not restart {u} again: it had already come back")
        router_ready(120)
        self.assertEqual(json.loads(open(ROUTER).read()), legacy_router)
        self.assertTrue(is_active("xray-router"))
        rc, out = installer("rollback")
        self.assertEqual(rc, 0, out)
        self.assertIn("nothing to roll back", out)
        rc, out = installer("preflight")
        self.assertEqual(rc, 0, "after the completed rollback a new install is possible again:\n" + out)

    def test_an_automatic_rollback_that_fails_leaves_the_run_open_until_a_plain_rollback_completes_it(self):
        a = snapshot("i8-pristine")
        rc, out = installer("apply", env={"MULTILOC_TEST_FAIL_AT": "verify", "MULTILOC_TEST_FAIL_ROLLBACK_AT": "services"})
        self.assertEqual(rc, 3, out[-1500:])
        self.assertIn("injected failure at step verify", out)
        self.assertIn("injected failure in the rollback at services", out)
        self.assertIn("ROLLBACK ALSO FAILED", out)
        run = the_open_run()
        st = run_state(run)
        self.assertFalse(st.get("rolled_back"), "the failed automatic rollback must not close the run")
        self.assertTrue(st.get("rollback_started"))
        self.assertFalse(installed(), "the data part went through before the services part failed")
        rc, out = installer("preflight")
        self.assertNotEqual(rc, 0)
        self.assertIn("neither finished nor rolled back", out)
        rc, out = installer("rollback")
        self.assertEqual(rc, 0, out[-1500:])
        self.assertIn("post-rollback problems: none", out)
        self.assertTrue(run_state(run).get("rolled_back"))
        time.sleep(2)
        rc2, diff = snap_diff(a, snapshot("i8-after"))
        self.assertEqual(rc2, 0, "back to the pristine state:\n" + diff)
        for u in ("hiddify-xray", "hiddify-haproxy", "hiddify-panel", "xray-router", "mariadb"):
            self.assertTrue(is_active(u), u)
        self.assertIn(via(dict(links("active"))["RU_GLOBAL tls WS direct vless"]), ("exit-a", "exit-b"))

    def test_a_router_config_changed_after_the_install_is_left_as_is_and_force_restores_it_keeping_a_copy(self):
        legacy_router = json.loads(open(ROUTER).read())
        rc, out = installer("apply")
        self.assertEqual(rc, 0, out)
        reset_locations_to_stand_default()
        apply_router()
        run = the_open_run()
        live = json.loads(open(ROUTER).read())
        self.assertIn("from-hiddify-s1", json.dumps(live))
        live["routing"]["rules"].insert(0, {"type": "field", "domain": ["domain:foreign-change.test"], "outboundTag": "block"})
        open(ROUTER, "w").write(json.dumps(live, indent=2))               # somebody else's change AFTER the install
        rc, out = installer("rollback")
        self.assertEqual(rc, 0, out[-2000:])
        self.assertIn("LEFT AS IS", out)
        self.assertEqual(json.loads(open(ROUTER).read()), live, "a router config the installer cannot account for must not be overwritten")
        copies = glob.glob(f"{BACKUPS}/{run}/router-config.at-rollback-*.json")
        self.assertEqual(len(copies), 1, copies)
        self.assertEqual(json.loads(open(copies[0]).read()), live)
        st = run_state(run)
        self.assertTrue(st.get("rolled_back"))
        self.assertTrue(st.get("router_left_as_is"))
        self.assertTrue(is_active("xray-router"))
        # the operator decides to put the pre-install config back anyway
        rc, out = installer("rollback", run, "--force-router")
        self.assertEqual(rc, 0, out[-2000:])
        self.assertIn("(--force-router)", out)
        router_ready(120)
        self.assertEqual(json.loads(open(ROUTER).read()), legacy_router)
        self.assertFalse(run_state(run).get("router_left_as_is"))
        kept = [json.loads(open(c).read()) for c in glob.glob(f"{BACKUPS}/{run}/router-config.at-rollback-*.json")]
        self.assertIn(live, kept, "the replaced file is kept")

    def test_a_file_changed_after_the_install_is_copied_aside_before_the_rollback_puts_the_original_back(self):
        a = snapshot("i9-pristine")
        rc, out = installer("apply")
        self.assertEqual(rc, 0, out)
        run = the_open_run()
        replaced = "/opt/hiddify-manager/haproxy/maps/path_v10.j2"                    # a stock file the install modified
        added = "/opt/hiddify-manager/xray/configs/05_inbounds_30_locations.json.j2"  # a file the install added
        for p in (replaced, added):
            open(p, "ab").write(b"\n# a later change by somebody else\n")
        rc, out = installer("rollback")
        self.assertEqual(rc, 0, out[-2000:])
        self.assertIn("is not the version this install wrote", out)
        for p in (replaced, added):
            kept = f"{BACKUPS}/{run}/diverged-at-rollback{p}"
            self.assertTrue(os.path.exists(kept), kept)
            self.assertIn(b"a later change by somebody else", open(kept, "rb").read())
        self.assertFalse(os.path.exists(added))
        time.sleep(2)
        rc2, diff = snap_diff(a, snapshot("i9-after"))
        self.assertEqual(rc2, 0, "the original state is back:\n" + diff)

    # ---- round-3 audit: the three remaining ways to a wrong rollback result (+ what the self-review of the same code found)
    def test_a_missing_router_config_is_flagged_by_verify_and_restored_by_the_rollback(self):
        legacy_router = json.loads(open(ROUTER).read())
        rc, out = installer("apply")
        self.assertEqual(rc, 0, out)
        reset_locations_to_stand_default()
        apply_router()
        run = the_open_run()
        os.unlink(ROUTER)                                                  # the file disappears after the install
        rc, out = installer("verify")
        self.assertNotEqual(rc, 0, "verify must not skip the router just because its config is gone:\n" + out)
        self.assertIn("is missing", out)
        rc, out = installer("rollback")
        self.assertEqual(rc, 0, out[-2000:])
        self.assertIn("MISSING", out)
        self.assertTrue(os.path.exists(ROUTER), "a rollback must not end 'successfully' without the router config")
        self.assertEqual(json.loads(open(ROUTER).read()), legacy_router)
        router_ready(120)
        self.assertTrue(is_active("xray-router"))
        self.assertEqual(run_state(run).get("router_restore"), "done")
        rc, out = installer("verify")
        self.assertEqual(rc, 0, out)

    def test_a_changed_observatory_is_a_foreign_change_the_rollback_leaves_alone(self):
        rc, out = installer("apply")
        self.assertEqual(rc, 0, out)
        reset_locations_to_stand_default()
        apply_router()
        run = the_open_run()
        live = json.loads(open(ROUTER).read())
        self.assertIn("observatory", json.load(open(f"{BACKUPS}/{run}/router-config.json")),
                      "precondition: the pre-install router config already has an observatory (otherwise the test proves nothing)")
        self.assertIn("observatory", live)
        live["observatory"]["probeInterval"] = "37s"                      # somebody else changes a probe setting after the install
        open(ROUTER, "w").write(json.dumps(live, indent=2))
        try:
            rc, out = installer("rollback")
            self.assertEqual(rc, 0, out[-2000:])
            self.assertIn("LEFT AS IS", out)
            self.assertIn("observatory.probeInterval", out)
            self.assertEqual(json.loads(open(ROUTER).read()), live, "an observatory edit after the install is not the layer's: the file must stay")
            self.assertEqual(len(glob.glob(f"{BACKUPS}/{run}/router-config.at-rollback-*.json")), 1)
            self.assertTrue(run_state(run).get("router_left_as_is"))
        finally:
            # leave the stand as found: put the pre-install router config back, otherwise the next install would take THIS file (layer + the edit) as its "pre-install" copy
            installer("rollback", run, "--force-router")
            router_ready(120)

    def test_a_crash_right_after_a_file_was_replaced_is_still_rolled_back(self):
        a = snapshot("i10-pristine")
        m = json.load(open("/work/src/MANIFEST.json"))["files"]
        first_stock = 1 + next(i for i, e in enumerate(m) if e["base"] != "NEW")
        first_new = 1 + next(i for i, e in enumerate(m) if e["base"] == "NEW")
        for label, n in (("a replaced stock file", first_stock), ("a newly added file", first_new)):
            with self.subTest(file=label):
                rc, out = installer("apply", env={"MULTILOC_TEST_CRASH_AT": f"file-replaced:{n}"})
                self.assertEqual(rc, 137, out[-1500:])                     # killed like kill -9: no cleanup, no state save after the copy
                entry = m[n - 1]
                self.assertEqual(hashlib.sha256(open("/" + entry["path"], "rb").read()).hexdigest(), entry["new"],
                                 "the crash happened AFTER the file was replaced")
                rc, out = installer("preflight")
                self.assertNotEqual(rc, 0)
                self.assertIn("neither finished nor rolled back", out)
                rc, out = installer("rollback")
                self.assertEqual(rc, 0, out[-1500:])
                time.sleep(2)
                rc2, diff = snap_diff(a, snapshot(f"i10-after-{n}"))
                self.assertEqual(rc2, 0, f"{label}: the replaced file was missed by the rollback:\n{diff}")

    def test_a_crash_right_after_the_hiddify_xray_restart_is_still_rolled_back_completely(self):
        a = snapshot("i11-pristine")

        def loc_tags():
            """the country inbounds the RUNNING hiddify-xray serves (None while its API does not answer)"""
            r = sh([f"{H_XRAY}", "api", "lsi", "--server=127.0.0.1:10085"], check=False)
            try:
                return sorted(i["tag"] for i in json.loads(r.stdout).get("inbounds", []) if "-loc" in i.get("tag", ""))
            except Exception:
                return None

        rc, out = installer("apply", env={"MULTILOC_TEST_CRASH_AT": "xray-restarted"})
        self.assertEqual(rc, 137, out[-1500:])
        wait_until(lambda: loc_tags() is not None, 40, what="hiddify-xray API after the restart")
        self.assertTrue(loc_tags(), "the running hiddify-xray already serves the country inbounds (the crash came after its restart)")
        rc, out = installer("rollback")
        self.assertEqual(rc, 0, out[-1500:])
        wait_until(lambda: loc_tags() is not None, 40, what="hiddify-xray API after the rollback")
        self.assertEqual(loc_tags(), [], "the rollback has to restart hiddify-xray even though the crash hid the restart from the state file")
        rc2, diff = snap_diff(a, snapshot("i11-after"))
        self.assertEqual(rc2, 0, diff)

    def test_a_failing_database_dump_stops_the_install_before_anything_changes(self):
        a = snapshot("i12-pristine")
        with failing_mysqldump() as env:
            rc, out = installer("apply", env=env)
        self.assertNotEqual(rc, 0, "an install without a valid database dump must not go on:\n" + out[-1500:])
        self.assertFalse(installed())
        self.assertEqual(snap_diff(a, snapshot("i12-after"))[0], 0, "nothing may be left behind")

    def test_a_failing_dump_of_the_feature_tables_stops_the_rollback_before_the_drop(self):
        rc, out = installer("apply")
        self.assertEqual(rc, 0, out)
        run = the_open_run()
        with failing_mysqldump() as env:
            rc, out = installer("rollback", env=env)
        self.assertEqual(rc, 3, out[-1500:])
        self.assertIn("NOT dropped", out)
        self.assertTrue(installed(), "the tables must still be there: nothing is thrown away without a copy")
        self.assertFalse(run_state(run).get("rolled_back"))
        rc, out = installer("rollback")                                   # the cause is gone: a plain rollback finishes the job
        self.assertEqual(rc, 0, out[-1500:])
        self.assertFalse(installed())
        self.assertIn("Dump completed", open(f"{BACKUPS}/{run}/feature-tables-at-rollback.sql", errors="replace").read()[-400:])

    def test_two_installer_runs_cannot_overlap(self):
        import fcntl
        lock = open("/run/multiloc-installer.lock", "w")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)                  # somebody else's apply / rollback is running
        try:
            for cmd in ("apply", "rollback"):
                rc, out = installer(cmd)
                self.assertNotEqual(rc, 0, cmd)
                self.assertIn("another multiloc_installer", out, cmd)
            self.assertFalse(installed(), "a refused run changes nothing")
            for cmd in ("status", "verify"):                              # read-only commands are not blocked
                rc, out = installer(cmd)
                self.assertNotIn("another multiloc_installer", out, cmd)
        finally:
            lock.close()

    def test_an_unreadable_run_state_is_reported_not_crashed_on(self):
        d = f"{BACKUPS}/20200101-000000"
        os.makedirs(d, exist_ok=True)
        open(f"{d}/STATE.json", "w").write('{"run": "20200101-000000", "steps": [')           # truncated JSON
        try:
            rc, out = installer("status")
            self.assertNotEqual(rc, 0)
            self.assertIn("OPEN", out)
            self.assertIn("unreadable", out)
            self.assertNotIn("Traceback", out)
            rc, out = installer("preflight")
            self.assertNotEqual(rc, 0)
            self.assertIn("unreadable", out)
            self.assertNotIn("Traceback", out)
            rc, out = installer("rollback")
            self.assertEqual(rc, 3, out)
            self.assertIn("unreadable", out)
            self.assertNotIn("Traceback", out)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(installer("preflight")[0], 0, "clean again once the broken record is gone")




import golden  # noqa: E402

if __name__ == "__main__":
    unittest.main(verbosity=2)
