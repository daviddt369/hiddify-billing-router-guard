"""Editable texts of the Telegram bot.

Every phrase the bot sends goes through `_()` of telebot's Usage.py; that `_` is wrapped by `make_gettext()`: a text edited on the panel page (Бизнес → Telegram-бот)
replaces the built-in phrase, an empty/unchanged one leaves the built-in text. Overrides live next to the other bot settings in business-telegram-ui.json
(key `bot_texts`: {slug: text}); the file is re-read when it changes, so a saved text is used by the very next message, no restart.
Placeholders are written {name} (the code's %(name)s); only the names of the phrase itself are allowed."""
import json
import os
import re

UI_PATH = "/opt/hiddify-manager/hiddify-panel/var/business-telegram-ui.json"

# (slug, group, label, built-in phrase exactly as in Usage.py)
CATALOG = [
    ("reg_phone_prompt", "reg", "Просьба прислать номер телефона", "Отправьте номер телефона, который указан у вас в панели. Он используется как логин."),
    ("reg_press_button", "reg", "Подсказка про кнопку регистрации", "Для регистрации нажмите кнопку «Отправить номер телефона»."),
    ("reg_trial_granted", "reg", "Выдан пробный доступ (сразу после регистрации)", "Добро пожаловать! Вам выдан пробный доступ: %(gb)s ГБ на %(days)s дн."),
    ("reg_trial_next", "reg", "После пробного доступа, перед списком тарифов", "Тест активирован. Если вас всё устраивает, выберите тариф:"),
    ("reg_no_plans", "reg", "Тарифы ещё не опубликованы", "Тарифы пока не опубликованы. Напишите администратору."),
    ("reg_not_in_base", "reg", "Номера нет в базе (регистрация закрыта)", "Вас нет в базе активных пользователей. Обратитесь к администратору для создания аккаунта."),
    ("reg_trial_used", "reg", "Пробный доступ с этого аккаунта уже выдавался", "С этого Telegram-аккаунта уже выдавался доступ к VPN. Используйте меню для управления подпиской."),
    ("reg_trial_failed", "reg", "Не удалось создать пробный доступ", "Не удалось создать пробный доступ. Попробуйте позже или обратитесь к администратору."),
    ("links_one", "links", "Ссылка на подписку (одна ссылка)", "Вот ваша ссылка на подписку:\n%(user_link)s\n\nСкопируйте её и вставьте в ваш клиент. Эта же ссылка открывает личный кабинет."),
    ("links_two", "links", "Ссылки на подписку (обычная и для Happ / INCY)", "Вот ваши ссылки на подписку:\n\nHiddify и другие приложения:\n%(user_link)s\n\nHapp и INCY (кнопки: AUTO и страны):\n%(happ_link)s\n\nСкопируйте нужную ссылку и вставьте в ваш клиент. Первая ссылка открывает личный кабинет."),
    ("links_btn_open", "links", "Кнопка «Открыть подписку»", "Открыть подписку"),
    ("links_btn_happ", "links", "Кнопка ссылки для Happ / INCY", "Happ / INCY (AUTO и страны)"),
    ("pay_choose", "pay", "Предложение выбрать тариф", "Выберите тариф:"),
    ("pay_done", "pay", "Оплата получена, тариф включён", "Оплата получена. Тариф активирован."),
    ("pay_not_activated", "pay", "Оплата получена, но тариф не включился", "Платёж получен, но активация не выполнена. Обратитесь к администратору."),
    ("pay_duplicate", "pay", "Платёж уже обработан", "Этот платёж уже обработан."),
    ("pay_not_configured", "pay", "Оплата не настроена", "Оплата пока не настроена"),
    ("pay_renew_sent", "pay", "Запрос на продление отправлен администратору", "Запрос на продление отправлен администратору"),
    ("pay_waiting", "pay", "Аккаунт ждёт включения тарифа", "Аккаунт ожидает активации тарифа."),
    ("misc_menu_hint", "misc", "Подсказка про меню", "Используйте меню ниже для дальнейших действий."),
    ("misc_no_instruction", "misc", "Инструкция не настроена", "Инструкция пока не настроена."),
    ("misc_btn_contact", "misc", "Кнопка «Связаться с администратором»", "Связаться с администратором"),
    ("misc_btn_phone", "misc", "Кнопка «Отправить номер телефона»", "Отправить номер телефона"),
    ("err_own_phone", "err", "Прислан чужой номер", "Можно делиться только своим номером телефона."),
    ("err_bad_phone", "err", "Номер не распознан", "Не удалось распознать номер. Убедитесь, что в Telegram указан российский номер +7."),
    ("err_tg_taken", "err", "Telegram-аккаунт уже связан с другим доступом", "Этот Telegram-аккаунт уже связан с другим VPN-доступом. Обратитесь к администратору."),
    ("err_phone_taken", "err", "Номер уже привязан к другому Telegram", "Этот номер уже привязан к другому Telegram-аккаунту. Обратитесь к администратору для разрешения ситуации."),
    ("err_bind", "err", "Ошибка при привязке аккаунта", "Произошла ошибка при привязке аккаунта. Попробуйте позже или обратитесь к администратору."),
    ("err_account_taken", "err", "Доступ привязан к другому Telegram", "Этот аккаунт уже привязан к другому Telegram. Если вам нужна перепривязка, обратитесь к администратору."),
]
GROUPS = [
    ("reg", "Регистрация и пробный доступ", "Что пользователь читает с первого сообщения: просьба прислать номер, пробный доступ, ответы на ошибки регистрации."),
    ("links", "Ссылка на подписку", "Сообщение со ссылкой, которое бот присылает после регистрации и по кнопке «Моя подписка»."),
    ("pay", "Тарифы и оплата", "Ответы бота при выборе тарифа и оплате."),
    ("err", "Ошибки привязки номера", "Что бот отвечает, когда номер или Telegram-аккаунт уже заняты или не распознаны."),
    ("misc", "Подсказки и кнопки", "Короткие служебные фразы. Названия кнопок меню «Мой тариф», «Сменить тариф», «Продлить тариф», «Моя подписка» здесь не меняются: по ним бот узнаёт нажатие."),
]
_BY_SLUG = {c[0]: c for c in CATALOG}
TRIAL_DEFAULT = {"gb": 1.0, "days": 2, "max_ips": 1}
_cache = {"mtime": None, "texts": {}, "trial": dict(TRIAL_DEFAULT)}


