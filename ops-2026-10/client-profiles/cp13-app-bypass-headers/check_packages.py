"""verify Android package names against the RuStore catalog (200 = exists; the page title shows which app it is). usage: check_packages.py  -> prints one line per candidate"""
import concurrent.futures
import re
import urllib.error
import urllib.request

CANDS = {
    "Сбербанк Онлайн": ["ru.sberbankmobile", "ru.sberbank.mobile"],
    "СберБанк ID": ["ru.sberbank.sberbankid", "ru.sberbankmobile.sberid"],
    "СберПрайм/СберМаркет": ["ru.sbermarket.sbermarket", "ru.instamart"],
    "Мегамаркет": ["ru.sbermegamarket", "ru.beru.android.megamarket", "ru.megamarket.android", "ru.sbermegamarket.android"],
    "ВТБ": ["ru.vtb24.mobilebanking.android", "ru.vtb.mobile"],
    "Альфа-Банк": ["ru.alfabank.mobile.android"],
    "Т-Банк": ["com.idamob.tinkoff.android"],
    "Райффайзен": ["ru.raiffeisennews", "ru.raiffeisen.rbo"],
    "Ozon Банк": ["ru.ozon.bank", "ru.ozon.fintech.finance", "ru.ozon.finance"],
    "Ozon": ["ru.ozon.app.android"],
    "Wildberries": ["com.wildberries.ru"],
    "Яндекс Маркет": ["ru.yandex.market"],
    "СДЭК": ["ru.cdek.client", "ru.cdek.android", "ru.cdek.mobile"],
    "Кари": ["ru.kari.app", "ru.kari.shop", "ru.kari.android"],
    "МИР Pay": ["ru.nspk.mirpay", "ru.nspk.mirpay.android"],
    "Яндекс Пэй": ["ru.yandex.pay", "ru.yandex.yandexpay"],
    "Пятёрочка": ["ru.pyaterochka.app.android", "ru.x5.pyaterochka", "ru.pyaterochka.app.browser", "ru.pyaterochka.app"],
    "Красный Яр": ["ru.krasnoyarsk.app", "ru.krasnyyar.app"],
    "Вуш": ["com.whoosh.app", "ru.whoosh.app", "com.mobile.whoosh", "app.whoosh"],
    "Юрент": ["com.urent.app", "ru.urentbike", "ru.urent", "app.urent"],
    "Мой МТС": ["ru.mts.mymts"],
    "МегаФон": ["ru.megafon.mlk"],
    "Билайн": ["ru.beeline.services", "ru.beeline.myb"],
    "Tele2": ["ru.tele2.mytele2"],
    "Госуслуги": ["ru.rostel"],
    "Госключ": ["ru.rostel.goskey", "ru.gosuslugi.goskey", "ru.gosuslugi.key"],
    "Мой налог": ["com.gnivts.selfemployed"],
    "ФНС ЛК ФЛ": ["ru.fns.lkfl"],
    "Домклик": ["ru.sberbank.domclick", "ru.domclick.mobile", "ru.domclick"],
    "RuStore": ["ru.vk.store"],
    "Почта России": ["ru.russianpost.mobileapp", "ru.pochta.mobile"],
    "VK": ["com.vkontakte.android"],
    "VK Мессенджер/Max": ["ru.oneme.app"],
    "Профи.ру": ["ru.profi.client", "ru.profi.android", "ru.profi.app"],
    "Авито": ["com.avito.android"],
    "2ГИС": ["ru.dublgis.dgismobile"],
    "Детский мир": ["ru.detmir.dmbonus"],
    "DNS": ["ru.dns.shop.android"],
    "Домофон (Росдомофон)": ["com.rosdomofon.orion"],
    "Яндекс Go": ["ru.yandex.taxi"],
    "Яндекс": ["ru.yandex.searchplugin"],
    "Яндекс Браузер": ["com.yandex.browser"],
    "Яндекс Банк": ["ru.yandex.bank"],
    "Яндекс Еда": ["ru.foodfox.client"],
    "Lamoda": ["com.lamoda.lamoda", "ru.lamoda.lamoda"],
    "Магнит": ["ru.tander.magnit"],
    "Перекрёсток/Vprok": ["ru.perekrestok.app", "ru.vprok.app"],
    "Лента": ["ru.lenta.lentochka", "ru.lentaonline.android"],
    "Сбермобайл": ["ru.sbermobile", "ru.sbermobile.app"],
    "Газпромбанк": ["ru.gazprombank.android.mobilebank.app", "ru.gazprombank.mobile"],
    "Россельхозбанк": ["ru.rshb.mobile"],
    "Совкомбанк": ["ru.sovcombank.android"],
    "Почта Банк": ["ru.pochta.bank", "ru.pochtabank.android"],
    "Банк Открытие/Мои Финансы": ["ru.openbank.mobile"],
    "МКБ": ["ru.mkb.mobile"],
    "Росбанк": ["ru.rosbank.android"],
    "СБП/НСПК": ["ru.nspk.sbp"],
    "РЖД Пассажирам": ["ru.rzd.pass"],
    "Аэрофлот": ["ru.aeroflot"],
    "Ситимобил": ["ru.citymobil"],
    "Мосру": ["ru.mos.app"],
    "Мой ГИБДД/Штрафы": ["ru.gibdd.mobile"],
}


def title(pkg):
    req = urllib.request.Request(f"https://www.rustore.ru/catalog/app/{pkg}", headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            html = r.read(60000).decode("utf-8", "replace")
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S)
        return 200, (m.group(1).strip()[:70] if m else "")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        return 0, type(e).__name__


jobs = [(name, p) for name, ps in CANDS.items() for p in ps]
import time


def slow(j):
    for attempt in range(3):
        code, t = title(j[1])
        if "Ошибка" in t or code == 429:
            time.sleep(15 * (attempt + 1))
            continue
        time.sleep(1.5)
        return j, (code, t)
    return j, (0, "rate-limited")


res = [slow(j) for j in jobs]
for (name, p), (code, t) in res:
    print(f"{'OK ' if code == 200 else '-- '} {name:26} {p:44} {code} {t}")
