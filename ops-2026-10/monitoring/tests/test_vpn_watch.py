"""CP-08 tests on the STAND: the real script, the real router + exits of the stand, a fake Telegram server.
Run (inside WSL as root):  /opt/hiddify-manager/.venv313/bin/python -m unittest -v /work/stage2/cp08/test_vpn_watch.py
Failure injection uses the stand's own exits (stand-exit-a = Germany, stand-exit-b = USA) exactly like the stand's data-plane tests."""
import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse

sys.path.insert(0, "/work/tests")
from stand_lib import router_ready, systemd, sh  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
WATCH = os.path.join(HERE, "vpn_watch.py")
PY = "/opt/hiddify-manager/.venv313/bin/python"


class FakeTelegram:
    def __init__(self):
        self.messages, self.fail = [], False
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                body = urllib.parse.parse_qs(self.rfile.read(n).decode())
                if outer.fail:
                    self.send_response(500); self.end_headers(); return
                outer.messages.append(body.get("text", [""])[0])
                self.send_response(200); self.end_headers(); self.wfile.write(b'{"ok":true}')

            def log_message(self, *a):
                pass
        self.srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


def base_config(tg_port, state):
    return {
        "role": "ru", "host_label": "stand-ru",
        "services": ["xray-router", "hiddify-xray", "hiddify-haproxy", "hiddify-panel"],
        "router_ports": [20808, 20811, 20812],
        "slots": {"1": "DE", "2": "US"},
        "probe": {"url": "http://13.0.0.20:18080/", "extract": "json:via", "timeout": 4},
        "canaries": [{"name": "DE", "port": 20811, "expect": "exit-a"}, {"name": "US", "port": 20812, "expect": "exit-b"},
                     {"name": "AUTO", "port": 20808, "expect": ["exit-a", "exit-b"]}],
        "disks": ["/"], "min_disk_free_pct": 1, "min_mem_available_mb": 50, "max_load_per_core": 50,
        "telegram": {"api_base": f"http://127.0.0.1:{tg_port}", "env_file": "/nonexistent"},
        "thresholds": {"crit_runs": 3, "warn_runs": 5, "repeat_minutes": 180, "node_crit_runs": 3, "node_warn_runs": 3},
        "dns_error_limit": 10000,
    }


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for u in ("stand-exit-a", "stand-exit-b", "stand-shim-a", "stand-shim-b"):
            systemd("start", u)
        router_ready()

    def setUp(self):
        self.tg = FakeTelegram()
        self.state = tempfile.mkdtemp(prefix="vw-state-")
        self.cfgdir = tempfile.mkdtemp(prefix="vw-cfg-")
        self.cfg = base_config(self.tg.port, self.state)

    def tearDown(self):
        self.tg.close()
        for u in ("stand-exit-a", "stand-exit-b", "stand-shim-a", "stand-shim-b"):
            systemd("start", u)
        shutil.rmtree(self.state, ignore_errors=True)
        shutil.rmtree(self.cfgdir, ignore_errors=True)

    def run_watch(self, cmd="run"):
        path = os.path.join(self.cfgdir, "c.json")
        with open(path, "w") as f:
            json.dump(self.cfg, f)
        env = dict(os.environ, VPN_WATCH_STATE=self.state, VPN_WATCH_TG_TOKEN="T", VPN_WATCH_TG_CHAT="C", PATH=os.environ["PATH"] + ":/usr/local/bin")
        r = subprocess.run([PY, WATCH, cmd, "--conf", path], capture_output=True, text=True, env=env, timeout=200)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        return r.stdout

    def alerts(self):
        return [m for m in self.tg.messages if "эталон" not in m and "сводка" not in m]

    def status(self):
        with open(os.path.join(self.state, "state.json")) as f:
            return json.load(f)["checks"]


