"""The router rules are COMPACTED (hutils/router_compact.py) and the legacy layers' `xray run -test` is answered by a SLIM test inside the full apply's namespace (XRAY_SHIM_SCRIPT of multiloc_full).
Why (2026-10-13, production): ~15k single-entry rules make `xray run -test` take ~110 s (limit of the layers: 120 s) and the router start 245-274 s; joined, the same rules are checked in 0.3 s and start in 0.6 s.
The routing result must be IDENTICAL: pure-function checks, a DIFFERENTIAL test with two real xray routers (plain vs compacted rules, thousands of destinations), and the shim.
Run: /opt/hiddify-manager/.venv313/bin/python -W ignore -m unittest -v test_18_compact   (from /work/tests)"""
import collections
import copy
import ipaddress
import json
import os
import random
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hiddifypanel.hutils import multiloc_full as mf  # noqa: E402
from hiddifypanel.hutils import router_compact as rc  # noqa: E402

XRAY = shutil.which("xray") or "/usr/bin/xray"


def rule(outbound="direct-ru", **kw):
    return {"type": "field", **kw, "outboundTag": outbound}


class TestCompactUnit(unittest.TestCase):

    def test_neighbours_with_the_same_target_are_joined_and_the_order_is_kept(self):
        rules = [rule(domain=["domain:a.example"]), rule(domain=["domain:b.example"]), rule(domain=["domain:c.example", "domain:d.example"]),
                 rule(ip=["10.0.0.1"]), rule(ip=["10.0.0.2/32"]), rule("block", domain=["domain:x.example"]), rule("block", domain=["domain:y.example"])]
        out = rc.compact_bulk_rules(rules)
        self.assertEqual(out, [rule(domain=["domain:a.example", "domain:b.example", "domain:c.example", "domain:d.example"]), rule(ip=["10.0.0.1", "10.0.0.2/32"]),
                               rule("block", domain=["domain:x.example", "domain:y.example"])])
        self.assertEqual(rc.flat_entries(out), rc.flat_entries(rules), "the same entries in the same order")

    def test_plain_and_special_lists_and_other_keys_and_other_targets_stop_the_join(self):
        rules = [rule(domain=["domain:a.example"]), rule(domain=["regexp:.*\\.ru$"]), rule(domain=["domain:b.example"]), rule("upstream-1", domain=["domain:c.example"]),
                 rule(ip=["geoip:ru"]), rule(ip=["1.1.1.1"]), {"type": "field", "inboundTag": ["from-hiddify-s3"], "outboundTag": "direct-ru", "ip": ["2.2.2.2"]},
                 {"type": "field", "balancerTag": "b", "domain": ["domain:d.example"]}, rule(ip=["3.3.3.3"]), rule(ip=["4.4.4.4"])]
        out = rc.compact_bulk_rules(rules)
        self.assertEqual(out, rules[:8] + [rule(ip=["3.3.3.3", "4.4.4.4"])], "only the last two (plain, same target, neighbours) are joined")

    def test_the_input_is_not_changed_and_a_second_pass_changes_nothing(self):
        rules = [rule(domain=["domain:a.example"]), rule(domain=["domain:b.example"])]
        snapshot = copy.deepcopy(rules)
        once = rc.compact_bulk_rules(rules)
        self.assertEqual(rules, snapshot)
        self.assertEqual(rc.compact_bulk_rules(once), once)
        cfg = {"routing": {"rules": rules, "balancers": [1]}, "outbounds": [2]}
        c = rc.compact_config(cfg)
        self.assertEqual(cfg["routing"]["rules"], snapshot, "the original config is not changed")
        self.assertEqual((c["routing"]["balancers"], c["outbounds"], len(c["routing"]["rules"])), ([1], [2], 1))

    def test_a_single_rule_a_non_dict_and_an_empty_list_are_safe(self):
        self.assertEqual(rc.compact_bulk_rules([]), [])
        self.assertEqual(rc.compact_bulk_rules([rule(ip=["1.1.1.1"])]), [rule(ip=["1.1.1.1"])])
        self.assertEqual(rc.compact_bulk_rules([None, "x", rule(ip=["1.1.1.1"])]), [None, "x", rule(ip=["1.1.1.1"])])


def socks_connect(port, host):
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.sendall(b"\x05\x01\x00")
    s.recv(2)
    try:
        req = b"\x05\x01\x00\x01" + socket.inet_aton(host) + struct.pack(">H", 443)
    except OSError:
        hb = host.encode()
        req = b"\x05\x01\x00\x03" + bytes([len(hb)]) + hb + struct.pack(">H", 443)
    s.sendall(req)
    s.settimeout(3)
    try:
        s.recv(16)
    except OSError:
        pass
    s.close()


