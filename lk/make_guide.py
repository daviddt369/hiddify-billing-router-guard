#!/usr/bin/env python3
"""Make lk/guide.json: THE one source of the connection instruction texts shared by the registration page (lk), the cabinet and the bot
(apps with the download links, the order and the recommendation per platform, the full "change the App Store region" guide).

Since 2026-10-30 INCY is gone from the Russian App Store as well (the page answers 404 there, it is in the US store): on iPhone and iPad all three apps
come from the US App Store, so the region has to be changed first. usage: make_guide.py <routing-landing/clients.json> <out guide.json>"""
import json
import sys

src, dst = sys.argv[1], sys.argv[2]
old = json.load(open(src, encoding="utf-8"))
US_INCY = "https://apps.apple.com/us/app/incy/id6756943388"

region = old["region_help"]
region["intro"] = ("На iPhone и iPad Hiddify, Happ и INCY сейчас есть только в App Store США, поэтому сначала нужно сменить страну или регион в Apple Account. "
                   "Это займёт около пяти минут. Если приложение уже установлено, менять регион не нужно.")

apps = {}
for c in old["clients"]:
    d = {}
    for plat, items in c["downloads"].items():
        row = []
        for it in items:
            label, url = it["label"], it.get("url", "")
            if plat in ("ios", "macos") and "apps.apple.com" in url:
                url = url.replace("/ru/app/incy/", "/us/app/incy/")
                label = "App Store (США)"
            row.append([label, url, it.get("dynamic", "")])
        d[plat] = row
    apps[c["id"]] = {"name": c["name"], "downloads": d}
assert apps["incy"]["downloads"]["ios"][0][1] == US_INCY, apps["incy"]["downloads"]["ios"]

guide = {
    "platforms": [[p["id"], p["name"]] for p in old["platforms"]],
    "apps": apps,
    "desc": {"hiddify": "Больше всего протоколов, всё работает сразу.", "happ": "Проще, протоколов меньше.", "incy": "Проще, протоколов меньше."},
    "order": {"ios": ["hiddify", "happ", "incy"], "android": ["hiddify", "happ", "incy"], "windows": ["hiddify", "happ", "incy"], "macos": ["hiddify", "happ", "incy"]},
    "recommend": {
        "ios": [
            {"b": "Рекомендуем Hiddify:", "t": "у него больше всего протоколов, всё работает сразу."},
            {"t": "Hiddify, Happ и INCY на iPhone и iPad ставятся только из App Store США: сначала смените регион (подробная инструкция ниже), потом установите приложение. "
                  "Happ и INCY проще, но протоколов у них меньше."},
        ],
        "other": [
            {"b": "Рекомендуем Hiddify:", "t": "у него больше всего протоколов, всё работает сразу."},
            {"t": "Happ и INCY проще, но протоколов у них меньше. Если провайдер сильно блокирует соединения, лучше Hiddify."},
        ],
    },
    "steps": [
        "Скопируйте ссылку подписки: в личном кабинете (кнопка «Скопировать») или со страницы после регистрации. Ссылка для Hiddify и других приложений основная; ссылка «для Happ и INCY» (если она есть в кабинете) нужна только им.",
        "В приложении нажмите «+» и вставьте ссылку из буфера обмена.",
        "Выберите сервер и включите VPN.",
    ],
    "trouble": [
        "Обновите подписку в приложении.",
        "Выберите другой сервер.",
        "Если провайдер сильно блокирует соединения, попробуйте Hiddify: у него больше протоколов.",
        "Перезапустите приложение.",
        "Переключитесь между Wi-Fi и мобильным интернетом.",
    ],
    "note": "Маршрутизация (российские сайты открываются напрямую) уже внутри подписки: ничего настраивать и импортировать не нужно. В Hiddify при первом запуске выберите страну «Россия».",
    "region_help": region,
}
json.dump(guide, open(dst, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)
open(dst, "a", encoding="utf-8").write("\n")
print("guide:", dst, "| apps:", list(apps), "| region blocks:", len(region["blocks"]))
