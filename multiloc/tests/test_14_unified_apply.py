"""The single "Apply" of the routing area. The owner's rule: ONE button with ONE name on every page; press it and everything is done; restart the router only when it is
really needed. The button compares the router with the database and does the minimum: nothing / only the countries layer (one restart) / the full legacy cycle.
The comparison must be EXACT: a missed input would make "Apply" silently do nothing, a noisy one would restart the router for nothing (production: one restart ~5-8 min without connections).
Run: /opt/hiddify-manager/.venv313/bin/python -W ignore -m unittest -v test_14_unified_apply   (from /work/tests; real panel, real router, real legacy pipeline)"""
import contextlib
import json
import os
import re
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import requests  # noqa: E402
from stand_lib import *  # noqa: E402,F403
from test_13_apply import COMMANDER, LOG, RA, STATE, clean_state, lock_held, ra_status, start_cli, wait_done  # noqa: E402

APPLIED = "/var/lib/multiloc-router-apply/applied.json"
UI_JSON = "/opt/hiddify-manager/hiddify-panel/var/commercial-routing-ui.json"
ROUTER = "/etc/xray-router/config.json"
BASE = "https://ru.stand.test"
CA = "/etc/stand/ca.crt"
PP = "stPx0Main1234"
ROUTING = f"{BASE}/{PP}/admin/routing-admin/"
SOURCES = ROUTING + "rule-sources/"
NODES = ROUTING + "upstreams/"
LOC = ROUTING + "locations/"
PARTS_CODE = "import json\nfrom hiddifypanel.hutils import multiloc_apply as m\nprint(json.dumps(m.legacy_parts()))"


def parts():
    return json.loads(panel_python(PARTS_CODE).splitlines()[-1])


UP_COLS = ["last_status", "last_error", "last_checked_at", "updated_at", "vless_uri", "enabled", "priority"]
RULE_COLS = ["enabled", "updated_at"]


def snapshot(table, cols):
    rows = []
    for line in mysql(f"select id, {', '.join(cols)} from {table} order by id").splitlines():
        rows.append(line.split("\t"))
    return rows


def restore(table, cols, rows):
    """put the listed columns back exactly (updated_at is assigned explicitly, so ON UPDATE CURRENT_TIMESTAMP does not move it)"""
    for r in rows:
        sets = ", ".join(f"{c}=" + ("NULL" if v == "NULL" else "'" + v.replace("'", "''") + "'") for c, v in zip(cols, r[1:]))
        mysql(f"update {table} set {sets} where id={r[0]}")


class ExactRestore:
    """mix-in for the tests that mutate the legacy tables: leave them byte-identical (updated_at included)"""

    def setUp(self):
        self._up = snapshot("commercial_routing_upstream", UP_COLS)
        self._rules = snapshot("commercial_routing_custom_rule", RULE_COLS)

    def tearDown(self):
        drop_probe_rules()
        restore("commercial_routing_upstream", UP_COLS, self._up)
        restore("commercial_routing_custom_rule", RULE_COLS, self._rules)


def set_setting(sql):
    """a direct SQL change of a setting: the panel caches settings in redis (its own saves clear the cache, SQL does not)"""
    mysql(sql)
    flush_cache()


def changed(a, b):
    return sorted(k for k in a if a[k] != b[k])


def setUpModule():
    prepare_apply_tests()


def tearDownModule():
    wait_done()
    drop_probe_rules()
    reset_locations_to_stand_default()
    apply_router()
    ensure_baseline(force=True)


def admin_session():
    from test_07_ui import admin_session as s07
    return s07()


