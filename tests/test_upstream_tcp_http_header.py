"""Upstream outbounds: `tcp` + `headerType=http` ("raw http" proxies of Hiddify) must be built WITH the http request header.

The exit's HAProxy tells such proxies apart by the HTTP request (path / Host). Without the header it answers "400 Bad request" and the node never carries traffic, although
its port answers and a plain connection test reports it online. Every other transport is built exactly as before.

`_build_stream_settings` is cut out of the overlay source with `ast` (no panel needed).
Run: python -m unittest tests.test_upstream_tcp_http_header
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote

SOURCE = (
    Path(__file__).parents[1]
    / "release" / "routing-installer" / "payload" / "panel-overlay" / "hiddifypanel" / "hutils" / "proxy" / "router_core.py"
)
FUNCTIONS = {"_first", "_split_csv", "_as_bool", "_build_stream_settings"}


def load():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in FUNCTIONS]
    module = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {"Any": Any, "unquote": unquote}
    exec(compile(module, str(SOURCE), "exec"), namespace)
    return namespace["_build_stream_settings"]


def stream(query: str, host: str = "203.0.113.10", port: int = 443) -> dict:
    return load()(parse_qs(query, keep_blank_values=True), host, port)


class TcpHttpHeaderTest(unittest.TestCase):

    def test_rawhttp_gets_the_http_request_header_with_path_and_host(self):
        ss = stream("sni=exit.example&security=tls&alpn=http%2F1.1&type=tcp&path=%2Fsecret-path&host=exit.example&headerType=http")
        self.assertEqual(ss["network"], "tcp")
        self.assertEqual(ss["tcpSettings"], {"header": {"type": "http", "request": {"version": "1.1", "method": "GET", "path": ["/secret-path"], "headers": {"Host": ["exit.example"]}}}})
        self.assertEqual(ss["tlsSettings"]["serverName"], "exit.example")

    def test_the_host_falls_back_to_the_sni_a_host_list_is_split_and_a_missing_path_is_a_slash(self):
        ss = stream("sni=sni.example&security=tls&type=tcp&headerType=http")
        self.assertEqual(ss["tcpSettings"]["header"]["request"], {"version": "1.1", "method": "GET", "path": ["/"], "headers": {"Host": ["sni.example"]}})
        ss = stream("sni=sni.example&security=tls&type=tcp&headerType=http&path=%2Fp&host=a.example%2C%20b.example")
        self.assertEqual(ss["tcpSettings"]["header"]["request"]["headers"], {"Host": ["a.example", "b.example"]})

    def test_the_header_type_is_case_insensitive(self):
        self.assertIn("tcpSettings", stream("sni=e.example&security=tls&type=tcp&headerType=HTTP"))

    def test_tcp_without_the_header_type_is_unchanged(self):
        for query in ("sni=e.example&security=tls&type=tcp", "sni=e.example&security=tls&type=tcp&headerType=none", "sni=e.example&security=tls&alpn=h2&type=tcp&flow=xtls-rprx-vision"):
            ss = stream(query)
            self.assertEqual(ss["network"], "tcp", query)
            self.assertNotIn("tcpSettings", ss, query)

    def test_other_transports_never_get_a_tcp_header(self):
        for query, key in (("sni=e.example&security=tls&type=xhttp&mode=auto&path=%2Fx&host=e.example&headerType=http", "xhttpSettings"),
                           ("sni=e.example&security=tls&type=ws&path=%2Fw&host=e.example&headerType=http", "wsSettings"),
                           ("sni=e.example&security=tls&type=grpc&serviceName=svc&headerType=http", "grpcSettings"),
                           ("sni=e.example&security=tls&type=httpupgrade&path=%2Fh&host=e.example&headerType=http", "httpupgradeSettings")):
            ss = stream(query)
            self.assertNotIn("tcpSettings", ss, query)
            self.assertIn(key, ss, query)


if __name__ == "__main__":
    unittest.main()