class TestRoutingIsTheSame(unittest.TestCase):
    """two REAL xray routers (blackhole outbounds, no DNS, AsIs): plain rules vs compacted rules, the decision of every request is read from the access log.
    The entrances sit BELOW the ephemeral port range (32768+): tens of thousands of short test connections would otherwise hold the next router's port in TIME_WAIT ("address already in use")."""

    def test_thousands_of_destinations_are_routed_identically(self):
        rnd = random.Random(20261013)
        doms = [f"domain:d{i}.example" for i in range(500)] + [f"full:exact{i}.example" for i in range(50)]
        nets = [str(ipaddress.ip_network((rnd.getrandbits(32) & 0xFFFFFF00, 24))) if i % 3 else str(ipaddress.IPv4Address(rnd.getrandbits(32))) + "/32" for i in range(500)]
        rules = [rule(domain=[d]) for d in doms] + [rule(domain=["regexp:.*\\.ru$", "regexp:.*\\.su$"])] + [rule(ip=[n]) for n in nets] + [rule(ip=["geoip:private"])]
        rules += [rule("block", domain=[f"domain:blocked{i}.example"]) for i in range(40)]
        rules += [{"type": "field", "inboundTag": ["from-hiddify-s3"], "outboundTag": "BAL-s3"}, {"type": "field", "network": "tcp,udp", "outboundTag": "BAL-all"}]
        base = {"inbounds": [{"tag": "from-hiddify", "listen": "127.0.0.1", "port": 0, "protocol": "socks", "settings": {"auth": "noauth"}},
                             {"tag": "from-hiddify-s3", "listen": "127.0.0.1", "port": 0, "protocol": "socks", "settings": {"auth": "noauth"}}],
                "outbounds": [{"tag": t, "protocol": "blackhole"} for t in ("direct-ru", "block", "BAL-s3", "BAL-all", "upstream-1")],
                "routing": {"domainStrategy": "AsIs", "rules": rules}}
        dests = set(d.split(":", 1)[1] for d in doms) | {"sub." + d.split(":", 1)[1] for d in doms[:500]} | {f"blocked{i}.example" for i in range(0, 40, 3)}
        for n in rnd.sample(nets, 200):
            net = ipaddress.ip_network(n)
            dests |= {str(net.network_address + (1 if net.num_addresses > 2 else 0)), str(net.broadcast_address + 1)}
        dests |= {str(ipaddress.IPv4Address(rnd.getrandbits(32))) for _ in range(400)}
        dests |= {"foo.ru", "a.b.su", "10.1.2.3", "192.168.1.1", "nothing.example.org", "d1.example.com"} | {f"rnd{rnd.getrandbits(32):x}.example.net" for _ in range(100)}
        dests = sorted(dests)
        decisions, tmp = {}, tempfile.mkdtemp(prefix="t18-")
        procs = []
        try:
            for name, merged, port0 in (("plain", False, 21500), ("merged", True, 21600)):
                cfg = copy.deepcopy(base)
                for i, ib in enumerate(cfg["inbounds"]):
                    ib["port"] = port0 + i
                if merged:
                    cfg = rc.compact_config(cfg)
                    self.assertLess(len(cfg["routing"]["rules"]), 20, "the bulk rules really became a few lists")
                log = os.path.join(tmp, name + ".log")
                open(log, "w").close()
                cfg["log"] = {"loglevel": "info", "access": log}
                path = os.path.join(tmp, name + ".json")
                json.dump(cfg, open(path, "w"))
                errlog = open(os.path.join(tmp, name + ".err"), "w")
                p = subprocess.Popen([XRAY, "run", "-c", path], stdout=errlog, stderr=subprocess.STDOUT)
                procs.append(p)
                t0 = time.time()
                for off in (0, 1):                                                         # every entrance, not only the first one
                    while time.time() - t0 < 120:
                        try:
                            socket.create_connection(("127.0.0.1", port0 + off), timeout=1).close()
                            break
                        except OSError:
                            self.assertIsNone(p.poll(), f"{name}: xray exited ({p.returncode}): " + open(os.path.join(tmp, name + ".err")).read()[-600:])
                            time.sleep(0.3)
                    else:
                        self.fail(f"{name}: the entrance {port0 + off} did not listen within 120 s: " + open(os.path.join(tmp, name + ".err")).read()[-600:])
                for off in (0, 1):
                    for h in dests:
                        socks_connect(port0 + off, h)
                time.sleep(2)
                pat = re.compile(r"accepted (?:tcp|udp):(.+?):\d+ \[([^\]]+?) -> ([^\]]+?)\]")
                decisions[name] = {}
                for line in open(log, errors="replace"):
                    m = pat.search(line)
                    if m:
                        decisions[name][(m.group(2), m.group(1))] = m.group(3)
        finally:
            for p in procs:
                p.send_signal(signal.SIGTERM)
            for p in procs:
                try:
                    p.wait(10)
                except Exception:
                    p.kill()
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertGreater(len(decisions["plain"]), 2 * len(dests) * 0.95, "the routers really routed the requests")
        self.assertEqual(set(decisions["plain"]), set(decisions["merged"]))
        diff = [(k, decisions["plain"][k], decisions["merged"][k]) for k in decisions["plain"] if decisions["plain"][k] != decisions["merged"][k]]
        self.assertEqual(diff, [], f"{len(diff)} different decisions of {len(decisions['plain'])}")
        outcomes = collections.Counter(decisions["plain"].values())
        for want in ("direct-ru", "block", "BAL-all", "BAL-s3"):
            self.assertGreater(outcomes[want], 0, f"the test really exercised {want}: {dict(outcomes)}")