class TestHealthyAndCanaries(Base):
    def test_healthy_stand_is_quiet_and_every_canary_reports_the_expected_exit(self):
        for _ in range(3):
            self.run_watch()
        st = self.status()
        self.assertTrue(st["canary:DE"]["ok"], st["canary:DE"])
        self.assertIn("exit-a", st["canary:DE"]["detail"])
        self.assertIn("exit-b", st["canary:US"]["detail"])
        self.assertTrue(all(v["ok"] for k, v in st.items() if k.startswith(("port:", "svc:", "canary:"))))
        self.assertEqual(self.alerts(), [], "a healthy system must not alert")

    def test_a_dead_country_alerts_after_exactly_three_runs_once_and_recovers(self):
        self.run_watch()
        systemd("stop", "stand-exit-a")
        time.sleep(5)                       # the router's observatory needs a probe interval to notice
        self.run_watch(); self.run_watch()
        self.assertEqual(self.alerts(), [], "two failing runs are below the hysteresis")
        self.run_watch()
        a = self.alerts()
        self.assertEqual(len(a), 1, a)
        self.assertIn("DE", a[0]); self.assertIn("🔴", a[0])
        self.assertNotIn("AUTO", a[0], "AUTO is still served by the other exit")
        self.run_watch(); self.run_watch()
        self.assertEqual(len(self.alerts()), 1, "no repeat spam while broken")
        systemd("start", "stand-exit-a"); systemd("start", "stand-shim-a")
        time.sleep(8)
        self.run_watch()
        rec = [m for m in self.tg.messages if "восстановлено" in m]
        self.assertEqual(len(rec), 1, self.tg.messages)
        self.assertIn("DE", rec[0])

    def test_wrong_exit_is_reported_as_misrouting(self):
        self.cfg["canaries"][0]["expect"] = "exit-b"          # pretend Germany must leave through the USA exit: i.e. traffic goes "to the wrong place"
        for _ in range(3):
            self.run_watch()
        a = self.alerts()
        self.assertEqual(len(a), 1, a)
        self.assertIn("не туда", a[0])

    def test_router_down_is_reported(self):
        self.run_watch()
        systemd("stop", "xray-router")
        try:
            for _ in range(3):
                self.run_watch()
            a = " ".join(self.alerts())
            self.assertIn("xray-router", a); self.assertIn("20811", a)
        finally:
            systemd("start", "xray-router"); router_ready()
        self.run_watch()
        self.assertTrue(any("восстановлено" in m for m in self.tg.messages))


class TestNodeProbes(Base):
    def setUp(self):
        super().setUp()
        self.cfg["router_config"] = "/etc/xray-router/config.json"

    def test_every_node_is_probed_individually_and_a_dead_exit_names_its_nodes(self):
        out = self.run_watch("nodes")
        st = self.status()
        nodes = sorted(k for k in st if k.startswith("node:"))
        self.assertGreaterEqual(len(nodes), 4, nodes)
        self.assertTrue(all(st[k]["ok"] for k in nodes), {k: st[k]["detail"] for k in nodes})
        systemd("stop", "stand-exit-b")
        time.sleep(3)
        for _ in range(3):
            self.run_watch("nodes")
        a = [m for m in self.alerts() if "узел" in m or "резерва" in m]
        self.assertTrue(a, self.tg.messages)
        text = "\n".join(a)
        self.assertIn("US", text)
        dead = [k for k in nodes if not self.status()[k]["ok"]]
        self.assertGreaterEqual(len(dead), 2, "both US nodes live on exit B")

    def test_expected_dead_nodes_are_not_evaluated(self):
        self.run_watch("nodes")
        first = sorted(k for k in self.status() if k.startswith("node:"))[0].split(":")[1]
        self.cfg["expected_dead_nodes"] = [first]
        systemd("stop", "stand-exit-a"); time.sleep(3)
        for _ in range(3):
            self.run_watch("nodes")
        self.assertIn("ожидаемо мёртвых", self.status()[f"node:{first}"]["detail"])