class TestEveryInputMovesExactlyItsPart(ExactRestore, unittest.TestCase):
    """legacy_parts() hashes every input of the legacy pipeline; volatile probe results are not inputs"""

    def test_deterministic_and_blind_to_probe_results(self):
        p0 = parts()
        self.assertEqual(parts(), p0)
        mysql("update commercial_routing_upstream set last_status='down', last_error='boom', last_checked_at=now(), updated_at=now()")
        self.assertEqual(parts(), p0, "the results of the node probe (and the 'updated' stamps they cause) are not inputs of the router config")

    def test_rules(self):
        p0 = parts()
        add_probe_rule()
        self.assertEqual(changed(p0, parts()), ["rules"])
        drop_probe_rules()
        self.assertEqual(parts(), p0, "back to the same content = nothing to apply")
        first = mysql("select min(id) from commercial_routing_custom_rule where enabled=1")
        mysql(f"update commercial_routing_custom_rule set enabled=0 where id={first}")
        try:
            self.assertEqual(changed(p0, parts()), ["rules"], "a switched-off rule leaves the router config")
        finally:
            mysql(f"update commercial_routing_custom_rule set enabled=1 where id={first}")
        self.assertEqual(parts(), p0)

    def test_the_routing_save_recreates_the_rules_with_new_ids_and_that_is_not_a_change(self):
        p0 = parts()
        mysql("update commercial_routing_custom_rule set id = id + 1000 order by id desc")        # same content, same order, other ids
        try:
            self.assertEqual(parts(), p0)
        finally:
            mysql("update commercial_routing_custom_rule set id = id - 1000 order by id asc")

    def test_external_nodes(self):
        p0 = parts()
        uri = mysql("select vless_uri from commercial_routing_upstream where enabled=1 order by id limit 1")
        node = mysql("select id from commercial_routing_upstream where enabled=1 order by id limit 1")
        mysql(f"update commercial_routing_upstream set vless_uri=concat(vless_uri, '&probe=1') where id={node}")
        self.assertEqual(changed(p0, parts()), ["upstreams"])
        mysql(f"update commercial_routing_upstream set vless_uri='{uri}' where id={node}")
        self.assertEqual(parts(), p0)
        pr = mysql(f"select priority from commercial_routing_upstream where id={node}")
        mysql(f"update commercial_routing_upstream set priority=priority+50 where id={node}")
        self.assertEqual(changed(p0, parts()), ["upstreams"], "priority orders the nodes")
        mysql(f"update commercial_routing_upstream set priority={pr} where id={node}")
        mysql(f"update commercial_routing_upstream set enabled=0 where id={node}")
        self.assertEqual(changed(p0, parts()), ["upstreams"], "a disabled node leaves the config")
        mysql(f"update commercial_routing_upstream set enabled=1 where id={node}")
        self.assertEqual(parts(), p0)

    def test_settings_and_the_ui_files(self):
        p0 = parts()
        old = mysql("select value from str_config where `key`='commercial_ru_domain_suffixes' limit 1")
        set_setting("update str_config set value=concat(value, ',.probe') where `key`='commercial_ru_domain_suffixes'")
        self.assertEqual(changed(p0, parts()), ["settings"])
        set_setting(f"update str_config set value='{old}' where `key`='commercial_ru_domain_suffixes'")
        self.assertEqual(parts(), p0)
        had = os.path.exists(UI_JSON)
        orig = open(UI_JSON, "rb").read() if had else b""
        try:
            with open(UI_JSON, "wb") as f:
                f.write(orig + b" ")
            self.assertEqual(changed(p0, parts()), ["ui_files"], "the block / DNS lists live in the ui json files")
        finally:
            if had:
                open(UI_JSON, "wb").write(orig)
            else:
                os.remove(UI_JSON)
        self.assertEqual(parts(), p0)


