"""CP-19: trial (GB / days / devices) settings for the Telegram bot - patches the CP-18 sources (tg_texts.py, the Usage.py / BusinessAdmin.py / template builders) in place. Run once."""
import os

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cp18")


def edit(name, pairs):
    p = os.path.join(D, name)
    s = open(p, encoding="utf-8").read()
    for old, new in pairs:
        assert s.count(old) == 1, (name, old[:70])
        s = s.replace(old, new)
    open(p, "w", encoding="utf-8", newline="\n").write(s)


# ---------------------------------------------------------------- tg_texts.py
edit("tg_texts.py", [
    ('_cache = {"mtime": None, "texts": {}}',
     'TRIAL_DEFAULT = {"gb": 1.0, "days": 2, "max_ips": 1}\n_cache = {"mtime": None, "texts": {}, "trial": dict(TRIAL_DEFAULT)}'),
    ('        _cache.update(mtime=m, texts=texts)\n    return _cache["texts"]\n',
     '''        _cache.update(mtime=m, texts=texts, trial=_parse_trial(_read_all().get("trial")))
    return _cache["texts"]


def _num(value, kind, lo, hi):
    """a number from the settings file or the form: float / int in [lo, hi]; ValueError when it is not"""
    v = float(str(value).strip().replace(",", ".")) if kind == "float" else int(str(value).strip())
    if not lo <= v <= hi:
        raise ValueError
    return v


def _parse_trial(raw) -> dict:
    out = dict(TRIAL_DEFAULT)
    if isinstance(raw, dict):
        for key, kind, lo, hi in (("gb", "float", 0.1, 1000), ("days", "int", 1, 365), ("max_ips", "int", 1, 20)):
            try:
                out[key] = _num(raw[key], kind, lo, hi)
            except Exception:
                pass
    return out


def trial_settings() -> dict:
    """the trial the bot gives a newly registered user: {gb, gb_text, days, max_ips}; defaults 1 GB / 2 days / 1 device"""
    _custom()
    t = dict(_cache["trial"])
    t["gb_text"] = str(int(t["gb"])) if float(t["gb"]).is_integer() else str(t["gb"])
    return t


def trial_form() -> dict:
    t = trial_settings()
    return {"gb": t["gb_text"], "days": str(t["days"]), "max_ips": str(t["max_ips"])}


def check_trial(form):
    """the trial values the form asks for ({gb, days, max_ips}) or None when the form has no such fields; ValueError says which one is wrong"""
    if form.get("trial_gb") is None and form.get("trial_days") is None and form.get("trial_max_ips") is None:
        return None
    out = {}
    for key, kind, lo, hi, label in (("gb", "float", 0.1, 1000, "Трафик пробного доступа, ГБ (от 0,1 до 1000)"), ("days", "int", 1, 365, "Срок пробного доступа, дней (целое от 1 до 365)"),
                                     ("max_ips", "int", 1, 20, "Устройств пробного доступа (целое от 1 до 20)")):
        raw = form.get("trial_" + key)
        if raw is None:
            continue
        try:
            out[key] = _num(raw, kind, lo, hi)
        except Exception:
            raise ValueError(f"«{label}»: неверное значение")
    return out
'''),
    ('        new[slug] = value\n    return new\n',
     '        new[slug] = value\n    check_trial(form)                                                   # a wrong trial number refuses the whole save too\n    return new\n'),
    ('    data = _read_all()\n    data["bot_texts"] = new\n',
     '    data = _read_all()\n    data["bot_texts"] = new\n    trial = check_trial(form)\n    if trial:\n        data["trial"] = {**_parse_trial(data.get("trial")), **trial}\n'),
])