class TestDriftAndSystem(Base):
    def test_new_public_port_is_critical_and_reported_after_hysteresis(self):
        self.cfg["services"] = []
        self.cfg["canaries"] = []; self.cfg["router_ports"] = []
        self.run_watch()                                   # learns the baseline
        s = socket.socket(); s.bind(("0.0.0.0", 45999)); s.listen(1)
        try:
            for _ in range(3):
                self.run_watch()
            a = [m for m in self.alerts() if "порт" in m]
            self.assertEqual(len(a), 1, self.tg.messages)
            self.assertIn("tcp/45999", a[0])
        finally:
            s.close()
        self.run_watch()
        self.assertTrue(any("восстановлено" in m for m in self.tg.messages))

    def test_certificate_thresholds_are_per_pattern(self):
        d = tempfile.mkdtemp()
        for name, days in (("short.crt", 3), ("long.crt", 200)):
            subprocess.run(f"openssl req -x509 -newkey rsa:2048 -nodes -keyout {d}/{name}.key -out {d}/{name} -subj /CN=t -days {days} 2>/dev/null", shell=True, check=True)
        self.cfg.update(services=[], canaries=[], router_ports=[], certs=[{"glob": f"{d}/short.crt", "warn_days": 2, "crit_days": 1}, {"glob": f"{d}/long.crt"}])
        self.run_watch(); self.run_watch()
        st = self.status()
        self.assertTrue(st["cert:short.crt"]["ok"], "a 3-day cert with a 2-day threshold is fine (short-lived IP certificates)")
        self.assertTrue(st["cert:long.crt"]["ok"])
        self.cfg["certs"] = [{"glob": f"{d}/short.crt", "warn_days": 14, "crit_days": 5}]
        for _ in range(3):
            self.run_watch()
        self.assertTrue(any("short.crt" in m for m in self.alerts()), self.tg.messages)

    def _drift_case(self, broken_tag, expect_in_message):
        d = tempfile.mkdtemp()
        balancers = [{"tag": "upstream-balancer", "selector": ["upstream-1"], "fallbackTag": "block"},
                     {"tag": "balancer-s1", "selector": ["upstream-1"], "fallbackTag": "block"}]
        for b in balancers:
            if b["tag"] == broken_tag:
                del b["fallbackTag"]
        path = f"{d}/router.json"
        with open(path, "w") as f:
            json.dump({"outbounds": [{"tag": "block", "protocol": "blackhole"}], "routing": {"balancers": balancers, "rules": []}}, f)
        self.cfg.update(services=[], canaries=[], router_ports=[], router_config=path)
        for _ in range(4):
            self.run_watch()
        self.assertEqual([m for m in self.alerts() if "fallbackTag" in m], [], "a warning needs five runs")
        self.run_watch()
        a = [m for m in self.alerts() if "fallbackTag" in m]
        self.assertEqual(len(a), 1, self.tg.messages)
        self.assertIn(expect_in_message, a[0])
        for b in balancers:                                                        # repaired -> recovery notice
            b["fallbackTag"] = "block"
        with open(path, "w") as f:
            json.dump({"outbounds": [{"tag": "block", "protocol": "blackhole"}], "routing": {"balancers": balancers, "rules": []}}, f)
        self.run_watch()
        self.assertTrue(any("восстановлено" in m and "fallbackTag" in m for m in self.tg.messages), self.tg.messages)

    def test_fail_closed_drift_of_a_country_is_a_warning_only_after_five_runs(self):
        self._drift_case("balancer-s1", "balancer-s1")

    def test_fail_closed_drift_of_auto_is_a_warning_only_after_five_runs(self):
        self._drift_case("upstream-balancer", "AUTO")

    def test_fingerprint_change_is_reported_once(self):
        d = tempfile.mkdtemp(); f = os.path.join(d, "maps"); open(f, "w").write("/a backend1\n/b backend2\n")
        self.cfg.update(services=[], canaries=[], router_ports=[], fingerprints=[{"name": "paths", "glob": f, "mode": "keys", "why": "пути изменились"}])
        self.run_watch()
        open(f, "w").write("/a backend9\n/b backend2\n")        # values may move, keys do not: no alert
        self.run_watch()
        self.assertEqual([m for m in self.alerts() if "paths" in m], [])
        open(f, "w").write("/a backend1\n/c backend2\n")        # a key changed
        self.run_watch(); self.run_watch()
        self.assertEqual(len([m for m in self.alerts() if "paths" in m]), 1, self.tg.messages)