class TestTheComparisonIsCompleteAgainstTheRealPipeline(ExactRestore, unittest.TestCase):
    """THE property that makes "Apply does nothing" safe: whenever the real legacy pipeline would produce a DIFFERENT router config, the parts differ.
    (The converse may be loose - an unnecessary full apply is slow but safe.) Checked with the real pipeline on the stand's router, mutation by mutation."""

    def run_pipeline(self):
        r = sh(["/opt/hiddify-manager/.venv313/bin/python", COMMANDER, "commercial-routing-apply"], check=False, timeout=900)
        self.assertEqual(r.returncode, 0, r.stdout[-800:] + r.stderr[-800:])
        return json.load(open(ROUTER))

    def tearDown(self):
        super().tearDown()
        self.run_pipeline()

    def test_a_different_output_always_means_different_parts(self):
        base_cfg, base_parts = self.run_pipeline(), parts()
        self.assertEqual(self.run_pipeline(), base_cfg, "the pipeline is deterministic: the same inputs give the same router config")
        node = mysql("select id from commercial_routing_upstream where enabled=1 order by id limit 1")
        uri = mysql(f"select vless_uri from commercial_routing_upstream where id={node}")
        suffixes = mysql("select value from str_config where `key`='commercial_ru_domain_suffixes' limit 1")
        had_ui = os.path.exists(UI_JSON)
        ui_orig = open(UI_JSON, "rb").read() if had_ui else None

        def ui_write():
            doc = json.loads(ui_orig or b"{}")
            doc["commercial_blocked_domains"] = (doc.get("commercial_blocked_domains", "") + "\nprobe-block.example").strip()
            open(UI_JSON, "w").write(json.dumps(doc, indent=2))

        def ui_restore():
            if had_ui:
                open(UI_JSON, "wb").write(ui_orig)
            else:
                os.remove(UI_JSON)
        mutations = [
            ("a new custom rule", add_probe_rule, drop_probe_rules),
            ("a node uri", lambda: mysql(f"update commercial_routing_upstream set vless_uri=concat(vless_uri, '&p=1') where id={node}"),
             lambda: mysql(f"update commercial_routing_upstream set vless_uri='{uri}' where id={node}")),
            ("a node disabled", lambda: mysql(f"update commercial_routing_upstream set enabled=0 where id={node}"),
             lambda: mysql(f"update commercial_routing_upstream set enabled=1 where id={node}")),
            ("the domestic suffixes", lambda: set_setting("update str_config set value='.ru,.su,.probe' where `key`='commercial_ru_domain_suffixes'"),
             lambda: set_setting(f"update str_config set value='{suffixes}' where `key`='commercial_ru_domain_suffixes'")),
            ("the udp443 policy", lambda: set_setting("update str_config set value='allow_to_router' where `key`='commercial_udp443_policy'"),
             lambda: set_setting("update str_config set value='keep_block' where `key`='commercial_udp443_policy'")),
            ("the ui json (block list)", ui_write, ui_restore),
        ]
        visible_changes = 0
        for name, do, undo in mutations:
            do()
            try:
                cfg, p1 = self.run_pipeline(), parts()
                if cfg != base_cfg:
                    visible_changes += 1
                    self.assertNotEqual(p1, base_parts, f"{name}: the router config changed but the comparison saw nothing - 'Apply' would do nothing")
            finally:
                undo()
            back_cfg, back_parts = self.run_pipeline(), parts()
            self.assertEqual(back_cfg, base_cfg, f"{name}: undoing the change gives the old router config again")
            self.assertEqual(back_parts, base_parts, f"{name}: ... and the old parts")
        self.assertGreaterEqual(visible_changes, 3, "the mutations really move the router config (otherwise the property above proves nothing)")


