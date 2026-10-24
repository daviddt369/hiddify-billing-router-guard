"""every link of the new V2 menu answers: run in the stand"""
import subprocess

import requests

BASE, CA, PP, PA = "https://ru.stand.test", "/etc/stand/ca.crt", "stPx0Main1234", "stAdm0Path12345"
uuid = subprocess.run(["mysql", "-N", "-B", "hiddifypanel", "-e", "select uuid from admin_user order by id limit 1"], capture_output=True, text=True).stdout.strip()
s = requests.Session()
s.verify = CA
s.get(f"{BASE}/{PA}/{uuid}/", timeout=30)
import json, re
html = s.get(f"{BASE}/{PP}/admin/v2/", timeout=60).text
menu = json.loads(re.search(r"window\.__ADMIN_MENU__ = (.*?);\s*window\.__ADMIN_NOTICES__", html, re.S).group(1))
bad = 0
for grp in menu:
    print(f"[{grp['label']}]")
    for it in grp["items"]:
        url = it["url"]
        if it.get("method") == "post" or it.get("target") == "_blank" or url.startswith(("tg://", "http://", "https://github")):
            print(f"   --    {it['label']:28} ({'post action' if it.get('method') == 'post' else 'external'})")
            continue
        r = s.get(url if url.startswith("http") else BASE + url, timeout=60)
        flag = "OK " if r.status_code == 200 else "BAD"
        bad += r.status_code != 200
        print(f"   {flag} {r.status_code} {it['label']:28} {url.replace(PP, '<pp>')[:70]}")
print("broken links:", bad)