class TestAlertingMechanics(Base):
    def test_messages_are_queued_while_telegram_is_down_and_delivered_later(self):
        self.cfg.update(services=["no-such-unit-for-test"], canaries=[], router_ports=[])
        self.tg.fail = True
        for _ in range(3):
            self.run_watch()
        self.assertEqual(self.tg.messages, [])
        self.tg.fail = False
        self.run_watch()
        self.assertTrue(any("no-such-unit-for-test" in m for m in self.tg.messages), self.tg.messages)

    def test_daily_summary_is_sent_once_per_day(self):
        self.cfg.update(services=[], canaries=[], router_ports=[], daily_summary_hour=0)
        self.run_watch(); self.run_watch()
        self.assertEqual(len([m for m in self.tg.messages if "сводка" in m]), 1)

    def _counters(self, sysfs, iface, rx, tx):
        os.makedirs(f"{sysfs}/{iface}/statistics", exist_ok=True)
        with open(f"{sysfs}/{iface}/statistics/rx_packets", "w") as f:
            f.write(str(rx))
        with open(f"{sysfs}/{iface}/statistics/tx_packets", "w") as f:
            f.write(str(tx))

    def test_kernel_wireguard_exit_side_stale_handshake_is_critical(self):
        bindir = tempfile.mkdtemp()
        old = int(time.time()) - 1000
        with open(f"{bindir}/wg", "w") as f:
            f.write(f"#!/bin/bash\nif [ \"$3\" = dump ]; then printf 'wgT\\tPRIV\\tPUB\\t1\\toff\\nwgT\\tPEER\\t(none)\\t1.2.3.4:51821\\t0.0.0.0/0\\t{old}\\t1000\\t900\\t25\\n'; fi\n")
        os.chmod(f"{bindir}/wg", 0o755)
        self.cfg.update(services=[], canaries=[], router_ports=[], wireguard={"interfaces": ["wgT"], "max_handshake_age": 240})
        env_path = os.environ["PATH"]
        os.environ["PATH"] = f"{bindir}:{env_path}"
        try:
            for _ in range(3):
                self.run_watch()
            a = [m for m in self.alerts() if "wgT" in m]
            self.assertEqual(len(a), 1, self.tg.messages)
            self.assertIn("рукопожатие", a[0])
        finally:
            os.environ["PATH"] = env_path

    def test_tun_wireguard_on_ru_dead_return_path_is_critical_and_recovers(self):
        """RU: Xray's own WireGuard is a TUN device (wg(8) sees nothing): TX grows while RX stays at zero = the .190 NAT defect"""
        sysfs = tempfile.mkdtemp()
        self._counters(sysfs, "wgT", 0, 100)
        self.cfg.update(services=[], canaries=[], router_ports=[], wireguard={"tun_only": True, "interfaces": ["wgT"], "rx_window": 0, "rx_min_packets": 3, "sysfs_net": sysfs})
        self.run_watch()                                       # baseline sample
        tx = 100
        for _ in range(3):
            tx += 10
            self._counters(sysfs, "wgT", 0, tx)
            self.run_watch()
        a = [m for m in self.alerts() if "wgT" in m]
        self.assertEqual(len(a), 1, self.tg.messages)
        self.assertIn("ОБРАТНЫЙ ПУТЬ МЁРТВ", a[0]); self.assertIn("🔴", a[0])
        self._counters(sysfs, "wgT", 12, tx + 10)              # the NAT fix is applied: packets come back
        self.run_watch()
        self.assertTrue(any("восстановлено" in m and "wgT" in m for m in self.tg.messages), self.tg.messages)

    def test_tun_wireguard_idle_tunnel_is_only_a_slow_warning(self):
        sysfs = tempfile.mkdtemp()
        self._counters(sysfs, "wgT", 5, 5)
        self.cfg.update(services=[], canaries=[], router_ports=[], wireguard={"tun_only": True, "interfaces": ["wgT"], "rx_window": 0, "sysfs_net": sysfs})
        self.run_watch()
        for _ in range(4):
            self.run_watch()
        self.assertEqual([m for m in self.alerts() if "wgT" in m], [], "an idle tunnel must not page anybody within five runs")
        self.run_watch()
        a = [m for m in self.alerts() if "wgT" in m]
        self.assertEqual(len(a), 1, self.tg.messages)
        self.assertIn("🟠", a[0]); self.assertIn("молчит", a[0])

    def test_wg_node_probe_finds_the_tunnel_interface_by_its_address(self):
        d = tempfile.mkdtemp()
        router = {"outbounds": [{"tag": "upstream-77", "protocol": "wireguard", "settings": {"address": ["10.99.99.2/32"], "peers": [{"endpoint": "1.2.3.4:51821"}]}}, {"tag": "block", "protocol": "blackhole"}],
                  "routing": {"balancers": [{"tag": "balancer-s1", "selector": ["upstream-77"]}], "rules": []}}
        with open(f"{d}/router.json", "w") as f:
            json.dump(router, f)
        sh("ip link del vwgT 2>/dev/null; ip link add vwgT type veth peer name vwgTp && ip addr add 10.99.99.2/32 dev vwgT && ip link set vwgT up", check=False)
        try:
            self.cfg.update(services=[], canaries=[{"name": "DE", "port": 20811, "expect": "exit-a"}], router_ports=[], router_config=f"{d}/router.json", wg_nodes=True)
            self.cfg["probe"]["timeout"] = 3
            for _ in range(3):
                self.run_watch("nodes")
            st = self.status()
            self.assertIn("vwgT", st["node:77"]["detail"], st["node:77"])        # found by the address, then the bound request failed (no route in the lab): reported per node
            self.assertFalse(st["node:77"]["ok"])
            self.assertTrue(any("WG-узел 77" in m for m in self.alerts()), self.tg.messages)
        finally:
            sh("ip link del vwgT 2>/dev/null", check=False)


