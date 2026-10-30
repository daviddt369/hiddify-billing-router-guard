"""Compact cabinet: a short hero (name, plan, status, date), ONE row of three figures (days, traffic, devices), the link, the payment with two clear options
(Telegram bot: instant; the site: within a day) in an accordion, and the whole instruction in an accordion. Accounts that must pay get the payment first and open.
Only markup, CSS and a few lines of the page script change; every id the script uses stays.   usage: redesign_compact.py <cabinet.html>"""
import re
import sys

p = sys.argv[1]
t = open(p, encoding="utf-8", newline="").read().replace("\r\n", "\n")


def rep(old, new, count=1):
    global t
    assert t.count(old) == count, (t.count(old), old[:70])
    t = t.replace(old, new)


# ---- 1. hero + three figures
a = t.index('  <div class="card" id="status">')
b = t.index('  <div class="card" id="linkcard">')
t = t[:a] + '''  <div class="card" id="status">
   <div class="toprow">
    <div><div class="kicker">Здравствуйте</div><div class="who" id="who"></div></div>
    <span class="pill ok" id="pill"><i class="dot"></i><span id="pilltxt"></span></span>
   </div>
   <div class="planline"><span class="planname" id="planname"></span><span class="until" id="until"></span></div>
   <div class="bar-bg thin"><div class="bar-fg" id="daybar"></div></div>
   <div class="banner warn" id="banner" hidden></div>
   <div class="stats">
    <div class="stat"><div class="sv"><span id="days"></span> <small id="dayunit"></small></div><div class="sl">осталось</div></div>
    <div class="stat"><div class="sv"><span id="gb"></span> <small id="gbtotal"></small></div><div class="sl">трафик, ГБ</div><div class="bar-bg thin"><div class="bar-fg" id="gbbar"></div></div></div>
    <div class="stat"><div class="sv" id="devices"></div><div class="sl">устройства</div></div>
   </div>
   <span id="paid" hidden></span><span id="paidwrap" hidden></span><span id="reset" hidden></span><a id="cta" href="#" hidden></a>
   <div class="herobtns">
    <a class="btn sec" id="goconnect" href="#linkcard">Подключить устройство</a>
    <a class="btn" id="goextend" href="#renew">Продлить</a>
   </div>
  </div>

''' + t[b:]

# ---- 2. the link card: shorter text
rep('<p class="sub">Её нужно добавить в приложение (шаги ниже). Не передавайте её другим людям.</p>', '<p class="sub">Добавьте её в приложение (инструкция ниже). Не передавайте другим людям.</p>')

# ---- 3. the instruction is ONE accordion
rep('''  <div class="card" id="connect">
   <h2>Подключение</h2>
   <p class="sub">Два шага. Если уже подключены, этот раздел можно не открывать.</p>''', '''  <details class="card acc" id="connect">
   <summary>Инструкция: как подключиться</summary>
   <div class="in">
   <p class="sub">Если приложение уже настроено, этот раздел можно не открывать.</p>''')
a = t.index('  <div class="card" id="renew">')
close_connect = t.rindex("  </div>\n", 0, a)
t = t[:close_connect] + "  </div></details>\n" + t[close_connect + len("  </div>\n"):]

# ---- 4. payment: an accordion with two clear options
a = t.index('  <div class="card" id="renew">')
b = t.index('  <div class="card" id="supportcard">')
t = t[:a] + '''  <details class="card acc" id="renew">
   <summary id="renewtitle">Оплата и продление</summary>
   <div class="in">
    <p class="sub" id="renewsub"></p>
    <div class="opt">
     <a class="btn" id="paybot" href="#" rel="noopener" hidden style="margin-top:0">Оплатить в Telegram-боте</a>
     <div class="optnote" id="bothint" hidden>Зачисление сразу</div>
    </div>
    <details id="sitepay" class="opt2"><summary id="sitepaysum"><span>Оплатить на сайте</span><span class="optnote2">зачисление в течение суток</span></summary><div class="in">
     <div class="plans" id="plans" role="group" aria-label="Тариф"></div>
     <form id="payform" method="post" accept-charset="utf-8">
      <div class="field"><span class="lbl">Телефон</span><input id="phone" name="cps_phone" aria-label="Телефон, привязанный к Telegram" inputmode="tel" autocomplete="tel" placeholder="+7 999 123-45-67"></div>
      <div class="err" id="phoneerr" role="alert">Укажите номер телефона, привязанный к Telegram.</div>
      <input type="hidden" name="customerNumber" id="customer">
      <input type="hidden" name="sum" id="sum">
      <input type="hidden" name="shopId" id="shop">
      <div class="hint" id="phonehint"></div>
      <button class="btn" type="submit" id="pay">Оплатить <span id="payamt"></span></button>
     </form>
     <div class="hint">Зачисление в течение суток: платёж через сайт обрабатывается вручную. Оплата проходит на стороне ЮKassa, данные карты здесь не вводятся.</div>
    </div></details>
   </div>
  </details>

''' + t[b:]