# ---------------------------------------------------------------- Usage.py builder
edit("make_usage.py", [
    ("from hiddifypanel.hutils.tg_texts import make_gettext\\n", "from hiddifypanel.hutils.tg_texts import make_gettext, trial_settings\\n"),
    ("""    ('text=_("Happ / INCY: три кнопки")', 'text=_("Happ / INCY (AUTO и страны)")'),\n]""",
     """    ('text=_("Happ / INCY: три кнопки")', 'text=_("Happ / INCY (AUTO и страны)")'),
    # the trial the bot gives is set on the panel page (Бизнес -> Telegram-бот); 1 GB / 2 days / 1 device stay the defaults
    ("        usage_limit=TRIAL_USAGE_LIMIT_GB * ONE_GIG,\\n        package_days=TRIAL_PACKAGE_DAYS,\\n        max_ips=TRIAL_MAX_IPS,\\n",
     "        usage_limit=int(trial_settings()[\\"gb\\"] * ONE_GIG),\\n        package_days=trial_settings()[\\"days\\"],\\n        max_ips=trial_settings()[\\"max_ips\\"],\\n"),
    ("gb=TRIAL_USAGE_LIMIT_GB, days=TRIAL_PACKAGE_DAYS)", "gb=trial_settings()[\\"gb_text\\"], days=trial_settings()[\\"days\\"])"),
    ("Статус: trial {TRIAL_USAGE_LIMIT_GB} ГБ / {TRIAL_PACKAGE_DAYS} дн.", "Статус: trial {trial_settings()['gb_text']} ГБ / {trial_settings()['days']} дн."),
]"""),
])

# ---------------------------------------------------------------- BusinessAdmin builder
edit("make_business.py", [
    ('bot_text_groups=_tg_text_groups() if active_section == \\"telegram\\" else [],\\n        )\\n"),',
     'bot_text_groups=_tg_text_groups() if active_section == \\"telegram\\" else [],\\n            bot_trial=_tg_trial() if active_section == \\"telegram\\" else {},\\n        )\\n"),'),
    ('def _normalize_multiline_text(value: str) -> str:\\n"),',
     'def _tg_trial() -> dict:\\n    try:\\n        from hiddifypanel.hutils import tg_texts\\n        return tg_texts.trial_form()\\n    except Exception:\\n        return {}\\n\\n\\ndef _normalize_multiline_text(value: str) -> str:\\n"),'),
])

# ---------------------------------------------------------------- template builder: the three trial fields in the registration section
edit("make_templates.py", [
    ('''              <div class="form-group"><label for="{{ form.telegram_registration_mode.id }}">''',
     '''              <div class="form-row">
                <div class="form-group col-md-4"><label for="trial-gb">Пробный доступ: трафик, ГБ</label><input type="text" class="form-control" id="trial-gb" name="trial_gb" value="{{ bot_trial.gb }}" inputmode="decimal"></div>
                <div class="form-group col-md-4"><label for="trial-days">Пробный доступ: срок, дней</label><input type="text" class="form-control" id="trial-days" name="trial_days" value="{{ bot_trial.days }}" inputmode="numeric"></div>
                <div class="form-group col-md-4"><label for="trial-ips">Пробный доступ: устройств</label><input type="text" class="form-control" id="trial-ips" name="trial_max_ips" value="{{ bot_trial.max_ips }}" inputmode="numeric"></div>
              </div>
              <small class="form-text text-muted mb-3">Это настоящие условия пробной подписки: столько трафика, дней и устройств бот выдаёт каждому новому пользователю при автоматической регистрации (по умолчанию 1 ГБ, 2 дня, 1 устройство). Уже выданные пробные доступы не меняются. Те же числа подставляются в текст «Выдан пробный доступ» вместо <code>{gb}</code> и <code>{days}</code>.</small>
              <div class="form-group"><label for="{{ form.telegram_registration_mode.id }}">'''),
    ("при автоматической регистрации выдаётся пробный доступ (сейчас 1 ГБ на 2 дня), при ручной",
     "при автоматической регистрации выдаётся пробный доступ (трафик, срок и число устройств задаются в разделе «Регистрация и пробный доступ»), при ручной"),
])
print("builders patched")