class FakeSocks5:
    """Minimal SOCKS5 (no authentication, CONNECT by domain name): forwards every connection to one local port and records what was asked for."""
    def __init__(self, target_port, refuse=False):
        import socketserver
        self.asked, self.refuse, self.target_port = [], refuse, target_port
        outer = self

        class H(socketserver.BaseRequestHandler):
            def handle(self):
                s = self.request
                _ver, n = s.recv(2)
                s.recv(n)
                s.sendall(b"\x05\x00")
                head = s.recv(4)                                    # VER CMD RSV ATYP
                if head[3] == 3:
                    host, kind = s.recv(s.recv(1)[0]).decode(), "domain"
                else:
                    host, kind = socket.inet_ntoa(s.recv(4)), "ip"
                port = int.from_bytes(s.recv(2), "big")
                outer.asked.append((kind, host, port))
                if outer.refuse:
                    s.sendall(b"\x05\x05\x00\x01\x00\x00\x00\x00\x00\x00")      # connection refused
                    return
                up = socket.create_connection(("127.0.0.1", outer.target_port))
                s.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")

                def pump(a, b):
                    try:
                        while True:
                            d = a.recv(4096)
                            if not d:
                                break
                            b.sendall(d)
                    except OSError:
                        pass
                    finally:
                        try:
                            b.shutdown(socket.SHUT_WR)
                        except OSError:
                            pass
                t = threading.Thread(target=pump, args=(up, s), daemon=True)
                t.start()
                pump(s, up)
                t.join(5)
                up.close()
        socketserver.ThreadingTCPServer.allow_reuse_address = True
        self.srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class TestTelegramThroughProxy(unittest.TestCase):
    """RU cannot reach api.telegram.org directly: the monitor sends through the router entrance exactly like the panel's own bot (socks5h)."""
    def setUp(self):
        sys.path.insert(0, HERE)
        import importlib
        self.vw = importlib.import_module("vpn_watch")
        self.tg = FakeTelegram()
        os.environ.update(VPN_WATCH_TG_TOKEN="T", VPN_WATCH_TG_CHAT="C")

    def tearDown(self):
        self.tg.close()
        for k in ("VPN_WATCH_TG_TOKEN", "VPN_WATCH_TG_CHAT", "VPN_WATCH_TG_PROXY"):
            os.environ.pop(k, None)

    def _tg(self, socks):
        return self.vw.Telegram({"host_label": "t", "telegram": {"api_base": "http://api.telegram.org", "env_file": "/nonexistent", "proxy": f"socks5h://127.0.0.1:{socks.port}"}})

    def test_the_message_goes_through_socks5_and_the_name_is_resolved_by_the_proxy(self):
        socks = FakeSocks5(self.tg.port)
        try:
            self.assertTrue(self._tg(socks).send("hello"))
            self.assertEqual(self.tg.messages, ["[t] hello"])
            self.assertEqual(socks.asked, [("domain", "api.telegram.org", 80)], "the host name must reach the proxy as a name (socks5h), never resolved locally")
        finally:
            socks.close()

    def test_a_refusing_proxy_keeps_the_message_and_it_is_delivered_later_with_a_delay_note(self):
        socks = FakeSocks5(self.tg.port, refuse=True)
        clock = [1_700_000_000.0]
        state = tempfile.mkdtemp(prefix="vw-state-")
        try:
            mon = self.vw.Monitor({"telegram": {}}, state_dir=state, now=lambda: clock[0], tg=self._tg(socks))
            mon.notify([(self.vw.CRIT, "🔴 обе страны недоступны")], [])
            self.assertEqual(self.tg.messages, [], "the exits are down and AUTO is closed: nothing can leave")
            self.assertEqual(len(mon.state["pending"]), 1)
            clock[0] += 600
            socks.refuse = False                                   # an exit is back
            mon.notify([], [])
            self.assertEqual(len(self.tg.messages), 1, self.tg.messages)
            self.assertIn("обе страны недоступны", self.tg.messages[0])
            self.assertIn("доставлено с опозданием 10 мин", self.tg.messages[0])
            self.assertEqual(mon.state["pending"], [])
        finally:
            socks.close()
            shutil.rmtree(state, ignore_errors=True)

    def test_the_token_never_appears_in_the_output_of_a_failed_send(self):
        os.environ["VPN_WATCH_TG_TOKEN"] = "123456:SECRET-TOKEN-VALUE"
        socks = FakeSocks5(self.tg.port, refuse=True)
        import contextlib
        import io
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertFalse(self._tg(socks).send("x"))
            self.assertNotIn("SECRET-TOKEN-VALUE", out.getvalue() + err.getvalue())
        finally:
            socks.close()


