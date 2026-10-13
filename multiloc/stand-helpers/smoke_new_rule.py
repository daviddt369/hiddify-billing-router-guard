"""One-minute behaviour smoke check (NOT the test suite) for the owner's final rule:
  * the usual link and EVERY other endpoint are stock for every client and every user (testers included, no User-Agent switching);
  * AUTO / countries only on the dedicated `<user link>/happ/` link (3 buttons) and in the user panel's "All Configs" list;
  * the Telegram bot gives a tester two links (usual + /happ/), everybody else the single usual one.
Run inside the stand:  python3 /work/tools/smoke_new_rule.py"""
import base64
import json
import subprocess
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, "/work/tools/dp")
import stand_client as sc  # noqa: E402

PC = "stCli0Path1234567890abcdef"
T, R = "22222222-2222-4222-8222-222222222222", "11111111-1111-4111-8111-111111111111"
BOT = "/opt/hiddify-manager/.venv313/bin/python /work/tests/bot_runner.py"


def get(uuid, fmt, ua, headers=None):
    h = {"Host": "ru.stand.test", "User-Agent": ua, **(headers or {})}
    r = urllib.request.urlopen(urllib.request.Request(f"{sc.SUB_BASE}/{PC}/{uuid}/{fmt}", headers=h), timeout=60)
    return r.read().decode("utf-8", "replace"), {k.lower(): v for k, v in r.getheaders()}


def decode(b):
    return b if ("://" in b or b.lstrip()[:1] in "[{") else base64.b64decode(b + "=" * (-len(b) % 4)).decode("utf-8", "replace")


def has_locations(txt):
    return any(w in txt for w in ("Германия", "США", "AUTO"))


rows = []


def show(label, ok, extra=""):
    rows.append(ok)
    print(("OK   " if ok else "FAIL ") + label + (f"   {extra}" if extra else ""))


UAS = ["Happ/3.5.0", "INCY/1.0", "HiddifyNext/2.0.5 (android) like ClashMeta v2ray sing-box", "v2RayTun/1.0", "v2rayNG/1.8.5", "v2rayNG/1.8.20",
       "ClashMeta/1.18.0 mihomo", "SFA/1.9.0 (239; sing-box 1.9.0)", "Streisand/1.5", "Mozilla/5.0 (Windows NT 10.0) Chrome/135"]
for ua in UAS:
    body, _ = get(T, "", ua)
    show(f"tester, usual link, {ua[:40]:40} = stock", not has_locations(decode(body)) and "balancers" not in body)
for fmt in ("sub/", "sub64/", "singbox/", "clash/", "clashmeta/", "auto/", "xray/"):
    for ua in ("Happ/3.5.0", "v2rayNG/1.8.5"):
        body, _ = get(T, fmt, ua)
        show(f"tester, {fmt:10} {ua:14} = stock", not has_locations(decode(body)) and "balancers" not in body)

for ua in ("Happ/3.5.0", "INCY/1.0", "Mozilla/5.0 Chrome/135"):
    body, hd = get(T, "happ/", ua)
    j = json.loads(body)
    names = [c["remarks"] for c in j]
    ok = names == ["🟢 AUTO", "🇩🇪 Германия", "🇺🇸 США"] and hd.get("content-type", "").startswith("application/json") and "subscription-userinfo" in hd
    show(f"tester, /happ/ {ua[:22]:22} = 3 buttons + usage header", ok, str(names) if not ok else "")
all_ok = True
for c in json.loads(get(T, "happ/", "Happ/3.5.0")[0]):
    open("/tmp/h.json", "w").write(json.dumps(c))
    r = subprocess.run(["/usr/bin/xray", "run", "-test", "-config", "/tmp/h.json"], capture_output=True, text=True)
    all_ok &= r.returncode == 0
show("each /happ/ button is accepted by the real xray (-test)", all_ok)
_, hd_happ = get(T, "happ/", "Happ/3.5.0")
_, hd_usual = get(T, "", "Happ/3.5.0")
show("tester, /happ/: the apps' Site button (profile-web-page-url) opens the personal cabinet, like the usual link",
     hd_happ.get("profile-web-page-url") == hd_usual.get("profile-web-page-url") and hd_happ.get("profile-web-page-url", "").endswith(f"/{T}/"), str(hd_happ.get("profile-web-page-url")))
body, _ = get(R, "happ/", "Happ/3.5.0")
show("regular user, /happ/ = stock JSON list (no groups, no countries)", "balancers" not in body and not has_locations(body) and isinstance(json.loads(body), list))
body, _ = get(R, "", "Happ/3.5.0")
show("regular user, usual link in Happ = stock plain list", not has_locations(decode(body)))

for who, key, expect in (("tester", T, True), ("regular", R, False)):
    req = urllib.request.Request(f"{sc.SUB_BASE}/{PC}/api/v2/user/all-configs/", headers={"Host": "ru.stand.test", "Hiddify-API-Key": key, "User-Agent": "Mozilla/5.0 Chrome/135"})
    items = json.loads(urllib.request.urlopen(req, timeout=60).read())
    ctry = [i["name"] for i in items if "Германия" in i["name"] or "США" in i["name"]]
    show(f"user panel All Configs ({who}): " + ("6 country profiles" if expect else "unchanged, 22 items"), (len(ctry) == 6) if expect else (not ctry and len(items) == 22))
    happ_item = [i for i in items if i["name"].startswith("Happ")]
    ok_h = (len(happ_item) == 1 and items.index(happ_item[0]) == 1 and happ_item[0]["link"].startswith(f"https://ru.stand.test/{PC}/{key}/happ/")) if expect else (not happ_item)
    show(f"user panel All Configs ({who}): " + ("Happ entry right after Auto, pointing at /happ/" if expect else "no Happ entry"), ok_h)

for who, key, two in (("tester", T, True), ("regular", R, False)):
    out = subprocess.run(["bash", "-c", f"cd /opt/hiddify-manager/hiddify-panel && {BOT} {key} 2>/dev/null | grep ^JSON:"], capture_output=True, text=True).stdout.strip()
    v = json.loads(out[5:])
    links = [ln for ln in v["message"].splitlines() if ln.startswith("https://")]
    usual = f"https://ru.stand.test/{PC}/{key}/"
    ok = (links == [usual, usual + "happ/"] and len(v["keyboard"]) == 2) if two else (links == [usual] and len(v["keyboard"]) == 1)
    show(f"bot message for the {who}: " + ("usual link + /happ/ link" if two else "single usual link, as before"), ok, str(links) if not ok else "")

print("\nALL OK" if all(rows) else "\nSOME FAILED")