class TestDecisions(unittest.TestCase):
    """the runner's `auto` mode on the real stand: nothing / countries / full"""

    def setUp(self):
        prepare_apply_tests()
        clean_state()

    def tearDown(self):
        wait_done()
        drop_probe_rules()
        reset_locations_to_stand_default()

    def test_nothing_pending_means_nothing_is_done_and_the_router_is_not_restarted(self):
        self.assertEqual(plan_now()["action"], "none")
        t0 = now_iso()
        self.assertEqual(start_cli("auto").returncode, 0)
        st = wait_done()
        self.assertEqual((st["state"], st["mode"], st["rc"]), ("ok", "auto", 0), st)
        self.assertIn("nothing to apply", st["summary"])
        self.assertEqual(router_starts_since(t0), 0, "no restart when there is nothing to apply")

    def test_a_country_change_is_exactly_one_router_restart_and_the_legacy_pipeline_is_not_run(self):
        applied_before = open(APPLIED).read()
        ctl("unassign", 4)
        pl = plan_now()
        self.assertEqual((pl["action"], pl["legacy"], pl["countries"], pl["restarts"]), ("countries", "clean", "pending", 1), pl)
        t0 = now_iso()
        self.assertEqual(start_cli("auto").returncode, 0)
        st = wait_done()
        self.assertEqual((st["state"], st["rc"]), ("ok", 0), st)
        log = open(LOG).read()
        self.assertNotIn("Applied split DNS postfix", log, "the legacy pipeline did not run")
        self.assertEqual(router_starts_since(t0), 1, "exactly one restart of the router")
        self.assertEqual(plan_now()["action"], "none")
        self.assertEqual(open(APPLIED).read(), applied_before, "the legacy side was untouched")

    def test_a_rule_change_runs_the_full_cycle_and_afterwards_nothing_is_pending(self):
        add_probe_rule()
        pl = plan_now()
        self.assertEqual((pl["action"], pl["changed"], pl["restarts"]), ("full", ["rules"], 1), pl)
        self.assertIn("правила маршрутизации", pl["text"])
        t0 = now_iso()
        self.assertEqual(start_cli("auto").returncode, 0)
        st = wait_done(timeout=600)
        self.assertEqual((st["state"], st["rc"]), ("ok", 0), st)
        log = open(LOG).read()
        self.assertIn("Applied split DNS postfix", log)
        self.assertIn("Applied multi-location router layer", log, "the countries layer is the last step of the full cycle")
        self.assertIn("ONE router restart", log)
        self.assertEqual(router_starts_since(t0), 1, "the legacy cycle is built in the background and the router restarts ONCE")
        after = plan_now()
        self.assertEqual((after["action"], after["legacy"]), ("none", "clean"), after)
        self.assertEqual(json.load(open(APPLIED))["how"], "full")
        self.assertEqual(entrances_ok(), 5)
        drop_probe_rules()
        self.assertEqual(plan_now()["changed"], ["rules"], "removing the rule is a change as well")
        self.assertEqual(start_cli("auto").returncode, 0)
        wait_done(timeout=600)
        self.assertEqual(plan_now()["action"], "none")

    def test_names_visibility_order_and_testers_need_no_apply_at_all(self):
        ctl("set", 2, "--title", "Другое имя")
        ctl("set", 2, "--order", 77)
        ctl("auto", "--visibility", "testers")
        ctl("tester", "add", USERS["active"], "--note", "plan test")
        try:
            self.assertEqual(plan_now()["action"], "none", "labels are served by the subscription at once")
        finally:
            ctl("tester", "remove", USERS["active"])

    def test_a_node_added_to_the_database_is_a_full_cycle(self):
        remember_autoinc("commercial_routing_upstream")
        mysql("insert into commercial_routing_upstream (name, label, enabled, priority, tunnel_type, wg_endpoint, wg_public_key, wg_private_key_ref, wg_addresses, wg_mtu, "
              "vless_uri, trojan_uri, last_status, created_at, updated_at) select 'probe-node', 'probe', 1, 99, tunnel_type, wg_endpoint, wg_public_key, wg_private_key_ref, "
              "wg_addresses, wg_mtu, vless_uri, trojan_uri, '', now(), now() from commercial_routing_upstream order by id limit 1")
        try:
            pl = plan_now()
            self.assertEqual((pl["action"], pl["changed"]), ("full", ["upstreams"]), pl)
        finally:
            mysql("delete from commercial_routing_upstream where name='probe-node'")
            give_back_autoinc("commercial_routing_upstream")
        self.assertEqual(plan_now()["action"], "none")

    def test_without_a_record_the_state_is_unknown_and_that_means_the_full_cycle(self):
        os.remove(APPLIED)
        pl = plan_now()
        self.assertEqual((pl["action"], pl["legacy"]), ("full", "unknown"), pl)
        ensure_baseline(force=True)
        self.assertEqual(plan_now()["action"], "none")

    def test_a_failed_run_records_nothing_so_the_change_stays_pending(self):
        add_probe_rule()
        before = open(APPLIED).read()
        systemd("stop", "mariadb")
        try:
            self.assertEqual(start_cli("auto").returncode, 0)
            st = wait_done()
            self.assertEqual(st["state"], "failed", st)
        finally:
            systemd("start", "mariadb")
            wait_until(lambda: sh("mysql -N hiddifypanel -e 'select 1'", check=False).returncode == 0, timeout=60, what="database")
            restart_panel()
        self.assertEqual(open(APPLIED).read(), before)
        self.assertEqual(plan_now()["action"], "full", "still pending")


def entrances_ok():
    return int(sh("ss -Htln | grep -c -E ':208(08|11|12|13|14) ' || true").stdout.strip() or 0)


