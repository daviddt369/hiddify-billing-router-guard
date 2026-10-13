"""Unit tests: router layer (pure function), helpers, subscription entry builder (DB-backed, in-process). Run with the panel python:
   /opt/hiddify-manager/.venv313/bin/python -m unittest -v test_01_unit        (from /work/tests)
"""
import copy
import json
import os
import re
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("HIDDIFY_CFG_PATH", "/opt/hiddify-manager/hiddify-panel/app.cfg")
os.environ.setdefault("HIDDIFY_CONFIG_PATH", "/opt/hiddify-manager/")
os.chdir("/opt/hiddify-manager/hiddify-panel")

import sqlalchemy  # noqa: E402
import hiddifypanel.panel.init_db as _idb  # noqa: E402
_idb.text = getattr(_idb, "text", sqlalchemy.text)
from hiddifypanel import create_app  # noqa: E402
from hiddifypanel.hutils import multiloc as ml, multiloc_router as mr  # noqa: E402

APP = None
CTX = None


def setUpModule():
    global APP, CTX
    APP = create_app(app_mode="cli")
    CTX = APP.app_context()
    CTX.push()


def tearDownModule():
    CTX.pop()


# --------------------------------------------------------------------------------------------- fixtures
def legacy_router(ids=(4, 5, 6, 10, 11, 15), n_ru=50, balancer=True, single_final=False, observatory=True):
    """a router config shaped like the output of the existing pipeline (gov block, DNS, custom RU rules, base RU, final)"""
    outs = [{"tag": f"upstream-{i}", "protocol": "vless", "settings": {"vnext": []}} for i in ids]
    outs += [{"tag": "direct-ru", "protocol": "freedom"}, {"tag": "block", "protocol": "blackhole"}]
    rules = [
        {"type": "field", "domain": ["domain:blocked.example"], "outboundTag": "block"},
        {"type": "field", "ip": ["77.88.8.8"], "outboundTag": "direct-ru"},
        {"type": "field", "ip": ["1.1.1.1", "8.8.8.8"], **({"balancerTag": "upstream-balancer"} if balancer else {"outboundTag": f"upstream-{ids[0]}"})},
    ]
    for i in range(n_ru):
        rules.append({"type": "field", "domain": [f"domain:ru{i}.example"], "outboundTag": "direct-ru"} if i % 2 else
                     {"type": "field", "ip": [f"10.{i % 250}.{i // 250}.0/24"], "outboundTag": "direct-ru"})
    rules += [{"type": "field", "domain": ["regexp:.*\\.ru$"], "outboundTag": "direct-ru"},
              {"type": "field", "ip": ["geoip:ru"], "outboundTag": "direct-ru"}]
    if balancer and not single_final:
        rules.append({"type": "field", "network": "tcp,udp", "balancerTag": "upstream-balancer"})
    else:
        rules.append({"type": "field", "network": "tcp,udp", "outboundTag": f"upstream-{ids[0]}"})
    cfg = {
        "log": {"loglevel": "warning"},
        "inbounds": [{"tag": "from-hiddify", "listen": "127.0.0.1", "port": 20808, "protocol": "socks",
                      "settings": {"auth": "noauth", "udp": True, "ip": "127.0.0.1"},
                      "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"], "routeOnly": True}}],
        "outbounds": outs,
        "routing": {"domainStrategy": "IPIfNonMatch", "rules": rules},
    }
    if balancer:
        cfg["routing"]["balancers"] = [{"tag": "upstream-balancer", "selector": [f"upstream-{i}" for i in ids],
                                        "strategy": {"type": "leastPing"}}]
    if observatory and balancer:
        cfg["observatory"] = {"subjectSelector": [f"upstream-{i}" for i in ids], "probeUrl": "https://x/", "probeInterval": "1m",
                              "enableConcurrency": True}
    return cfg


def plan(de=(4, 10, 6), us=(5, 11, 15), s3=(), s4=(), de_on=True, us_on=True):
    return {"base_port": 20808, "slots": {1: {"enabled": de_on, "upstream_ids": list(de)},
                                           2: {"enabled": us_on, "upstream_ids": list(us)},
                                           3: {"enabled": False, "upstream_ids": list(s3)},
                                           4: {"enabled": False, "upstream_ids": list(s4)}}}


