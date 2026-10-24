"""The routing form «Маршрутизация» (owner approved 2026-10-13 after the incident of 04:40): the text holds ONLY the manually added rules, Save changes them in place and never touches the rules
imported from the rule sources or anything the text cannot show; `ip:x.x.x.x/32` is a single host (how the sources store them) so the text can always be saved; at most 10 error lines are shown.
Before: the text held ~15k rules, could not be saved because of `/32` rows and Save deleted EVERY row and re-created them without their source link.
Run: /opt/hiddify-manager/.venv313/bin/python -W ignore -m unittest -v test_19_routing_form   (from /work/tests; real panel, real database, real background apply)"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stand_lib import *  # noqa: E402,F403
from test_07_ui import admin_session  # noqa: E402
from test_13_apply import form_fields, wait_done  # noqa: E402

BASE = "https://ru.stand.test"
PP = "stPx0Main1234"
ROUTING = f"{BASE}/{PP}/admin/routing-admin/"
TABLES = "commercial_routing_custom_rule commercial_routing_rule_source"
COLS = "id, rule_type, value, normalized_value, outbound_policy, enabled, comment, source_id"
DUMP = {"sql": None}

SEED = """
delete from commercial_routing_custom_rule where normalized_value in ('203.0.113.5','198.51.100.0/24','source.example','manual1.example','manual2.example','192.0.2.7','off.example','blk.example');
delete from commercial_routing_rule_source where id = 901;
insert into commercial_routing_rule_source (id, name, label, enabled, source_type, rule_family, outbound_policy, rules_count) values (901, 't19src', 'T19 source', 1, 'text', 'ip', 'direct_ru', 3);
insert into commercial_routing_custom_rule (rule_type, value, normalized_value, outbound_policy, enabled, comment, source_id) values
 ('ip', '203.0.113.5/32', '203.0.113.5', 'direct_ru', 1, NULL, 901),
 ('cidr', '198.51.100.0/24', '198.51.100.0/24', 'direct_ru', 1, NULL, 901),
 ('domain_suffix', 'source.example', 'source.example', 'direct_ru', 1, NULL, 901),
 ('domain_suffix', 'manual1.example', 'manual1.example', 'direct_ru', 1, 'my comment', NULL),
 ('domain_suffix', 'manual2.example', 'manual2.example', 'direct_ru', 1, NULL, NULL),
 ('ip', '192.0.2.7', '192.0.2.7', 'direct_ru', 1, NULL, NULL),
 ('domain_suffix', 'off.example', 'off.example', 'direct_ru', 0, NULL, NULL),
 ('domain_suffix', 'blk.example', 'blk.example', 'block', 1, NULL, NULL);
