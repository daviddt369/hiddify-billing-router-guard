"""builds cp18/Usage.py from the live bot module (stand copy of production): bot texts editable on the panel + the stale 'AUTO, Germany, USA' wording replaced.
usage: make_usage.py <Usage.py in> <Usage.py out>"""
import sys

src = open(sys.argv[1], encoding="utf-8").read()
pairs = [
    ("from flask_babel import gettext as _\n",
     "from flask_babel import gettext as _flask_gettext\nfrom hiddifypanel.hutils.tg_texts import make_gettext, trial_settings\n_ = make_gettext(_flask_gettext)  # texts edited on the panel (Бизнес -> Telegram-бот) replace the built-in phrases\n"),
    ('"Happ и INCY (три кнопки: AUTO, Германия, США):\\n"', '"Happ и INCY (кнопки: AUTO и страны):\\n"'),
    ('text=_("Happ / INCY: три кнопки")', 'text=_("Happ / INCY (AUTO и страны)")'),
    # the trial the bot gives is set on the panel page (Бизнес -> Telegram-бот); 1 GB / 2 days / 1 device stay the defaults
    ("        usage_limit=TRIAL_USAGE_LIMIT_GB * ONE_GIG,\n        package_days=TRIAL_PACKAGE_DAYS,\n        max_ips=TRIAL_MAX_IPS,\n",
     "        usage_limit=int(trial_settings()[\"gb\"] * ONE_GIG),\n        package_days=trial_settings()[\"days\"],\n        max_ips=trial_settings()[\"max_ips\"],\n"),
    ("gb=TRIAL_USAGE_LIMIT_GB, days=TRIAL_PACKAGE_DAYS)", "gb=trial_settings()[\"gb_text\"], days=trial_settings()[\"days\"])"),
    ("Статус: trial {TRIAL_USAGE_LIMIT_GB} ГБ / {TRIAL_PACKAGE_DAYS} дн.", "Статус: trial {trial_settings()['gb_text']} ГБ / {trial_settings()['days']} дн."),
]
for old, new in pairs:
    assert src.count(old) == 1, old
    src = src.replace(old, new, 1)
open(sys.argv[2], "w", encoding="utf-8", newline="\n").write(src)
print("ok")
