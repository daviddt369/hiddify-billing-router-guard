"""WCAG contrast of the text/background pairs of the cabinet theme (both themes). usage: contrast_check.py <cabinet.html>"""
import re
import sys

t = open(sys.argv[1], encoding="utf-8").read()
PAL = sys.argv[2] if len(sys.argv) > 2 else ""          # lime (the file default) | purple | amber
css = re.search(r"<style>(.*?)</style>", t, re.S).group(1)


def vars_of(block):
    return dict(re.findall(r"--([a-z0-9-]+):([^;}]+)", block))


base = vars_of(re.search(r"^:root\{(.*?)\}$", css, re.M).group(1))
light_raw = vars_of(re.search(r"^:root\[data-theme=light\]\{(.*?)\}$", css, re.M).group(1))
pd = re.search(r"^:root\[data-palette=%s\]\{(.*?)\}$" % PAL, css, re.M) if PAL not in ("", "lime") else None
pl = re.search(r"^:root\[data-theme=light\]\[data-palette=%s\]\{(.*?)\}$" % PAL, css, re.M) if pd else None
pal_d = vars_of(pd.group(1)) if pd else {}
pal_l = vars_of(pl.group(1)) if pl else {}
# the same order as the browser's cascade
dark = {**base, **pal_d}
light = {**base, **pal_d, **light_raw, **pal_l}


def fill(v, vs):
    return re.sub(r"var\(--([a-z0-9-]+)\)", lambda m: vs.get(m.group(1), m.group(0)), v)


dark = {k: fill(v, dark) for k, v in dark.items()}
light = {k: fill(v, light) for k, v in light.items()}


def rgba(v, over=None):
    v = v.strip()
    m = re.match(r"#([0-9a-f]{6})$", v, re.I)
    if m:
        h = m.group(1)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)), 1.0
    m = re.match(r"rgba?\(([^)]*)\)", v)
    p = [x.strip() for x in m.group(1).split(",")]
    return tuple(float(x) for x in p[:3]), float(p[3]) if len(p) > 3 else 1.0


def flat(fg, bg):
    (c, a) = fg
    return tuple(c[i] * a + bg[i] * (1 - a) for i in range(3))


def lum(c):
    def ch(x):
        x /= 255
        return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c[0]) + 0.7152 * ch(c[1]) + 0.0722 * ch(c[2])


def ratio(a, b):
    la, lb = lum(a), lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def run(name, v):
    bg = rgba(v["bg"])[0]
    card = rgba(v["card"])[0]
    card2 = rgba(v["card2"])[0]
    soft = flat(rgba(v["soft"]), card)
    pairs = [
        ("main text on the page", rgba(v["ink"])[0], bg),
        ("main text on a card", rgba(v["ink"])[0], card),
        ("main text on the lighter card edge", rgba(v["ink"])[0], card2),
        ("secondary text on a card", rgba(v["mute"])[0], card),
        ("secondary text on the lighter card edge", rgba(v["mute"])[0], card2),
        ("secondary text on the 'soft' panel", rgba(v["mute"])[0], soft),
        ("button text on the accent", rgba(v["acc-ink"])[0], rgba(v["acc"])[0]),
        ("accent as text/border on a card", rgba(v["acc"])[0], card),
        ("pill 'active' (green)", rgba(v["ok"])[0], flat(rgba(v["ok-bg"]), card)),
        ("pill/banner 'soon' (yellow)", rgba(v["warn"])[0], flat(rgba(v["warn-bg"]), card)),
        ("pill/banner 'expired' (red)", rgba(v["bad"])[0], flat(rgba(v["bad-bg"]), card)),
    ]
    print(f"== {name}")
    worst = 99
    for label, fg, b in pairs:
        r = ratio(fg, b)
        worst = min(worst, r)
        print(f"  {r:5.1f}:1  {'AAA' if r >= 7 else ('AA ' if r >= 4.5 else 'LOW')}  {label}")
    return worst


w1 = run("dark (default)", dark)
w2 = run("light", light)
print(f"\nworst pair: dark {w1:.1f}:1, light {w2:.1f}:1 (AA text needs 4.5:1, AAA 7:1)")
sys.exit(0 if min(w1, w2) >= 4.5 else 1)
