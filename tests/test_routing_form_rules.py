"""Routing form «Маршрутизация»: the text of the form must always be savable.

Rule sources store single hosts as `ip` rules written `x.x.x.x/32`; the form printed them back as `ip:x.x.x.x/32` and the stock `normalize_ip_rule("ip", ...)` refused them, so the form could
not be saved at all (and it held every rule of every source in one text). Now `ip:` takes a single host written /32 (/128), a network still needs `cidr:`, and the form shows only the
manually added rules (`load_enabled_custom_rules(manual_only=True)`).

The functions are cut out of the overlay source with `ast` (no panel, no database), like the other tests of this directory.
Run: python -m unittest tests.test_routing_form_rules
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

SOURCE = (
    Path(__file__).parents[1]
    / "release" / "routing-installer" / "payload" / "panel-overlay" / "hiddifypanel" / "hutils" / "commercial_routing.py"
)
FUNCTIONS = {"_idna_host", "normalize_domain_rule", "normalize_ip_rule", "validate_custom_rule", "parse_bulk_rules", "custom_rules_to_bulk_text", "load_enabled_custom_rules"}


class FakeQuery:
    """records the filters of `CommercialRoutingCustomRule.query...` and returns canned rows"""

    def __init__(self, rows, calls):
        self.rows, self.calls = rows, calls

    def filter_by(self, **kw):
        self.calls.append(("filter_by", kw))
        return self

    def filter(self, *conditions):
        self.calls.append(("filter", conditions))
        return self

    def order_by(self, *_):
        return self

    def all(self):
        return self.rows


class FakeColumn:
    def __init__(self, name):
        self.name = name

    def is_(self, value):
        return (self.name, "is", value)

    def asc(self):
        return (self.name, "asc")


def load(rows=None, calls=None):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    body = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("hiddifypanel"):
                continue
            body.append(node)
        elif isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "PREFIX_MAP" for t in node.targets):
            body.append(node)
        elif isinstance(node, ast.ClassDef) and node.name == "BulkParseError":
            body.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS:
            body.append(node)
    module = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(module)
    calls = [] if calls is None else calls

    class Rule:
        query = FakeQuery(rows or [], calls)
        id = FakeColumn("id")
        source_id = FakeColumn("source_id")

    namespace = {"CommercialRoutingCustomRule": Rule}
    exec(compile(module, str(SOURCE), "exec"), namespace)
    return namespace


class NormalizeIpRuleTest(unittest.TestCase):

    def setUp(self):
        self.ns = load()

    def test_a_single_host_written_as_a_network_is_a_host(self):
        n = self.ns["normalize_ip_rule"]
        self.assertEqual(n("ip", "203.0.113.200/32"), "203.0.113.200")
        self.assertEqual(n("ip", "::1/128"), "::1")
        self.assertEqual(n("ip", "2001:db8::7/128"), "2001:db8::7")

    def test_a_plain_address_and_the_cidr_rule_are_unchanged(self):
        n = self.ns["normalize_ip_rule"]
        self.assertEqual(n("ip", " 203.0.113.5 "), "203.0.113.5")
        self.assertEqual(n("cidr", "203.0.113.0/24"), "203.0.113.0/24")
        self.assertEqual(n("cidr", "203.0.113.9/32"), "203.0.113.9/32")

    def test_a_real_network_under_ip_is_refused_with_a_hint(self):
        with self.assertRaises(ValueError) as caught:
            self.ns["normalize_ip_rule"]("ip", "203.0.113.0/24")
        self.assertIn("cidr:", str(caught.exception))

    def test_garbage_is_refused(self):
        for bad in ("nonsense", "999.1.1.1", "203.0.113.5/33", ""):
            with self.assertRaises(ValueError, msg=bad):
                self.ns["normalize_ip_rule"]("ip", bad)


class FormTextRoundTripTest(unittest.TestCase):

    def test_the_text_the_form_prints_for_source_rules_is_accepted_when_it_comes_back(self):
        ns = load()
        rules = [
            {"rule_type": "ip", "value": "203.0.113.5/32", "normalized_value": "203.0.113.5/32", "outbound_policy": "direct_ru"},
            {"rule_type": "cidr", "value": "198.51.100.0/24", "normalized_value": "198.51.100.0/24", "outbound_policy": "direct_ru"},
            {"rule_type": "domain_suffix", "value": "example.org", "normalized_value": "example.org", "outbound_policy": "direct_ru"},
        ]
        text = ns["custom_rules_to_bulk_text"](rules)
        parsed, errors = ns["parse_bulk_rules"](text)
        self.assertEqual(errors, [], text)
        self.assertEqual(len(parsed), 3)
        self.assertEqual(next(r for r in parsed if r["rule_type"] == "ip")["normalized_value"], "203.0.113.5")

    def test_errors_carry_their_line_numbers(self):
        ns = load()
        parsed, errors = ns["parse_bulk_rules"]("suffix:a.example\nip:203.0.113.0/24\nip:nonsense")
        self.assertEqual([e.line_no for e in errors], [2, 3])
        self.assertEqual(len(parsed), 1)


class ManualOnlyTest(unittest.TestCase):

    def row(self, i, value):
        return type("Row", (), {"id": i, "rule_type": "domain_suffix", "value": value, "normalized_value": value, "outbound_policy": "direct_ru", "enabled": True, "comment": None})()

    def test_the_default_keeps_every_enabled_rule(self):
        calls = []
        ns = load([self.row(1, "a.example")], calls)
        self.assertEqual([r["value"] for r in ns["load_enabled_custom_rules"]()], ["a.example"])
        self.assertEqual(calls, [("filter_by", {"enabled": True})], "no extra filter")

    def test_manual_only_adds_the_no_source_filter(self):
        calls = []
        ns = load([self.row(1, "mine.example")], calls)
        out = ns["load_enabled_custom_rules"](manual_only=True)
        self.assertEqual([r["value"] for r in out], ["mine.example"])
        self.assertEqual(calls[0], ("filter_by", {"enabled": True}))
        self.assertEqual(calls[1], ("filter", (("source_id", "is", None),)), "rules imported from a source (source_id set) are left out")


if __name__ == "__main__":
    unittest.main()
