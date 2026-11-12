"""Put the shared instruction (lk/guide.json) into the cabinet: the apps, the order and the recommendation per platform (the script data of cabinet.html) and the full region guide.
usage: apply_guide.py <lk/guide.json> <cabinet.html>      (then run make_region_block.py for the region block)"""
import html
import json
import re
import sys

g = json.load(open(sys.argv[1], encoding="utf-8"))
path = sys.argv[2]
t = open(path, encoding="utf-8", newline="").read().replace("\r\n", "\n")

apps = {k: {"n": v["name"], "b": "", "d": {p: [[l[0], l[1]] for l in r] for p, r in v["downloads"].items()}} for k, v in g["apps"].items()}
rec = {k: ["<b>%s</b> %s" % (html.escape(x["b"]), html.escape(x["t"])) if x.get("b") else html.escape(x["t"]) for x in v] for k, v in g["recommend"].items()}
J = lambda o: json.dumps(o, ensure_ascii=False, separators=(",", ":"))
block = (f"var APPS={J(apps)};\n"
         f"var ORDER={J(g['order'])};\n"
         f"var DESC={J(g['desc'])};\n"
         f"var REC={J(rec)};\n")
m = re.search(r"var APPS=.*?\nvar ua=navigator\.userAgent", t, re.S)
assert m, "APPS ... var ua not found"
t = t[:m.start()] + block + "var ua=navigator.userAgent" + t[m.end():]

old = "$('#regionhelp').hidden=plat!=='ios';"
assert t.count(old) == 1
t = t.replace(old, "$('#regionhelp').hidden=plat!=='ios';$('#regionhelp').open=plat==='ios';")
open(path, "w", encoding="utf-8", newline="").write(t)
print("cabinet script data replaced:", len(block), "chars")