def installer_module():
    """the installer as a module (its decision functions are pure): loaded from the very file the stand runs"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("multiloc_installer_under_test", "/work/src/multiloc_installer.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def rules_of(cfg):
    return cfg["routing"]["rules"]


def idx_final(rules):
    return max(i for i, r in enumerate(rules) if mr._is_catch_all(r))


# --------------------------------------------------------------------------------------------- helpers
class TestHelpers(unittest.TestCase):
    def test_flags(self):
        self.assertEqual(ml.flag_for("DE"), "\U0001F1E9\U0001F1EA")
        self.assertEqual(ml.flag_for("us"), "\U0001F1FA\U0001F1F8")
        self.assertEqual(ml.flag_for(""), "")
        self.assertEqual(ml.flag_for("D1"), "")
        self.assertEqual(ml.flag_for("DEU"), "")

    def test_new_path_shape_and_entropy(self):
        seen = set()
        for _ in range(500):
            p = ml.new_path()
            self.assertRegex(p, r"^[A-Za-z0-9]{22}$")
            seen.add(p)
        self.assertEqual(len(seen), 500)

    def test_check_paths_prefix_and_reserved(self):
        self.assertEqual(ml.check_paths(["Abcdefghijkl1", "Zyxwvutsrqpo2"]), [])
        self.assertTrue(ml.check_paths(["Abcdefghijkl1", "Abcdefghijkl1xx"]))            # prefix
        self.assertTrue(ml.check_paths(["Abcdefghijkl1", "Abcdefghijkl1"]))              # duplicate
        self.assertTrue(ml.check_paths(["short"]))                                       # too short
        self.assertTrue(ml.check_paths(["Abcdefghijkl1"], reserved=["Abcdefghijkl1/panel"]))
        self.assertTrue(ml.check_paths(["Abcdefghijkl1xxxx"], reserved=["Abcdefghijkl1"]))
        self.assertEqual(ml.check_paths(["Qqqqqqqqqqqq1"], reserved=["Rrrrrrrrrrrr"]), [])

    def test_tags_and_ports(self):
        self.assertEqual(ml.inbound_tag(2, "xhttp"), "v10-vless-xhttp-loc2")
        self.assertEqual(ml.inbound_tag(3, "ws"), "v10-vless-ws-loc3")
        self.assertEqual(ml.inbound_tag(4, "trojan"), "v10-trojan-ws-loc4")
        for slot in range(1, 5):                                   # xray_api.__add_uuid_to_tag detects protocol by SUBSTRING
            for kind in ml.INBOUNDS:
                t = ml.inbound_tag(slot, kind)
                self.assertTrue(("vless" in t) != ("trojan" in t) or kind == "trojan", t)
                self.assertNotIn("reality", t)
                self.assertNotIn("quic", t)
        self.assertEqual([ml.router_port(s) for s in range(1, 5)], [20811, 20812, 20813, 20814])

    def test_validate_location(self):
        class R:  # minimal row
            def __init__(self, **kw):
                self.kind, self.slot, self.visibility, self.code, self.country_iso, self.enabled, self.title = "country", 1, "testers", "de", "DE", True, "X"
                self.__dict__.update(kw)
        self.assertEqual(ml.validate_location(R()), [])
        self.assertTrue(ml.validate_location(R(slot=5)))
        self.assertTrue(ml.validate_location(R(visibility="everyone")))
        self.assertTrue(ml.validate_location(R(code="DE!")))
        self.assertTrue(ml.validate_location(R(country_iso="DEU")))
        self.assertTrue(ml.validate_location(R(title="  ")))
        self.assertTrue(ml.validate_location(R(title="x" * 65)))
        self.assertEqual(ml.validate_location(R(title="x" * 64)), [])
        self.assertTrue(ml.validate_location(R(flag="f" * 17)))
        self.assertTrue(ml.validate_location(R(kind="auto", slot=1)))
        self.assertEqual(ml.validate_location(R(kind="auto", slot=0)), [])


# --------------------------------------------------------------------------------------------- router transform
class TestRouterTransform(unittest.TestCase):
    def setUp(self):
        self.base = legacy_router()

    def test_no_slots_is_identity(self):
        self.assertEqual(mr.transform(self.base, {"slots": {}}), self.base)
        self.assertEqual(mr.transform(self.base, {"slots": {}, "base_port": 20808}), self.base)

    def test_structure_and_order(self):
        out = mr.transform(self.base, plan())
        self.assertEqual(mr.check_invariants(out), [])
        r = rules_of(out)
        fi = idx_final(r)
        self.assertEqual(r[fi], {"type": "field", "network": "tcp,udp", "balancerTag": "upstream-balancer"})
        finals = [x for x in r if mr._is_ours(x) and not (x.get("ip") or x.get("domain"))]
        self.assertEqual(len(finals), 4)
        # every legacy direct-ru rule is BEFORE every slot final, which are right before the generic final
        last_ru = max(i for i, x in enumerate(r) if x.get("outboundTag") == "direct-ru")
        first_slot = min(i for i, x in enumerate(r) if mr._is_ours(x) and not (x.get("ip") or x.get("domain")))
        self.assertLess(last_ru, first_slot)
        self.assertEqual(r[fi - 4:fi], finals)
        # targets
        by_in = {x["inboundTag"][0]: x for x in finals}
        self.assertEqual(by_in["from-hiddify-s1"]["balancerTag"], "balancer-s1")
        self.assertEqual(by_in["from-hiddify-s2"]["balancerTag"], "balancer-s2")
        self.assertEqual(by_in["from-hiddify-s3"]["outboundTag"], "block")
        self.assertEqual(by_in["from-hiddify-s4"]["outboundTag"], "block")
        # inbounds
        ports = {i["tag"]: i["port"] for i in out["inbounds"]}
        self.assertEqual(ports, {"from-hiddify": 20808, "from-hiddify-s1": 20811, "from-hiddify-s2": 20812,
                                 "from-hiddify-s3": 20813, "from-hiddify-s4": 20814})
        for i in out["inbounds"]:
            self.assertEqual(i["sniffing"], self.base["inbounds"][0]["sniffing"])
        # balancers: exact membership, leastPing, NO fallback (fail closed)
        b = {x["tag"]: x for x in out["routing"]["balancers"]}
        self.assertEqual(sorted(b["balancer-s1"]["selector"]), ["upstream-10", "upstream-4", "upstream-6"])
        self.assertEqual(sorted(b["balancer-s2"]["selector"]), ["upstream-11", "upstream-15", "upstream-5"])
        for t in ("balancer-s1", "balancer-s2"):
            self.assertEqual(b[t]["strategy"], {"type": "leastPing"})
            self.assertNotIn("fallbackTag", b[t])
        self.assertIn("upstream-balancer", b)
        # the legacy part is untouched: removing our additions gives back the original
        self.assertEqual(mr.strip(out), self.base)

    def test_dns_rules_are_duplicated_per_slot(self):
        out = mr.transform(self.base, plan())
        r = rules_of(out)
        orig = [i for i, x in enumerate(r) if x.get("ip") == ["1.1.1.1", "8.8.8.8"] and not x.get("inboundTag")]
        self.assertEqual(len(orig), 1)
        copies = [x for x in r if x.get("ip") == ["1.1.1.1", "8.8.8.8"] and x.get("inboundTag")]
        self.assertEqual(len(copies), 4)
        self.assertTrue(all(i < orig[0] for i, x in enumerate(r) if x in copies))      # copies come BEFORE the original
        tgt = {c["inboundTag"][0]: c.get("balancerTag") or c.get("outboundTag") for c in copies}
        self.assertEqual(tgt, {"from-hiddify-s1": "balancer-s1", "from-hiddify-s2": "balancer-s2",
                               "from-hiddify-s3": "block", "from-hiddify-s4": "block"})

    def test_single_node_uses_outbound_and_empty_is_block(self):
        out = mr.transform(self.base, plan(de=(4,), us=(), de_on=True, us_on=True))
        self.assertEqual(mr.check_invariants(out), [])
        fin = {x["inboundTag"][0]: x for x in rules_of(out) if mr._is_ours(x) and not (x.get("ip") or x.get("domain"))}
        self.assertEqual(fin["from-hiddify-s1"]["outboundTag"], "upstream-4")
        self.assertEqual(fin["from-hiddify-s2"]["outboundTag"], "block")                 # enabled but no node -> closed
        self.assertEqual([b["tag"] for b in out["routing"]["balancers"]], ["upstream-balancer"])

    def test_disabled_slot_blocks_even_with_nodes(self):
        out = mr.transform(self.base, plan(de_on=False))
        fin = {x["inboundTag"][0]: x for x in rules_of(out) if mr._is_ours(x) and not (x.get("ip") or x.get("domain"))}
        self.assertEqual(fin["from-hiddify-s1"]["outboundTag"], "block")
        self.assertNotIn("balancer-s1", [b["tag"] for b in out["routing"].get("balancers", [])])

    def test_unknown_or_blackhole_nodes_are_ignored(self):
        cfg = legacy_router()
        cfg["outbounds"].append({"tag": "upstream-99", "protocol": "blackhole"})
        out = mr.transform(cfg, plan(de=(4, 99, 12345), us=(5,)))
        fin = {x["inboundTag"][0]: x for x in rules_of(out) if mr._is_ours(x) and not (x.get("ip") or x.get("domain"))}
        self.assertEqual(fin["from-hiddify-s1"]["outboundTag"], "upstream-4")            # 99 is a blackhole, 12345 does not exist
        self.assertEqual(fin["from-hiddify-s2"]["outboundTag"], "upstream-5")

    def test_idempotent_and_off_restores_legacy(self):
        p = plan()
        once = mr.transform(self.base, p)
        twice = mr.transform(once, p)
        self.assertEqual(once, twice)
        self.assertEqual(mr.transform(once, {"slots": {}}), self.base)                   # feature off -> exact legacy config
        # plan change is applied on top of an already transformed config
        again = mr.transform(once, plan(de=(4,)))
        self.assertEqual(mr.check_invariants(again), [])
        self.assertEqual(mr.strip(again), self.base)

    def test_prefix_hazard_is_detected(self):
        """Xray matches balancer selectors by PREFIX: 'upstream-1' would also pull in upstream-10 / upstream-11"""
        cfg = legacy_router(ids=(1, 10, 11, 2))
        out = mr.transform(cfg, plan(de=(1, 2), us=(10, 11)))
        problems = mr.check_invariants(out)
        self.assertTrue(any("prefix" in p for p in problems), problems)
        # while the production-like id set is clean
        self.assertEqual(mr.check_invariants(mr.transform(legacy_router(), plan())), [])

    def test_observatory_created_or_extended(self):
        cfg = legacy_router(observatory=False)                                           # balancer but no observatory (odd, but guard it)
        out = mr.transform(cfg, dict(plan(), probe_url="http://p/", probe_interval="5s"))
        self.assertEqual(out["observatory"]["probeUrl"], "http://p/")
        self.assertEqual(out["observatory"]["probeInterval"], "5s")
        self.assertEqual(mr.check_invariants(out), [])
        cfg2 = legacy_router()
        cfg2["observatory"]["subjectSelector"] = ["upstream-4"]                          # selector doesn't cover node 10
        out2 = mr.transform(cfg2, plan(de=(4, 10)))
        self.assertIn("upstream-10", out2["observatory"]["subjectSelector"])

    def test_single_upstream_final_is_still_recognised(self):
        cfg = legacy_router(ids=(4,), balancer=False)
        out = mr.transform(cfg, plan(de=(4,), us=()))
        r = rules_of(out)
        self.assertEqual(r[idx_final(r)], {"type": "field", "network": "tcp,udp", "outboundTag": "upstream-4"})
        self.assertEqual(mr.check_invariants(out), [])

    def test_invariants_catch_regressions(self):
        out = mr.transform(self.base, plan())
        bad = copy.deepcopy(out)
        bad["routing"]["balancers"][1]["fallbackTag"] = "upstream-4"
        self.assertTrue(any("fallback" in p for p in mr.check_invariants(bad)))
        bad2 = copy.deepcopy(out)
        r = rules_of(bad2)
        ru = next(x for x in r if x.get("outboundTag") == "direct-ru" and x.get("ip") == ["geoip:ru"])
        r.remove(ru)
        r.append(ru)                                                                       # RU rule AFTER the generic final
        self.assertTrue(mr.check_invariants(bad2))
        bad3 = copy.deepcopy(out)
        rules_of(bad3)[idx_final(rules_of(bad3)) - 1]["balancerTag"] = "balancer-nope"
        self.assertTrue(any("missing balancer" in p for p in mr.check_invariants(bad3)))

    def test_production_sized_config_15k_rules(self):
        big = legacy_router(n_ru=15076)
        t0 = time.monotonic()
        out = mr.transform(big, plan())
        dt = time.monotonic() - t0
        self.assertLess(dt, 2.5, f"transform took {dt:.2f}s")
        self.assertEqual(mr.check_invariants(out), [])
        r = rules_of(out)
        last_ru = max(i for i, x in enumerate(r) if x.get("outboundTag") == "direct-ru")
        first_slot = min(i for i, x in enumerate(r) if mr._is_ours(x) and not (x.get("ip") or x.get("domain")))
        self.assertLess(last_ru, first_slot)
        self.assertEqual(len(r), len(rules_of(big)) + 4 + 4)                               # 4 finals + 4 DNS copies
        self.assertEqual(mr.strip(out), big)

    def test_real_xray_accepts_the_transformed_config(self):
        """xray run -test on a config produced from the REAL renderer + our layer (valid vless/xhttp outbounds)"""
        import subprocess
        import tempfile
        from hiddifypanel.hutils.proxy import router_core
        ups = [{"id": i, "tunnel_type": "vless",
                "vless_uri": f"vless://aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa@127.0.0.{i}:9443?type=xhttp&security=tls&sni=x.test&alpn=h2&path=%2Fp{i}&encryption=none"}
               for i in (4, 5, 6, 10, 11, 15)]
        base = router_core.render_xray_router_config({"commercial_router_port": 20808}, [], ups)
        out = mr.transform(base, plan())
        self.assertEqual(mr.check_invariants(out), [])
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(out, f)
        r = subprocess.run(["/opt/hiddify-manager/xray/bin/xray", "run", "-test", "-config", f.name], capture_output=True, text=True)
        os.unlink(f.name)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Configuration OK", r.stdout + r.stderr)

    def test_the_installers_layer_free_view_agrees_with_the_layer(self):
        """the installer decides "only the feature's entrances differ" with its own copy of strip() (it must not import code it is about to
        remove): both must agree on everything transform() produces, and a change that is not the layer's must be visible"""
        import importlib.util
        spec = importlib.util.spec_from_file_location("multiloc_installer_under_test", "/work/src/multiloc_installer.py")
        inst = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(inst)
        cases = [(legacy_router(), plan()), (legacy_router(), plan(de=(4,), us=())), (legacy_router(), plan(de_on=False)),
                 (legacy_router(observatory=False), dict(plan(), probe_url="http://p/", probe_interval="5s")),
                 (legacy_router(ids=(4,), balancer=False), plan(de=(4,), us=())), (legacy_router(), {"slots": {}})]
        for base, p in cases:
            out = mr.transform(base, p)
            self.assertEqual(inst.layer_free(out), inst.layer_free(base))
            self.assertEqual(inst.layer_free(out), inst.layer_free(mr.strip(out)))
        foreign_rule = mr.transform(legacy_router(), plan())
        foreign_rule["routing"]["rules"].insert(0, {"type": "field", "domain": ["domain:foreign.example"], "outboundTag": "block"})
        self.assertNotEqual(inst.layer_free(foreign_rule), inst.layer_free(legacy_router()))
        foreign_outbound = mr.transform(legacy_router(), plan())
        foreign_outbound["outbounds"].append({"tag": "upstream-777", "protocol": "freedom"})
        self.assertNotEqual(inst.layer_free(foreign_outbound), inst.layer_free(legacy_router()))

    def test_the_installers_divergence_check_explains_the_layer_and_nothing_else(self):
        """foreign_changes(): exactly what transform() does is explained (entrances, rules, balancers; an observatory created or with selectors
        APPENDED); every other edit - also inside the observatory - is somebody else's and makes the rollback leave the router config alone"""
        inst = installer_module()
        cases = [(legacy_router(), plan()), (legacy_router(), plan(de=(4,), us=())), (legacy_router(), plan(de_on=False)),
                 (legacy_router(observatory=False), dict(plan(), probe_url="http://p/", probe_interval="5s")),
                 (legacy_router(), dict(plan(), probe_url="http://p/")), (legacy_router(ids=(4,), balancer=False), plan(de=(4,), us=()))]
        for base, p in cases:
            out = mr.transform(base, p)
            self.assertEqual(inst.foreign_changes(out, base), [], "the layer's own output must be explained completely")
            self.assertEqual(inst.foreign_changes(mr.transform(out, p), base), [], "... also after a second pass")
        base = legacy_router()
        ours = mr.transform(base, plan())

        def edited(fn):
            cfg = copy.deepcopy(ours)
            fn(cfg)
            return inst.foreign_changes(cfg, base)

        self.assertTrue(edited(lambda c: c["observatory"].__setitem__("probeInterval", "37s")))
        self.assertTrue(edited(lambda c: c["observatory"].__setitem__("probeUrl", "http://elsewhere/")))
        self.assertTrue(edited(lambda c: c["observatory"].__setitem__("enableConcurrency", False)))
        self.assertTrue(edited(lambda c: c["observatory"].__setitem__("extra", 1)))
        self.assertTrue(edited(lambda c: c["observatory"].__setitem__("subjectSelector", c["observatory"]["subjectSelector"][1:])))       # an original entry removed
        self.assertTrue(edited(lambda c: c["observatory"].__setitem__("subjectSelector", list(reversed(c["observatory"]["subjectSelector"])))))
        self.assertTrue(edited(lambda c: c.pop("observatory")))
        self.assertTrue(edited(lambda c: c["routing"]["rules"].insert(0, {"type": "field", "domain": ["domain:x.example"], "outboundTag": "block"})))
        self.assertTrue(edited(lambda c: c["outbounds"].append({"tag": "upstream-777", "protocol": "freedom"})))
        extended = copy.deepcopy(ours)
        extended["observatory"]["subjectSelector"].append("upstream-99")                                  # appended selectors are the layer's way
        self.assertEqual(inst.foreign_changes(extended, base), [])
        no_obs = legacy_router(observatory=False)
        created = mr.transform(no_obs, plan())
        self.assertEqual(inst.foreign_changes(created, no_obs), [])                                       # created by the layer
        weird = copy.deepcopy(created)
        weird["observatory"]["foo"] = 1                                                                   # ... but with a key the layer never writes
        self.assertTrue(inst.foreign_changes(weird, no_obs))


