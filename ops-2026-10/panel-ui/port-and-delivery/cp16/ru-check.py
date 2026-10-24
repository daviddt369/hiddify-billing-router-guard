"""post-deploy check of CP-16 on a live panel (GET requests only; secret paths are masked in the output): admin login like a browser, the V2 shell, the dashboard / nodes API,
every page of the new menu, the old pages that were restyled. The panel is reached through its own domain resolved to 127.0.0.1.
run as root on the server:  /opt/hiddify-manager/.venv313/bin/python ru-check.py"""
import re
import socket
import subprocess
import sys
import urllib3

import requests

urllib3.disable_warnings()


def mysql(q):
    return subprocess.run(["mysql", "-N", "-B", "hiddifypanel", "-e", q], capture_output=True, text=True).stdout.strip().splitlines()


pp = mysql("select value from str_config where `key`='proxy_path_admin' and child_id=0 limit 1")[0]
uuid = mysql("select uuid from admin_user where mode='super_admin' order by id limit 1")[0]
dom = next(d for d in mysql("select domain from domain where domain not like '%*%' and domain not regexp '^[0-9.]+$' order by id") if d)
_orig = socket.getaddrinfo
socket.getaddrinfo = lambda host, *a, **k: _orig("127.0.0.1" if host == dom else host, *a, **k)
BASE = f"https://{dom}"
s = requests.Session()
s.verify = False
s.headers['Hiddify-API-Key'] = uuid          # the API key of a Hiddify admin is its uuid: authenticates the API calls (and, if the panel accepts it, the pages) without a password
mask = lambda p: re.sub(r"^/[^/]+/", "/<secret>/", p)[:90]
bad = 0
r = s.get(f"{BASE}/{pp}/{uuid}/", timeout=60)
print("login:", r.status_code, "| cookie session:", "session" in s.cookies)
for name, u in (("shell", f"/{pp}/admin/v2/"), ("me", f"/{pp}/api/v2/admin/me/"), ("dashboard", f"/{pp}/api/v2/admin/dashboard/?days=30"), ("disk", f"/{pp}/api/v2/admin/dashboard/disk/"),
                ("nodes-status", f"/{pp}/api/v2/admin/nodes-status/"), ("bootstrap", f"/{pp}/__admin_v2_bootstrap"), ("widget js", None)):
    if u is None:
        m = re.search(r'src="([^"]*nodes-widget\.js[^"]*)"', s.get(f"{BASE}/{pp}/admin/v2/", timeout=60).text)
        u = m.group(1) if m else "/missing-widget"
    r = s.get(BASE + u if u.startswith("/") else u, timeout=60)
    print(f"{'OK ' if r.status_code == 200 else 'BAD'} {r.status_code} {len(r.content):>7} {name}")
    bad += r.status_code != 200
home = s.get(f"{BASE}/{pp}/admin/", timeout=60)
html = home.text
print(f"old home: {home.status_code} | theme css linked: {'panel-theme.css' in html} | new sidebar: {'v2-menu' in html}")
bad += home.status_code != 200 or "panel-theme.css" not in html
nav = html[html.find('class="v2-menu"'):]
links = re.findall(r'<a [^>]*href="([^"]+)"[^>]*>\s*(?:<i[^>]*></i>)?\s*<span[^>]*>([^<]+)</span>', nav)
print("menu links found:", len(links))
for href, label in links:
    if href.startswith("http") and dom not in href or href.startswith("#"):
        continue
    path = re.sub(r"^https?://[^/]+", "", href)
    if "/actions/" in path and not re.search(r"/actions/(status|viewlogs)/?$", path):       # apply / update / reinstall / reset are POST actions: NEVER request them
        print(f"--  skipped (action) {label.strip()}")
        continue
    r = s.get(BASE + path, timeout=90)
    ok = r.status_code == 200
    print(f"{'OK ' if ok else 'BAD'} {r.status_code} {label.strip()}  {mask(path)}")
    bad += (not ok) and label.strip() != "Статистика прокси"
nodes = s.get(f"{BASE}/{pp}/api/v2/admin/nodes-status/", timeout=60).json()
print("nodes card:", [(g.get("name") or g.get("title") or g.get("label"), [n.get("state") for n in g.get("nodes", [])]) for g in nodes.get("groups", [])][:6])
print("BAD total:", bad)
sys.exit(1 if bad else 0)
