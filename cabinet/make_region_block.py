"""Put the FULL "change the App Store region" instruction into the cabinet (details#regionhelp), from the text of the routing landing (clients.json region_help).
The block is kept between two comment markers so that it can be regenerated.   usage: make_region_block.py <clients.json> <cabinet.html>"""
import html
import json
import re
import sys

src, dst = sys.argv[1], sys.argv[2]
rh = json.load(open(src, encoding="utf-8"))["region_help"]
e = lambda s: html.escape(s, quote=True)

parts = [f'<p class="hint" style="margin:0 0 8px">{e(rh["intro"])}</p>']
for b in rh["blocks"]:
    parts.append(f'<h4>{e(b["title"])}</h4>')
    if b.get("steps"):
        parts.append("<ol>" + "".join(f"<li>{e(s)}</li>" for s in b["steps"]) + "</ol>")
    if b.get("items"):
        parts.append("<ul>" + "".join(f'<li><b>{e(i["label"])}:</b> {e(i["text"])}</li>' for i in b["items"]) + "</ul>")
    if b is rh["blocks"][-1]:
        for a in rh.get("addresses", []):
            rows = "".join(f"<dt>{e(k)}</dt><dd>{e(v)}</dd>" for k, v in a["rows"])
            parts.append(f'<div class="adr"><b>{e(a["title"])}</b><dl>{rows}</dl></div>')
        if rh.get("after_addresses"):
            parts.append(f'<p style="margin:8px 0 0">{e(rh["after_addresses"])}</p>')
if rh.get("warnings"):
    parts.append('<div class="hint" style="margin-top:10px">' + "".join(f"<p style=\"margin:4px 0\">{e(w)}</p>" for w in rh["warnings"]) + "</div>")
block = "<!-- region-guide:start --><div class=\"rg\">" + "".join(parts) + "</div><!-- region-guide:end -->"

t = open(dst, encoding="utf-8", newline="").read().replace("\r\n", "\n")
m = re.search(r'(<details id="regionhelp"><summary>)(.*?)(</summary><div class="in">)(.*?)(<p class="hint" id="guidewrap".*?</p>)(</div></details>)', t, re.S)
assert m, "regionhelp block not found"
summary = "iPhone: как сменить регион App Store (подробная инструкция)"
t = t[:m.start()] + m.group(1) + summary + m.group(3) + block + m.group(5) + m.group(6) + t[m.end():]
css = ".rg h4{margin:14px 0 4px;font-size:.95rem}.rg ul{margin:6px 0 0;padding-left:20px}.rg li{margin:5px 0}.rg .adr{border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin:8px 0}.rg dl{display:grid;grid-template-columns:max-content 1fr;gap:4px 12px;margin:6px 0 0}.rg dt{color:var(--mute)}.rg dd{margin:0;font-weight:600;word-break:break-word}\n"
if ".rg h4" not in t:
    anchor = "details ol{margin:6px 0 0;padding-left:22px}"
    assert t.count(anchor) == 1
    t = t.replace(anchor, anchor + css.rstrip("\n"))
open(dst, "w", encoding="utf-8", newline="").write(t)
print("region guide block:", len(block), "chars")