class TestDirectorChecks(unittest.TestCase):
    """check_director reads the controller's status files (no Xray, no router needed)"""
    def setUp(self):
        sys.path.insert(0, HERE)
        import importlib
        self.vw = importlib.import_module("vpn_watch")
        self.dir = tempfile.mkdtemp(prefix="dir-")
        self.cfg = {"auto_director": {"state_dir": self.dir, "max_age_s": 60, "max_switches_per_hour": 4}}

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, **kw):
        st = {"ts": time.time(), "cur": "DE", "applied": True, "switches_last_hour": 0, "last_switch": 0, "reason": "start"}
        st.update(kw)
        with open(os.path.join(self.dir, "status.json"), "w") as f:
            json.dump(st, f)

    def results(self, state):
        return {r.id: r for r in self.vw.check_director(self.cfg, state)}

    def test_off_without_the_section(self):
        self.assertEqual(self.vw.check_director({}, {}), [])

    def test_healthy_controller_is_quiet(self):
        self.write()
        r = self.results({})
        self.assertTrue(all(x.ok for x in r.values()), {k: x.detail for k, x in r.items() if not x.ok})

    def test_missing_stale_unapplied_flapping_and_closed_are_reported(self):
        self.assertFalse(self.results({})["director:alive"].ok)                       # no file at all
        self.write(ts=time.time() - 600)
        self.assertFalse(self.results({})["director:alive"].ok)
        self.write(applied=False)
        self.assertFalse(self.results({})["director:applied"].ok)
        self.write(switches_last_hour=9)
        self.assertFalse(self.results({})["director:flaps"].ok)
        self.write(cur="NONE")
        r = self.results({})["director:closed"]
        self.assertFalse(r.ok)
        self.assertEqual(r.level, self.vw.CRIT)

    def test_a_switch_is_reported_once_as_an_event_and_the_first_sight_is_silent(self):
        state = {}
        self.write(last_switch=1000.0)
        self.assertNotIn("director:switch", self.results(state))                       # first sight: just remember
        with open(os.path.join(self.dir, "state.json"), "w") as f:
            json.dump({"switches": [{"t": 2000.0, "from": "DE", "to": "NL", "reason": "NL is faster by 31 ms"}]}, f)
        self.write(cur="NL", last_switch=2000.0)
        r = self.results(state)
        self.assertIn("director:switch", r)
        self.assertIn("DE → NL", r["director:switch"].detail)
        self.assertTrue(r["director:switch"].event)
        self.assertNotIn("director:switch", self.results(state))                       # ... and not again


if __name__ == "__main__":
    unittest.main(verbosity=2)