class TestGroupConfig(unittest.TestCase):
    """_group_config(): one full xray config = members + leastPing balancer + burstObservatory (pure function)"""

    BASE = {"remarks": "base", "log": {"loglevel": "warning"}, "inbounds": [{"tag": "socks", "port": 10808, "protocol": "socks"}],
            "outbounds": [{"tag": "fragment", "protocol": "freedom"}, {"tag": "direct", "protocol": "freedom"}, {"tag": "block", "protocol": "blackhole"}],
            "routing": {"domainStrategy": "AsIs", "rules": [
                {"type": "field", "inboundTag": ["api"], "outboundTag": "api"},
                {"type": "field", "outboundTag": "direct", "ip": ["geoip:private"]},
                {"type": "field", "port": "0-65535", "outboundTag": "proxy"}]}}
    MEMBERS = [{"tag": "RU_GLOBAL x", "protocol": "vless", "settings": {}}, {"tag": "RU_GLOBAL y", "protocol": "trojan", "settings": {}},
               {"tag": "RU_GLOBAL z", "protocol": "vless", "settings": {}}]
    PROBE = ("http://probe.test/", "1m")

    def build(self, label="🟢 AUTO", base=None):
        return ml._group_config(copy.deepcopy(base or self.BASE), copy.deepcopy(self.MEMBERS), label, "loc0-balancer", self.PROBE)

    def test_members_are_renamed_and_come_first_and_service_outbounds_follow(self):
        c = self.build()
        self.assertEqual([o["tag"] for o in c["outbounds"]], ["🟢 AUTO", "🟢 AUTO-2", "🟢 AUTO-3", "fragment", "direct", "block"])
        self.assertEqual(c["remarks"], "🟢 AUTO")
        self.assertEqual([o["protocol"] for o in c["outbounds"][:3]], ["vless", "trojan", "vless"])

    def test_balancer_selector_fallback_and_observatory(self):
        c = self.build()
        bal = c["routing"]["balancers"]
        self.assertEqual(bal, [{"tag": "loc0-balancer", "selector": ["🟢 AUTO"], "strategy": {"type": "leastPing"}, "fallbackTag": "🟢 AUTO"}])
        self.assertEqual(c["burstObservatory"], {"subjectSelector": ["🟢 AUTO"], "pingConfig": {
            "destination": "http://probe.test/", "connectivity": "", "interval": "1m", "sampling": 3, "timeout": "5s"}})

    def test_catch_all_goes_to_the_balancer_and_other_rules_are_untouched(self):
        c = self.build()
        rules = c["routing"]["rules"]
        self.assertEqual(rules[:2], self.BASE["routing"]["rules"][:2])
        self.assertEqual(rules[2], {"type": "field", "port": "0-65535", "balancerTag": "loc0-balancer"})
        self.assertFalse([r for r in rules if r.get("outboundTag") == "proxy"])

    def test_a_base_without_the_proxy_catch_all_still_gets_one(self):
        base = copy.deepcopy(self.BASE)
        base["routing"]["rules"] = base["routing"]["rules"][:2]
        rules = self.build(base=base)["routing"]["rules"]
        self.assertEqual(rules[-1], {"type": "field", "port": "0-65535", "balancerTag": "loc0-balancer"})

    def test_inputs_are_not_mutated(self):
        base, members = copy.deepcopy(self.BASE), copy.deepcopy(self.MEMBERS)
        ml._group_config(base, members, "x", "b", self.PROBE)
        self.assertEqual(base, self.BASE)
        self.assertEqual(members, self.MEMBERS)

    def test_the_selector_prefix_matches_the_members_only(self):
        for label in ("🟢 AUTO", "🇩🇪 Германия", "🇺🇸 США"):
            c = self.build(label)
            tags = [o["tag"] for o in c["outbounds"]]
            sel = c["routing"]["balancers"][0]["selector"]
            self.assertEqual([t for t in tags if any(t.startswith(s) for s in sel)], tags[:3], label)

    def test_supported_protocols_exclude_what_one_xray_config_cannot_run(self):
        self.assertEqual(ml.XRAY_JSON_PROTOS, {"vless", "trojan", "vmess", "shadowsocks"})
        for p in ("tuic", "hysteria2", "naive", "mieru", "wireguard"):
            self.assertNotIn(p, ml.XRAY_JSON_PROTOS)

    def test_only_the_dedicated_happ_link_and_the_panel_list_see_locations_never_by_user_agent(self):
        U = "22222222-2222-4222-8222-222222222222"

        def ask(path, ua="Happ/3.5.0"):
            with APP.test_request_context(path, headers={"User-Agent": ua}):
                return ml.layout_client(), ml.location_client()
        for ua in ("Happ/3.5.0", "INCY/1.0", "HiddifyNext/2.0", "v2RayTun/1.0", "v2rayNG/1.8.5", "Mozilla/5.0", ""):
            self.assertEqual(ask(f"/pc/{U}/happ/", ua), (True, True), ua)
            self.assertEqual(ask(f"/pc/{U}/happ", ua), (True, True), ua)
            for path in (f"/pc/{U}/", f"/pc/{U}/xray/", f"/pc/{U}/sub/", f"/pc/{U}/sub64/", f"/pc/{U}/auto/", f"/pc/{U}/singbox/", f"/pc/{U}/clash/", f"/pc/{U}/clashmeta/",
                         f"/pc/{U}/new/"):
                self.assertEqual(ask(path, ua), (False, False), f"{path} {ua}: the usual link and every other endpoint stay stock")
            self.assertEqual(ask("/pc/api/v2/user/all-configs/", ua), (False, True), "the panel list shows the countries but is not the layout")
        self.assertEqual((ml.layout_client(), ml.location_client()), (False, False), "no request context: stock")


