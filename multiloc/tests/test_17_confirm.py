"""A full cycle must not silently replace the working router config with a structurally different one (the 2026-10-13 incident: the first complete pipeline run on production gave a
config with no RU IP ranges, a DNS section with a dead tunnel address and another probe interval - and it went live at once). Now the finished config is COMPARED with the running one:
what the owner's change explains is applied by itself; anything else is KEPT (rc 4, state needs-confirm), the router is not touched, and only «Применить новый конфиг» replaces it.
Run: /opt/hiddify-manager/.venv313/bin/python -W ignore -m unittest -v test_17_confirm   (from /work/tests; real pipeline, real router)"""
import copy
import hashlib
import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stand_lib import *  # noqa: E402,F403
from test_13_apply import LOG, clean_state, start_cli, wait_done  # noqa: E402
from test_15_full_apply import FULL, LIVE, entrances_ok, enter_stamp, mark, run_ctl, sha  # noqa: E402

STAGED = "/var/lib/multiloc-router-apply/staged"
ACCEPT = CTL + ["apply", "--accept-staged"]
DISCARD = CTL + ["apply", "--discard-staged"]
ALWAYS = FULL + ["--accept-diff"]


def setUpModule():
    prepare_apply_tests()


def tearDownModule():
    drop_probe_rules()
    run_ctl(ALWAYS)
    reset_locations_to_stand_default()
    apply_router()
    ensure_baseline(force=True)


def rd():
    from hiddifypanel.hutils import router_diff
    return router_diff


# ------------------------------------------------------------------------------------------------------------------ the comparison itself (pure functions)
def cfg(custom_domains=3, custom_ips=3, dns=False, probe="1m", extra_other=()):
    rules = [{"type": "field", "domain": [f"domain:site{i}.example"], "outboundTag": "direct-ru"} for i in range(custom_domains)]
    rules += [{"type": "field", "ip": [f"10.{i}.0.0/16"], "outboundTag": "direct-ru"} for i in range(custom_ips)]
    rules += [{"type": "field", "domain": ["regexp:.*\\.ru$"], "outboundTag": "direct-ru"}, {"type": "field", "ip": ["geoip:ru"], "outboundTag": "direct-ru"},
              {"type": "field", "inboundTag": ["from-hiddify-s1"], "balancerTag": "balancer-s1"}, {"type": "field", "network": "tcp,udp", "balancerTag": "upstream-balancer"}]
    rules += list(extra_other)
    c = {"log": {"loglevel": "warning"}, "inbounds": [{"tag": "from-hiddify", "port": 20808, "protocol": "socks"}],
         "outbounds": [{"tag": "upstream-1", "protocol": "vless"}, {"tag": "direct-ru", "protocol": "freedom"}, {"tag": "block", "protocol": "blackhole"}],
         "observatory": {"subjectSelector": ["upstream-1"], "probeInterval": probe},
         "routing": {"domainStrategy": "IPIfNonMatch", "rules": rules, "balancers": [{"tag": "upstream-balancer", "selector": ["upstream-1"], "strategy": {"type": "leastPing"}}]}}
    if dns:
        c["dns"] = {"queryStrategy": "UseIPv4", "servers": [{"address": "77.88.8.8", "domains": ["domain:ru"]}, "tcp://10.77.77.1"]}
    return c


