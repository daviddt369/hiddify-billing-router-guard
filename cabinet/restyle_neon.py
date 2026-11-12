"""Restyle the cabinet: dark "neon" look (near-black green, lime accent, WHITE text), hero card + three tiles, bottom navigation on phones.
Only CSS, the markup of the status block and the header are changed; every id the script of the page uses stays.   usage: restyle_neon.py <cabinet.html> [<out>]"""
import re
import sys

src = sys.argv[1]
dst = sys.argv[2] if len(sys.argv) > 2 else src
t = open(src, encoding="utf-8", newline="").read().replace("\r\n", "\n")

CSS = r""":root{--bg:#050a06;--card:#0d1610;--card2:#132218;--ink:#ffffff;--mute:#d6e1d8;--line:rgba(140,255,46,.20);--acc:#8cff2e;--acc-ink:#04100a;--soft:rgba(140,255,46,.10);--hdr:#050a06;--ok:#a6ff63;--ok-bg:rgba(140,255,46,.16);--warn:#ffd861;--warn-bg:rgba(255,210,74,.15);--bad:#ffa39b;--bad-bg:rgba(255,120,110,.16);--glow:0 0 0 1px rgba(140,255,46,.06),0 14px 44px rgba(0,0,0,.5);--field:rgba(255,255,255,.05);--nav:rgba(13,22,16,.94)}
:root[data-theme=light]{--bg:#f3f8f1;--card:#ffffff;--card2:#f2f8ee;--ink:#07120a;--mute:#2b3a2e;--line:#cfe0ca;--acc:#46a600;--acc-ink:#04100a;--soft:#e6f5da;--hdr:#ffffff;--ok:#12631b;--ok-bg:#dcf4cf;--warn:#6f4a00;--warn-bg:#fff0c2;--bad:#9d211a;--bad-bg:#fde3e0;--glow:0 1px 2px rgba(0,0,0,.06),0 10px 28px rgba(0,0,0,.07);--field:#f1f6ef;--nav:rgba(255,255,255,.96)}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;scroll-behavior:smooth}
body{margin:0;color:var(--ink);font:16px/1.5 Inter,system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;padding-bottom:110px;background:radial-gradient(900px 520px at 88% -8%,rgba(140,255,46,.13),transparent 62%),radial-gradient(760px 560px at -12% 34%,rgba(30,130,50,.20),transparent 62%),var(--bg);background-attachment:fixed}
:root[data-theme=light] body{background:var(--bg)}
button,input{font:inherit;color:inherit}
.wrap{max-width:760px;margin:0 auto;padding:0 16px}
header.top{background:var(--hdr);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:5}
.bar{display:flex;align-items:center;gap:12px;min-height:60px}
.brand{display:flex;align-items:center;gap:10px;font-weight:900;font-size:1.2rem;letter-spacing:-.2px}
.logo{width:32px;height:32px;border-radius:10px;background:var(--acc);display:grid;place-items:center;box-shadow:0 0 20px rgba(140,255,46,.35)}
.logo svg{width:19px;height:19px}
.grow{flex:1}
.ibtn{appearance:none;border:1px solid var(--line);background:var(--soft);color:var(--ink);border-radius:12px;min-width:42px;height:42px;cursor:pointer;display:inline-grid;place-items:center;padding:0 12px;font-weight:800}
.tabs{display:flex;gap:6px;position:fixed;left:12px;right:12px;bottom:calc(12px + env(safe-area-inset-bottom));z-index:30;background:var(--nav);border:1px solid var(--line);border-radius:22px;padding:6px;box-shadow:0 10px 40px rgba(0,0,0,.55);max-width:736px;margin:0 auto}
.tab{appearance:none;border:0;background:transparent;color:var(--mute);padding:12px 10px;font-weight:800;cursor:pointer;white-space:nowrap;flex:1;border-radius:16px;text-align:center}
.tab[aria-selected=true]{background:var(--acc);color:var(--acc-ink)}
main{padding-top:18px}
.col{display:grid;gap:16px;min-width:0}
.card{min-width:0;background:linear-gradient(180deg,var(--card2),var(--card));border:1px solid var(--line);border-radius:22px;padding:20px;box-shadow:var(--glow)}
.card h2{margin:0 0 6px;font-size:1.3rem;font-weight:900;letter-spacing:-.3px}
.card .sub{margin:0 0 14px;color:var(--mute)}
.kicker{color:var(--acc);font-weight:800;font-size:.8rem;letter-spacing:.14em;text-transform:uppercase}
:root[data-theme=light] .kicker{color:var(--ok)}
.kicker:before{content:"> "}
.who{font-weight:900;font-size:1.6rem;line-height:1.2;word-break:break-all;margin:4px 0 10px;letter-spacing:-.3px}
.heroline{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.planname{font-weight:800;color:var(--ink)}
.pill{display:inline-flex;align-items:center;gap:7px;border-radius:999px;padding:5px 13px;font-weight:800;font-size:.86rem;border:1px solid currentColor}
.pill.ok{background:var(--ok-bg);color:var(--ok)}.pill.warn{background:var(--warn-bg);color:var(--warn)}.pill.bad{background:var(--bad-bg);color:var(--bad)}
.dot{width:8px;height:8px;border-radius:50%;background:currentColor}
.bigdays{margin:2px 0 4px;display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.bigdays .num{font-size:3.3rem;font-weight:900;line-height:1;letter-spacing:-1px;color:var(--ink)}
.bigdays .u{font-size:1.15rem;font-weight:800;color:var(--mute)}
.klabel{color:var(--mute);font-size:.88rem;font-weight:700;letter-spacing:.04em;text-transform:uppercase}
.bar-bg{height:10px;border-radius:99px;background:rgba(255,255,255,.12);overflow:hidden;margin-top:10px}
:root[data-theme=light] .bar-bg{background:#d9e6d4}
.bar-fg{height:100%;border-radius:99px;background:var(--acc);transition:width .35s;box-shadow:0 0 14px rgba(140,255,46,.5)}
.bar-fg.warn{background:#ffc531;box-shadow:none}.bar-fg.bad{background:#ff7b70;box-shadow:none}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.tile{min-width:0;background:linear-gradient(180deg,var(--card2),var(--card));border:1px solid var(--line);border-radius:20px;padding:16px;box-shadow:var(--glow)}
#paidwrap{grid-column:1/-1}#paidwrap .v{font-size:1.25rem}
.tile .v{font-size:1.7rem;font-weight:900;line-height:1.15;margin:6px 0 2px;letter-spacing:-.4px}
.tile .v small{font-size:.9rem;font-weight:700;color:var(--mute);letter-spacing:0}
.banner{border-radius:14px;padding:13px 15px;margin:16px 0 0;font-weight:700;border:1px solid currentColor}
.banner.warn{background:var(--warn-bg);color:var(--warn)}.banner.bad{background:var(--bad-bg);color:var(--bad)}
.plans{display:grid;gap:10px;margin:6px 0 14px}
.plan{appearance:none;display:flex;justify-content:space-between;align-items:center;gap:12px;width:100%;text-align:left;border:1px solid var(--line);background:var(--field);border-radius:16px;padding:12px 16px;min-height:60px;cursor:pointer;color:var(--ink)}
.plan[aria-pressed=true]{border-color:var(--acc);box-shadow:0 0 0 2px var(--soft),0 0 22px rgba(140,255,46,.18);background:var(--soft)}
.plan b{display:block}.plan small{display:block;color:var(--mute);font-size:.84rem}
.plan .pp{font-weight:900;white-space:nowrap;font-size:1.05rem}
.plan .cur{font-size:.72rem;font-weight:800;color:var(--acc);margin-left:6px}
:root[data-theme=light] .plan .cur{color:var(--ok)}
.field{display:flex;align-items:center;gap:8px;border:1px solid var(--line);background:var(--field);border-radius:14px;padding:0 14px;min-height:50px}
.field input{flex:1;border:0;background:transparent;outline:0;min-width:0;color:var(--ink)}
.field .lbl{color:var(--mute);font-size:.88rem}
.btn{appearance:none;display:flex;align-items:center;justify-content:center;gap:8px;width:100%;min-height:54px;border:0;border-radius:15px;background:var(--acc);color:var(--acc-ink);font-weight:900;font-size:1.04rem;cursor:pointer;margin-top:14px;text-decoration:none;text-align:center;box-shadow:0 0 26px rgba(140,255,46,.28)}
.btn.sec{background:transparent;color:var(--ink);border:1px solid rgba(140,255,46,.55);box-shadow:none}
:root[data-theme=light] .btn.sec{border-color:#7fbf45}
.btn.small{min-height:46px;width:auto;padding:0 18px;margin:0;font-size:.97rem}
.btn.top{margin-top:16px}
.herobtns{display:flex;gap:10px;flex-wrap:wrap;margin-top:18px}
.herobtns .btn{width:auto;flex:1 1 170px;margin:0}
.hint{color:var(--mute);font-size:.9rem;margin:8px 0 0}
.lbl2{color:var(--mute);font-size:.9rem;font-weight:700;margin:16px 0 7px}
.lbl2:first-of-type{margin-top:0}
.err{display:none;color:var(--bad);font-size:.9rem;margin:6px 0 0}.err.on{display:block}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin:6px 0 14px}
.chip{appearance:none;border:1px solid var(--line);background:var(--field);color:var(--ink);border-radius:999px;padding:9px 16px;min-height:46px;cursor:pointer;font-weight:700}
.chip[aria-pressed=true]{background:var(--acc);border-color:var(--acc);color:var(--acc-ink);font-weight:900}
.rec{background:var(--soft);border:1px solid var(--line);border-radius:14px;padding:13px 15px;margin:0 0 12px}
.rec p{margin:0 0 6px}.rec p:last-child{margin:0}
.app{min-width:0;display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap;border:1px solid var(--line);background:var(--field);border-radius:16px;padding:14px 16px;margin:0 0 10px}
.app.first{border-color:var(--acc);box-shadow:0 0 0 2px var(--soft),0 0 22px rgba(140,255,46,.16)}
.app .n{font-weight:900;font-size:1.05rem}.app .b{background:var(--acc);color:var(--acc-ink);border-radius:999px;font-size:.72rem;font-weight:900;padding:3px 10px;margin-left:8px}
.app .d{display:block;color:var(--mute);font-size:.88rem;font-weight:400;margin-top:3px}
details{min-width:0;border:1px solid var(--line);border-radius:16px;padding:0 16px;margin:0 0 10px;background:var(--field)}
summary{cursor:pointer;font-weight:800;padding:13px 0;min-height:50px;display:flex;align-items:center;gap:11px}
summary .num{display:inline-grid;place-items:center;min-width:28px;height:28px;border-radius:50%;background:var(--acc);color:var(--acc-ink);font-size:.86rem;font-weight:900}
details .in{padding:0 0 16px}
details ol{margin:6px 0 0;padding-left:22px}details li{margin:6px 0}
.rg h4{margin:16px 0 4px;font-size:1rem}.rg ul{margin:6px 0 0;padding-left:20px}.rg li{margin:6px 0}.rg .adr{border:1px solid var(--line);border-radius:12px;padding:11px 13px;margin:10px 0}.rg dl{display:grid;grid-template-columns:max-content 1fr;gap:4px 12px;margin:6px 0 0}.rg dt{color:var(--mute)}.rg dd{margin:0;font-weight:700;word-break:break-word}
.linkbox{display:flex;gap:8px;align-items:center}
.linkbox .field{flex:1;min-width:0}
.field .mono{flex:1;min-width:0}
.mono{font-family:ui-monospace,Consolas,monospace;font-size:.85rem;color:var(--mute);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.row{display:flex;gap:10px;flex-wrap:wrap;margin-top:14px}
.qr{display:grid;place-items:center;margin:16px 0 4px}
.qr canvas,.qr img{border-radius:14px;background:#fff;padding:10px}
.timer{color:var(--bad);font-weight:800;font-variant-numeric:tabular-nums}
.list{display:grid;gap:10px}
.cfg{display:flex;justify-content:space-between;align-items:center;gap:10px;border:1px solid var(--line);background:var(--field);border-radius:14px;padding:11px 15px}
.cfg span{min-width:0;overflow:hidden;text-overflow:ellipsis}
.toast{position:fixed;left:50%;bottom:92px;transform:translateX(-50%) translateY(30px);background:#fff;color:#04100a;font-weight:700;border-radius:14px;padding:13px 17px;max-width:92vw;opacity:0;pointer-events:none;transition:.25s;z-index:40}
.toast.on{opacity:1;transform:translateX(-50%)}
footer{color:var(--mute);font-size:.86rem;text-align:center;margin:26px 0 0}
footer a{color:var(--mute)}
[hidden]{display:none!important}
@media (min-width:900px){.tabs{left:50%;right:auto;width:736px;transform:translateX(-50%)}}
"""

