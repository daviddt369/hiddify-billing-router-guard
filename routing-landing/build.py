#!/usr/bin/env python3
"""Render the routing landing page (static HTML) from clients.json + RoscomVPN deeplinks.

The deeplinks are validated but never modified: the exact string from the upstream
DEFAULT.DEEPLINK file is placed into the page's <a href>.
"""
from __future__ import annotations

import argparse
import base64
import datetime
import hashlib
import html
import json
import os
import re
import sys
import tempfile

DEEPLINK_RE = {
    "happ": re.compile(r"^happ://routing/onadd/([A-Za-z0-9+/]+={0,2})$"),
    "incy": re.compile(r"^incy://routing/onadd/([A-Za-z0-9+/]+={0,2})$"),
}
MAX_DEEPLINK_LEN = 16000
URL_OK = re.compile(r"^https://[A-Za-z0-9.\-]+(/[A-Za-z0-9._~%!$&'()*+,;=:@/\-?#]*)?$")


class DeeplinkError(ValueError):
    pass


def read_deeplink(path: str, scheme: str) -> tuple[str, dict]:
    """Return (deeplink, decoded profile). Raises DeeplinkError if it is not a sane routing deeplink."""
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    link = raw.strip()
    if not link or "\n" in link or "\r" in link or len(link) > MAX_DEEPLINK_LEN:
        raise DeeplinkError(f"{scheme}: unexpected shape (empty, multi-line or too long)")
    m = DEEPLINK_RE[scheme].match(link)
    if not m:
        raise DeeplinkError(f"{scheme}: does not match {scheme}://routing/onadd/<base64>")
    payload = m.group(1)
    try:
        profile = json.loads(base64.b64decode(payload + "=" * (-len(payload) % 4), validate=True))
    except Exception as exc:  # noqa: BLE001 - any decode problem means "invalid"
        raise DeeplinkError(f"{scheme}: payload is not base64 JSON ({exc})") from exc
    if not isinstance(profile, dict) or not profile.get("Name"):
        raise DeeplinkError(f"{scheme}: profile JSON has no Name")
    if not any(k in profile for k in ("DirectSites", "ProxySites", "BlockSites", "DirectIp")):
        raise DeeplinkError(f"{scheme}: profile JSON has no routing rules")
    return link, profile


def esc(s: str) -> str:
    return html.escape(s, quote=True)


def safe_url(url: str) -> str:
    if not URL_OK.match(url):
        raise ValueError(f"refusing unsafe URL: {url!r}")
    return url


CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#14171c;--mute:#5b6472;--line:#e1e5ea;--acc:#1f6feb;--acc-ink:#fff;--soft:#eaf1fe;--warn:#fff6e0;--warn-line:#f1d28a;--ok:#0f7b46}
@media (prefers-color-scheme:dark){:root{--bg:#0f1216;--card:#171b21;--ink:#e8ebef;--mute:#9aa4b2;--line:#262c35;--acc:#5b9bff;--acc-ink:#07111f;--soft:#16233a;--warn:#2a2412;--warn-line:#5b4a16;--ok:#4cc38a}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif}
main{max-width:680px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:1.65rem;line-height:1.2;margin:8px 0 6px}
h2{font-size:1.15rem;margin:28px 0 10px;display:flex;gap:10px;align-items:center}
h3{font-size:1.05rem;margin:0}
.n{display:inline-grid;place-items:center;min-width:26px;height:26px;border-radius:50%;background:var(--acc);color:var(--acc-ink);font-size:.85rem;font-weight:700}
.lead{color:var(--mute);margin:0 0 4px}
.notice{background:var(--soft);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin:14px 0}
.chips{display:none;gap:8px;flex-wrap:wrap;margin:0 0 12px}
.js .chips{display:flex}
.chip{appearance:none;border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:999px;padding:9px 14px;min-height:44px;font:inherit;cursor:pointer}
.chip[aria-pressed=true]{background:var(--acc);border-color:var(--acc);color:var(--acc-ink);font-weight:600}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;margin:0 0 12px}
.card.focus{border-color:var(--acc);box-shadow:0 0 0 2px var(--soft)}
.card p{margin:8px 0 0;color:var(--mute);font-size:.95rem}
.badge{display:inline-block;margin-left:8px;padding:2px 9px;border-radius:999px;background:var(--soft);color:var(--acc);font-size:.75rem;font-weight:600;vertical-align:middle}
.dl{margin-top:10px}
.js .dl{display:none}
.js .dl.on{display:block}
.pl{font-size:.8rem;color:var(--mute);margin:10px 0 2px}
.js .pl{display:none}
.btns{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}
.btn{display:inline-flex;align-items:center;justify-content:center;min-height:44px;padding:10px 16px;border-radius:10px;background:var(--acc);color:var(--acc-ink);text-decoration:none;font:inherit;font-weight:600;border:0;cursor:pointer;text-align:center}
.btn.sec{background:transparent;color:var(--acc);border:1px solid var(--acc)}
.btn.big{display:flex;width:100%;min-height:54px;font-size:1.05rem;border-radius:12px}
.hint{font-size:.85rem;color:var(--mute);margin:6px 0 0}
details{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:0 14px;margin:0 0 8px}
summary{cursor:pointer;padding:12px 0;font-weight:600;min-height:44px}
details ol{margin:0 0 12px;padding-left:22px}
details li{margin:4px 0}
.warn{background:var(--warn);border:1px solid var(--warn-line);border-radius:12px;padding:12px 14px;margin:12px 0;font-size:.92rem}
.warn p{margin:6px 0}
ul.tt{margin:8px 0;padding-left:22px}
.rt{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
.rt-main{flex:1 1 200px;min-width:0}
.card .sub{margin:4px 0 0;color:var(--mute);font-size:.9rem}
.pill{display:inline-flex;align-items:center;min-height:36px;padding:6px 14px;border-radius:999px;background:var(--soft);color:var(--acc);font-weight:600;font-size:.9rem;border:1px solid var(--line)}
.important{background:var(--card);border:2px solid var(--acc);border-radius:14px;padding:14px;margin:14px 0}
.important h3{font-size:1.1rem;margin:0 0 6px}
.important p{margin:6px 0}
.important details{background:var(--bg)}
.important .hint{margin:0 0 8px}
.ios-only{display:block}
.js .ios-only{display:none}
.js[data-plat="ios"] .ios-only{display:block}
.region h3{font-size:1.02rem;margin:20px 0 6px}
.region ol{margin:6px 0 0;padding-left:22px}
.region li{margin:5px 0}
ul.kv{margin:6px 0;padding-left:20px}
ul.kv li{margin:6px 0}
.addr{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin:8px 0}
.addr h4{margin:0 0 8px;font-size:1rem}
.addr dl{margin:0;display:grid;grid-template-columns:max-content 1fr;gap:4px 14px}
.addr dt{color:var(--mute)}
.addr dd{margin:0;font-weight:600;word-break:break-word}
.pay{background:var(--soft);border:1px solid var(--line);border-radius:16px;padding:18px 16px;margin:30px 0 0}
.pay h2{margin:0 0 6px;font-size:1.25rem}
.pay p{margin:0 0 14px;color:var(--mute)}
.pay-form{display:none}
.js .pay-form{display:block}
.js .pay-nojs{display:none}
.pay-form form{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;margin:0 0 12px}
.pay-plans{display:grid;gap:8px;margin:0 0 12px}
.plan{appearance:none;display:flex;justify-content:space-between;align-items:center;gap:12px;width:100%;text-align:left;border:1px solid var(--line);background:var(--bg);color:var(--ink);border-radius:12px;padding:10px 14px;min-height:54px;font:inherit;cursor:pointer}
.plan[aria-pressed=true]{border-color:var(--acc);box-shadow:0 0 0 2px var(--soft);background:var(--soft)}
.plan b{display:block}
.plan small{display:block;color:var(--mute);font-size:.82rem;font-weight:400}
.plan .pp{font-weight:700;white-space:nowrap}
.pay-form .ym-input{width:100%;min-height:48px;padding:10px 12px;border:1px solid var(--line);border-radius:10px;background:var(--bg);color:var(--ink);font:inherit}
.pay-form .sumrow{display:flex;justify-content:space-between;align-items:baseline;margin:10px 0 0;color:var(--mute)}
.pay-form .sumrow b{color:var(--ink);font-size:1.1rem}
.pay-form .ym-display-none{display:none}
.pay-err{display:none;color:#b3261e;font-size:.88rem;margin:6px 0 0}
@media (prefers-color-scheme:dark){.pay-err{color:#ff8a80}}
.pay-err.on{display:block}
.pay-form .ym-btn-pay{display:flex;width:100%;min-height:54px;margin-top:12px;align-items:center;justify-content:center;gap:8px;font:inherit;font-size:1.05rem;font-weight:600;border:0;border-radius:12px;background:var(--acc);color:var(--acc-ink);cursor:pointer}
.pay-form .hint{margin:8px 0 0}
.pay-form>.hint:first-child{margin:0 0 12px}
a{color:var(--acc)}
"""

JS = r"""(function(){
var d=document,h=d.documentElement;h.className+=' js';
var ua=navigator.userAgent||'';
var isIos=/iPhone|iPad|iPod/i.test(ua)||(/Macintosh/.test(ua)&&navigator.maxTouchPoints>1);
function detect(){if(isIos)return 'ios';if(/Android/i.test(ua))return 'android';if(/Windows/i.test(ua))return 'windows';if(/Macintosh|Mac OS X/i.test(ua))return 'macos';return 'windows';}
function all(s){return [].slice.call(d.querySelectorAll(s));}
function show(p){h.setAttribute('data-plat',p);all('.dl').forEach(function(e){e.classList.toggle('on',e.getAttribute('data-p')===p);});all('.chip').forEach(function(e){e.setAttribute('aria-pressed',e.getAttribute('data-p')===p?'true':'false');});}
all('.chip').forEach(function(b){b.addEventListener('click',function(){show(b.getAttribute('data-p'));});});
show(detect());
var m=location.pathname.replace(/^\/+|\/+$/g,'');
var card=m&&d.getElementById('c-'+m);
if(card){card.classList.add('focus');var s=d.getElementById('s-'+m);if(s)s.open=true;setTimeout(function(){try{card.scrollIntoView({block:'start'});}catch(e){}},60);}
var cp=d.getElementById('copy-url');
if(cp){cp.addEventListener('click',function(){var u=location.href.split('#')[0];function ok(){cp.textContent='Адрес скопирован';}function fb(){var t=d.createElement('textarea');t.value=u;t.setAttribute('readonly','');t.style.position='fixed';t.style.opacity='0';d.body.appendChild(t);t.select();try{d.execCommand('copy');ok();}catch(e){}d.body.removeChild(t);}if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(u).then(ok,fb);}else{fb();}});}
var auto=(m==='happ'||m==='incy')?d.querySelector('[data-auto="'+m+'"]'):null;
if(auto){var wv=/Telegram|FBAN|FBAV|Instagram|MicroMessenger|; wv\)/i.test(ua)||(isIos&&!/Safari\/|CriOS|FxiOS|EdgiOS/i.test(ua));var seen=false;try{seen=sessionStorage.getItem('ro-'+m)==='1';}catch(e){}
if(!wv&&!seen){try{sessionStorage.setItem('ro-'+m,'1');}catch(e){}setTimeout(function(){location.href=auto.href;},800);}}
})();"""


PAY_JS = r"""(function(){
var d=document,f=d.querySelector('.yoomoney-payment-form');if(!f)return;
var sum=f.querySelector('input[name=sum]'),cn=f.querySelector('input[name=customerNumber]'),ph=f.querySelector('input[name=cps_phone]'),out=f.querySelector('.ym-price-output'),err=d.getElementById('pay-err'),pre=f.getAttribute('data-cn-prefix')||'';
var btns=[].slice.call(f.querySelectorAll('.plan'));
function fmt(n){return n.toFixed(2).replace('.',',')+'\u00a0\u20bd';}
function norm(v){var t=(v||'').replace(/[^0-9+]/g,''),plus=t.charAt(0)==='+',g=t.replace(/\+/g,'');if(!plus){if(g.length===11&&g.charAt(0)==='8')g='7'+g.slice(1);else if(g.length===10)g='7'+g;}return g?'+'+g:'';}
function pick(b){var p=parseFloat(b.getAttribute('data-price'));btns.forEach(function(x){x.setAttribute('aria-pressed',x===b?'true':'false');});sum.value=p;out.textContent=fmt(p);}
function sync(){cn.value=pre+norm(ph.value);}
btns.forEach(function(b){b.addEventListener('click',function(){pick(b);});});
if(btns.length)pick(btns[0]);
ph.addEventListener('input',function(){err.classList.remove('on');sync();});
f.addEventListener('submit',function(e){var v=norm(ph.value);if(!/^\+[0-9]{10,15}$/.test(v)){e.preventDefault();e.stopImmediatePropagation();err.classList.add('on');ph.focus();return;}ph.value=v;sync();},false);
})();"""


def render_downloads(client: dict, platforms: list[dict], dyn: dict) -> str:
    out = []
    for p in platforms:
        items = client.get("downloads", {}).get(p["id"])
        if not items:
            continue
        btns, hints = [], []
        for it in items:
            url = dyn.get(it.get("dynamic", ""), it["url"]) if it.get("dynamic") else it["url"]
            cls = "btn sec" if it.get("secondary") else "btn"
            btns.append(f'<a class="{cls}" href="{esc(safe_url(url))}" rel="noopener noreferrer">{esc(it["label"])}</a>')
            if it.get("hint"):
                hints.append(it["hint"])
        hint_html = "".join(f'<p class="hint">{esc(h)}</p>' for h in dict.fromkeys(hints))
        out.append(
            f'<div class="dl" data-p="{esc(p["id"])}"><div class="pl">{esc(p["name"])}</div>'
            f'<div class="btns">{"".join(btns)}</div>{hint_html}</div>'
        )
    return "".join(out)


def render_routing_card(c: dict, deeplinks: dict[str, str]) -> str:
    """One row of section 3: client name + one-line description + action (import button or 'not needed' pill)."""
    r, none = c.get("routing"), c.get("routing_none")
    if r:
        link = deeplinks[r["scheme"]]
        sub = r["sub"]
        action = f'<a class="btn" data-auto="{esc(r["scheme"])}" href="{esc(link)}">Открыть в {esc(r["label"])}</a>'
    elif none:
        sub = none["sub"]
        action = f'<span class="pill">{esc(none["pill"])}</span>'
    else:
        return ""
    return (
        f'<div class="card rt" id="r-{esc(c["id"])}"><div class="rt-main"><h3>{esc(c["name"])}</h3>'
        f'<p class="sub">{esc(sub)}</p></div>{action}</div>'
    )


def render_after_import(cfg: dict, clients: list[dict]) -> str:
    items = []
    for c in clients:
        ai = (c.get("routing") or {}).get("after_import")
        if not ai:
            continue
        steps = "".join(f"<li>{esc(s)}</li>" for s in ai["steps"])
        items.append(
            f'<details open><summary>{esc(c["name"])}</summary>'
            f'<p class="hint">{esc(ai["prelude"])}</p><ol>{steps}</ol></details>'
        )
    if not items:
        return ""
    return (
        f'<div class="important"><h3>{esc(cfg["after_import_title"])}</h3><p>{esc(cfg["after_import_intro"])}</p>'
        f'{"".join(items)}<p class="hint">{esc(cfg["after_import_note"])}</p></div>'
    )


def render_region(rh: dict) -> str:
    parts = [f'<section class="region ios-only" id="region"><h2>{esc(rh["title"])}</h2><p>{esc(rh["intro"])}</p>']
    for b in rh["blocks"]:
        parts.append(f'<h3>{esc(b["title"])}</h3>')
        if b.get("steps"):
            parts.append("<ol>" + "".join(f"<li>{esc(s)}</li>" for s in b["steps"]) + "</ol>")
        if b.get("items"):
            parts.append(
                '<ul class="kv">'
                + "".join(f'<li><strong>{esc(i["label"])}:</strong> {esc(i["text"])}</li>' for i in b["items"])
                + "</ul>"
            )
        if b is rh["blocks"][-1]:
            for a in rh.get("addresses", []):
                rows = "".join(f"<dt>{esc(k)}</dt><dd>{esc(v)}</dd>" for k, v in a["rows"])
                parts.append(f'<div class="addr"><h4>{esc(a["title"])}</h4><dl>{rows}</dl></div>')
            if rh.get("after_addresses"):
                parts.append(f'<p>{esc(rh["after_addresses"])}</p>')
    if rh.get("warnings"):
        parts.append('<div class="warn">' + "".join(f"<p>{esc(w)}</p>" for w in rh["warnings"]) + "</div>")
    parts.append("</section>")
    return "".join(parts)


YK_ACTION = "https://yookassa.ru/integration/simplepay/payment"
SHOP_ID_RE = re.compile(r"^[0-9]{3,12}$")
# the form posts to this address only; tests may point it at a local echo server
ACTION_OK = re.compile(r"^(https://[A-Za-z0-9.\-]+(/[A-Za-z0-9._~%\-/]*)?|http://127\.0\.0\.1:[0-9]{2,5}(/[A-Za-z0-9._~%\-/]*)?)$")


def money(price: float) -> str:
    """300 -> '300,00 ₽' (the way YooKassa's own form shows it)."""
    return f"{price:,.2f}".replace(",", " ").replace(".", ",") + " ₽"


def price_label(price: float) -> str:
    return str(int(price)) if float(price).is_integer() else f"{price:.2f}".replace(".", ",")


def load_yk_script(path: str | None, form: dict) -> str | None:
    """The YooKassa form script (kept byte for byte). Returned only if it is the pinned one; otherwise the page falls back to the pay link."""
    if not path or not form:
        return None
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    if hashlib.sha256(data).hexdigest() != str(form.get("script_sha256", "")).lower():
        print("build: WARNING: the YooKassa form script does not match the pinned sha256, using the pay link instead", file=sys.stderr)
        return None
    text = data.decode("utf-8")
    if "</script" in text.lower():
        return None
    return text


def render_pay_forms(form: dict, action: str) -> str:
    """The YooKassa 'free sum' form (same fields as the form the cabinet generates: phone, customerNumber, sum, shopId) with tariff buttons that fill the sum."""
    shop = str(form.get("shop_id", ""))
    if not SHOP_ID_RE.match(shop):
        raise ValueError("payment.form.shop_id must be digits")
    plans = form["plans"]
    if not plans:
        raise ValueError("payment.form.plans is empty")
    prefix = str(form.get("customer_prefix", ""))
    buttons = []
    for p in plans:
        price = float(p["price"])
        if not (0 < price < 1_000_000):
            raise ValueError("plan price out of range")
        name, label = str(p["name"]), price_label(price)
        note = f"<small>{esc(str(p['note']))}</small>" if p.get("note") else ""
        buttons.append(
            f'<button type="button" class="plan" aria-pressed="false" data-name="{esc(name)}" data-price="{label.replace(",", ".")}" data-label="{esc(label)}">'
            f'<span><b>{esc(name)}</b>{note}</span><span class="pp">{money(price)}</span></button>')
    first = plans[0]
    first_price = float(first["price"])
    ph = esc(form.get("phone_placeholder", "Телефон"))
    return f"""<form class="yoomoney-payment-form" action="{esc(action)}" method="post" accept-charset="utf-8" data-cn-prefix="{esc(prefix)}">
<div class="pay-plans" role="group" aria-label="Тариф">
{"".join(buttons)}
</div>
<div class="ym-customer-info"><input name="cps_phone" class="ym-input" placeholder="{ph}" aria-label="{ph}" type="text" inputmode="tel" autocomplete="tel" value=""></div>
<div class="pay-err" id="pay-err" role="alert">Укажите номер телефона, привязанный к Telegram.</div>
<input name="customerNumber" type="hidden" value="">
<div class="ym-payment-btn-block">
<div class="ym-display-none"><input name="sum" class="ym-input ym-sum-input ym-required-input" type="number" step="any" value="{price_label(first_price).replace(",", ".")}" readonly></div>
<button type="submit" data-text="Заплатить" class="ym-btn-pay ym-result-price"><span class="ym-text-crop">Заплатить</span> <span class="ym-price-output">{money(first_price)}</span></button>
</div>
<input name="shopId" type="hidden" value="{esc(shop)}">
</form>"""


def build(cfg: dict, deeplinks: dict[str, str], profiles: dict[str, dict], dyn: dict,
          yk_script: str | None = None, pay_action: str | None = None, pay_redirect: str | None = None) -> str:
    platforms = cfg["platforms"]
    clients = cfg["clients"]

    chips = "".join(
        f'<button type="button" class="chip" data-p="{esc(p["id"])}" aria-pressed="false">{esc(p["name"])}</button>'
        for p in platforms
    )

    cards = []
    for c in clients:
        badge = f'<span class="badge">{esc(c["badge"])}</span>' if c.get("badge") else ""
        cards.append(
            f'<article class="card" id="c-{esc(c["id"])}"><h3>{esc(c["name"])}{badge}</h3>'
            f'<p>{esc(c["note"])}</p>{render_downloads(c, platforms, dyn)}</article>'
        )

    steps = []
    for c in clients:
        li = "".join(f"<li>{esc(s)}</li>" for s in c["steps"])
        steps.append(f'<details id="s-{esc(c["id"])}"><summary>{esc(c["name"])}</summary><ol>{li}</ol></details>')

    routing_rows = [render_routing_card(c, deeplinks) for c in clients]
    updated = ""
    for c in clients:
        r = c.get("routing")
        if not r:
            continue
        ts = profiles[r["scheme"]].get("LastUpdated")
        if ts:
            try:
                updated = datetime.datetime.fromtimestamp(int(ts), datetime.timezone.utc).strftime("%d.%m.%Y")
            except (ValueError, OverflowError, OSError):
                updated = ""
            break

    tt = "".join(f"<li>{esc(t)}</li>" for t in cfg["troubleshooting"])
    upd = f" Профиль обновлён {esc(updated)}." if updated else ""
    pay = cfg["payment"]
    pform = pay.get("form") or {}
    action = pay_action or pform.get("action") or YK_ACTION
    if not ACTION_OK.match(action):
        raise ValueError(f"refusing unsafe payment form action: {action!r}")
    # shop_id "0" (or empty) = no YooKassa shop configured on this node: keep the plain pay link
    use_form = bool(pform) and yk_script is not None and str(pform.get("shop_id", "")) not in ("", "0")
    if use_form:
        pay_forms = render_pay_forms(pform, action)
        pay_note = f'<p class="hint">{esc(pform["note"])}</p>' if pform.get("note") else ""
        pay_body = f"""<div class="pay-form">
{pay_note}
{pay_forms}
<p class="hint">{esc(pform.get("footer", "Оплата проходит на стороне ЮKassa: данные карты на этой странице не вводятся."))}</p>
</div>"""
        if pay.get("url"):
            pay_body += f"""
<div class="pay-nojs"><a class="btn big" href="{esc(safe_url(pay["url"]))}" rel="noopener noreferrer">{esc(pay["button"])}</a></div>"""
        pay_script = f"\n<script>{PAY_JS}</script>\n<script>{yk_script}</script>"
        # the page is served with "Referrer-Policy: no-referrer", which makes the browser send "Origin: null" and no Referer to the pay endpoint
        pay_referrer = '\n<meta name="referrer" content="strict-origin-when-cross-origin">'
        form_origin = re.match(r"^(https://[^/]+|http://127\.0\.0\.1:[0-9]+)", action).group(1)
        # the CSP form-action also applies to the REDIRECTS the pay endpoint answers with (YooKassa sends the browser on to its checkout page on another host):
        # without them the browser blocks the redirect and the button "does nothing"
        redirects = pform.get("redirect_origins", ["https:"])
        if not all(re.match(r"^(https:|https://(\*\.)?[A-Za-z0-9.\-]+)$", o) for o in redirects):
            raise ValueError("payment.form.redirect_origins: only https: or https://host entries")
        if pay_redirect:
            redirects = list(redirects) + [pay_redirect]
        form_origin = " ".join([form_origin] + list(redirects))
    else:
        pay_body = f"""<a class="btn big" href="{esc(safe_url(pay["url"]))}" rel="noopener noreferrer">{esc(pay["button"])}</a>"""
        pay_script = ""
        pay_referrer = ""
        form_origin = "'none'"

    def sha(s: str) -> str:
        return "'sha256-%s'" % base64.b64encode(hashlib.sha256(s.encode("utf-8")).digest()).decode("ascii")

    script_src = sha(JS) + (" " + sha(PAY_JS) + " " + sha(yk_script) if use_form else "")
    csp = (
        "default-src 'none'; style-src 'unsafe-inline'; script-src %s; "
        "img-src data:; base-uri 'none'; form-action %s" % (script_src, form_origin)
    )
    title = esc(cfg["title"])
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">{pay_referrer}
<meta name="robots" content="noindex,nofollow">
<meta http-equiv="Content-Security-Policy" content="{esc(csp)}">
<meta property="og:title" content="{title}">
<meta property="og:description" content="Установка приложения, добавление подписки и маршрутизация RoscomVPN.">
<title>{title}</title>
<style>{CSS}</style>
</head>
<body>
<main>
<header>
<h1>{title}</h1>
<p class="lead">Установите приложение, добавьте подписку и (для Happ и INCY) маршрутизацию RoscomVPN.</p>
</header>
<div class="notice">Персональную ссылку подписки выдаёт бот. Не передавайте её другим людям.</div>

<section>
<h2><span class="n">1</span>Установите приложение</h2>
<div class="chips" role="group" aria-label="Платформа">{chips}</div>
{"".join(cards)}
</section>

{render_region(cfg["region_help"])}

<section>
<h2><span class="n">2</span>Добавьте подписку</h2>
{"".join(steps)}
<p class="hint">{esc(cfg["after_steps"])}</p>
</section>

<section>
<h2><span class="n">3</span>Маршрутизация RoscomVPN</h2>
<p class="lead">{esc(cfg["routing_text"])}{upd}</p>
{"".join(routing_rows)}
<div class="warn">
<p><strong>Кнопка не открывает приложение?</strong> Встроенный браузер Telegram часто блокирует такие ссылки. Откройте эту страницу во внешнем браузере (Safari или Chrome): меню «⋯» → «Открыть в браузере» — и нажмите кнопку ещё раз. Приложение должно быть уже установлено.</p>
<div class="btns"><button type="button" class="btn sec" id="copy-url">Скопировать адрес страницы</button></div>
</div>
{render_after_import(cfg, clients)}
</section>

<section>
<h2>Если не подключается</h2>
<ul class="tt">{tt}</ul>
</section>

<section class="pay">
<h2>{esc(pay["title"])}</h2>
<p>{esc(pay["text"])}</p>
{pay_body}
</section>
</main>
<script>{JS}</script>{pay_script}
</body>
</html>
"""


def atomic_write(path: str, text: str) -> None:
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".build-", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clients", required=True)
    ap.add_argument("--happ", required=True)
    ap.add_argument("--incy", required=True)
    ap.add_argument("--dynamic")
    ap.add_argument("--out", required=True)
    ap.add_argument("--yk-script", help="the pinned YooKassa form script (payment.form.script_sha256); without it the page keeps the pay link")
    ap.add_argument("--pay-action", help="TEST ONLY: post the pay form to this address instead of YooKassa")
    ap.add_argument("--pay-redirect", help="TEST ONLY: one more origin the pay endpoint may redirect to")
    args = ap.parse_args()

    with open(args.clients, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    dyn: dict = {}
    if args.dynamic and os.path.exists(args.dynamic):
        try:
            with open(args.dynamic, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            dyn = {k: v for k, v in loaded.items() if isinstance(v, str) and URL_OK.match(v)}
        except (OSError, ValueError):
            dyn = {}

    try:
        happ_link, happ_profile = read_deeplink(args.happ, "happ")
        incy_link, incy_profile = read_deeplink(args.incy, "incy")
    except DeeplinkError as exc:
        print(f"build: INVALID DEEPLINK: {exc}", file=sys.stderr)
        return 2

    yk = load_yk_script(args.yk_script, (cfg.get("payment") or {}).get("form") or {})
    try:
        page = build(cfg, {"happ": happ_link, "incy": incy_link}, {"happ": happ_profile, "incy": incy_profile}, dyn,
                     yk_script=yk, pay_action=args.pay_action, pay_redirect=args.pay_redirect)
    except (ValueError, KeyError, TypeError) as exc:
        print(f"build: INVALID PAYMENT CONFIG: {exc}", file=sys.stderr)
        return 3
    atomic_write(args.out, page)
    print(f"build: wrote {args.out} ({len(page)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