class TestTheComparison(unittest.TestCase):

    def test_the_incident_shape_is_not_explained_by_a_change_of_the_rules(self):
        old = cfg(custom_domains=5, custom_ips=40)                                    # the working config: many RU IP rules, no DNS section
        new = cfg(custom_domains=5, custom_ips=2, dns=True, probe="3m", extra_other=[{"type": "field", "domain": ["domain:blocked.example"], "outboundTag": "block"}])
        v = rd().review(old, new, {"rules"})
        self.assertEqual({d["key"] for d in v["explained"]}, {"rules.custom", "rules.entries"})
        self.assertEqual({d["key"] for d in v["unexplained"]}, {"dns", "observatory"}, "the DNS section and the probe interval are not a consequence of the rules; a plain extra block rule is (rules.entries)")
        text = " | ".join(ln for d in v["unexplained"] for ln in d["lines"])
        self.assertIn("не задана", text)
        self.assertIn("1m → 3m", text)
        self.assertTrue(any("10.77.77.1" in w and "частной сети" in w for w in v["warnings"]), v["warnings"])
        everything = rd().review(old, new, {"rules", "settings", "ui_files"})
        self.assertEqual(everything["unexplained"], [], "when the settings and the ui files changed as well, the same differences are explained")

    def test_equal_configs_have_no_difference_and_the_order_of_other_rules_is_reported(self):
        a = cfg()
        self.assertEqual(rd().compare(a, copy.deepcopy(a)), [])
        b = copy.deepcopy(a)
        r = b["routing"]["rules"]
        r[-1], r[-2] = r[-2], r[-1]
        diffs = rd().compare(a, b)
        self.assertEqual([d["key"] for d in diffs], ["rules.other"])
        self.assertIn("порядок", diffs[0]["lines"][0])

    def test_nodes_countries_and_the_rest_need_their_own_parts_to_be_explained(self):
        a, b = cfg(), cfg()
        b["outbounds"].append({"tag": "upstream-2", "protocol": "vless"})
        b["routing"]["balancers"][0]["selector"].append("upstream-2")
        b["observatory"]["subjectSelector"].append("upstream-2")
        b["inbounds"].append({"tag": "from-hiddify-s1", "port": 20811, "protocol": "socks"})
        b["log"] = {"loglevel": "debug"}
        keys = {d["key"] for d in rd().compare(a, b)}
        self.assertEqual(keys, {"outbounds", "balancers", "observatory", "inbounds", "other"})
        v = rd().review(a, b, {"upstreams"})
        self.assertEqual({d["key"] for d in v["unexplained"]}, {"inbounds", "other"}, "a node change explains the nodes, their balancers and probes - not the entrances or the log")
        self.assertEqual(rd().review(a, b, {"upstreams", "countries", "settings"})["unexplained"], [])
        self.assertEqual({d["key"] for d in rd().review(a, b, set())["unexplained"]}, keys, "nothing is explained when no change is recorded (unknown state)")

    def test_what_counts_as_a_custom_rule(self):
        c = rd().is_custom
        self.assertTrue(c({"type": "field", "domain": ["domain:a.example", "full:b.example"], "outboundTag": "direct-ru"}))
        self.assertTrue(c({"type": "field", "ip": ["10.0.0.0/8"], "outboundTag": "direct-ru"}))
        self.assertFalse(c({"type": "field", "domain": ["regexp:.*\\.ru$"], "outboundTag": "direct-ru"}))
        self.assertFalse(c({"type": "field", "ip": ["geoip:ru"], "outboundTag": "direct-ru"}))
        self.assertFalse(c({"type": "field", "domain": ["domain:a.example"], "outboundTag": "block"}))
        self.assertFalse(c({"type": "field", "inboundTag": ["x"], "domain": ["domain:a.example"], "outboundTag": "direct-ru"}))

    def test_rules_of_the_tunnel_policy_and_their_per_slot_copies_are_explained_by_the_rules_but_other_rules_are_not(self):
        base = cfg()
        own = [{"type": "field", "domain": ["domain:probe.example"], "balancerTag": "upstream-balancer"},
               {"type": "field", "domain": ["domain:probe.example"], "inboundTag": ["from-hiddify-s1"], "balancerTag": "balancer-s1"},
               {"type": "field", "domain": ["domain:probe.example"], "inboundTag": ["from-hiddify-s3"], "outboundTag": "block"}]
        v = rd().review(base, cfg(extra_other=own), {"rules"})
        self.assertEqual([d["key"] for d in v["explained"]], ["rules.entries"])
        self.assertEqual(v["unexplained"], [])
        self.assertIn("добавлено правил: 3", v["explained"][0]["lines"][0])
        foreign = [{"type": "field", "ip": ["geoip:cn"], "outboundTag": "block"}, {"type": "field", "port": "25", "outboundTag": "block"}]
        v = rd().review(base, cfg(extra_other=foreign), {"rules"})
        self.assertEqual({d["key"] for d in v["unexplained"]}, {"rules.other"}, "a geoip / port rule is not made by the owner's custom rules")

    def test_a_compacted_and_a_plain_list_of_the_same_rules_are_not_a_difference(self):
        from hiddifypanel.hutils import router_compact
        plain = cfg(custom_domains=6, custom_ips=9)
        packed = json.loads(json.dumps(plain))
        packed["routing"]["rules"] = router_compact.compact_bulk_rules(packed["routing"]["rules"])
        self.assertLess(len(packed["routing"]["rules"]), len(plain["routing"]["rules"]), "the rules really were joined")
        self.assertEqual(rd().compare(plain, packed), [], "same entries, same order = nothing changed")
        reordered = json.loads(json.dumps(packed))
        r0 = reordered["routing"]["rules"]
        i, j = [k for k, x in enumerate(r0) if x.get("domain") and x.get("outboundTag") == "direct-ru"][0], [k for k, x in enumerate(r0) if x.get("ip") and x.get("outboundTag") == "direct-ru"][0]
        r0[i]["domain"] = list(reversed(r0[i]["domain"]))
        self.assertEqual([d["key"] for d in rd().compare(plain, reordered)], ["rules.custom"], "another ORDER of the entries is a difference")
        self.assertIsNotNone(j)

    def test_a_changed_domain_list_of_the_dns_servers_is_explained_by_the_rules_but_nothing_else_about_dns_is(self):
        old = cfg(custom_domains=3, dns=True)
        new = cfg(custom_domains=4, dns=True)
        new["dns"]["servers"][0]["domains"] = ["domain:ru", "domain:site3.example"]                  # the layer collects the domain rules into the server
        v = rd().review(old, new, {"rules"})
        self.assertEqual([d["key"] for d in v["explained"]], ["rules.custom", "dns.domains"])
        self.assertEqual(v["unexplained"], [])
        v = rd().review(old, new, {"upstreams"})
        self.assertIn("dns.domains", {d["key"] for d in v["unexplained"]}, "only a change of the rules (or the settings) explains it")
        other = cfg(custom_domains=3, dns=True)
        other["dns"]["servers"][1] = "tcp://8.8.8.8"
        v = rd().review(old, other, {"rules"})
        self.assertEqual({d["key"] for d in v["unexplained"]}, {"dns"}, "other servers are not a consequence of the rules")

    def test_a_dns_server_inside_a_private_network_is_warned_about(self):
        w = rd().warnings_for(cfg(dns=True))
        self.assertEqual(len(w), 1)
        self.assertEqual(rd().warnings_for(cfg()), [])


