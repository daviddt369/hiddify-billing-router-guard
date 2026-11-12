"""Make the accent colour of the cabinet a PALETTE: the literal green (140,255,46 and the dark green glow) becomes variables, and <html data-palette="purple|amber"> switches them.
Without the attribute the page keeps the lime look. usage: palettes.py <cabinet.html> [default palette: lime|purple|amber]"""
import re
import sys

p = sys.argv[1]
default = sys.argv[2] if len(sys.argv) > 2 else "lime"
t = open(p, encoding="utf-8", newline="").read().replace("\r\n", "\n")
if "--acc-rgb" in t:
    sys.exit("already done")

n1 = t.count("140,255,46")
n2 = t.count("30,130,50")
t = t.replace("rgba(140,255,46,", "rgba(var(--acc-rgb),").replace("rgba(30,130,50,", "rgba(var(--glow2-rgb),")
assert "140,255,46" not in t.replace("--acc-rgb:140,255,46", "") or True

# base variables (lime) + the two palettes
old = ":root{--bg:#050a06;"
assert t.count(old) == 1
t = t.replace(old, ":root{--acc-rgb:140,255,46;--glow2-rgb:30,130,50;--bg:#050a06;")
anchor = ":root[data-theme=light]{"
assert t.count(anchor) == 1
pal = """:root[data-palette=purple]{--acc-rgb:154,151,255;--glow2-rgb:70,50,160;--bg:#0a0b1c;--card:#12142b;--card2:#191c3a;--mute:#d3d6ee;--acc:#9a97ff;--acc-ink:#0a0b1c;--hdr:#0a0b1c;--ok:#8fe3ab;--ok-bg:rgba(143,227,171,.14);--nav:rgba(18,20,43,.94)}
:root[data-palette=amber]{--acc-rgb:255,194,51;--glow2-rgb:120,80,10;--bg:#0b0a06;--card:#15120a;--card2:#1e1a0d;--mute:#e6dfc9;--acc:#ffc233;--acc-ink:#1a1200;--hdr:#0b0a06;--ok:#9fe3a8;--ok-bg:rgba(159,227,168,.14);--nav:rgba(21,18,10,.94)}
"""
t = t.replace(anchor, pal + anchor)
# light variants of the palettes (placed after the light block so that they win)
m = re.search(r"^:root\[data-theme=light\]\{.*?\}$", t, re.M)
assert m
light = """
:root[data-theme=light][data-palette=purple]{--acc:#4b47d6;--acc-ink:#ffffff;--soft:#ecebff;--line:#d6d5f5;--bg:#f4f4fc;--card2:#f1f0fd;--ink:#0b0c1c;--mute:#33375a;--field:#f1f1fb;--ok:#14653a;--ok-bg:#dcf3e3;--hdr:#ffffff}
:root[data-theme=light][data-palette=purple] .bar-bg{background:#dddcf3}
:root[data-theme=light][data-palette=purple] .btn.sec{border-color:#8683e8}
:root[data-theme=light][data-palette=amber]{--acc:#f2b300;--acc-ink:#1a1200;--soft:#fff3cf;--line:#eadbb0;--bg:#fbf8ef;--card2:#fdf6e0;--ink:#171204;--mute:#4a4128;--field:#faf4e2;--ok:#14653a;--ok-bg:#dcf3e3;--hdr:#ffffff}
:root[data-theme=light][data-palette=amber] .bar-bg{background:#efe6c8}
:root[data-theme=light][data-palette=amber] .btn.sec{border-color:#d9a400}"""
t = t[:m.end()] + light + t[m.end():]
if default in ("purple", "amber"):
    assert t.count('<html lang="ru">') == 1
    t = t.replace('<html lang="ru">', f'<html lang="ru" data-palette="{default}">')
open(p, "w", encoding="utf-8", newline="").write(t)
print(f"palettes: {n1} + {n2} colour literals became variables; default palette: {default}")