"""


def rules_table():
    return mysql(f"select {COLS} from commercial_routing_custom_rule order by id")


def setUpModule():
    prepare_apply_tests()
    sh(CTL + ["apply", "--discard-staged"], check=False, cwd="/opt/hiddify-manager/hiddify-panel")        # a config kept by an earlier test would add its own forms to the page
    DUMP["sql"] = sh(f"mysqldump --no-tablespaces hiddifypanel {TABLES}").stdout
    for stmt in [x for x in SEED.split(";\n") if x.strip()]:
        mysql(stmt)
    sh(CTL + ["apply", "--full", "--accept-diff"], check=False, timeout=1800, cwd="/opt/hiddify-manager/hiddify-panel")      # the router reflects the seeded database ...
    ensure_baseline(force=True)                                                       # ... and that is the starting point: nothing is pending


def tearDownModule():
    sh(["mysql", "hiddifypanel"], input=DUMP["sql"])
    wait_done(timeout=600)
    sh(CTL + ["apply", "--full", "--accept-diff"], check=False, timeout=1800, cwd="/opt/hiddify-manager/hiddify-panel")
    ensure_baseline(force=True)


def page_fields(s):
    """the fields of the SETTINGS form (its action is the page itself; other forms of the page - Apply bar, a kept config's buttons - have longer addresses)"""
    r = s.get(ROUTING + "?section=routing", timeout=60)
    assert r.status_code == 200, r.status_code
    m = re.search(r'<form\b[^>]*action="[^"]*/routing-admin/"[^>]*>(.*?)</form>', r.text, re.S)
    assert m, "the settings form is not on the page"
    return form_fields('<form action="/routing-admin/">' + m.group(1) + "</form>", "/routing-admin/")


class TestRoutingForm(unittest.TestCase):

    def setUp(self):
        self.s = admin_session()

    def test_a_the_text_holds_only_the_manual_rules(self):
        text = page_fields(self.s)["custom_ru_rules_bulk"]
        lines = [x.strip() for x in text.splitlines() if x.strip()]
        for want in ("suffix:manual1.example", "suffix:manual2.example", "ip:192.0.2.7"):
            self.assertIn(want, lines)
        for hidden in ("source.example", "203.0.113.5", "198.51.100.0", "off.example", "blk.example"):
            self.assertFalse([x for x in lines if hidden in x], f"{hidden}: imported from a source / switched off / another policy - not in the text")

    def test_b_save_without_a_change_changes_nothing_and_starts_no_apply(self):
        before = rules_table()
        data = page_fields(self.s)
        r = self.s.post(ROUTING, data=data, timeout=120)
        self.assertEqual(r.status_code, 200)
        self.assertIn("Свои правила сохранены: добавлено 0, убрано 0", r.text)
        self.assertRegex(r.text, r"Правила из источников и прочие \(\d+\) не затронуты")
        self.assertEqual(rules_table(), before, "every row is as it was: ids, source links, comments, switched-off and other-policy rows")
        self.assertEqual(plan_now()["action"], "none", "nothing to apply")

    def test_c_save_changes_in_place_and_never_touches_the_sources(self):
        before = {row.split("\t")[3]: row for row in rules_table().splitlines()}
        data = page_fields(self.s)
        lines = [x for x in data["custom_ru_rules_bulk"].splitlines() if x.strip() and "manual2.example" not in x]
        lines += ["suffix:new1.example", "ip:198.51.100.77/32", "cidr:198.51.100.0/24"]              # the last one equals a SOURCE rule
        data["custom_ru_rules_bulk"] = "\n".join(lines)
        r = self.s.post(ROUTING, data=data, timeout=120)
        self.assertEqual(r.status_code, 200)
        self.assertIn("добавлено 2, убрано 1", r.text)
        self.assertIn("1 уже есть в источниках - пропущено", r.text)
        after = {row.split("\t")[3]: row for row in rules_table().splitlines()}
        for key in ("203.0.113.5", "198.51.100.0/24", "source.example"):
            self.assertEqual(after[key], before[key], f"{key}: a source rule is untouched (same id, same source link)")
        for key in ("off.example", "blk.example", "manual1.example", "192.0.2.7"):
            self.assertEqual(after[key], before[key], f"{key}: untouched (same id; the comment of manual1 is kept)")
        self.assertNotIn("manual2.example", after, "the line the text no longer has is deleted")
        self.assertTrue(after["new1.example"].endswith("\tNULL") and "\t0\t" not in after["new1.example"], after["new1.example"])
        self.assertIn("198.51.100.77", after, "a single host written /32 is stored as the host")
        self.assertEqual(after["198.51.100.77"].split("\t")[1], "ip")
        self.assertEqual(mysql("select count(*) from commercial_routing_custom_rule where source_id=901"), "3", "the source keeps its three rules")
        st = wait_done(timeout=600)                                                       # the rules changed: the unified Apply ran in the background
        self.assertEqual((st["state"], st["rc"]), ("ok", 0), st)

    def test_d_at_most_ten_error_lines_are_shown_and_nothing_is_saved(self):
        before = rules_table()
        data = page_fields(self.s)
        data["custom_ru_rules_bulk"] = data["custom_ru_rules_bulk"] + "\n" + "\n".join(f"ip:999.1.1.{i}" for i in range(25))
        r = self.s.post(ROUTING, data=data, timeout=120)
        self.assertEqual(len(re.findall(r"Ошибка в строке \d+ списка правил", r.text)), 10)
        self.assertIn("…и ещё 15 ошибок", r.text)
        self.assertEqual(rules_table(), before, "an invalid text saves nothing")

    def test_e_a_single_host_may_be_written_as_a_network_but_a_network_needs_cidr(self):
        out = panel_python("""
import json
from hiddifypanel.hutils import commercial_routing as cr
res = {}
for rt, v in (("ip", "203.0.113.200/32"), ("ip", "::1/128"), ("ip", "1.2.3.4"), ("ip", "1.2.3.0/24"), ("ip", "nonsense"), ("cidr", "10.0.0.0/8"), ("cidr", "1.2.3.4/32")):
    try:
        res[f"{rt} {v}"] = cr.normalize_ip_rule(rt, v)
    except ValueError as exc:
        res[f"{rt} {v}"] = "ERR " + str(exc)[:140]
rules, errors = cr.parse_bulk_rules("ip:203.0.113.200/32\\ncidr:10.0.0.0/8\\nsuffix:a.example")
res["bulk"] = [len(rules), len(errors)]
print(json.dumps(res))
""")
        res = json.loads(out.splitlines()[-1])
        self.assertEqual(res["ip 203.0.113.200/32"], "203.0.113.200")
        self.assertEqual(res["ip ::1/128"], "::1")
        self.assertEqual(res["ip 1.2.3.4"], "1.2.3.4")
        self.assertTrue(res["ip 1.2.3.0/24"].startswith("ERR"), res["ip 1.2.3.0/24"])
        self.assertIn("cidr:", res["ip 1.2.3.0/24"])
        self.assertTrue(res["ip nonsense"].startswith("ERR"))
        self.assertEqual(res["cidr 10.0.0.0/8"], "10.0.0.0/8")
        self.assertEqual(res["bulk"], [3, 0], "the rows the sources store (x.x.x.x/32) round-trip through the text")


if __name__ == "__main__":
    unittest.main(verbosity=2)