# ------------------------------------------------------------------------------------------------------------------ the real pipeline on the real router
def strip_dns_from_live():
    """the production situation: the working config never went through the layers that add the DNS section"""
    c = json.load(open(LIVE))
    c.pop("dns", None)
    with open(LIVE, "w") as f:
        f.write(json.dumps(c, indent=2) + "\n")


def staged_meta():
    return json.load(open(STAGED + "/meta.json")) if os.path.exists(STAGED + "/meta.json") else None


class TestKeptForADecision(unittest.TestCase):

    def setUp(self):
        prepare_apply_tests()
        clean_state()
        run_ctl(ALWAYS)                                       # the live config = what the pipeline builds from the database
        self.addCleanup(self.converge)

    @staticmethod
    def converge():
        drop_probe_rules()
        run_ctl(DISCARD)
        run_ctl(ALWAYS)

    def test_a_change_only_in_the_rules_is_applied_by_itself_with_one_restart(self):
        add_probe_rule("confirm-probe.example")
        t0 = mark()
        r = run_ctl(FULL)
        self.assertEqual(r.returncode, 0, r.stdout[-1200:])
        self.assertIn("explained by your change", r.stdout)
        self.assertEqual(router_starts_since(t0), 1)
        self.assertFalse(os.path.exists(STAGED))
        self.assertEqual(plan_now()["action"], "none")

    def test_a_difference_the_change_does_not_explain_is_kept_and_the_router_is_not_touched(self):
        strip_dns_from_live()
        add_probe_rule("confirm-probe.example")
        h0, e0, t0 = sha(), enter_stamp(), mark()
        r = run_ctl(FULL)
        self.assertEqual(r.returncode, 4, r.stdout[-1500:] + r.stderr[-500:])
        self.assertIn("NOT APPLIED", r.stdout)
        self.assertIn("Секция DNS", r.stdout)
        self.assertEqual((sha(), enter_stamp(), router_starts_since(t0)), (h0, e0, 0), "the running router and its file were not touched")
        m = staged_meta()
        self.assertTrue(m and any("Секция DNS" in ln for ln in m["unexplained"]), m)
        self.assertTrue(any(("Правила «напрямую (РФ)»" in ln or "Правила по адресам и доменам" in ln) for ln in m["explained"]), "the owner's own change is listed as explained")
        self.assertEqual(oct(os.stat(STAGED + "/config.json").st_mode & 0o777), "0o600", "the kept config holds credentials: root only")
        pl = plan_now()
        self.assertEqual(pl["action"], "confirm", pl)
        self.assertIn("ждёт вашего решения", pl["text"])

    def test_accept_applies_exactly_the_kept_config_with_one_restart(self):
        strip_dns_from_live()
        add_probe_rule("confirm-probe.example")
        self.assertEqual(run_ctl(FULL).returncode, 4)
        kept = open(STAGED + "/config.json", "rb").read()
        t0 = mark()
        r = run_ctl(ACCEPT)
        self.assertEqual(r.returncode, 0, r.stdout[-1200:])
        self.assertEqual(open(LIVE, "rb").read(), kept, "exactly what was kept is now the running config")
        self.assertEqual(router_starts_since(t0), 1)
        self.assertFalse(os.path.exists(STAGED))
        self.assertEqual(plan_now()["action"], "none")
        self.assertEqual(entrances_ok(), 5)

    def test_discard_leaves_the_running_config_and_the_change_pending(self):
        strip_dns_from_live()
        add_probe_rule("confirm-probe.example")
        self.assertEqual(run_ctl(FULL).returncode, 4)
        h0, t0 = sha(), mark()
        r = run_ctl(DISCARD)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertFalse(os.path.exists(STAGED))
        self.assertEqual((sha(), router_starts_since(t0)), (h0, 0))
        self.assertEqual(plan_now()["action"], "full", "the edit stays unapplied")

    def test_a_kept_config_is_refused_when_the_inputs_changed_after_it_was_built(self):
        strip_dns_from_live()
        add_probe_rule("confirm-probe.example")
        self.assertEqual(run_ctl(FULL).returncode, 4)
        mysql("update commercial_routing_custom_rule set value='confirm-probe-2.example', normalized_value='confirm-probe-2.example' where comment='%s'" % PROBE_RULE_COMMENT)
        h0 = sha()
        r = run_ctl(ACCEPT)
        self.assertEqual(r.returncode, 2, r.stdout[-600:])
        self.assertIn("REFUSED", r.stdout)
        self.assertEqual(sha(), h0)
        self.assertFalse(os.path.exists(STAGED), "a stale kept config is thrown away")

    def test_a_kept_config_is_refused_when_the_running_config_changed_after_it_was_built(self):
        strip_dns_from_live()
        add_probe_rule("confirm-probe.example")
        self.assertEqual(run_ctl(FULL).returncode, 4)
        c = json.load(open(LIVE))
        c["log"]["loglevel"] = "info"
        with open(LIVE, "w") as f:
            f.write(json.dumps(c, indent=2) + "\n")
        h0 = sha()
        r = run_ctl(ACCEPT)
        self.assertEqual(r.returncode, 2, r.stdout[-600:])
        self.assertIn("REFUSED", r.stdout)
        self.assertEqual(sha(), h0)
        self.assertFalse(os.path.exists(STAGED))

    def test_accept_diff_on_the_console_applies_whatever_the_pipeline_built(self):
        strip_dns_from_live()
        add_probe_rule("confirm-probe.example")
        t0 = mark()
        r = run_ctl(ALWAYS)
        self.assertEqual(r.returncode, 0, r.stdout[-800:])
        self.assertEqual(router_starts_since(t0), 1)
        self.assertFalse(os.path.exists(STAGED))
        self.assertIn(b'"dns"', open(LIVE, "rb").read())

    def test_a_new_run_that_finds_no_difference_drops_an_old_kept_config(self):
        strip_dns_from_live()
        add_probe_rule("confirm-probe.example")
        self.assertEqual(run_ctl(FULL).returncode, 4)
        self.assertEqual(run_ctl(ALWAYS).returncode, 0)
        drop_probe_rules()
        self.assertEqual(run_ctl(FULL).returncode, 0)
        self.assertFalse(os.path.exists(STAGED), "nothing waits any more")


