"""stand probe of the ported V2 shell: login like the tests do, then the page, the shell API and the dashboard API. run inside the stand:  /opt/hiddify-manager/.venv313/bin/python /work/stage2/cp16-probe.py"""
import json
import subprocess
import sys

import requests

BASE, CA, PP, PA = "https://ru.stand.test", "/etc/stand/ca.crt", "stPx0Main1234", "stAdm0Path12345"
uuid = subprocess.run(["mysql", "-N", "-B", "hiddifypanel", "-e", "select uuid from admin_user order by id limit 1"], capture_output=True, text=True).stdout.strip()
s = requests.Session()
s.verify = CA
s.get(f"{BASE}/{PA}/{uuid}/", timeout=30)
for url in (f"/{PP}/admin/v2/", f"/{PP}/api/v2/admin/me/", f"/{PP}/api/v2/admin/dashboard/?days=30", f"/{PP}/api/v2/admin/dashboard/?include=system", f"/{PP}/api/v2/admin/dashboard/disk/",
            f"/{PP}/__admin_v2_bootstrap", f"/{PP}/admin/"):
    r = s.get(BASE + url, timeout=60)
    body = r.text
    info = ""
    if "json" in r.headers.get("content-type", ""):
        try:
            d = r.json()
            info = " keys=" + ",".join(list(d)[:10]) if isinstance(d, dict) else f" list[{len(d)}]"
        except Exception as e:
            info = f" (json error {e})"
    print(f"{r.status_code} {len(body):>7}  {url.replace(PP, '<pp>')[:70]}{info}")
    if r.status_code >= 400:
        print("    ", body[:300].replace("\n", " "))
