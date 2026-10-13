"""Background router apply: the panel's buttons start `scripts/multiloc_router_apply.py` as a systemd service through the panel's existing root entry point
(`sudo commander.py router-apply-start --mode countries|full`) and return at once; the Countries page shows the progress.
Why: the panel used to wait 60 s (rule sources) / 90 s (routing "Save") for the legacy pipeline and KILLED it: on a production-sized router the first restart was
done, the later layers and the country entrances were never applied (reproduced on the stand with 15,000 rules).
Run: /opt/hiddify-manager/.venv313/bin/python -W ignore -m unittest -v test_13_apply   (from /work/tests; real systemd, real panel, real router)"""
import contextlib
import fcntl
import importlib.util
import json
import os
import re
import sys
import tempfile
import time
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import requests  # noqa: E402
from stand_lib import *  # noqa: E402,F403

PKG = "/opt/hiddify-manager/.venv313/lib/python3.13/site-packages/hiddifypanel"
RA = "/opt/hiddify-manager/scripts/multiloc_router_apply.py"
HELPER = PKG + "/hutils/multiloc_apply.py"
COMMANDER = "/opt/hiddify-manager/common/commander.py"
UNIT = "multiloc-router-apply"
STATE = "/var/lib/multiloc-router-apply/status.json"
LOG = "/var/log/multiloc-router-apply.log"
LOCK = "/run/multiloc-router-apply.lock"
BASE = "https://ru.stand.test"
CA = "/etc/stand/ca.crt"
PP = "stPx0Main1234"
LOC = f"{BASE}/{PP}/admin/routing-admin/locations/"
ROUTING = f"{BASE}/{PP}/admin/routing-admin/"
SOURCES = f"{BASE}/{PP}/admin/routing-admin/rule-sources/"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def ra_status():
    return json.loads(sh([VENV_PY, RA, "status", "--json"]).stdout)


def wait_done(timeout=240):
    t0 = time.monotonic()
    st = {}
    while time.monotonic() - t0 < timeout:
        st = ra_status()
        if st.get("state") != "running" and not st.get("unit_live"):
            return st
        time.sleep(0.5)
    raise AssertionError(f"still running after {timeout} s: {st}")


def start_cli(mode, as_panel=False):
    cmd = [COMMANDER, "router-apply-start", "--mode", mode]
    if as_panel:
        cmd = ["sudo", "-u", "hiddify-panel", "sudo", "-n"] + cmd
    return sh(cmd, check=False)


@contextlib.contextmanager
def lock_held():
    """pretend some other apply (a console `multiloc_ctl.py apply`) holds the apply lock"""
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def clean_state():
    wait_done()
    for p in (STATE,):
        with contextlib.suppress(FileNotFoundError):
            os.remove(p)
    with contextlib.suppress(FileNotFoundError):
        open(LOG, "w").close()


def entrances_listening():
    return int(sh("ss -Htln | grep -c -E ':208(08|11|12|13|14) ' || true").stdout.strip() or 0)


def setUpModule():
    prepare_apply_tests()
    flush_cache()


def tearDownModule():
    wait_done()
    reset_locations_to_stand_default()
    apply_router()