# ---- 5. css
css = """
#status{padding:16px}
.toprow{display:flex;justify-content:space-between;align-items:flex-start;gap:10px}
.who{font-size:1.25rem;margin:2px 0 0;line-height:1.2}
.planline{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap;margin:8px 0 0}
.until{color:var(--mute);font-size:.92rem}
.bar-bg.thin{height:6px;margin-top:8px}
.stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin-top:14px}
.stat{min-width:0;background:var(--field);border:1px solid var(--line);border-radius:14px;padding:10px 10px 12px}
.stat .sv{font-weight:900;font-size:1.3rem;line-height:1.15;letter-spacing:-.3px}
.stat .sv small{font-size:.74rem;font-weight:700;color:var(--mute);letter-spacing:0}
.stat .sl{color:var(--mute);font-size:.72rem;font-weight:800;text-transform:uppercase;letter-spacing:.05em;margin-top:2px}
.herobtns{margin-top:14px}
.herobtns .btn{min-height:48px;font-size:1rem}
#status{order:0}#linkcard{order:2}#renew{order:3}#connect{order:4}#supportcard{order:5}
#linkcard,#supportcard{padding:16px}
details.card.acc{padding:0 16px}
details.card.acc>summary{padding:16px 0;min-height:56px;font-size:1.1rem;font-weight:900}
summary{list-style:none}summary::-webkit-details-marker{display:none}
summary::after{content:"+";margin-left:auto;padding-left:10px;font-weight:900;font-size:1.2rem;color:var(--acc)}
details[open]>summary::after{content:"\\2013"}
:root[data-theme=light] summary::after{color:var(--ok)}
details.card.acc>.in{padding:0 0 16px}
.opt .btn{margin-top:0}
.optnote{color:var(--ok);font-weight:800;font-size:.9rem;margin:6px 0 0;text-align:center}
.opt2{margin:14px 0 0;padding:0;border:0;background:transparent}
.opt2>summary{border:1px solid rgba(140,255,46,.55);border-radius:15px;padding:12px 16px;min-height:60px;display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:2px 10px}
.opt2>summary>span:first-child{font-weight:900;font-size:1.02rem}
.opt2 .optnote2{color:var(--mute);font-size:.85rem;font-weight:600;flex-basis:100%;order:3}
.opt2>summary::after{order:2}
.opt2>.in{padding:14px 0 0}
"""
rep("[hidden]{display:none!important}\n", "[hidden]{display:none!important}" + css.replace("\\\\2013", "\\2013"))

# ---- 6. script
rep("$('#gbtotal').textContent='ГБ из '+fmtGb(D.traffic.total)", "$('#gbtotal').textContent='/ '+fmtGb(D.traffic.total)")
# the payment button of the hero: the account that must pay gets the payment right below the hero (open), the hero button says what it is
a = t.index("if(D.need_pay){var cta=$('#cta');")
b = t.index("/* ---- payment:")
t = t[:a] + "if(D.need_pay){$('#goextend').textContent=D.expired?'Продлить':(D.plan==='Пробный доступ'?'Выбрать тариф':'Оплатить')}\n" + t[b:]
rep("if(!D.pay.bot_url){$('#sitepay').open=true;$('#sitepaysum').textContent='Оплата на сайте'}", "if(!D.pay.bot_url){$('#sitepay').open=true;$('#sitepaysum').firstChild.textContent='Оплатить на сайте'}")
rep("if($('#renew').hidden||D.need_pay)$('#goextend').hidden=true;", """if($('#renew').hidden)$('#goextend').hidden=true;
/* the layout follows the state of the account */
if(D.need_pay){$('#renew').style.order=1;$('#renew').open=true}
if(D.plan==='Пробный доступ'){$('#connect').open=true}
$('#goextend').onclick=function(e){e.preventDefault();$('#renew').open=true;$('#renew').scrollIntoView({behavior:'smooth'})};
$('#goconnect').onclick=function(e){e.preventDefault();$('#connect').open=true;$('#linkcard').scrollIntoView({behavior:'smooth'})};""")
rep("$('#renewtitle').textContent=trialAcc?'Выбрать тариф':'Оплата и продление';", "$('#renewtitle').textContent=trialAcc?'Выбрать тариф':'Оплата и продление';")
open(p, "w", encoding="utf-8", newline="").write(t)
print("compact cabinet:", len(t), "chars")