class TestXrayShim(unittest.TestCase):
    """XRAY_SHIM_SCRIPT: `xray run -test -config FILE` runs on the SLIM copy (single-entry bulk rules cut out), every other call and the real exit code go through"""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="t18-shim-")
        shim = os.path.join(self.d, "xray")
        open(shim, "w").write(mf.XRAY_SHIM_SCRIPT)
        os.chmod(shim, 0o755)
        self.env = dict(os.environ, PATH=self.d + ":" + os.environ["PATH"], MULTILOC_SLIM_DIR=self.d)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def cfg(self, rules):
        c = {"inbounds": [{"tag": "in", "listen": "127.0.0.1", "port": 21999, "protocol": "socks", "settings": {}}], "outbounds": [{"tag": "direct-ru", "protocol": "freedom"}, {"tag": "block", "protocol": "blackhole"}],
             "routing": {"rules": rules}}
        p = os.path.join(self.d, "c.json")
        json.dump(c, open(p, "w"))
        return p

    def run_xray(self, *args):
        return subprocess.run(["xray", *args], capture_output=True, text=True, env=self.env, timeout=120)

    def test_a_good_config_passes_and_the_real_exit_code_of_a_broken_one_is_kept(self):
        good = self.cfg([rule(ip=["10.0.0.1"]), rule(domain=["domain:a.example"]), rule(domain=["regexp:.*\\.ru$", "domain:b.example"])])
        r = self.run_xray("run", "-test", "-config", good)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Configuration OK", r.stdout + r.stderr)
        broken = self.cfg([rule(domain=["regexp:[unclosed", "domain:a.example"])])
        r = self.run_xray("run", "-test", "-config", broken)
        self.assertNotEqual(r.returncode, 0, "a broken rule that is not a single-entry bulk rule is still caught")
        self.assertEqual(os.listdir(self.d).count("c.json"), 1)
        self.assertFalse([f for f in os.listdir(self.d) if f.startswith("slim.")], "no slim copy is left behind")

    def test_the_bulk_rules_are_cut_from_the_tested_copy_and_other_xray_calls_pass_through(self):
        many = [rule(ip=[f"203.0.{i // 250}.{i % 250}"]) for i in range(3000)] + [rule(domain=[f"domain:h{i}.example"]) for i in range(3000)]
        t0 = time.time()
        r = self.run_xray("run", "-test", "-config", self.cfg(many + [rule(domain=["domain:keep.example", "domain:keep2.example"])]))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertLess(time.time() - t0, 30)
        v = self.run_xray("version")
        self.assertEqual(v.returncode, 0)
        self.assertIn("Xray", v.stdout, "an ordinary call reaches the real binary")
        r = self.run_xray("run", "-test", "-config", os.path.join(self.d, "does-not-exist.json"))
        self.assertNotEqual(r.returncode, 0, "a missing file is the real xray's error")

    def test_the_module_writes_the_shim_next_to_the_systemctl_shim(self):
        import inspect
        src = inspect.getsource(mf._prepare_scratch)
        self.assertIn("XRAY_SHIM_SCRIPT", src)
        self.assertIn('Path(SHIM_DIR) / "xray"', src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