# 1. the stylesheet
t = re.sub(r"<style>.*?</style>", lambda m: "<style>\n" + CSS + "</style>", t, count=1, flags=re.S)
# the page is dark by default now (the light variant is only an option): the OS preference no longer decides
t = t.replace('<meta name="color-scheme" content="light dark">', '<meta name="color-scheme" content="dark light">')

# 2. header: brand + theme button; the tabs become a bottom bar outside the header
m = re.search(r'<header class="top">.*?</header>', t, flags=re.S)
assert m
header = '''<header class="top">
 <div class="wrap">
  <div class="bar">
   <div class="brand"><span class="logo"><svg viewBox="0 0 24 24" fill="#04100a"><rect x="3" y="13" width="4" height="8" rx="1"/><rect x="10" y="8" width="4" height="13" rx="1"/><rect x="17" y="3" width="4" height="18" rx="1"/></svg></span>{{ brand }}</div>
   <span class="grow"></span>
   <button class="ibtn" id="theme" type="button" aria-label="Тема">◐</button>
  </div>
 </div>
</header>
<nav class="tabs" role="tablist" aria-label="Разделы">
 <button class="tab" type="button" role="tab" data-tab="home" aria-selected="true">Главная</button>
 <button class="tab" type="button" role="tab" data-tab="configs" aria-selected="false">Все конфиги</button>
 <button class="tab" type="button" role="tab" data-tab="tg" aria-selected="false" id="tab-btn-tg" hidden>TG-прокси</button>
</nav>'''
t = t[:m.start()] + header + t[m.end():]