class TestOneButtonEverywhere(unittest.TestCase):
    """the same button, the same label, the same action on every page of the routing area"""

    OLD_LABELS = ("Применить xray-router", "Применить страны на router", "▶ Применить")

    def setUp(self):
        prepare_apply_tests()
        clean_state()
        self.s = admin_session()

    def tearDown(self):
        wait_done()
        drop_probe_rules()

    def page(self, url):
        r = self.s.get(url, timeout=60)
        self.assertEqual(r.status_code, 200, url)
        return r.text

    @staticmethod
    def run_evidence():
        """what the background run (if any) says about itself: its status file and the head of its log (the plan it started with)"""
        st = open(STATE).read() if os.path.exists(STATE) else ""
        lg = open(LOG).read()[:1500] if os.path.exists(LOG) else ""
        return st + " || " + lg

    def test_the_bar_with_the_one_label_is_on_the_countries_sources_and_nodes_pages(self):
        for name, url in (("countries", LOC), ("rule sources", SOURCES), ("external nodes", NODES)):
            h = self.page(url)
            self.assertEqual(h.count('id="apply-bar"'), 1, name)
            self.assertEqual(h.count('id="apply-btn"'), 1, name)
            self.assertRegex(h, r'<button type="submit" id="apply-btn"[^>]*>Применить</button>', name)
            self.assertIn(f'action="/{PP}/admin/routing-admin/apply/"', h, name + ": every page posts to the one common address")
            self.assertIn(f"/{PP}/admin/routing-admin/apply-status/", h, name)
            for old in self.OLD_LABELS:
                self.assertNotIn(old, h, f"{name}: the old label {old!r} is gone")

    def test_the_routing_settings_page_applies_with_its_own_submit_and_shows_the_state(self):
        h = self.page(ROUTING + "?section=routing")
        self.assertEqual(h.count('id="apply-bar"'), 1)
        self.assertNotIn('id="apply-btn"', h, "no second Apply button: the form's own submit is the Apply")
        self.assertRegex(h, r'<button type="submit" class="btn btn-primary">Применить</button>')
        self.assertIn("onsubmit=\"return confirm('Применить настройки маршрутизации?", h)
        for old in self.OLD_LABELS:
            self.assertNotIn(old, h)
        tele = self.page(ROUTING + "?section=telegram")
        self.assertNotIn('id="apply-bar"', tele, "the Telegram tab has nothing to do with the router")
        self.assertRegex(tele, r'<button type="submit" class="btn btn-primary">Сохранить</button>', "other tabs keep their Save")

    def test_the_status_endpoint_tells_the_state_and_the_plan(self):
        d = self.s.get(ROUTING + "apply-status/", timeout=60).json()
        self.assertEqual(set(d), {"status", "plan", "staged"})
        self.assertEqual(d["plan"]["action"], "none")
        self.assertEqual(d["status"]["state"], "idle")
        ctl("unassign", 4)
        d = self.s.get(ROUTING + "apply-status/", timeout=60).json()
        self.assertEqual((d["plan"]["action"], d["plan"]["restarts"]), ("countries", 1), "the page always sees the current plan")
        self.assertIn("4–8 минут", d["plan"]["text"])

    def test_pressing_it_with_nothing_to_apply_does_nothing_and_says_so(self):
        t0 = now_iso()
        for from_url in (LOC, SOURCES, NODES):
            r = self.s.post(ROUTING + "apply/", headers={"Referer": from_url}, timeout=60)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.url.split("?")[0], from_url, "back to the page where it was pressed")
            self.assertIn("Всё уже применено", r.text)
        r = self.s.post(SOURCES + "apply-router/", timeout=60)
        self.assertIn("Всё уже применено", r.text, "the old address of the sources page is the same Apply")
        self.assertFalse(os.path.exists(STATE), "nothing was started")
        self.assertEqual(router_starts_since(t0), 0)

    def test_pressing_it_after_a_country_change_does_one_restart_and_the_page_follows(self):
        ctl("unassign", 4)
        t0 = now_iso()
        r = self.s.post(ROUTING + "apply/", headers={"Referer": LOC}, timeout=60)
        self.assertIn("Применение запущено", r.text)
        self.assertIn("4–8 минут", r.text)
        st = wait_done()
        self.assertEqual((st["state"], st["mode"]), ("ok", "auto"), st)
        self.assertEqual(router_starts_since(t0), 1)
        self.assertIn("router: синхронизирован", self.page(LOC))

    def test_the_routing_save_without_a_change_restarts_nothing(self):
        """the stock Save used to run the whole pipeline every time, even for identical content"""
        from test_13_apply import form_fields
        data = form_fields(self.page(ROUTING + "?section=routing"), "/routing-admin/")
        dump = sh("mysqldump --no-tablespaces hiddifypanel commercial_routing_custom_rule").stdout
        t0 = now_iso()
        try:
            r = self.s.post(ROUTING, data=data, timeout=60)
            self.assertEqual(r.status_code, 200)
            self.assertIn("Настройки сохранены", r.text)
            self.assertIn("Всё уже применено", r.text)
            self.assertFalse(os.path.exists(STATE), "a Save without a change started a run: " + self.run_evidence())
            self.assertEqual(router_starts_since(t0), 0, "same settings saved again: no restart")
            r = self.s.post(ROUTING, data=data, timeout=60)               # and again: the ui json written twice is the same content
            self.assertIn("Всё уже применено", r.text)
        finally:
            sh(["mysql", "hiddifypanel"], input=dump)

    def test_the_routing_save_with_a_changed_setting_runs_the_full_cycle(self):
        from test_13_apply import form_fields
        data = form_fields(self.page(ROUTING + "?section=routing"), "/routing-admin/")
        key = "commercial_blocked_domains"
        self.assertIn(key, data)
        old_value = data[key]
        data[key] = (old_value + "\nprobe-apply-block.example").strip()
        dump = sh("mysqldump --no-tablespaces hiddifypanel commercial_routing_custom_rule").stdout
        old_ui = open(UI_JSON, "rb").read() if os.path.exists(UI_JSON) else None
        try:
            r = self.s.post(ROUTING, data=data, timeout=120)
            self.assertIn("Настройки сохранены", r.text)
            self.assertIn("запущено в фоне", r.text)
            self.assertIn("полный цикл", r.text)
            st = wait_done(timeout=600)
            self.assertEqual((st["state"], st["rc"]), ("ok", 0), st)
            self.assertEqual(plan_now()["action"], "none")
        finally:
            data[key] = old_value
            self.s.post(ROUTING, data=data, timeout=120)
            wait_done(timeout=600)
            sh(["mysql", "hiddifypanel"], input=dump)
            if old_ui is not None:
                open(UI_JSON, "wb").write(old_ui)
            ensure_baseline(force=True)

    def test_a_double_press_starts_one_run(self):
        ctl("unassign", 4)
        r1 = self.s.post(ROUTING + "apply/", headers={"Referer": LOC}, timeout=60)
        r2 = self.s.post(ROUTING + "apply/", headers={"Referer": LOC}, timeout=60)
        self.assertIn("Применение запущено", r1.text)
        self.assertTrue("уже идёт" in r2.text or "Всё уже применено" in r2.text, "the second press is refused or finds nothing left: " + r2.text[-300:])
        self.assertEqual(wait_done()["state"], "ok")

    def test_nobody_but_an_admin_can_use_it(self):
        anon = requests.Session()
        anon.verify = CA
        for method, url in (("post", ROUTING + "apply/"), ("get", ROUTING + "apply-status/"), ("post", SOURCES + "apply-router/")):
            r = getattr(anon, method)(url, timeout=20, allow_redirects=False)
            self.assertIn(r.status_code, (301, 302, 303, 307, 401, 403, 404), url)
        self.assertFalse(os.path.exists(STATE))

    def test_the_redirect_back_never_leaves_the_admin_area(self):
        ctl("unassign", 4)
        r = self.s.post(ROUTING + "apply/", headers={"Referer": "https://evil.example/steal?x=1"}, timeout=60, allow_redirects=False)
        self.assertIn(r.status_code, (301, 302, 303, 307))
        self.assertTrue(r.headers["Location"].startswith(f"/{PP}/admin/routing-admin/"), r.headers["Location"])
        wait_done()


if __name__ == "__main__":
    unittest.main(verbosity=2)
