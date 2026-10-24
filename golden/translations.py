#!/usr/bin/env python3
"""Add the five subscription-status texts of the commercial layer to the panel's translation catalogs (en, ru) and compile them.
The panel code of the overlay uses these msgids; stock Hiddify does not have them (the catalogs are not part of the overlay: they are large and mostly upstream's own).
Idempotent: an entry that is already there is left alone. Run with the python of the panel venv (it has Babel).

usage: translations.py [--site-packages DIR]"""
import argparse
import glob
import os
import sys

ENTRIES = {
    "en": {
        "IP limit exceeded": "IP limit exceeded",
        "Traffic exhausted": "Traffic exhausted",
        "Time expired": "Time expired",
        "Access disabled": "Access disabled",
        "Wait 2 min": "Wait 2 min",
    },
    "ru": {
        "IP limit exceeded": "Лимит IP превышен",
        "Traffic exhausted": "Трафик исчерпан",
        "Time expired": "Срок истёк",
        "Access disabled": "Доступ отключён",
        "Wait 2 min": "Подождите 2 мин",
    },
}

ap = argparse.ArgumentParser()
ap.add_argument("--site-packages", default="")
a = ap.parse_args()
sp = a.site_packages or (glob.glob("/opt/hiddify-manager/.venv313/lib/python3.*/site-packages") or [""])[0]
if not sp:
    sys.exit("no panel site-packages found")

from babel.messages.mofile import write_mo  # noqa: E402
from babel.messages.pofile import read_po  # noqa: E402

for lang, entries in ENTRIES.items():
    d = os.path.join(sp, "hiddifypanel", "translations", lang, "LC_MESSAGES")
    po = os.path.join(d, "messages.po")
    if not os.path.isfile(po):
        print(f"{lang}: no catalog at {po}, skipped")
        continue
    text = open(po, encoding="utf-8").read()
    add = "".join(f'\nmsgid "{k}"\nmsgstr "{v}"\n' for k, v in entries.items() if f'msgid "{k}"' not in text)
    if add:
        open(po, "a", encoding="utf-8", newline="\n").write(add)
    with open(po, "rb") as f:
        cat = read_po(f, locale=lang)
    with open(os.path.join(d, "messages.mo"), "wb") as f:
        write_mo(f, cat)
    print(f"{lang}: {'added ' + str(add.count('msgid')) + ' text(s), ' if add else ''}catalog compiled")