class TestRunnerPureLogic(unittest.TestCase):
    """the runner's own logic on a scratch directory (no systemd, no router)"""

    def setUp(self):
        self.m = load(RA, "mra_unit")
        self.d = tempfile.mkdtemp()
        self.m.STATE_DIR, self.m.STATE_FILE = self.d, self.d + "/status.json"
        self.m.LOG_FILE, self.m.LOCK_FILE = self.d + "/apply.log", self.d + "/apply.lock"
        self.m.unit_state = lambda: "inactive"

    def test_status_roundtrip_is_atomic_and_world_readable(self):
        self.assertIsNone(self.m.read_status())
        self.m.write_status({"state": "ok", "mode": "full", "rc": 0})
        self.assertEqual(self.m.read_status(), {"state": "ok", "mode": "full", "rc": 0})
        self.assertEqual(os.stat(self.m.STATE_FILE).st_mode & 0o777, 0o644)
        self.assertFalse(os.path.exists(self.m.STATE_FILE + ".tmp"), "written through a temp file that was renamed")
        open(self.m.STATE_FILE, "w").write("{broken")
        self.assertIsNone(self.m.read_status(), "an unreadable status file is 'no status', not a crash")

    def test_a_running_status_without_a_live_service_or_lock_is_reported_aborted(self):
        self.m.write_status({"state": "running", "mode": "countries", "started": time.time() - 100})
        st = self.m.current()
        self.assertEqual(st["state"], "aborted")
        self.assertIn("process is gone", st["summary"])
        self.m.unit_state = lambda: "active"
        self.assertEqual(self.m.current()["state"], "running", "a live service: running")
        self.m.unit_state = lambda: "inactive"
        with contextlib.ExitStack() as es:
            fd = os.open(self.m.LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o644)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            es.callback(os.close, fd)
            self.assertEqual(self.m.current()["state"], "running", "the lock is held (a console apply): still running")
        self.assertEqual(self.m.current()["state"], "aborted")

    def test_lock_probe_does_not_keep_the_lock(self):
        self.assertFalse(self.m.lock_is_held())
        self.assertFalse(self.m.lock_is_held())
        fd = os.open(self.m.LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.assertTrue(self.m.lock_is_held())
        os.close(fd)
        self.assertFalse(self.m.lock_is_held())

    def test_tail_and_trim_of_the_log(self):
        with open(self.m.LOG_FILE, "w") as f:
            f.write("".join(f"line {i}\n" for i in range(1000)))
        self.assertEqual(self.m.tail_of(self.m.LOG_FILE, 3), ["line 997", "line 998", "line 999"])
        self.assertEqual(self.m.tail_of(self.m.LOG_FILE, 0), [])
        self.assertEqual(self.m.tail_of(self.d + "/missing.log", 5), [])
        with open(self.m.LOG_FILE, "w") as f:
            f.write("x" * 100 + "\n")
            f.write("".join(f"line {i}\n" for i in range(200000)))
        self.assertGreater(os.path.getsize(self.m.LOG_FILE), self.m.LOG_KEEP_BYTES)
        self.m.trim_log()
        self.assertLess(os.path.getsize(self.m.LOG_FILE), 10000)
        self.assertEqual(self.m.tail_of(self.m.LOG_FILE, 1), ["line 199999"], "the newest lines are kept")

    def test_ansi_colours_of_the_pipeline_logger_never_reach_the_log(self):
        self.assertEqual(self.m._ANSI.sub("", "\x1b[32m2026-10-12\x1b[0m | \x1b[1mINFO\x1b[0m"), "2026-10-12 | INFO")


class TestPanelHelperPureLogic(unittest.TestCase):
    """the panel-side helper with a faked sudo / systemctl"""

    def setUp(self):
        self.h = load(HELPER, "mapply_unit")
        self.d = tempfile.mkdtemp()
        self.h.STATE_FILE, self.h.LOG_FILE = self.d + "/status.json", self.d + "/apply.log"
        self.h._unit_live = lambda: False

    def fake(self, stdout="", stderr="", rc=0, boom=None):
        class R:
            pass
        r = R()
        r.stdout, r.stderr, r.returncode = stdout, stderr, rc

        def run(cmd, **kw):
            self.cmd = cmd
            if boom:
                raise boom
            return r
        self.h.subprocess = types.SimpleNamespace(run=run)          # only THIS module instance sees the fake (never patch the shared subprocess module)

    def test_start_runs_exactly_the_fixed_root_command_with_a_whitelisted_mode(self):
        self.fake('{"started": true, "unit": "multiloc-router-apply", "mode": "full"}')
        self.assertEqual(self.h.start("full")["started"], True)
        self.assertEqual(self.cmd, ["sudo", "-n", COMMANDER, "router-apply-start", "--mode", "full"])
        self.h.start("auto")
        self.assertEqual(self.cmd, ["sudo", "-n", COMMANDER, "router-apply-start", "--mode", "auto"])
        for bad in ("", "FULL", "full; id", "--help", "countries ", None):
            self.assertEqual(self.h.start(bad)["reason"], "bad-mode", repr(bad))

    def test_start_turns_every_odd_answer_into_a_readable_failure_and_never_raises(self):
        self.fake('{"started": false, "reason": "busy", "mode": "full"}', rc=3)
        self.assertEqual((self.h.start("full")["started"], self.h.start("full")["reason"]), (False, "busy"))
        self.fake("", "sudo: a password is required", 1)
        r = self.h.start("full")
        self.assertEqual((r["started"], r["reason"]), (False, "no-answer"))
        self.assertIn("password is required", r["detail"])
        self.fake("garbage {not json", "", 0)
        self.assertFalse(self.h.start("countries")["started"])
        self.fake(boom=TimeoutError("sudo hung"))
        r = self.h.start("countries")
        self.assertEqual((r["started"], r["reason"]), (False, "error"))
        self.fake('some log line\n{"started": true, "unit": "u", "mode": "countries"}\n')
        self.assertTrue(self.h.start("countries")["started"], "the last JSON line counts")

    def test_status_reads_files_only_and_reports_a_dead_run_as_aborted(self):
        self.assertEqual(self.h.status()["state"], "idle")
        self.assertEqual(self.h.status()["state_text"], "не запускалось")
        now = time.time()
        json.dump({"state": "running", "mode": "full", "started": now - 30, "started_text": "t", "finished": None, "rc": None}, open(self.h.STATE_FILE, "w"))
        open(self.h.LOG_FILE, "w").write("".join(f"l{i}\n" for i in range(50)))
        st = self.h.status(tail=5)
        self.assertEqual(st["state"], "aborted", "a recorded 'running' with no service behind it")
        self.assertEqual(st["log_tail"], ["l45", "l46", "l47", "l48", "l49"])
        self.h._unit_live = lambda: True
        st = self.h.status()
        self.assertEqual((st["state"], st["state_text"], st["mode_text"]), ("running", "идёт", "полный цикл (правила, ноды, страны)"))
        self.assertGreaterEqual(st["elapsed"], 30)
        json.dump({"state": "ok", "mode": "countries", "started": now - 100, "finished": now - 90, "rc": 0, "summary": "done"}, open(self.h.STATE_FILE, "w"))
        st = self.h.status()
        self.assertEqual((st["state"], st["elapsed"]), ("ok", 10), "a finished run shows how long it took, not how long ago it started")

    def test_flash_texts_say_what_happens_and_where_to_look(self):
        t, c = self.h.flash_for({"started": True}, {"action": "full", "changed": ["rules", "upstreams"]})
        self.assertEqual(c, "info")
        for w in ("в фоне", "15–20 минут", "один перезапуск", "правила маршрутизации", "внешние ноды", "Применение на router", "не нажимайте"):
            self.assertIn(w, t)
        t, c = self.h.flash_for({"started": True}, {"action": "countries", "changed": []})
        self.assertIn("4–8 минут", t)
        self.assertIn("один перезапуск", t)
        t, c = self.h.flash_for({"started": True}, {"action": "full", "changed": ["settings"]}, saved=True)
        self.assertTrue(t.startswith("Настройки сохранены."))
        t, c = self.h.flash_for({"started": False, "reason": "nothing"}, {"action": "none"})
        self.assertEqual(c, "success")
        self.assertIn("Всё уже применено", t)
        self.assertIn("перезапуск router не нужен", t)
        t, c = self.h.flash_for({"started": False, "reason": "busy"}, {"action": "countries"})
        self.assertEqual(c, "warning")
        self.assertIn("уже идёт", t)
        t, c = self.h.flash_for({"started": False, "reason": "no-answer", "detail": "sudo: a password is required"}, {"action": "countries"}, saved=True)
        self.assertEqual(c, "danger")
        self.assertTrue(t.startswith("Настройки сохранены."))
        self.assertIn("password is required", t)
        self.assertIn("multiloc_ctl.py apply --auto", t, "the console way is always offered")

    def test_apply_now_starts_nothing_when_nothing_is_pending_and_the_auto_run_otherwise(self):
        calls = []
        self.h.start = lambda mode="auto": calls.append(mode) or {"started": True}
        self.h.plan = lambda use_cache=False: {"action": "none"}
        pl, res = self.h.apply_now()
        self.assertEqual((pl["action"], res["started"], res["reason"], calls), ("none", False, "nothing", []), "no root call at all when there is nothing to do")
        for action in ("countries", "full"):
            self.h.plan = lambda use_cache=False, a=action: {"action": a}
            pl, res = self.h.apply_now()
            self.assertEqual((res["started"], calls[-1]), (True, "auto"), "the decision is made again by the root run itself; the panel only asks for auto")


class TestBackgroundApply(unittest.TestCase):
    """real systemd, real sudo, real router of the stand"""

    def setUp(self):
        reset_locations_to_stand_default()
        apply_router()
        clean_state()

    def test_the_root_command_takes_only_the_two_modes_and_starts_nothing_else(self):
        for bad in ("bogus", "", "countries;id", "FULL"):
            r = sh([COMMANDER, "router-apply-start", "--mode", bad], check=False)
            self.assertNotEqual(r.returncode, 0, repr(bad))
        r = sh([COMMANDER, "router-apply-start"], check=False)
        self.assertNotEqual(r.returncode, 0, "the mode is required")
        self.assertFalse(os.path.exists(STATE), "nothing was started")
        self.assertEqual(sh(["systemctl", "is-active", UNIT], check=False).stdout.strip(), "inactive")

    def test_countries_mode_runs_as_a_service_and_reports(self):
        t0 = time.monotonic()
        r = start_cli("countries")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLess(time.monotonic() - t0, 8, "returns at once, never waits for the apply")
        self.assertEqual(json.loads(r.stdout.strip().splitlines()[-1]), {"started": True, "unit": UNIT, "mode": "countries"})
        st = wait_done()
        self.assertEqual((st["state"], st["mode"], st["rc"]), ("ok", "countries", 0), st)
        self.assertTrue(st["summary"])
        log = open(LOG).read()
        self.assertIn("=== router apply started: mode=countries", log)
        self.assertIn("=== router apply finished: mode=countries rc=0", log)
        self.assertNotIn("\x1b[", log, "plain text")
        self.assertEqual(sh(["systemctl", "is-active", UNIT], check=False).stdout.strip(), "inactive", "the service is gone when the job is done")
        for p in (STATE, LOG):
            self.assertEqual(os.stat(p).st_mode & 0o777, 0o644, p)
        self.assertEqual(json.loads(ctl("status", "--json"))["router"]["in_sync"], True)

    def test_full_mode_runs_the_whole_legacy_pipeline_to_the_end_including_the_countries_layer(self):
        r = start_cli("full")
        self.assertEqual(r.returncode, 0, r.stderr)
        st = wait_done()
        self.assertEqual((st["state"], st["mode"], st["rc"]), ("ok", "full", 0), st)
        log = open(LOG).read()
        self.assertIn("Applied multi-location router layer", log, "the countries layer is the LAST step of the pipeline")
        self.assertIn("Applied /etc/xray-router/config.json and restarted xray-router", log)
        self.assertEqual(entrances_listening(), 5, "the default entrance and the four country entrances all listen")
        self.assertEqual(json.loads(ctl("status", "--json"))["router"]["in_sync"], True)

    def test_only_one_apply_runs_at_a_time_against_the_console_too(self):
        with lock_held():
            r = start_cli("countries")
            self.assertEqual(r.returncode, 3)
            self.assertEqual(json.loads(r.stdout.strip().splitlines()[-1])["reason"], "busy")
            self.assertEqual(start_cli("full").returncode, 3)
            c = sh(CTL + ["apply"], check=False, cwd="/opt/hiddify-manager/hiddify-panel")
            self.assertNotEqual(c.returncode, 0)
            self.assertIn("another router apply is running", c.stdout + c.stderr)
            self.assertFalse(os.path.exists(STATE), "a refused start leaves no status behind")
        self.assertEqual(start_cli("countries").returncode, 0, "free again")
        self.assertEqual(wait_done()["state"], "ok")
        c = sh(CTL + ["apply"], check=False, cwd="/opt/hiddify-manager/hiddify-panel")
        self.assertEqual(c.returncode, 0, "the console apply works when nothing else runs (and releases the lock afterwards)")
        self.assertFalse(self.is_locked())

    def test_a_double_click_starts_one_run_and_the_other_request_is_told_it_is_busy(self):
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(4) as ex:
            results = list(ex.map(lambda _: start_cli("full"), range(4)))
        codes = sorted(r.returncode for r in results)
        self.assertIn(0, codes, "at least one request started the run")
        self.assertTrue(set(codes) <= {0, 3}, f"the others are told 'busy' (3), never a raw systemd error: {[(r.returncode, r.stdout[-200:]) for r in results]}")
        for r in results:
            if r.returncode == 3:
                self.assertEqual(json.loads(r.stdout.strip().splitlines()[-1])["reason"], "busy")
        self.assertEqual(wait_done()["state"], "ok")

    def is_locked(self):
        return load(RA, "mra_probe").lock_is_held()

    def test_the_panel_user_starts_it_through_sudo_and_reads_the_status_without_privileges(self):
        probe = ("import importlib.util, json;"
                 f"s = importlib.util.spec_from_file_location('m', '{HELPER}'); m = importlib.util.module_from_spec(s); s.loader.exec_module(m);"
                 "print(json.dumps(m.start('countries')))")
        r = sh(["sudo", "-u", "hiddify-panel", VENV_PY, "-c", probe], check=False)
        self.assertEqual(json.loads(r.stdout.strip().splitlines()[-1])["started"], True, r.stdout + r.stderr)
        wait_done()
        read = ("import importlib.util, json;"
                f"s = importlib.util.spec_from_file_location('m', '{HELPER}'); m = importlib.util.module_from_spec(s); s.loader.exec_module(m);"
                "st = m.status(); print(json.dumps({'state': st['state'], 'mode': st['mode'], 'tail': len(st['log_tail'])}))")
        r = sh(["sudo", "-u", "hiddify-panel", VENV_PY, "-c", read], check=False)
        got = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual((got["state"], got["mode"]), ("ok", "countries"))
        self.assertGreaterEqual(got["tail"], 2, "the panel user can read the log")
        self.assertNotEqual(sh(["sudo", "-u", "hiddify-panel", "touch", LOCK], check=False).returncode, 0, "and cannot write the runner's files")

    def test_a_stale_running_status_without_a_process_does_not_block_a_new_start(self):
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        json.dump({"state": "running", "mode": "full", "started": time.time() - 500, "started_text": "x", "finished": None, "rc": None}, open(STATE, "w"))
        self.assertEqual(ra_status()["state"], "aborted")
        r = start_cli("countries")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(wait_done()["state"], "ok")

    def test_a_failing_job_is_reported_as_failed_with_its_last_line_and_the_lock_is_released(self):
        systemd("stop", "mariadb")
        try:
            r = start_cli("countries")
            self.assertEqual(r.returncode, 0, "the start itself does not depend on the job")
            st = wait_done()
            self.assertEqual(st["state"], "failed", st)
            self.assertNotEqual(st["rc"], 0)
            self.assertTrue(st["summary"], "the last line of the job tells why")
            self.assertIn("=== router apply finished: mode=countries rc=", open(LOG).read())
        finally:
            systemd("start", "mariadb")
            wait_until(lambda: sh("mysql -N hiddifypanel -e 'select 1'", check=False).returncode == 0, timeout=60, what="database")
            restart_panel()
        self.assertFalse(self.is_locked(), "a failed run releases the lock")
        self.assertEqual(start_cli("countries").returncode, 0, "and the next start is possible")
        self.assertEqual(wait_done()["state"], "ok")


def admin_session():
    from test_07_ui import admin_session as s07
    return s07()


def form_fields(html, form_action_contains):
    """what a browser would send for the settings form: every control of the form whose action contains the given text (regex scraping, no HTML parser here)"""
    # the form whose action ENDS with the given text first (the settings form's action is the page itself); other forms of the page (Apply bar, the kept config's buttons) have longer addresses
    m = (re.search(r"<form\b[^>]*action=\"[^\"]*" + re.escape(form_action_contains) + r"\"[^>]*>(.*?)</form>", html, re.S)
         or re.search(r"<form\b[^>]*action=\"[^\"]*" + re.escape(form_action_contains) + r"[^\"]*\"[^>]*>(.*?)</form>", html, re.S))
    assert m, "form not found"
    body = m.group(1)
    data = {}
    for tag in re.findall(r"<input\b[^>]*>", body):
        name = re.search(r"\bname=\"([^\"]+)\"", tag)
        if not name:
            continue
        typ = (re.search(r"\btype=\"([^\"]+)\"", tag) or [None, "text"])[1]
        val = (re.search(r"\bvalue=\"([^\"]*)\"", tag) or [None, ""])[1]
        if typ in ("checkbox", "radio"):
            if "checked" in tag:
                data[name.group(1)] = val or "y"
        elif typ not in ("submit", "button", "file"):
            data[name.group(1)] = val
    for name, txt in re.findall(r"<textarea\b[^>]*\bname=\"([^\"]+)\"[^>]*>(.*?)</textarea>", body, re.S):
        data[name] = txt.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&quot;", '"').replace("&#34;", '"').replace("&#39;", "'").lstrip("\n")
    for sel in re.finditer(r"<select\b[^>]*\bname=\"([^\"]+)\"[^>]*>(.*?)</select>", body, re.S):
        chosen = re.search(r"<option\b[^>]*\bvalue=\"([^\"]*)\"[^>]*\bselected", sel.group(2)) or re.search(r"<option\b[^>]*\bvalue=\"([^\"]*)\"", sel.group(2))
        if chosen:
            data[sel.group(1)] = chosen.group(1)
    return data


class TestPanelButtons(unittest.TestCase):
    """the buttons that used to run the legacy pipeline inside the web request now start the background run and return at once (the decisions themselves are in test_14)"""

    def setUp(self):
        prepare_apply_tests()
        clean_state()
        self.s = admin_session()

    def tearDown(self):
        wait_done()
        drop_probe_rules()

    def test_the_old_apply_xray_router_address_is_the_same_apply_and_the_full_pipeline_runs_to_the_end(self):
        add_probe_rule()                                           # a legacy-side change: the full cycle is needed
        t0 = time.monotonic()
        r = self.s.post(f"{SOURCES}apply-router/", timeout=30)
        self.assertLess(time.monotonic() - t0, 15, "no 60 s wait any more")
        self.assertEqual(r.status_code, 200)
        self.assertIn("запущено в фоне", r.text)
        st = wait_done(timeout=600)
        self.assertEqual((st["state"], st["mode"], st["rc"]), ("ok", "auto", 0), st)
        self.assertIn("Applied multi-location router layer", open(LOG).read())
        self.assertEqual(entrances_listening(), 5)
        drop_probe_rules()
        self.assertEqual(start_cli("auto").returncode, 0)          # leave the stand consistent: the router without the probe rule
        wait_done(timeout=600)

    def test_pressing_a_button_while_an_apply_runs_is_refused_with_an_explanation(self):
        ctl("unassign", 4)                                         # something to apply, so the press really tries to start
        with lock_held():
            r = self.s.post(f"{SOURCES}apply-router/", timeout=30)
            self.assertIn("уже идёт", r.text)
            r = self.s.post(f"{ROUTING}apply/", headers={"Referer": LOC}, timeout=30)
            self.assertIn("уже идёт", r.text)
            self.assertFalse(os.path.exists(STATE))

    def test_the_countries_page_shows_the_bar_and_the_status_of_the_last_run(self):
        page = self.s.get(LOC, timeout=30).text
        self.assertIn('id="apply-bar"', page)
        self.assertEqual(self.s.get(f"{ROUTING}apply-status/", timeout=30).json()["status"]["state"], "idle")
        ctl("unassign", 4)
        self.assertEqual(self.s.post(f"{ROUTING}apply/", headers={"Referer": LOC}, timeout=30).status_code, 200)
        wait_done()
        st = self.s.get(f"{ROUTING}apply-status/", timeout=30).json()["status"]
        self.assertEqual((st["state"], st["state_text"], st["mode"]), ("ok", "завершено успешно", "auto"))
        for k in ("mode_text", "elapsed", "log_tail", "summary", "started_text"):
            self.assertIn(k, st)

    def test_an_anonymous_visitor_cannot_start_anything_through_any_button(self):
        anon = requests.Session()
        anon.verify = CA
        for url in (f"{SOURCES}apply-router/", f"{ROUTING}apply/"):
            r = anon.post(url, timeout=20, allow_redirects=False)
            self.assertIn(r.status_code, (301, 302, 303, 307, 401, 403, 404), url)
        self.assertFalse(os.path.exists(STATE), "nothing was started")

    def test_the_blocking_calls_that_killed_the_pipeline_are_gone_from_the_panel_code(self):
        for rel in ("panel/admin/RoutingRuleSourceAdmin.py", "panel/admin/BusinessAdmin.py"):
            src = open(f"{PKG}/{rel}", encoding="utf-8").read()
            self.assertNotIn('"commercial-routing-apply"', src, rel + ": the blocking call that killed the pipeline is gone")
            self.assertIn("multiloc_apply.apply_now()", src, rel)
        self.assertIn("multiloc_apply", open(f"{PKG}/panel/admin/RoutingLocationAdmin.py", encoding="utf-8").read())


if __name__ == "__main__":
    unittest.main(verbosity=2)
