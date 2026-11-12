"""STAND: mock of the GitHub API + release downloads + the Telegram sendMessage call (port 8098). State in /tmp/mock:
CURRENT = tag the API reports ("DOWN" = the API answers 500); <tag>/<asset> = files; <tag>/digest_override.json = {asset: "sha256:..."} to lie about a digest; tg.log = Telegram posts."""
import hashlib
import http.server
import json
import os

ROOT = "/tmp/mock"


class H(http.server.BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        cur = open(f"{ROOT}/CURRENT").read().strip()
        if self.path.endswith("/releases/latest"):
            if cur == "DOWN":
                return self._send(500, b"{}")
            ov = {}
            if os.path.exists(f"{ROOT}/{cur}/digest_override.json"):
                ov = json.load(open(f"{ROOT}/{cur}/digest_override.json"))
            assets = []
            for n in sorted(os.listdir(f"{ROOT}/{cur}")):
                if n.endswith(".dat"):
                    d = ov.get(n) or "sha256:" + hashlib.sha256(open(f"{ROOT}/{cur}/{n}", "rb").read()).hexdigest()
                    assets.append({"name": n, "digest": d})
            return self._send(200, json.dumps({"tag_name": cur, "assets": assets}).encode())
        if "/releases/download/" in self.path:
            tag, asset = self.path.split("/releases/download/")[1].split("/")[:2]
            p = f"{ROOT}/{tag}/{asset}"
            if os.path.exists(p):
                return self._send(200, open(p, "rb").read(), "application/octet-stream")
        self._send(404, b"{}")

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        d = json.loads(self.rfile.read(n))
        open(f"{ROOT}/tg.log", "a").write(f"chat={d['chat_id']} {d['text'][:160]}\n")
        self._send(200, b'{"ok":true}')

    def log_message(self, *a):
        pass


http.server.HTTPServer(("127.0.0.1", 8098), H).serve_forever()