# --------------------------------------------------------------------------------------------- RoscomVPN routing inside the Happ / INCY JSON
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "roscom")


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


def payload_of(deeplink):
    import base64
    return json.loads(base64.b64decode(re.match(r"^\w+://routing/onadd/(\S+)$", deeplink.strip()).group(1)))


def deeplink_of(profile, client="happ"):
    import base64
    return f"{client}://routing/onadd/" + base64.b64encode(json.dumps(profile).encode()).decode()


class TestRoscomJson(unittest.TestCase):
    """The REAL upstream files (fixtures/roscom) go through the pure functions: parsing, the header profile, the rules, the refusals"""

    def prof(self, client="happ"):
        return ml.parse_roscom_deeplink(fixture(f"{client.upper()}_DEFAULT.DEEPLINK"), client)

    def test_the_real_upstream_profiles_parse_and_are_what_we_expect(self):
        for c in ("happ", "incy"):
            p = self.prof(c)
            self.assertEqual((p["Name"], p["RouteOrder"], p["DomainStrategy"]), ("RoscomVPN", "block-proxy-direct", "IPIfNonMatch"), c)
            self.assertEqual({k: len(p[k]) for k in ("DirectSites", "DirectIp", "ProxySites", "ProxyIp", "BlockSites", "BlockIp")},
                             {"DirectSites": 12, "DirectIp": 2, "ProxySites": 5, "ProxyIp": 0, "BlockSites": 3, "BlockIp": 0}, c)

    def test_the_derived_header_profile_equals_the_upstream_jsonsub_for_both_apps(self):
        for c in ("happ", "incy"):
            self.assertEqual(ml.roscom_json_profile(self.prof(c)), payload_of(fixture(f"{c.upper()}_JSONSUB.DEEPLINK")), c)

    def test_the_header_value_is_one_line_of_the_clients_own_scheme_and_decodes_to_that_profile(self):
        for c in ("happ", "incy"):
            p = self.prof(c)
            v = ml.roscom_header_value(c, p)
            self.assertTrue(v.startswith(f"{c}://routing/onadd/"), c)
            self.assertNotIn("\n", v)
            self.assertLess(len(v), 3000, "a response header, not a document")
            self.assertEqual(payload_of(v), ml.roscom_json_profile(p))
            self.assertEqual(payload_of(v)["Name"], "RoscomVPN JSON", "NOT 'RoscomVPN': the user's own profile of that name must not be overwritten")
            for k in ("DirectSites", "DirectIp", "ProxySites", "ProxyIp", "BlockSites", "BlockIp"):
                self.assertEqual(payload_of(v)[k], [], "the profile in the header carries NO rules (they live inside the JSON)")
        self.assertEqual(self.prof("happ")["Name"], "RoscomVPN", "the parsed upstream profile itself is not modified")

    def test_the_ru_zones_and_the_banks_go_first_in_the_direct_list_and_can_be_switched_off(self):
        import tempfile
        old = ml.ROSCOM_SETTINGS
        with tempfile.TemporaryDirectory() as d:
            ml.ROSCOM_SETTINGS = os.path.join(d, "settings.json")
            try:
                p = self.prof()
                before = json.loads(json.dumps(p))
                self.assertTrue(ml.roscom_ru_zone(), "default ON (owner decision 2026-10-13)")
                want = ["regexp:.*\\.ru$", "regexp:.*\\.su$", "regexp:.*\\.xn\\-\\-p1ai$", "domain:sberbank.com", "domain:tbank-online.com", "domain:alfabank.com", "domain:alfa-bank.com",
                        "domain:gazprombank.com"]
                self.assertEqual(list(ml.ROSCOM_RU_ZONE) + list(ml.ROSCOM_RU_BANKS), want)
                q = ml.roscom_with_ru_zone(p)
                self.assertEqual(q["DirectSites"], want + p["DirectSites"])
                self.assertEqual(p, before, "the input profile is not modified")
                self.assertEqual(ml.roscom_with_ru_zone(q)["DirectSites"], q["DirectSites"], "idempotent: no duplicates")
                ml.check_roscom_profile(q)
                rules, _ = ml.roscom_rules(q, "b")
                direct = next(r for r in rules if r.get("outboundTag") == "direct" and "domain" in r)
                self.assertEqual(direct["domain"][:len(want)], want, "the baked rules carry them in the direct group")
                header = payload_of(ml.roscom_header_value("happ", q))
                self.assertEqual(header["DirectSites"], [], "default: the empty header variant has no lists at all")
                ml.roscom_set(header_lists=True)
                self.assertEqual(payload_of(ml.roscom_header_value("happ", q))["DirectSites"], want + p["DirectSites"], "with the lists: they are in the header too")
                ml.roscom_set(ru_zone=False)
                self.assertFalse(ml.roscom_ru_zone())
                self.assertIs(ml.roscom_with_ru_zone(p), p, "off: the profile is returned as it is")
                self.assertFalse(ml.roscom_status()["ru_zone"])
                ml.roscom_set(enabled=True)
                self.assertFalse(ml.roscom_ru_zone(), "setting another key keeps it")
            finally:
                ml.ROSCOM_SETTINGS = old

    def test_the_header_can_carry_the_lists_under_its_own_name_and_defaults_to_the_empty_variant(self):
        import tempfile
        old = ml.ROSCOM_SETTINGS
        with tempfile.TemporaryDirectory() as d:
            ml.ROSCOM_SETTINGS = os.path.join(d, "settings.json")
            try:
                p = self.prof()
                self.assertFalse(ml.roscom_header_lists(), "default: the v3 / v4 behaviour")
                self.assertEqual(payload_of(ml.roscom_header_value("happ", p))["DirectSites"], [])
                ml.roscom_set(header_lists=True)
                self.assertTrue(ml.roscom_header_lists())
                for c in ("happ", "incy"):
                    q = self.prof(c)
                    sent = payload_of(ml.roscom_header_value(c, q))
                    self.assertEqual(sent["Name"], "RoscomVPN JSON", "never the user's own 'RoscomVPN'")
                    for k in ("DirectSites", "DirectIp", "ProxySites", "ProxyIp", "BlockSites", "BlockIp"):
                        self.assertEqual(sent[k], q[k], f"{c}: the lists of the upstream profile go into the header")
                    self.assertEqual({k: v for k, v in sent.items() if k not in ("Name",)}, {k: v for k, v in q.items() if k not in ("Name",)}, "nothing else is changed")
                self.assertEqual(self.prof("happ")["Name"], "RoscomVPN", "the parsed profile itself is not modified")
                self.assertTrue(ml.roscom_status()["header_lists"])
                ml.roscom_set(header_lists=False)
                self.assertEqual(payload_of(ml.roscom_header_value("happ", p))["DirectSites"], [])
                ml.roscom_set(enabled=True)
                self.assertFalse(ml.roscom_header_lists(), "setting another key keeps the shape")
            finally:
                ml.ROSCOM_SETTINGS = old

    def test_rules_follow_the_route_order_and_proxy_means_the_locations_balancer(self):
        p = self.prof()
        rules, strategy = ml.roscom_rules(p, "loc1-balancer")
        self.assertEqual(strategy, "IPIfNonMatch")
        shape = [("domain" if "domain" in r else "ip" if "ip" in r else r["port"], r.get("outboundTag") or r.get("balancerTag")) for r in rules]
        self.assertEqual(shape, [("domain", "block"), ("domain", "loc1-balancer"), ("domain", "direct"), ("ip", "direct"), ("0-65535", "loc1-balancer")])
        self.assertEqual(rules[0]["domain"], p["BlockSites"])
        self.assertEqual(rules[1]["domain"], p["ProxySites"])
        self.assertEqual(rules[2]["domain"], p["DirectSites"])
        self.assertEqual(rules[3]["ip"], p["DirectIp"])
        self.assertTrue(all(r["type"] == "field" for r in rules))
        self.assertFalse([r for r in rules if r.get("outboundTag") == "proxy"], "no dangling 'proxy' target in a config that has no outbound of that name")

    def test_every_route_order_is_honoured_and_a_non_permutation_is_refused(self):
        for order in ("block-proxy-direct", "direct-block-proxy", "proxy-direct-block", "block-direct-proxy"):
            p = dict(self.prof(), RouteOrder=order)
            tags = [(r.get("outboundTag") or "proxy") for r in ml.roscom_rules(p, "b")[0][:-1]]
            seen = []
            for t in tags:
                t = "proxy" if t == "proxy" or t == "b" else t
                if t not in seen:
                    seen.append(t)
            self.assertEqual(seen, order.split("-"), order)
        for bad in ("block-block-direct", "block-proxy", "proxy-direct-block-x", "", "BLOCK-PROXY-DIRECT"):
            with self.assertRaises(ValueError, msg=bad):
                ml.check_roscom_profile(dict(self.prof(), RouteOrder=bad))

    def test_global_proxy_false_sends_the_rest_direct_and_the_balancer_only_serves_the_proxy_list(self):
        rules, _ = ml.roscom_rules(dict(self.prof(), GlobalProxy="false"), "b")
        self.assertEqual(rules[-1], {"type": "field", "port": "0-65535", "outboundTag": "direct"})
        rules, _ = ml.roscom_rules(dict(self.prof(), GlobalProxy="TRUE"), "b")
        self.assertEqual(rules[-1], {"type": "field", "port": "0-65535", "balancerTag": "b"})

    def test_rule_entries_are_normalised_and_checked(self):
        p = dict(self.prof(), DirectSites=["geosite:private", "example.com", "domain:a.b", "full:c.d", "regexp:.*\\.ru$", "keyword:vk"],
                 DirectIp=["geoip:private", "10.0.0.0/8", "2001:db8::/32", "1.2.3.4"])
        rules, _ = ml.roscom_rules(p, "b")
        direct = [r for r in rules if r.get("outboundTag") == "direct"]
        self.assertEqual(direct[0]["domain"], ["geosite:private", "domain:example.com", "domain:a.b", "full:c.d", "regexp:.*\\.ru$", "keyword:vk"],
                         "a bare host becomes a domain rule (a bare string would be a substring match in xray)")
        self.assertEqual(direct[1]["ip"], ["geoip:private", "10.0.0.0/8", "2001:db8::/32", "1.2.3.4"])

    def test_profiles_and_deeplinks_we_do_not_fully_understand_are_refused(self):
        good = self.prof()
        for what, mutate in {
            "an item that is not a string": lambda p: p.update(DirectSites=[1]),
            "a geoip tag in the site list": lambda p: p.update(DirectSites=["geoip:ru"]),
            "a geosite tag in the ip list": lambda p: p.update(DirectIp=["geosite:ru"]),
            "an ext: reference": lambda p: p.update(DirectSites=["ext:file.dat:tag"]),
            "a quote inside a tag": lambda p: p.update(DirectSites=['geosite:a"b']),
            "whitespace around an item": lambda p: p.update(DirectSites=[" geosite:private"]),
            "not an IP network": lambda p: p.update(DirectIp=["999.1.1.1"]),
            "too many items": lambda p: p.update(DirectSites=[f"domain:x{i}.example" for i in range(301)]),
            "a list that is not a list": lambda p: p.update(DirectSites="geosite:private"),
            "a http (not https) geo address": lambda p: p.update(Geoipurl="http://x/geoip.dat"),
            "a geo address that is not a .dat": lambda p: p.update(Geositeurl="https://x/geosite.txt"),
            "an unknown domain strategy": lambda p: p.update(DomainStrategy="Whatever"),
            "GlobalProxy that is not a boolean": lambda p: p.update(GlobalProxy="maybe"),
            "no Name": lambda p: p.pop("Name"),
            "no rules at all": lambda p: [p.update({k: []}) for k in ("DirectSites", "DirectIp", "ProxySites", "ProxyIp", "BlockSites", "BlockIp")],
        }.items():
            p = json.loads(json.dumps(good))
            mutate(p)
            with self.assertRaises(ValueError, msg=what):
                ml.parse_roscom_deeplink(deeplink_of(p), "happ")
        with self.assertRaises(ValueError):
            ml.parse_roscom_deeplink(deeplink_of(["not", "an", "object"]), "happ")
        for bad in ("", "garbage", "happ://routing/add/" + "QUJD", "https://routing/onadd/QUJD", "happ://routing/onadd/@@@@", "happ://routing/onadd/QUJD",
                    "happ://routing/onadd/" + "QUJD" * 6000, fixture("INCY_DEFAULT.DEEPLINK")):
            with self.assertRaises(ValueError, msg=bad[:40]):
                ml.parse_roscom_deeplink(bad, "happ")
        with self.assertRaises(ValueError):
            ml.parse_roscom_deeplink(fixture("HAPP_DEFAULT.DEEPLINK"), "hiddify")

    def test_only_the_two_apps_are_recognised_by_their_user_agent(self):
        for ua, want in (("Happ/3.5.0", "happ"), ("Happ/2.7.0/ios", "happ"), ("happ", "happ"), ("INCY/1.0", "incy"), ("INCY/2.4.5 (iOS 18)", "incy"),
                         ("HiddifyNext/2.0.5", None), ("v2rayNG/1.8.5", None), ("v2RayTun/1.0", None), ("Mozilla/5.0 Chrome/135", None),
                         ("ClashMeta/1.18", None), ("", None), (None, None)):
            self.assertEqual(ml.roscom_client_of(ua), want, ua)

    def test_a_group_config_gets_the_roscom_rules_and_keeps_everything_else(self):
        p = self.prof()
        roscom = {"client": "happ", "profile": p, "header": "x"}
        base = TestGroupConfig.BASE
        stock = ml._group_config(copy.deepcopy(base), copy.deepcopy(TestGroupConfig.MEMBERS), "🟢 AUTO", "loc0-balancer", TestGroupConfig.PROBE)
        baked = ml._group_config(copy.deepcopy(base), copy.deepcopy(TestGroupConfig.MEMBERS), "🟢 AUTO", "loc0-balancer", TestGroupConfig.PROBE, roscom)
        self.assertEqual(baked["routing"]["rules"], ml.roscom_rules(p, "loc0-balancer")[0])
        self.assertEqual(baked["routing"]["domainStrategy"], "IPIfNonMatch")
        self.assertEqual(stock["routing"]["domainStrategy"], "AsIs", "without roscom the stock rules stay exactly as they were")
        for k in ("outbounds", "balancers"):
            self.assertEqual(baked["routing"].get(k), stock["routing"].get(k), k)
        self.assertEqual({k: v for k, v in baked.items() if k != "routing"}, {k: v for k, v in stock.items() if k != "routing"})
        self.assertEqual(TestGroupConfig.BASE["routing"]["domainStrategy"], "AsIs", "the input is not mutated")

    def test_the_request_gates_switch_dependencies_user_agent_flag_and_file(self):
        import tempfile
        U = "22222222-2222-4222-8222-222222222222"
        with tempfile.TemporaryDirectory() as d:
            flag, landing = os.path.join(d, "flag"), os.path.join(d, "last-good")
            os.makedirs(landing)
            for c in ("happ", "incy"):
                with open(os.path.join(landing, f"{c}.deeplink"), "w") as f:
                    f.write(fixture(f"{c.upper()}_DEFAULT.DEEPLINK"))
            old = (ml.ROSCOM_FLAG, ml.ROSCOM_DIR, ml.ROSCOM_SETTINGS)
            ml.ROSCOM_FLAG, ml.ROSCOM_DIR, ml.ROSCOM_SETTINGS = flag, landing, os.path.join(d, "settings.json")      # no settings file: the flag file decides

            def ask(path="/pc/%s/happ/" % U, ua="Happ/3.5.0"):
                with APP.test_request_context(path, headers={"User-Agent": ua}):
                    r = ml.roscom_for_request()
                    return None if r is None else (r["client"], r["header"].split("://")[0])
            try:
                self.assertIsNone(ask(), "switched off (no flag file): nothing, whatever the rest is")
                open(flag, "w").close()
                self.assertEqual(ask(), ("happ", "happ"))
                self.assertEqual(ask(ua="INCY/1.0"), ("incy", "incy"))
                for ua in ("HiddifyNext/2.0.5", "v2rayNG/1.8.5", "Mozilla/5.0 Chrome/135", ""):
                    self.assertIsNone(ask(ua=ua), ua)
                for path in (f"/pc/{U}/", f"/pc/{U}/xray/", f"/pc/{U}/sub/", f"/pc/{U}/auto/", "/pc/api/v2/user/all-configs/"):
                    self.assertIsNone(ask(path), path + ": only the dedicated /happ/ link")
                with APP.test_request_context(f"/pc/{U}/happ/", headers={"User-Agent": "Happ/3.5.0"}):
                    self.assertIsNone(ml.roscom_header_used(), "the header is announced only after the layout was really built")
                    self.assertIs(ml.roscom_for_request(), ml.roscom_for_request(), "one decision per request: the rules and the header cannot disagree")
                self.assertIsNone(ml.roscom_for_request(), "no request context: nothing")
                with open(os.path.join(landing, "happ.deeplink"), "w") as f:
                    f.write("broken")
                self.assertIsNone(ask(), "an unusable landing copy: nothing (stock rules, no header)")
                self.assertEqual(ask(ua="INCY/1.0"), ("incy", "incy"), "... and only for the client whose copy is broken")
                os.remove(os.path.join(landing, "incy.deeplink"))
                self.assertIsNone(ask(ua="INCY/1.0"), "a missing landing copy: nothing")
                st = ml.roscom_status()
                self.assertTrue(st["enabled"])
                self.assertFalse(st["clients"]["happ"]["ok"])
                self.assertFalse(st["clients"]["incy"]["ok"])
            finally:
                ml.ROSCOM_FLAG, ml.ROSCOM_DIR, ml.ROSCOM_SETTINGS = old


if __name__ == "__main__":
    unittest.main(verbosity=2)
