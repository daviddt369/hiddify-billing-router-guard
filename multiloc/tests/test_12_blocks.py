"""Private-address and BitTorrent blocks for the country entrances (audit checklist B3). In the first-tier xray the country copies of the geosite rule
sit exactly where the stock geosite rule sits (BEFORE the blocks - parity with the stock behaviour); the final rule of a country sits AFTER the blocks, so
everything else that comes in through a country inbound passes the private / BitTorrent blocks exactly like it does for an ordinary profile.
The rule ORDER is pinned in test_08 (TestRuleOrderOfTheCountryEntrances); this module proves the BEHAVIOUR with real processes:
real xray client -> HAProxy:443 -> hiddify-xray -> router -> exit -> stand "internet".

Run: /opt/hiddify-manager/.venv313/bin/python -W ignore -m unittest -v test_12_blocks   (from /work/tests; ~1 min)"""
import os
import socket
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stand_lib import *  # noqa: E402,F403

PRIVATE = "http://127.0.0.1:18080/"        # the stand's whoami listens on 0.0.0.0:18080: a connection that is NOT blocked would be answered
FOREIGN_HOST, FOREIGN_PORT = "13.0.0.20", 18080
BT_HANDSHAKE = b"\x13BitTorrent protocol" + b"\x00" * 8 + b"\x11" * 20 + b"-ST0001-0123456789ab" + b"\r\n\r\n"
PLAIN_HTTP = b"GET / HTTP/1.0\r\n\r\n"
PROFILES = {}


def setUpModule():
    global PROFILES
    for u in ("stand-exit-a", "stand-exit-b", "stand-shim-a", "stand-shim-b"):
        systemd("start", u)
    reset_locations_to_stand_default()
    apply_router()
    ls = dict(links("tester"))
    for tname in ("XHTTP", "WS", "Trojan"):
        PROFILES[f"DE {tname}"] = ls[f"🇩🇪 Германия · {tname}"]
        PROFILES[f"US {tname}"] = ls[f"🇺🇸 США · {tname}"]
    PROFILES["ORDINARY ws"] = ls["RU_GLOBAL tls WS direct vless"]                      # the profile everybody has today
    PROFILES["ORDINARY xhttp"] = ls["RU_GLOBAL tls_h2 xhttp direct vless dl=h2"]
    PROFILES["ORDINARY trojan"] = ls["RU_GLOBAL tls WS direct trojan"]


def raw_exchange(client_port, host_ip, port, payload, timeout=6.0):
    """SOCKS5 CONNECT through the local client xray, send `payload`, report what comes back:
    ('data', bytes) = somebody answered, ('closed', b'') = the connection was closed without an answer, ('timeout' | 'refused' | 'socks-error', b'')"""
    s = socket.create_connection(("127.0.0.1", client_port), timeout=timeout)
    s.settimeout(timeout)
    try:
        s.sendall(b"\x05\x01\x00")
        if s.recv(2) != b"\x05\x00":
            return "socks-error", b""
        s.sendall(b"\x05\x01\x00\x01" + socket.inet_aton(host_ip) + struct.pack(">H", port))
        rep = s.recv(10)
        if len(rep) < 2 or rep[1] != 0:
            return "refused", b""
        s.sendall(payload)
        try:
            data = s.recv(4096)
        except socket.timeout:
            return "timeout", b""
        except ConnectionResetError:
            return "closed", b""
        return ("data", data) if data else ("closed", b"")
    finally:
        s.close()


def direct_exchange(host_ip, port, payload, timeout=6.0):
    """the same exchange without any proxy: what the stand's own service does with this payload"""
    s = socket.create_connection((host_ip, port), timeout=timeout)
    s.settimeout(timeout)
    try:
        s.sendall(payload)
        try:
            data = s.recv(4096)
        except socket.timeout:
            return "timeout", b""
        return ("data", data) if data else ("closed", b"")
    finally:
        s.close()


class TestBlocksApplyToCountries(unittest.TestCase):
    def test_calibration_the_stand_service_answers_both_payloads_when_nothing_blocks_them(self):
        """without this the 'blocked' results below could be an artefact of the service itself"""
        self.assertEqual(direct_exchange(FOREIGN_HOST, FOREIGN_PORT, PLAIN_HTTP)[0], "data")
        kind, data = direct_exchange(FOREIGN_HOST, FOREIGN_PORT, BT_HANDSHAKE)
        self.assertEqual(kind, "data", "the stand service must answer even a BitTorrent-looking payload (400), otherwise the test proves nothing")
        self.assertEqual(direct_exchange("127.0.0.1", FOREIGN_PORT, PLAIN_HTTP)[0], "data", "the private address is reachable when nothing blocks it")

    def test_a_private_address_is_blocked_for_every_country_profile_exactly_like_for_the_ordinary_ones(self):
        for name, link in PROFILES.items():
            with self.subTest(profile=name):
                with sc.Client(link) as c:
                    status, body = c.get(FOREIGN_IP, timeout=10)
                    self.assertIn(body.get("via") if isinstance(body, dict) else None, ("exit-a", "exit-b"), f"{name}: the path itself works (positive control)")
                    try:
                        status, body = c.get(PRIVATE, timeout=8)
                    except ConnectionError:                                   # curl: empty reply / connection closed = blocked
                        status, body = None, None
                    self.assertFalse(isinstance(body, dict) and body.get("who"),
                                     f"{name}: a private address answered through the proxy: {status} {body}")

    def test_bittorrent_is_blocked_for_every_country_profile_exactly_like_for_the_ordinary_ones(self):
        for name, link in PROFILES.items():
            with self.subTest(profile=name):
                with sc.Client(link) as c:
                    self.assertEqual(raw_exchange(c.port, FOREIGN_HOST, FOREIGN_PORT, PLAIN_HTTP)[0], "data", f"{name}: plain HTTP over the raw path works (control)")
                    kind, _ = raw_exchange(c.port, FOREIGN_HOST, FOREIGN_PORT, BT_HANDSHAKE)
                    self.assertEqual(kind, "closed", f"{name}: the BitTorrent handshake must be dropped by the xray block, got {kind}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
