"""hutils/proxy/router_core.py: `tcp` + `headerType=http` (Hiddify "rawhttp" proxies, vless and trojan) is built WITH the http header (path + Host); every other transport is built exactly as before.
Why (2026-10-13): the exit's HAProxy tells such proxies apart by the HTTP request; without the header it answered "400 Bad request" and a rawhttp node never worked (the NL trojan node).
Run: /opt/hiddify-manager/.venv313/bin/python -W ignore -m unittest -v test_20_router_core   (from /work/tests)"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stand_lib import *  # noqa: E402,F403

UUID = "00000000-0000-4000-8000-000000000001"


def build(scheme, query):
    out = panel_python(f"""
import json
from hiddifypanel.hutils.proxy import router_core as rc
uri = {scheme!r} + "://{UUID}@192.0.2.10:443/?" + {query!r}
ob = rc._build_vless_outbound(uri) if {scheme!r} == "vless" else rc._build_trojan_outbound(uri)
print(json.dumps(ob["streamSettings"]))
""")
    return json.loads(out.splitlines()[-1])


class TestTcpHttpHeader(unittest.TestCase):

    def test_rawhttp_gets_the_http_request_header_with_path_and_host_for_vless_and_trojan(self):
        for scheme in ("vless", "trojan"):
            ss = build(scheme, "fp=chrome&sni=exit.example&security=tls&alpn=http%2F1.1&type=tcp&path=%2Fsecret-path&host=exit.example&headerType=http")
            self.assertEqual(ss["network"], "tcp")
            self.assertEqual(ss["tcpSettings"], {"header": {"type": "http", "request": {"version": "1.1", "method": "GET", "path": ["/secret-path"], "headers": {"Host": ["exit.example"]}}}}, scheme)
            self.assertEqual(ss["tlsSettings"]["serverName"], "exit.example")

    def test_the_host_falls_back_to_the_sni_a_list_of_hosts_is_split_and_a_missing_path_is_a_slash(self):
        ss = build("trojan", "sni=sni.example&security=tls&type=tcp&headerType=http")
        self.assertEqual(ss["tcpSettings"]["header"]["request"], {"version": "1.1", "method": "GET", "path": ["/"], "headers": {"Host": ["sni.example"]}})
        ss = build("trojan", "sni=sni.example&security=tls&type=tcp&headerType=http&path=%2Fp&host=a.example%2C%20b.example")
        self.assertEqual(ss["tcpSettings"]["header"]["request"]["headers"], {"Host": ["a.example", "b.example"]})

    def test_tcp_without_the_header_type_and_every_other_transport_are_built_as_before(self):
        plain = build("vless", "sni=exit.example&security=tls&alpn=h2&type=tcp&flow=xtls-rprx-vision")
        self.assertNotIn("tcpSettings", plain)
        self.assertEqual(plain["network"], "tcp")
        self.assertNotIn("tcpSettings", build("vless", "sni=exit.example&security=tls&type=tcp&headerType=none"))
        for query, key, want in (("sni=e.example&security=tls&type=xhttp&mode=auto&path=%2Fx&host=e.example", "xhttpSettings", {"path": "/x", "host": "e.example"}),
                                 ("sni=e.example&security=tls&type=ws&path=%2Fw&host=e.example&headerType=http", "wsSettings", {"path": "/w", "headers": {"Host": "e.example"}}),
                                 ("sni=e.example&security=tls&type=grpc&serviceName=svc&authority=e.example", "grpcSettings", {"serviceName": "svc", "authority": ""}),
                                 ("sni=e.example&security=tls&type=httpupgrade&path=%2Fh&host=e.example&headerType=http", "httpupgradeSettings", {"path": "/h", "host": "e.example"})):
            ss = build("vless", query)
            self.assertNotIn("tcpSettings", ss, query)
            self.assertIn(key, ss, query)
            for k, v in want.items():
                if v != "":
                    self.assertEqual(ss[key].get(k), v, query)


if __name__ == "__main__":
    unittest.main(verbosity=2)