# 3. the status block: hero card + three tiles (all ids kept)
m = re.search(r'  <div class="card" id="status">.*?\n  </div>\n', t, flags=re.S)
assert m
status = '''  <div class="card" id="status">
   <div class="kicker">Здравствуйте</div>
   <div class="who" id="who"></div>
   <div class="heroline"><span class="planname" id="planname"></span><span class="pill ok" id="pill"><i class="dot"></i><span id="pilltxt"></span></span></div>
   <div class="klabel" style="margin-top:18px">Осталось</div>
   <div class="bigdays"><span class="num" id="days"></span><span class="u" id="dayunit"></span></div>
   <div class="bar-bg"><div class="bar-fg" id="daybar"></div></div>
   <div class="hint" id="until"></div>
   <div class="banner warn" id="banner" hidden></div>
   <div class="herobtns">
    <a class="btn sec" id="goconnect" href="#linkcard">Подключить устройство</a>
    <a class="btn" id="cta" href="#" rel="noopener" hidden>Оплатить в Telegram-боте</a>
    <a class="btn" id="goextend" href="#renew">Продлить</a>
   </div>
  </div>

  <div class="tiles">
   <div class="tile"><div class="klabel">Трафик</div><div class="v"><span id="gb"></span> <small id="gbtotal"></small></div><div class="bar-bg"><div class="bar-fg" id="gbbar"></div></div><div class="hint" id="reset"></div></div>
   <div class="tile"><div class="klabel">Устройства</div><div class="v" id="devices"></div></div>
   <div class="tile" id="paidwrap"><div class="klabel">Оплачено</div><div class="v" id="paid"></div></div>
  </div>
'''
t = t[:m.start()] + status + t[m.end():]

# 4. script: the extend button follows the payment block, the theme button works from the dark default
old = "if(!D.pay.bot_url&&!siteOk){$('#renew').hidden=true}"
assert t.count(old) == 1
t = t.replace(old, old + "\nif($('#renew').hidden||D.need_pay)$('#goextend').hidden=true;")
old = "d=r.getAttribute('data-theme')==='dark'||(!r.getAttribute('data-theme')&&matchMedia('(prefers-color-scheme:dark)').matches),n=d?'light':'dark'"
assert t.count(old) == 1
t = t.replace(old, "d=r.getAttribute('data-theme')!=='light',n=d?'light':'dark'")
open(dst, "w", encoding="utf-8", newline="").write(t)
print("restyled:", len(t), "chars")