def _ph(msgid: str) -> list:
    return re.findall(r"%\((\w+)\)s", msgid)


def ui_default(msgid: str) -> str:
    return re.sub(r"%\((\w+)\)s", r"{\1}", msgid)


def _read_all() -> dict:
    try:
        with open(UI_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _custom() -> dict:
    """{built-in phrase: replacement}, cached by the file's mtime"""
    try:
        m = os.stat(UI_PATH).st_mtime_ns
    except OSError:
        m = None
    if m != _cache["mtime"]:
        raw = _read_all().get("bot_texts") or {}
        texts = {}
        if isinstance(raw, dict):
            for slug, value in raw.items():
                c = _BY_SLUG.get(slug)
                if c and isinstance(value, str) and value.strip():
                    texts[c[3]] = value
        _cache.update(mtime=m, texts=texts, trial=_parse_trial(_read_all().get("trial")))
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


class _Safe(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def make_gettext(base):
    """a drop-in for flask_babel.gettext: a panel-edited text first, the built-in one otherwise"""
    def gettext(msgid, **kw):
        custom = _custom().get(msgid)
        if custom:
            try:
                return custom.format_map(_Safe(kw)) if kw else custom
            except Exception:
                pass
        return base(msgid, **kw)
    return gettext


def form_groups() -> list:
    """for the page: [{key, title, hint, items: [{slug, label, value, default, placeholders, changed}]}]"""
    cur = _read_all().get("bot_texts") or {}
    out = []
    for key, title, hint in GROUPS:
        items = []
        for slug, g, label, msgid in CATALOG:
            if g != key:
                continue
            default = ui_default(msgid)
            value = cur.get(slug) if isinstance(cur.get(slug), str) and cur.get(slug).strip() else default
            items.append({"slug": slug, "label": label, "value": value, "default": default, "placeholders": ["{" + p + "}" for p in _ph(msgid)],
                          "changed": value != default, "rows": min(8, max(2, value.count("\n") + 2))})
        out.append({"key": key, "title": title, "hint": hint, "items": items, "changed": sum(i["changed"] for i in items)})
    return out


def check_form(form) -> dict:
    """the overrides the form asks for ({slug: text}); a text equal to the built-in one (or empty) is no override. ValueError names a wrong placeholder."""
    new = {}
    for slug, g, label, msgid in CATALOG:
        raw = form.get("bot_text__" + slug)
        if raw is None:
            continue
        value = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not value or value == ui_default(msgid).strip():
            continue
        allowed = set(_ph(msgid))
        used = set(re.findall(r"\{(\w+)\}", value))
        if used - allowed:
            raise ValueError(f"«{label}»: в тексте нельзя использовать " + ", ".join("{" + u + "}" for u in sorted(used - allowed)) + (" (доступны: " + ", ".join("{" + a + "}" for a in sorted(allowed)) + ")" if allowed else " (подстановок в этом тексте нет)"))
        rest = re.sub(r"\{\w+\}", "", value)
        if "{" in rest or "}" in rest:
            raise ValueError(f"«{label}»: фигурные скобки допустимы только как подстановки вида {{name}}")
        new[slug] = value
    check_trial(form)                                                   # a wrong trial number refuses the whole save too
    return new


def save_from_form(form) -> int:
    """store the edited texts (see check_form); returns the number of overrides"""
    new = check_form(form)
    data = _read_all()
    data["bot_texts"] = new
    trial = check_trial(form)
    if trial:
        data["trial"] = {**_parse_trial(data.get("trial")), **trial}
    os.makedirs(os.path.dirname(UI_PATH), exist_ok=True)
    tmp = UI_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, UI_PATH)
    return len(new)