class TestInThePanel(unittest.TestCase):
    """the background service and the two buttons"""

    def setUp(self):
        prepare_apply_tests()
        clean_state()
        run_ctl(ALWAYS)
        self.addCleanup(TestKeptForADecision.converge)
        from test_07_ui import admin_session, ROUTING
        self.s, self.ROUTING = admin_session(), ROUTING

    def status(self):
        return self.s.get(self.ROUTING + "apply-status/", timeout=60).json()

    def test_the_service_reports_needs_confirm_and_the_buttons_accept_or_discard(self):
        strip_dns_from_live()
        add_probe_rule("confirm-probe.example")
        r = start_cli("full")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        st = wait_done(timeout=300)
        self.assertEqual((st["state"], st["rc"]), ("needs-confirm", 4), st)
        d = self.status()
        self.assertEqual(d["plan"]["action"], "confirm")
        self.assertTrue(d["staged"] and d["staged"]["valid"])
        self.assertTrue(any("Секция DNS" in ln for ln in d["staged"]["unexplained"]), d["staged"])
        html = self.s.get(self.ROUTING, timeout=60).text
        self.assertIn('id="apply-staged"', html)
        self.assertIn("Применить новый конфиг", html)
        self.assertIn("Не применять", html)
        # accept
        t0 = mark()
        resp = self.s.post(self.ROUTING + "apply-accept/", headers={"Referer": self.ROUTING}, timeout=90)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Применение собранного конфига запущено", resp.text)
        st = wait_done(timeout=300)
        self.assertEqual((st["state"], st["rc"]), ("ok", 0), st)
        self.assertEqual(router_starts_since(t0), 1)
        self.assertIsNone(self.status()["staged"])
        self.assertEqual(plan_now()["action"], "none")

    def test_discard_from_the_page_and_nobody_but_an_admin_can_decide(self):
        strip_dns_from_live()
        add_probe_rule("confirm-probe.example")
        self.assertEqual(start_cli("full").returncode, 0)
        self.assertEqual(wait_done(timeout=300)["state"], "needs-confirm")
        import requests
        anon = requests.Session()
        anon.verify = "/etc/stand/ca.crt"
        for path in ("apply-accept/", "apply-discard/"):
            r = anon.post(self.ROUTING + path, timeout=30, allow_redirects=False)
            self.assertIn(r.status_code, (301, 302, 303, 307, 401, 403, 404), path)
        self.assertTrue(os.path.exists(STAGED), "an anonymous request decided nothing")
        h0 = sha()
        resp = self.s.post(self.ROUTING + "apply-discard/", headers={"Referer": self.ROUTING}, timeout=90)
        self.assertIn("отклонён", resp.text)
        wait_done(timeout=120)
        self.assertFalse(os.path.exists(STAGED))
        self.assertEqual(sha(), h0)
        self.assertEqual(self.status()["plan"]["action"], "full")


if __name__ == "__main__":
    unittest.main(verbosity=2)
