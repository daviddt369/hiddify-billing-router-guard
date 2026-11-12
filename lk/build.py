#!/usr/bin/env python3
"""Build the registration pages of the trial access (separate host, e.g. hd.<domain>).

Three files:
  index.html  the FRONT: a plain cinema-style page ("HD Кино") and the "solve the example" gate. Nothing about the service is in it, neither visible nor in the source.
              The example is random, its answer is always the gate number (--gate, required).
  app.html    the working part (phone form, the access that is given, the connection instruction). nginx hands it out only with ?k=<gate>.
  app.js      its script, same rule.
The front's script pins the CSP of the page by hash; app.js is loaded from the same origin ('self') after the gate is passed.
usage: build.py --outdir DIR [--brand NAME] [--bot BOT_USERNAME|-] --gate N"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
e = html.escape

FRONT_CSS = r"""
:root{--bg:#0b0d14;--card:#141826;--card2:#1b2133;--ink:#fff;--mute:#d3d9ea;--line:rgba(255,255,255,.14);--acc:#ffb020;--acc-ink:#1a1200;--field:rgba(255,255,255,.07);--bad:#ffb4ab;--bad-bg:rgba(255,120,110,.16);--ok:#8fe3ab;--soft:rgba(255,176,32,.12)}
body.neon{--bg:#0a0b1c;--card:#12142b;--card2:#191c3a;--line:rgba(154,151,255,.22);--acc:#9a97ff;--acc-ink:#0a0b1c;--field:rgba(255,255,255,.06);--soft:rgba(154,151,255,.10)}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;min-height:100vh;color:var(--ink);font:16px/1.5 Inter,system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;background:radial-gradient(900px 520px at 85% -10%,rgba(255,176,32,.12),transparent 62%),radial-gradient(760px 560px at -10% 35%,rgba(90,60,200,.18),transparent 62%),var(--bg);background-attachment:fixed}
body.front{background:#05070d url(/cw.webp) center top/cover no-repeat fixed}
@media (max-aspect-ratio:1/1){body.front{background-image:url(/ct.webp);background-position:center top}}
.shade{position:fixed;inset:0;pointer-events:none;background:linear-gradient(180deg,rgba(5,7,13,0) 34%,rgba(5,7,13,.55) 62%,rgba(5,7,13,.92) 100%)}
.spacer{height:min(60vh,520px)}
#front .card{position:relative;background:rgba(10,14,24,.88);backdrop-filter:blur(6px)}
#front{min-height:100vh;min-height:100svh;display:flex;flex-direction:column;justify-content:center;align-items:center;padding-bottom:12px}
#front .spacer{display:none}
#front .card{width:100%;max-width:440px;padding:20px;margin:0;text-align:center}
#front .sum{font-size:2rem;margin:6px 0}
#front label{margin:8px 0 6px;text-align:left}
#front input{text-align:center}
#front .btn{margin:14px auto 0;min-height:52px}
#front .foot{text-align:center;color:var(--mute);font-size:.8rem;margin:10px 0 0}
body.neon{background:radial-gradient(900px 520px at 88% -8%,rgba(154,151,255,.13),transparent 62%),radial-gradient(760px 560px at -12% 34%,rgba(70,50,160,.20),transparent 62%),var(--bg);background-attachment:fixed}
.wrap{max-width:560px;margin:0 auto;padding:0 16px 40px}
.fh{display:flex;align-items:center;gap:10px;min-height:64px;font-weight:900;font-size:1.2rem}
.flogo{width:32px;height:32px;border-radius:10px;background:var(--acc);color:var(--acc-ink);display:grid;place-items:center;font-size:.95rem}
.nav{display:flex;gap:16px;color:var(--mute);font-size:.9rem;font-weight:600;overflow:hidden;white-space:nowrap}
.hero h1{margin:14px 0 8px;font-size:1.9rem;line-height:1.15;font-weight:900;letter-spacing:-.4px}
.hero p{margin:0 0 18px;color:var(--mute)}
.tiles{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:0 0 22px}
.tile{min-width:0;overflow:hidden;aspect-ratio:2/3;border-radius:14px;padding:10px;display:flex;flex-direction:column;justify-content:flex-end;font-size:.84rem;word-break:break-word;font-weight:900;line-height:1.15;border:1px solid var(--line)}
.tile span{display:block;font-weight:700;font-size:.78rem;opacity:.95;margin-top:3px}
.t1{background:linear-gradient(160deg,#5b3df5,#1c1450)}.t2{background:linear-gradient(160deg,#ef476f,#4a0f23)}.t3{background:linear-gradient(160deg,#ffb020,#5a3500)}
.t4{background:linear-gradient(160deg,#06b6d4,#073b4c)}.t5{background:linear-gradient(160deg,#22c55e,#0b3b1d)}.t6{background:linear-gradient(160deg,#a855f7,#2b0f4a)}
.card{background:linear-gradient(180deg,var(--card2),var(--card));border:1px solid var(--line);border-radius:22px;padding:22px;box-shadow:0 14px 44px rgba(0,0,0,.5);margin:0 0 16px}
h1.c{margin:0 0 6px;font-size:1.45rem;font-weight:900;letter-spacing:-.3px;line-height:1.2}
.kicker{color:var(--acc);font-weight:800;font-size:.8rem;letter-spacing:.14em;text-transform:uppercase;margin:0 0 6px}
.kicker:before{content:"> "}
p{margin:0 0 12px;color:var(--mute)}
label{display:block;font-weight:700;margin:14px 0 7px}
input{width:100%;min-height:54px;border:1px solid var(--line);background:var(--field);color:var(--ink);border-radius:14px;padding:0 16px;font:inherit;font-size:1.1rem;outline:0}
input:focus{border-color:var(--acc);box-shadow:0 0 0 3px var(--soft)}
input::placeholder{color:#b9c4d8}
.sum{font-size:2.4rem;font-weight:900;text-align:center;margin:8px 0 6px;font-variant-numeric:tabular-nums;letter-spacing:-.5px}
.btn{appearance:none;display:flex;align-items:center;justify-content:center;gap:8px;width:100%;min-height:56px;border:0;border-radius:15px;background:var(--acc);color:var(--acc-ink);font:inherit;font-weight:900;font-size:1.05rem;cursor:pointer;margin-top:16px;text-decoration:none;text-align:center}
body.neon .btn{box-shadow:0 0 26px rgba(154,151,255,.28)}
.btn[disabled]{opacity:.6;cursor:default}
.btn.sec{background:transparent;color:var(--ink);border:1px solid rgba(154,151,255,.55);box-shadow:none}
.msg{display:none;border-radius:14px;padding:13px 15px;margin:14px 0 0;font-weight:700;border:1px solid currentColor;background:var(--bad-bg);color:var(--bad)}
.msg.on{display:block}
.msg.ok{background:var(--soft);color:var(--ok)}
.hint{color:var(--mute);font-size:.92rem;margin:10px 0 0}
div.foot{display:block;text-align:center;color:var(--mute);font-size:.85rem;margin:20px 0 0}
[hidden]{display:none!important}
/* the working part (visible only after the gate) */
.logo{width:32px;height:32px;border-radius:10px;background:var(--acc);display:grid;place-items:center;box-shadow:0 0 20px rgba(154,151,255,.35)}
.logo svg{width:19px;height:19px}
.ah{display:flex;align-items:center;gap:10px;min-height:64px;font-weight:900;font-size:1.2rem}
.link{display:flex;gap:8px;align-items:center;margin-top:12px}
.link .mono{flex:1;min-width:0;border:1px solid var(--line);background:var(--field);border-radius:14px;min-height:50px;padding:0 14px;display:flex;align-items:center;font:.85rem ui-monospace,Consolas,monospace;color:var(--mute);overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.link .btn{width:auto;margin:0;min-height:50px;padding:0 16px;font-size:.95rem}
ol{margin:6px 0 0;padding-left:22px;color:var(--mute)}
li{margin:6px 0}
.hp{position:absolute;left:-9999px;width:1px;height:1px;opacity:0}
h2{margin:18px 0 6px;font-size:1.12rem;font-weight:900}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0 12px}
.chip{appearance:none;border:1px solid var(--line);background:var(--field);color:var(--ink);border-radius:999px;padding:9px 16px;min-height:46px;cursor:pointer;font:inherit;font-weight:700}
.chip[aria-pressed=true]{background:var(--acc);border-color:var(--acc);color:var(--acc-ink);font-weight:900}
.rec{background:var(--soft);border:1px solid var(--line);border-radius:14px;padding:13px 15px;margin:0 0 12px}
.rec p{margin:0 0 6px;color:var(--ink)}.rec p:last-child{margin:0}
.app{display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap;border:1px solid var(--line);background:var(--field);border-radius:16px;padding:14px 16px;margin:0 0 10px}
.app.first{border-color:var(--acc);box-shadow:0 0 0 2px var(--soft),0 0 22px rgba(154,151,255,.16)}
.app .n{font-weight:900;font-size:1.05rem}.app .b{background:var(--acc);color:var(--acc-ink);border-radius:999px;font-size:.72rem;font-weight:900;padding:3px 10px;margin-left:8px}
.app .d{display:block;color:var(--mute);font-size:.88rem;margin-top:3px}
.app .go{display:flex;gap:8px;flex-wrap:wrap}
.app .btn{width:auto;margin:0;min-height:46px;padding:0 16px;font-size:.95rem}
details{border:1px solid var(--line);border-radius:16px;padding:0 16px;margin:0 0 10px;background:var(--field)}
summary{cursor:pointer;font-weight:800;padding:13px 0;min-height:50px;display:flex;align-items:center}
details .in{padding:0 0 16px}
.rg h4{margin:16px 0 4px;font-size:1rem}.rg ul{margin:6px 0 0;padding-left:20px;color:var(--mute)}.rg li{margin:6px 0}.rg .adr{border:1px solid var(--line);border-radius:12px;padding:11px 13px;margin:10px 0}.rg dl{display:grid;grid-template-columns:max-content 1fr;gap:4px 12px;margin:6px 0 0}.rg dt{color:var(--mute)}.rg dd{margin:0;font-weight:700;word-break:break-word}
"""

FRONT_BODY = r"""
<div class="shade" aria-hidden="true"></div>
<div class="wrap" id="front">
 <div class="spacer"></div>
 <section class="card" id="gate">
  <div class="kicker">Портал</div>
  <h1 class="c">Для регистрации на портале решите пример</h1>
  <div class="sum" id="sum"></div>
  <label for="ans">Ответ</label>
  <input id="ans" type="number" inputmode="numeric" autocomplete="off" placeholder="Введите ответ">
  <div class="msg" id="gateerr" role="alert">Неверно, попробуйте ещё раз.</div>
  <button class="btn" id="gatebtn" type="button">Зарегистрироваться</button>
 </section>
 <div class="foot">&copy; Кино Иго-Го</div>
</div>
<div class="wrap" id="app" hidden></div>
"""

FRONT_JS = r"""(function(){
var d=document,$=function(i){return d.getElementById(i)};
var G=@@GATE@@,a=100+Math.floor(Math.random()*(G-199)),b=G-a;
$('sum').textContent=a+' + '+b+' = ?';
function bad(){$('gateerr').classList.add('on');$('ans').select()}
function open(){
 $('gatebtn').disabled=true;
 fetch('/app.html?k='+G,{cache:'no-store',credentials:'omit'}).then(function(r){if(!r.ok)throw 0;return r.text()}).then(function(h){
  $('app').innerHTML=h;$('front').hidden=true;var sh=d.querySelector('.shade');if(sh)sh.hidden=true;$('app').hidden=false;d.body.className='neon';
  var s=d.createElement('script');s.src='/app.js?k='+G;d.body.appendChild(s)
 }).catch(function(){$('gatebtn').disabled=false;bad()})}
function go(){if(parseInt($('ans').value,10)===G)open();else bad()}
$('gatebtn').onclick=go;$('ans').addEventListener('keydown',function(e){if(e.key==='Enter')go()});
$('ans').addEventListener('input',function(){$('gateerr').classList.remove('on')});
})();"""

APP_HTML = r"""
<div id="appmeta" data-bot="@@BOT@@" data-title="@@TITLE@@" hidden></div>
<header class="ah"><span class="logo"><svg viewBox="0 0 24 24" fill="#0a0b1c"><rect x="3" y="13" width="4" height="8" rx="1"/><rect x="10" y="8" width="4" height="13" rx="1"/><rect x="17" y="3" width="4" height="18" rx="1"/></svg></span>@@BRAND@@</header>

<section class="card" id="form">
 <div class="kicker">Пробный доступ</div>
 <h1 class="c">Получить пробный доступ</h1>
 <p>Укажите номер телефона. Этот же номер вы отправите боту в Telegram: так доступ привяжется к вам, и оплата и уведомления будут в одном месте.</p>
 <label for="phone">Телефон</label>
 <input id="phone" type="tel" inputmode="tel" autocomplete="tel" placeholder="+7 999 123-45-67">
 <input class="hp" id="site" type="text" tabindex="-1" autocomplete="off" aria-hidden="true">
 <div class="msg" id="err" role="alert"></div>
 <button class="btn" id="go" type="button">Получить пробный доступ</button>
</section>

<div id="after" hidden>
 <section class="card" id="done">
  <div class="kicker">Готово</div>
  <h1 class="c" id="donetitle">Доступ создан</h1>
  <div class="msg ok on" id="donemsg"></div>
  <div id="linkbox" hidden>
   <p class="hint" style="margin-top:14px">Ваша ссылка подписки. Не передавайте её другим людям.</p>
   <div class="link"><div class="mono" id="sub"></div><button class="btn" id="copy" type="button">Скопировать</button></div>
   <a class="btn" id="cab" href="#" rel="noopener">Открыть личный кабинет</a>
  </div>
  <div id="tg" hidden>
   <p class="hint" style="margin-top:18px"><b>Привяжите Telegram</b> (это нужно один раз):</p>
   <ol><li>Откройте бота и нажмите «Старт».</li><li>Нажмите кнопку «Отправить номер телефона».</li></ol>
   <a class="btn sec" id="bot" href="#" rel="noopener">Открыть бота в Telegram</a>
  </div>
 </section>

 <section class="card" id="how">
  <div class="kicker">Инструкция</div>
  <h1 class="c">Как подключиться</h1>
  <h2>1. Установите приложение</h2>
  <div class="chips" id="plats" role="group" aria-label="Платформа"></div>
  <div class="rec" id="rec"></div>
  <div id="apps"></div>
  <details id="regionhelp"><summary>iPhone и iPad: как сменить регион App Store (нужно для всех приложений)</summary><div class="in">@@REGION@@</div></details>
  <h2>2. Добавьте подписку</h2>
  <ol>@@STEPS@@</ol>
  <p class="hint">@@NOTE@@</p>
  <h2>Если не подключается</h2>
  <ol>@@TROUBLE@@</ol>
 </section>
</div>
<script type="application/json" id="guide">@@GUIDEJSON@@</script>
"""

APP_JS = r"""(function(){
var d=document,$=function(i){return d.getElementById(i)};
var meta=$('appmeta'),BOT=meta.getAttribute('data-bot')||'';
if(meta.getAttribute('data-title'))d.title=meta.getAttribute('data-title');
var t0=Date.now();
function show(id){$('form').hidden=id!=='form';$('after').hidden=id!=='after'}
function err(t){var e=$('err');e.textContent=t;e.classList.add('on')}
function copy(text,btn){var o=btn.textContent,done=function(){btn.textContent='Скопировано';setTimeout(function(){btn.textContent=o},1800)};
 function fb(){var x=d.createElement('textarea');x.value=text;x.setAttribute('readonly','');x.style.position='fixed';x.style.opacity='0';d.body.appendChild(x);x.select();try{d.execCommand('copy')}catch(e){}d.body.removeChild(x);done()}
 if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(text).then(done,fb)}else fb()}
function finish(j,code){
 var ok=(code===201||code===200)&&j&&(j.status==='created'||j.status==='exists');
 if(!ok){err((j&&j.message)||'Не удалось выполнить запрос. Попробуйте позже.');$('go').disabled=false;return}
 show('after');$('donemsg').textContent=j.message||'';
 if(j.status==='created'&&j.sub_url&&/^https:\/\//.test(j.sub_url)){$('donetitle').textContent='Доступ создан';$('linkbox').hidden=false;$('sub').textContent=j.sub_url;$('cab').href=j.sub_url;$('copy').onclick=function(){copy(j.sub_url,this)}}
 else{$('donetitle').textContent='Доступ уже выдавался'}
 if(BOT){$('tg').hidden=false;$('bot').href='https://t.me/'+BOT}
 how();window.scrollTo(0,0)}
$('go').onclick=function(){
 var v=($('phone').value||'').trim(),n=v.replace(/[^0-9]/g,'').length;
 $('err').classList.remove('on');
 if(n<10||n>15){err('Введите номер в формате +7 999 123-45-67');$('phone').focus();return}
 if($('site').value||Date.now()-t0<1500){err('Подождите секунду и нажмите ещё раз.');return}
 $('go').disabled=true;
 fetch('/api/trial',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone:v}),credentials:'omit',cache:'no-store'})
  .then(function(r){return r.json().then(function(j){return[j,r.status]},function(){return[null,r.status]})})
  .then(function(x){finish(x[0],x[1])})
  .catch(function(){err('Нет связи с сервером. Попробуйте позже.');$('go').disabled=false})};
$('phone').addEventListener('keydown',function(e){if(e.key==='Enter')$('go').click()});
$('phone').addEventListener('input',function(){$('err').classList.remove('on')});
/* ---- the instruction (shown together with the access) ---- */
var G=JSON.parse($('guide').textContent),ua=navigator.userAgent||'';
var plat=/iPhone|iPad|iPod/i.test(ua)||(/Macintosh/.test(ua)&&navigator.maxTouchPoints>1)?'ios':/Android/i.test(ua)?'android':/Macintosh/.test(ua)?'macos':'windows';
function mk(tag,cls,txt){var e=d.createElement(tag);if(cls)e.className=cls;if(txt!==undefined)e.textContent=txt;return e}
function how(){
 var c=$('plats');c.innerHTML='';G.platforms.forEach(function(p){var b=mk('button','chip',p[1]);b.type='button';b.setAttribute('aria-pressed',p[0]===plat?'true':'false');b.onclick=function(){plat=p[0];how()};c.appendChild(b)});
 var r=$('rec');r.innerHTML='';G.recommend[plat==='ios'?'ios':'other'].forEach(function(x){var p=mk('p');if(x.b){p.appendChild(mk('b','',x.b));p.appendChild(d.createTextNode(' '))}p.appendChild(d.createTextNode(x.t));r.appendChild(p)});
 $('regionhelp').open=plat==='ios';
 var a=$('apps');a.innerHTML='';G.order[plat].forEach(function(k,i){var x=G.apps[k],row=mk('div','app'+(i?'':' first')),l=mk('span'),n=mk('span','n',x.name);l.appendChild(n);
  if(!i)l.appendChild(mk('span','b','Рекомендуем'));l.appendChild(mk('span','d',G.desc[k]));var go=mk('span','go');
  (x.downloads[plat]||[]).forEach(function(l2,j){var A=mk('a','btn'+(j?' sec':''),l2[0]);A.href=l2[1];A.rel='noopener';A.target='_blank';go.appendChild(A)});
  row.appendChild(l);row.appendChild(go);a.appendChild(row)})}
$('phone').focus();
})();"""


def region_html(rh: dict) -> str:
    parts = [f'<p class="hint" style="margin:0 0 8px">{e(rh["intro"])}</p>']
    for b in rh["blocks"]:
        parts.append(f'<h4>{e(b["title"])}</h4>')
        if b.get("steps"):
            parts.append("<ol>" + "".join(f"<li>{e(s)}</li>" for s in b["steps"]) + "</ol>")
        if b.get("items"):
            parts.append("<ul>" + "".join(f'<li><b>{e(i["label"])}:</b> {e(i["text"])}</li>' for i in b["items"]) + "</ul>")
        if b is rh["blocks"][-1]:
            for ad in rh.get("addresses", []):
                parts.append(f'<div class="adr"><b>{e(ad["title"])}</b><dl>' + "".join(f"<dt>{e(k)}</dt><dd>{e(v)}</dd>" for k, v in ad["rows"]) + "</dl></div>")
            if rh.get("after_addresses"):
                parts.append(f'<p style="margin:8px 0 0">{e(rh["after_addresses"])}</p>')
    if rh.get("warnings"):
        parts.append('<div class="hint" style="margin-top:10px">' + "".join(f'<p style="margin:4px 0">{e(w)}</p>' for w in rh["warnings"]) + "</div>")
    return '<div class="rg">' + "".join(parts) + "</div>"


def build(brand: str, bot: str, gate: int) -> dict[str, str]:
    if bot and bot != "-" and not re.match(r"^[A-Za-z][A-Za-z0-9_]{3,31}$", bot):
        raise ValueError("bot username: 4-32 letters, digits or _")
    if not (300 <= gate <= 9999):
        raise ValueError("gate number: 300..9999")
    bot = "" if bot == "-" else bot
    g = json.load(open(os.path.join(HERE, "guide.json"), encoding="utf-8"))
    data = {k: g[k] for k in ("platforms", "desc", "order", "recommend")}
    data["apps"] = {k: {"name": v["name"], "downloads": {p: [[x[0], x[1]] for x in r] for p, r in v["downloads"].items()}} for k, v in g["apps"].items()}
    guide_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    app_html = (APP_HTML.replace("@@BOT@@", e(bot)).replace("@@TITLE@@", e(f"{brand}: пробный доступ")).replace("@@BRAND@@", e(brand))
                .replace("@@REGION@@", region_html(g["region_help"])).replace("@@STEPS@@", "".join(f"<li>{e(s)}</li>" for s in g["steps"]))
                .replace("@@NOTE@@", e(g["note"])).replace("@@TROUBLE@@", "".join(f"<li>{e(s)}</li>" for s in g["trouble"])).replace("@@GUIDEJSON@@", guide_json))
    front_js = FRONT_JS.replace("@@GATE@@", str(gate))
    sha = "'sha256-%s'" % base64.b64encode(hashlib.sha256(front_js.encode("utf-8")).digest()).decode("ascii")
    csp = f"default-src 'none'; style-src 'unsafe-inline'; script-src 'self' {sha}; connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    index = f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="dark">
<meta name="robots" content="noindex,nofollow">
<meta name="referrer" content="no-referrer">
<meta http-equiv="Content-Security-Policy" content="{e(csp)}">
<title>Кино Иго-Го — фильмы и сериалы</title>
<style>{FRONT_CSS}</style>
</head>
<body class="front">
{FRONT_BODY}
<script>{front_js}</script>
</body>
</html>
"""
    return {"index.html": index, "app.html": app_html, "app.js": APP_JS}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--brand", default="Чебурнатор")
    ap.add_argument("--bot", default="-")
    ap.add_argument("--gate", type=int, required=True)
    a = ap.parse_args()
    try:
        files = build(a.brand, a.bot, a.gate)
    except ValueError as exc:
        print(f"build: {exc}", file=sys.stderr)
        return 2
    os.makedirs(a.outdir, exist_ok=True)
    for name, text in files.items():
        with open(os.path.join(a.outdir, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    print("build: wrote " + ", ".join(f"{n} ({len(t)} bytes)" for n, t in files.items()) + f" into {a.outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
