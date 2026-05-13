from __future__ import annotations
from hiddifypanel.panel import hiddify
from telebot import types
from flask_babel import gettext as _
from flask_babel import force_locale
from flask import current_app as app, has_request_context, g
import datetime
import json
import logging
from celery import shared_task
import os
from sqlalchemy.exc import IntegrityError
from hiddifypanel.models import *
from . import bot
try:
    from .secrets import telegram_bot_token, telegram_payment_provider_token
except Exception:
    def telegram_bot_token():
        return (hconfig(ConfigEnum.telegram_bot_token) or "").strip()

    def telegram_payment_provider_token():
        return (hconfig(ConfigEnum.telegram_payment_provider_token) or "").strip()
from hiddifypanel.panel.user.user import get_common_data
from hiddifypanel.database import db
from hiddifypanel import hutils
try:
    from hiddifypanel.commercial_logic import renew_user_package
except Exception:
    def renew_user_package(*args, **kwargs):
        raise RuntimeError("commercial payment runtime is not installed")

TRIAL_USAGE_LIMIT_GB = 1
TRIAL_PACKAGE_DAYS = 2
TRIAL_MAX_IPS = 1
_ADMIN_NOTIFY_DEDUP: dict[tuple[int, int | None, str], float] = {}
_DEFAULT_SUPPORT_URL = "https://t.me/sisadmin_pro"
logger = logging.getLogger(__name__)
_DEFAULT_INSTRUCTION_BUTTON_TEXT = "Инструкция"


def _is_admin_chat(chat_id: int | None) -> bool:
    if not chat_id:
        return False
    return AdminUser.query.filter(AdminUser.telegram_id == int(chat_id)).first() is not None


def _normalize_phone(value: str | None) -> str:
    raw = (value or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return ""
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    if not digits.startswith("7") and len(digits) == 10:
        digits = "7" + digits
    return f"+{digits}"


def _phone_request_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    keyboard.add(types.KeyboardButton(text=_("Отправить номер телефона"), request_contact=True))
    return keyboard


def _admin_contact_url() -> str:
    return (
        (hconfig(ConfigEnum.support_url) or "")
        or
        os.environ.get("HIDDIFY_SUPPORT_URL", "")
        or os.environ.get("SUPPORT_URL", "")
        or os.environ.get("HIDDIFY_TELEGRAM_ADMIN_URL", "")
        or os.environ.get("TELEGRAM_ADMIN_URL", "")
        or _DEFAULT_SUPPORT_URL
    ).strip()


def _admin_contact_keyboard():
    url = _admin_contact_url()
    if not url:
        return None
    return types.InlineKeyboardMarkup(
        keyboard=[
            [
                types.InlineKeyboardButton(
                    text=_("Связаться с администратором"),
                    url=url,
                )
            ]
        ]
    )


def _telegram_welcome_message() -> str:
    return ((hconfig(ConfigEnum.telegram_welcome_message) or "").strip())


def _telegram_instruction_message() -> str:
    return ((hconfig(ConfigEnum.telegram_instruction_message) or "").strip()) or _telegram_welcome_message()


def _telegram_instruction_button_text() -> str:
    return ((hconfig(ConfigEnum.telegram_instruction_button_text) or "").strip() or _DEFAULT_INSTRUCTION_BUTTON_TEXT)


def _send_instruction_message(chat_id: int, user: User | None = None) -> bool:
    message = _telegram_instruction_message()
    if not message:
        bot.send_message(chat_id, _("Инструкция пока не настроена."), reply_markup=_user_menu_keyboard())
        return False
    locale = (user.lang if user else None) or hconfig(ConfigEnum.lang)
    try:
        with force_locale(locale):
            bot.send_message(chat_id, message, reply_markup=_user_menu_keyboard(), parse_mode="HTML", disable_web_page_preview=True)
    except Exception:
        bot.send_message(chat_id, message, reply_markup=_user_menu_keyboard(), disable_web_page_preview=True)
    return True


def _send_first_link_welcome(chat_id: int, user: User) -> bool:
    if user.telegram_welcome_sent:
        return False
    message = _telegram_welcome_message()
    if message:
        locale = user.lang or hconfig(ConfigEnum.lang)
        try:
            with force_locale(locale):
                bot.send_message(chat_id, message, reply_markup=_user_menu_keyboard(), parse_mode="HTML", disable_web_page_preview=True)
        except Exception:
            bot.send_message(chat_id, message, reply_markup=_user_menu_keyboard(), disable_web_page_preview=True)
    try:
        bot.send_message(
            chat_id,
            _subscription_links_message(user),
            reply_markup=_subscription_link_keyboard(user),
            disable_web_page_preview=True,
        )
    except Exception:
        pass
    user.telegram_welcome_sent = True
    db.session.add(user)
    db.session.commit()
    return True


def _auto_registration_enabled() -> bool:
    mode = ""
    try:
        with open("/opt/hiddify-manager/hiddify-panel/var/business-telegram-ui.json", "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            mode = str(data.get("telegram_registration_mode", "") or "").strip().lower()
    except Exception:
        pass
    if not mode:
        mode = (os.environ.get("HIDDIFY_TELEGRAM_REGISTRATION_MODE", "admin_only") or "").strip().lower()
    return mode in {"auto", "open", "public", "1", "true", "yes", "on"}


def _user_menu_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row(
        types.KeyboardButton(text=_("Мой тариф")),
        types.KeyboardButton(text=_("Сменить тариф")),
    )
    keyboard.row(
        types.KeyboardButton(text=_("Продлить тариф")),
        types.KeyboardButton(text=_("Моя подписка")),
    )
    return keyboard


def _find_user_by_phone(phone: str) -> User | None:
    normalized = _normalize_phone(phone)
    if not normalized:
        return None
    return User.query.filter(
        (User.name == normalized) | (User.username == normalized)
    ).order_by(User.id.desc()).first()


def _bind_user_to_telegram(user: User, chat_id: int, force: bool = False) -> bool:
    current_telegram_id = int(user.telegram_id or 0)
    if not force and current_telegram_id and current_telegram_id != int(chat_id):
        return False
    user.telegram_id = int(chat_id)
    if not user.username:
        user.username = user.name
    db.session.add(user)
    db.session.flush()
    db.session.commit()
    db.session.refresh(user)
    return int(user.telegram_id or 0) == int(chat_id)


def _telegram_admins() -> list[AdminUser]:
    return AdminUser.query.filter(AdminUser.telegram_id.isnot(None)).order_by(AdminUser.id.asc()).all()


def _notify_admins_for_user(user: User, text: str | None = None, *, skip_dedup: bool = False):
    from .admin import _admin_user_summary, admin_user_actions_keyboard_v3

    for admin in _telegram_admins():
        try:
            with force_locale(admin.lang or hconfig(ConfigEnum.admin_lang)):
                body = text or _admin_user_summary(user, admin)
                key = (admin.id, getattr(user, "id", None), body)
                now = datetime.datetime.now().timestamp()
                last_sent = _ADMIN_NOTIFY_DEDUP.get(key, 0)
                if not skip_dedup and now - last_sent < 90:
                    continue
                _ADMIN_NOTIFY_DEDUP[key] = now
                bot.send_message(
                    admin.telegram_id,
                    body,
                    reply_markup=admin_user_actions_keyboard_v3(user, admin),
                )
        except Exception:
            continue


def _public_plans() -> list[CommercialPlan]:
    return (
        CommercialPlan.query.filter(
            CommercialPlan.enable == True,
            CommercialPlan.is_public == True,
        )
        .order_by(CommercialPlan.sort_order.asc(), CommercialPlan.id.asc())
        .all()
    )


def _payment_provider_token() -> str:
    return telegram_payment_provider_token()


def _payments_enabled() -> bool:
    return bool(_payment_provider_token())


def _plan_amount_minor(plan: CommercialPlan) -> int:
    return int(round(float(plan.price or 0) * 100))


def _parse_plan_invoice_payload(payload: str) -> tuple[int, int] | tuple[None, None]:
    parts = (payload or "").split(":")
    if len(parts) != 4 or parts[0] != "plan" or parts[2] != "user":
        return None, None
    try:
        return int(parts[1]), int(parts[3])
    except Exception:
        return None, None


def _payment_charge_id(payment) -> str:
    return (
        getattr(payment, "telegram_payment_charge_id", "")
        or getattr(payment, "provider_payment_charge_id", "")
        or ""
    ).strip()


def _is_duplicate_external_payment_id_error(exc: IntegrityError) -> bool:
    orig = getattr(exc, "orig", None)
    args = getattr(orig, "args", ()) or ()
    code = args[0] if args else None
    if code != 1062:
        return False
    message = " ".join(str(arg) for arg in args)
    return (
        "external_payment_id" in message
        or "ux_commercial_subscription_external_payment_id" in message
    )


def _payment_matches_plan(*, plan: CommercialPlan, amount_minor: int, currency: str) -> bool:
    expected_amount = _plan_amount_minor(plan)
    expected_currency = (plan.currency or "RUB").upper()
    return expected_amount == int(amount_minor or 0) and expected_currency == (currency or "").upper()


def _format_price(plan: CommercialPlan) -> str:
    currency = "₽" if (plan.currency or "").upper() == "RUB" else (plan.currency or "")
    return f"{plan.price} {currency}".strip()


def _ru_plural_device_count(value) -> str:
    try:
        n = int(value or 0)
    except Exception:
        n = 0
    abs_n = abs(n)
    last_two = abs_n % 100
    last = abs_n % 10
    if 11 <= last_two <= 14:
        word = "устройств"
    elif last == 1:
        word = "устройство"
    elif 2 <= last <= 4:
        word = "устройства"
    else:
        word = "устройств"
    return f"{n} {word}"


def _plan_label(plan: CommercialPlan) -> str:
    title = (plan.name or "").strip() or f"{int(plan.usage_limit_GB)} ГБ / {_ru_plural_device_count(plan.max_ips)}"
    price = _format_price(plan)
    return f"{title} - {price}" if price else title


def _plans_keyboard():
    rows = []
    for plan in _public_plans():
        rows.append([
            types.InlineKeyboardButton(
                text=_plan_label(plan),
                callback_data=f"user_plan_info {plan.id}",
            )
        ])
    if rows:
        rows.append([
            types.InlineKeyboardButton(
                text=_("Обновить"),
                callback_data="user_show_plans",
            )
        ])
    return types.InlineKeyboardMarkup(keyboard=rows) if rows else None


def _plan_description(plan: CommercialPlan) -> str:
    description = _(
        "Тариф: %(name)s\n"
        "Трафик: %(gb)s ГБ\n"
        "Срок: %(days)s дней\n"
        "Устройств: %(ips)s\n"
        "Цена: %(price)s\n\n"
        "После успешной оплаты тариф активируется автоматически.",
        name=plan.name,
        gb=int(plan.usage_limit_GB),
        days=plan.package_days,
        ips=_ru_plural_device_count(plan.max_ips),
        price=_format_price(plan),
    )
    note = (plan.note or "").strip()
    if note:
        description += f"\n\n{note}"
    return description


def _plan_actions_keyboard(plan: CommercialPlan):
    rows = []
    if _payments_enabled() and float(plan.price or 0) > 0:
        rows.append([
            types.InlineKeyboardButton(
                text=_("Оплатить %(price)s", price=_format_price(plan)),
                callback_data=f"user_pay_plan {plan.id}",
            )
        ])
    rows.append([
        types.InlineKeyboardButton(
            text=_("Запросить активацию у администратора"),
            callback_data=f"user_request_plan {plan.id}",
        )
    ])
    rows.append([
        types.InlineKeyboardButton(
            text=_("Назад к тарифам"),
            callback_data="user_show_plans",
        )
    ])
    if _payments_enabled():
        rows = [
            row for row in rows
            if not any(getattr(button, "callback_data", "") == f"user_request_plan {plan.id}" for button in row)
        ]
    return types.InlineKeyboardMarkup(keyboard=rows)


def _send_plan_invoice(chat_id: int, user: User, plan: CommercialPlan):
    token = _payment_provider_token()
    amount = _plan_amount_minor(plan)
    if not token:
        return False, _("Оплата пока не настроена")
    if amount <= 0:
        return False, _("Для этого тарифа оплата не требуется")
    prices = [types.LabeledPrice(label=plan.name, amount=amount)]
    bot.send_invoice(
        chat_id,
        title=plan.name,
        description=_("Оплата тарифа %(name)s", name=plan.name),
        invoice_payload=f"plan:{plan.id}:user:{user.id}",
        provider_token=token,
        currency=(plan.currency or "RUB").upper(),
        prices=prices,
        start_parameter=f"plan-{plan.id}",
    )
    return True, _("Счёт отправлен")


def _inactive_user_message(user: User) -> str:
    lines = [
        _("Аккаунт ожидает активации тарифа."),
        _("Телефон: %(phone)s", phone=user.name or "-"),
        f"UUID: {user.uuid}",
    ]
    if user.plan:
        lines.append(
            _("Текущий тариф: %(name)s", name=user.plan.name)
        )
    return "\n".join(lines)


def _telegram_usage_fallback(user: User) -> str:
    plan_name = _display_plan_name(user)
    return _(
        "Тариф: %(plan)s\n"
        "⏳ Использование трафика %(usage).1fGB\n"
        "Из %(limit).1fGB\n"
        "Срок действия: %(expire)s",
        plan=plan_name,
        usage=float(user.current_usage_GB or 0),
        limit=float(user.usage_limit_GB or 0),
        expire=hutils.convert.format_timedelta(datetime.timedelta(days=user.remaining_days)),
    )


def _has_accessible_package(user: User) -> bool:
    return bool(user and user.is_active and int(user.usage_limit or 0) > 0)


def _display_plan_name(user: User) -> str:
    if getattr(user, "plan", None):
        return user.plan.name
    if _has_accessible_package(user):
        return _("Пробный доступ")
    return _("Тариф не выбран")


def _default_added_by_id() -> int:
    admin = AdminUser.query.order_by(AdminUser.id.asc()).first()
    return admin.id if admin else 1


def _create_user_from_phone(phone: str, chat_id: int) -> User:
    user = User(
        name=phone,
        username=phone,
        telegram_id=int(chat_id),
        added_by=_default_added_by_id(),
        enable=True,
        usage_limit=TRIAL_USAGE_LIMIT_GB * ONE_GIG,
        package_days=TRIAL_PACKAGE_DAYS,
        max_ips=TRIAL_MAX_IPS,
        mode=UserMode.no_reset,
        start_date=datetime.date.today(),
        last_reset_time=datetime.date.today(),
        comment="Telegram trial signup",
    )
    db.session.add(user)
    db.session.commit()
    db.session.refresh(user)
    hiddify.quick_apply_users()
    return user


def _subscription_links_message(user: User) -> str:
    domain = Domain.get_domains()[0]
    user_link = hiddify.get_account_panel_link(user, domain.domain)
    return _(
        "Вот ваша ссылка на подписку:\n"
        "%(user_link)s\n\n"
        "Скопируйте её и вставьте в ваш клиент. Эта же ссылка открывает личный кабинет.",
        user_link=user_link,
    )


def _subscription_link_keyboard(user: User):
    domain = Domain.get_domains()[0]
    user_link = hiddify.get_account_panel_link(user, domain.domain)
    return types.InlineKeyboardMarkup(
        keyboard=[
            [
                types.InlineKeyboardButton(
                    text=_("Открыть подписку"),
                    url=user_link,
                )
            ]
        ]
    )


def _user_menu_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row(
        types.KeyboardButton(text=_("Мой тариф")),
        types.KeyboardButton(text=_("Сменить тариф")),
    )
    keyboard.row(
        types.KeyboardButton(text=_("Продлить тариф")),
        types.KeyboardButton(text=_("Моя подписка")),
    )
    keyboard.row(types.KeyboardButton(text=_telegram_instruction_button_text()))
    return keyboard


def _handle_user_menu_action(message):
    text = (message.text or "").strip()
    instruction_button_text = _telegram_instruction_button_text()
    if text not in {"Мой статус", "Мой тариф", "Сменить тариф", "Продлить тариф", "Моя подписка", instruction_button_text}:
        return False
    user = User.query.filter(User.telegram_id == message.chat.id).order_by(User.id.desc()).first()
    if not user:
        bot.reply_to(
            message,
            _("Отправьте номер телефона, который указан у вас в панели. Он используется как логин."),
            reply_markup=_phone_request_keyboard(),
        )
        return True
    if text == instruction_button_text:
        _send_instruction_message(message.chat.id, user)
        return True
    if text in {"Мой статус", "Мой тариф"}:
        with force_locale(user.lang or hconfig(ConfigEnum.lang)):
            if not _has_accessible_package(user):
                bot.reply_to(message, _inactive_user_message(user), reply_markup=_plans_keyboard())
            else:
                try:
                    if has_request_context():
                        usage_msg = get_usage_msg(user.uuid)
                    else:
                        base_host = Domain.get_panel_link() or Domain.get_domains()[0].domain
                        with app.test_request_context(base_url=f"https://{base_host}/"):
                            g.account = user
                            usage_msg = get_usage_msg(user.uuid)
                except Exception:
                    usage_msg = _telegram_usage_fallback(user)
                bot.reply_to(message, usage_msg, reply_markup=user_keyboard(user.uuid))
        return True
    if text == "Сменить тариф":
        bot.reply_to(message, _("Выберите тариф:"), reply_markup=_plans_keyboard())
        return True
    if text == "Продлить тариф":
        if getattr(user, "plan", None) and _payments_enabled() and float(user.plan.price or 0) > 0:
            try:
                ok, payment_message = _send_plan_invoice(message.chat.id, user, user.plan)
                bot.reply_to(message, payment_message, reply_markup=_user_menu_keyboard())
            except Exception:
                bot.reply_to(message, _("Не удалось создать счёт"), reply_markup=_user_menu_keyboard())
            return True
        plan_name = user.plan.name if getattr(user, "plan", None) else _("Тариф не выбран")
        _notify_admins_for_user(
            user,
            text=(
                f"Пользователь запросил продление тарифа\n"
                f"Телефон: {user.name}\n"
                f"UUID: {user.uuid}\n"
                f"Текущий тариф: {plan_name}"
            ),
        )
        bot.reply_to(message, _("Запрос на продление отправлен администратору"), reply_markup=_user_menu_keyboard())
        return True
    if text == "Моя подписка":
        bot.reply_to(
            message,
            _subscription_links_message(user),
            reply_markup=_subscription_link_keyboard(user),
            disable_web_page_preview=True,
        )
        bot.send_message(message.chat.id, _("Используйте меню ниже для дальнейших действий."), reply_markup=_user_menu_keyboard())
        return True
    return False


@bot.message_handler(func=lambda message: (not (message.text or "").startswith("/")) and "admin" not in (message.text or ""))
def send_usage(message):
    if _is_admin_chat(getattr(message.chat, "id", None)):
        return
    if _handle_user_menu_action(message):
        return
    if getattr(message, "contact", None):
        return handle_phone_contact(message)
    if (message.text or "").strip() == _("Мой статус"):
        return send_welcome(message)
    if (message.text or "").strip() == _("Сменить тариф"):
        user = User.query.filter(User.telegram_id == message.chat.id).first()
        if user:
            return bot.reply_to(message, _("Выберите тариф:"), reply_markup=_plans_keyboard())
    if (message.text or "").strip() == _("Продлить тариф"):
        user = User.query.filter(User.telegram_id == message.chat.id).first()
        if user:
            plan_name = user.plan.name if getattr(user, "plan", None) else _("Тариф не выбран")
            _notify_admins_for_user(
                user,
                text=(
                    f"Пользователь запросил продление тарифа\n"
                    f"Телефон: {user.name}\n"
                    f"UUID: {user.uuid}\n"
                    f"Текущий тариф: {plan_name}"
                ),
            )
            return bot.reply_to(message, _("Запрос на продление отправлен администратору"), reply_markup=_user_menu_keyboard())
    if (message.text or "").strip() == _("Моя подписка"):
        user = User.query.filter(User.telegram_id == message.chat.id).first()
        if user:
            bot.reply_to(message, _subscription_links_message(user), reply_markup=_subscription_link_keyboard(user), disable_web_page_preview=True)
            return bot.send_message(message.chat.id, _("Используйте меню ниже для дальнейших действий."), reply_markup=_user_menu_keyboard())
    phone = _normalize_phone(message.text)
    if phone:
        return handle_phone_text(message, phone)
    return send_welcome(message)


@bot.message_handler(commands=['start'], func=lambda message: "admin" not in message.text)
def send_welcome(message):
    if _is_admin_chat(getattr(message.chat, "id", None)):
        return
    text = message.text
    uuid = text.split()[-1] if len(text.split()) > 0 else None
    new_binding = False
    if hutils.auth.is_uuid_valid(uuid):
        user = User.by_uuid(uuid)
        if user:
            new_binding = not bool(user.telegram_id)
            if not _bind_user_to_telegram(user, message.chat.id):
                bot.reply_to(
                    message,
                    _(
                        "Этот аккаунт уже привязан к другому Telegram. "
                        "Если вам нужна перепривязка, обратитесь к администратору."
                    ),
                    reply_markup=_phone_request_keyboard(),
                )
                return
    else:
        user = User.query.filter(User.telegram_id == message.chat.id).first()
    if user:
        if new_binding:
            _send_first_link_welcome(message.chat.id, user)
        _send_user_home(message.chat.id, user)
    else:
        bot.reply_to(
            message,
            _("Отправьте номер телефона, который указан у вас в панели. Он используется как логин."),
            reply_markup=_phone_request_keyboard(),
        )


@bot.message_handler(content_types=['contact'])
def handle_phone_contact(message):
    if _is_admin_chat(getattr(message.chat, "id", None)):
        return
    phone = _normalize_phone(getattr(message.contact, "phone_number", None))
    return _handle_phone_lookup(message, phone, allow_rebind=True)


def handle_phone_text(message, phone: str):
    return _handle_phone_lookup(message, phone, allow_rebind=False)


def _send_user_home(chat_id: int, user: User):
    with force_locale(user.lang or hconfig(ConfigEnum.lang)):
        if not _has_accessible_package(user):
            bot.send_message(
                chat_id,
                _inactive_user_message(user),
                reply_markup=_plans_keyboard(),
            )
        else:
            try:
                if has_request_context():
                    usage_msg = get_usage_msg(user.uuid)
                else:
                    base_host = Domain.get_panel_link() or Domain.get_domains()[0].domain
                    with app.test_request_context(base_url=f"https://{base_host}/"):
                        g.account = user
                        usage_msg = get_usage_msg(user.uuid)
            except Exception:
                usage_msg = _telegram_usage_fallback(user)
            bot.send_message(
                chat_id,
                usage_msg,
                reply_markup=user_keyboard(user.uuid),
            )
        bot.send_message(
            chat_id,
            _("Используйте меню ниже для дальнейших действий."),
            reply_markup=_user_menu_keyboard(),
        )


def _handle_phone_lookup(message, phone: str, allow_rebind: bool = False):
    user = _find_user_by_phone(phone)
    if user:
        new_binding = not bool(user.telegram_id)
        if not _bind_user_to_telegram(user, message.chat.id, force=allow_rebind):
            _notify_admins_for_user(
                user,
                text=(
                    f"Заблокирована попытка перепривязки Telegram\n"
                    f"Телефон: {user.name}\n"
                    f"UUID: {user.uuid}\n"
                    f"Текущий Telegram ID: {user.telegram_id}\n"
                    f"Новый Telegram ID: {message.chat.id}"
                ),
            )
            bot.reply_to(
                message,
                _(
                    "Этот аккаунт уже привязан к другому Telegram. "
                    "Если вам нужна перепривязка, отправьте контакт со своим номером телефона."
                ),
                reply_markup=_phone_request_keyboard(),
            )
            return
        if allow_rebind:
            _notify_admins_for_user(
                user,
                text=(
                    f"Аккаунт перепривязан по подтвержденному контакту\n"
                    f"Телефон: {user.name}\n"
                    f"UUID: {user.uuid}\n"
                    f"Новый Telegram ID: {message.chat.id}"
                ),
            )
        _send_user_home(message.chat.id, user)
        return
    if not _auto_registration_enabled():
        bot.reply_to(
            message,
            _(
                "Вас нет в базе активных пользователей. "
                "Обратитесь к администратору для создания аккаунта."
            ),
            reply_markup=_admin_contact_keyboard(),
        )
        if phone:
            for admin in _telegram_admins():
                try:
                    with force_locale(admin.lang or hconfig(ConfigEnum.admin_lang)):
                        bot.send_message(
                            admin.telegram_id,
                            _(
                                "Попытка входа в бота от номера, которого нет в базе:\n"
                                "Телефон: %(phone)s\n"
                                "Telegram ID: %(tgid)s",
                                phone=phone,
                                tgid=getattr(message.chat, "id", "-"),
                            ),
                        )
                except Exception:
                    continue
        return
    user = _create_user_from_phone(phone, message.chat.id)
    bot.reply_to(
        message,
        _("Аккаунт создан. Вам выдан тестовый период: 1 ГБ на 1 день."),
        reply_markup=_user_menu_keyboard(),
    )
    _notify_admins_for_user(
        user,
        text=(
            f"Новый пользователь зарегистрирован\n"
            f"Телефон: {user.name}\n"
            f"UUID: {user.uuid}\n"
            f"Статус: выдан тест 1 ГБ / 1 день"
        ),
    )
    _send_first_link_welcome(message.chat.id, user)
    plans_markup = _plans_keyboard()
    if plans_markup:
        bot.send_message(
            message.chat.id,
            _("Тест активирован. Если вас всё устраивает, выберите тариф:"),
            reply_markup=plans_markup,
        )
    else:
        bot.send_message(
            message.chat.id,
            _("Тарифы пока не опубликованы. Напишите администратору."),
        )
    return
    bot.reply_to(
        message,
        _(
            "Аккаунт создан.\n"
            "Телефон: %(phone)s\n"
            "UUID: %(uuid)s\n\n"
            "Сейчас аккаунт ожидает активации тарифа администратором.",
            phone=phone or "-",
            uuid=user.uuid,
        ),
        reply_markup=_user_menu_keyboard(),
        )
    _notify_admins_for_user(
        user,
        text=(
            f"Новый пользователь зарегистрирован\n"
            f"Телефон: {user.name}\n"
            f"UUID: {user.uuid}\n"
            f"Статус: ожидает выбора тарифа"
        ),
    )
    plans_markup = _plans_keyboard()
    if plans_markup:
        bot.send_message(
            message.chat.id,
            _("Выберите тариф:"),
            reply_markup=plans_markup,
        )
    else:
        bot.send_message(
            message.chat.id,
            _("Тарифы пока не опубликованы. Напишите администратору."),
        )


@bot.callback_query_handler(func=lambda call: call.data == "user_show_plans")
def user_show_plans(call):
    markup = _plans_keyboard()
    try:
        bot.edit_message_text(
            _("Выберите тариф:"),
            call.message.chat.id,
            call.message.message_id,
            reply_markup=markup,
        )
    except Exception:
        pass
    try:
        bot.answer_callback_query(call.id, text=_("Обновлено"), show_alert=False, cache_time=1)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda call: call.data.startswith("user_plan_info "))
def user_plan_info(call):
    plan_id = int(call.data.split(" ", 1)[1])
    plan = CommercialPlan.query.filter(CommercialPlan.id == plan_id, CommercialPlan.enable == True).first()
    if not plan:
        try:
            bot.answer_callback_query(call.id, text=_("Тариф не найден"), show_alert=True, cache_time=1)
        except Exception:
            pass
        return
    try:
        bot.edit_message_text(
            _plan_description(plan),
            call.message.chat.id,
            call.message.message_id,
            reply_markup=_plan_actions_keyboard(plan),
        )
    except Exception:
        pass
    try:
        bot.answer_callback_query(call.id, text=_("Тариф выбран"), show_alert=False, cache_time=1)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda call: call.data.startswith("user_request_plan "))
def user_request_plan(call):
    plan_id = int(call.data.split(" ", 1)[1])
    plan = CommercialPlan.query.filter(CommercialPlan.id == plan_id, CommercialPlan.enable == True).first()
    user = User.query.filter(User.telegram_id == call.message.chat.id).order_by(User.id.desc()).first()
    if not plan or not user:
        try:
            bot.answer_callback_query(call.id, text=_("\u0422\u0430\u0440\u0438\u0444 \u0438\u043b\u0438 \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d"), show_alert=True, cache_time=1)
        except Exception:
            pass
        return
    try:
        bot.answer_callback_query(call.id, text=_("Тариф выбран. Для смены нужен успешный платёж."), show_alert=False, cache_time=1)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda call: call.data.startswith("user_pay_plan "))
def user_pay_plan(call):
    plan_id = int(call.data.split(" ", 1)[1])
    plan = CommercialPlan.query.filter(CommercialPlan.id == plan_id, CommercialPlan.enable == True).first()
    user = User.query.filter(User.telegram_id == call.message.chat.id).order_by(User.id.desc()).first()
    token = _payment_provider_token()
    if not plan or not user:
        try:
            bot.answer_callback_query(call.id, text=_("\u0422\u0430\u0440\u0438\u0444 \u0438\u043b\u0438 \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d"), show_alert=True, cache_time=1)
        except Exception:
            pass
        return
    if not token:
        try:
            bot.answer_callback_query(call.id, text=_("\u041e\u043f\u043b\u0430\u0442\u0430 \u043f\u043e\u043a\u0430 \u043d\u0435 \u043d\u0430\u0441\u0442\u0440\u043e\u0435\u043d\u0430"), show_alert=True, cache_time=1)
        except Exception:
            pass
        return
    try:
        ok, message = _send_plan_invoice(call.message.chat.id, user, plan)
        bot.answer_callback_query(call.id, text=message, show_alert=not ok, cache_time=1)
    except Exception:
        try:
            bot.answer_callback_query(call.id, text=_("\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0441\u043e\u0437\u0434\u0430\u0442\u044c \u0441\u0447\u0451\u0442"), show_alert=True, cache_time=1)
        except Exception:
            pass


@bot.pre_checkout_query_handler(func=lambda query: True)
def process_pre_checkout_query(query):
    token = _payment_provider_token()
    if not token:
        return bot.answer_pre_checkout_query(
            query.id,
            ok=False,
            error_message=_("\u041e\u043f\u043b\u0430\u0442\u0430 \u0432\u0440\u0435\u043c\u0435\u043d\u043d\u043e \u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u043d\u0430"),
        )
    plan_id, user_id = _parse_plan_invoice_payload(getattr(query, "invoice_payload", "") or "")
    if not plan_id or not user_id:
        return bot.answer_pre_checkout_query(
            query.id,
            ok=False,
            error_message=_("\u041d\u0435\u0432\u0435\u0440\u043d\u044b\u0435 \u0434\u0430\u043d\u043d\u044b\u0435 \u0441\u0447\u0451\u0442\u0430"),
        )
    user = User.query.filter(User.id == user_id, User.telegram_id == query.from_user.id).first()
    plan = CommercialPlan.query.filter(CommercialPlan.id == plan_id, CommercialPlan.enable == True).first()
    if not user or not plan:
        return bot.answer_pre_checkout_query(
            query.id,
            ok=False,
            error_message=_("\u0422\u0430\u0440\u0438\u0444 \u0438\u043b\u0438 \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d"),
        )
    if not _payment_matches_plan(
        plan=plan,
        amount_minor=getattr(query, "total_amount", 0),
        currency=getattr(query, "currency", ""),
    ):
        return bot.answer_pre_checkout_query(
            query.id,
            ok=False,
            error_message=_("\u0421\u0443\u043c\u043c\u0430 \u0438\u043b\u0438 \u0432\u0430\u043b\u044e\u0442\u0430 \u0441\u0447\u0451\u0442\u0430 \u043d\u0435 \u0441\u043e\u0432\u043f\u0430\u0434\u0430\u0435\u0442"),
        )
    return bot.answer_pre_checkout_query(query.id, ok=True)


@bot.message_handler(content_types=['successful_payment'])
def successful_payment(message):
    payment = getattr(message, "successful_payment", None)
    payload = getattr(payment, "invoice_payload", "") or ""
    plan_id, user_id = _parse_plan_invoice_payload(payload)
    if not plan_id or not user_id:
        return
    user = User.query.filter(User.id == user_id, User.telegram_id == message.chat.id).first()
    plan = CommercialPlan.query.filter(CommercialPlan.id == plan_id, CommercialPlan.enable == True).first()
    if not user or not plan:
        return
    amount_minor = getattr(payment, "total_amount", 0)
    currency = getattr(payment, "currency", "") or ""
    if not _payment_matches_plan(plan=plan, amount_minor=amount_minor, currency=currency):
        amount = amount_minor / 100
        mismatch_message = (
            f"Payment amount mismatch for user_id={user.id} plan_id={plan.id}: "
            f"expected={_plan_amount_minor(plan)} {(plan.currency or 'RUB').upper()} "
            f"got={amount_minor} {currency.upper()}"
        )
        logger.error(mismatch_message)
        bot.reply_to(
            message,
            _("Платёж получен, но активация не выполнена. Обратитесь к администратору."),
            reply_markup=_user_menu_keyboard(),
        )
        _notify_admins_for_user(
            user,
            text=(
                "Ошибка активации после оплаты\n"
                f"Телефон: {user.name}\n"
                f"UUID: {user.uuid}\n"
                f"Тариф: {plan.name}\n"
                f"Ожидалось: {_format_price(plan)}\n"
                f"Получено: {amount} {currency}"
            ),
            skip_dedup=True,
        )
        return
    external_payment_id = _payment_charge_id(payment)
    if not external_payment_id:
        logger.error("Telegram successful_payment without external payment id for user_id=%s plan_id=%s", user.id, plan.id)
        bot.reply_to(
            message,
            _("Платёж получен, но активация не выполнена. Обратитесь к администратору."),
            reply_markup=_user_menu_keyboard(),
        )
        _notify_admins_for_user(
            user,
            text=(
                "Ошибка активации после оплаты\n"
                f"Телефон: {user.name}\n"
                f"UUID: {user.uuid}\n"
                f"Тариф: {plan.name}\n"
                "Не удалось определить payment charge id."
            ),
            skip_dedup=True,
        )
        return
    existing = CommercialSubscription.query.filter(
        CommercialSubscription.external_payment_id == external_payment_id
    ).first()
    if existing:
        bot.reply_to(
            message,
            _("\u042d\u0442\u043e\u0442 \u043f\u043b\u0430\u0442\u0451\u0436 \u0443\u0436\u0435 \u043e\u0431\u0440\u0430\u0431\u043e\u0442\u0430\u043d."),
            reply_markup=_user_menu_keyboard(),
        )
        return
    subscription = renew_user_package(
        user,
        plan,
        created_by=_default_added_by_id(),
        note=f"Telegram successful payment for {plan.name}",
    )
    subscription.external_payment_id = external_payment_id
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        if not _is_duplicate_external_payment_id_error(exc):
            raise
        existing = CommercialSubscription.query.filter(
            CommercialSubscription.external_payment_id == external_payment_id
        ).first()
        if not existing:
            raise
        bot.reply_to(
            message,
            _("\u042d\u0442\u043e\u0442 \u043f\u043b\u0430\u0442\u0451\u0436 \u0443\u0436\u0435 \u043e\u0431\u0440\u0430\u0431\u043e\u0442\u0430\u043d."),
            reply_markup=_user_menu_keyboard(),
        )
        return
    hiddify.quick_apply_users()
    bot.reply_to(
        message,
        _("\u041e\u043f\u043b\u0430\u0442\u0430 \u043f\u043e\u043b\u0443\u0447\u0435\u043d\u0430. \u0422\u0430\u0440\u0438\u0444 \u0430\u043a\u0442\u0438\u0432\u0438\u0440\u043e\u0432\u0430\u043d."),
        reply_markup=_user_menu_keyboard(),
    )
    with force_locale(user.lang or hconfig(ConfigEnum.lang)):
        bot.send_message(message.chat.id, get_usage_msg(user.uuid), reply_markup=user_keyboard(user.uuid))
    amount = amount_minor / 100
    _notify_admins_for_user(
        user,
        text=(
            f"\u041e\u043f\u043b\u0430\u0442\u0430 \u043f\u043e\u043b\u0443\u0447\u0435\u043d\u0430 \u0430\u0432\u0442\u043e\u043c\u0430\u0442\u0438\u0447\u0435\u0441\u043a\u0438\n"
            f"\u0422\u0435\u043b\u0435\u0444\u043e\u043d: {user.name}\n"
            f"UUID: {user.uuid}\n"
            f"\u0422\u0430\u0440\u0438\u0444: {plan.name}\n"
            f"\u0421\u0443\u043c\u043c\u0430: {amount} {currency}"
        ),
        skip_dedup=True,
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("user_pay_renew "))
def user_pay_renew(call):
    uuid = call.data.split(" ", 1)[1] if " " in call.data else None
    if not uuid:
        return
    user = User.by_uuid(uuid)
    if not user:
        try:
            bot.answer_callback_query(call.id, text=_("Пользователь не найден"), show_alert=True, cache_time=1)
        except Exception:
            pass
        return
    plan = getattr(user, "plan", None)
    if not plan:
        try:
            bot.answer_callback_query(call.id, text=_("Сначала выберите тариф"), show_alert=True, cache_time=1)
        except Exception:
            pass
        return
    try:
        ok, message = _send_plan_invoice(call.message.chat.id, user, plan)
        bot.answer_callback_query(call.id, text=message, show_alert=not ok, cache_time=1)
    except Exception:
        try:
            bot.answer_callback_query(call.id, text=_("Не удалось создать счёт"), show_alert=True, cache_time=1)
        except Exception:
            pass


@bot.callback_query_handler(func=lambda call: call.data.startswith("update_usage"))
def update_usage_callback(call):  # <- passes a CallbackQuery type object to your function
    text = call.data
    uuid = text.split()[1] if len(text.split()) > 1 else None

    if uuid:
        user = User.by_uuid(uuid)
        try:
            with force_locale(f'{user.lang or hconfig(ConfigEnum.lang)}'):
                if not _has_accessible_package(user):
                    new_text = _inactive_user_message(user)
                    reply_markup = _plans_keyboard()
                else:
                    new_text = get_usage_msg(uuid)
                    reply_markup = user_keyboard(uuid)
                bot.edit_message_text(new_text, call.message.chat.id, call.message.message_id, reply_markup=reply_markup)
                bot.answer_callback_query(call.id, text=_("Статус обновлён"), show_alert=False, cache_time=1)
        except Exception as e:
            print(e)
            try:
                bot.answer_callback_query(call.id, cache_time=1)
            except BaseException:
                pass
        return


def user_keyboard(uuid):
    return types.InlineKeyboardMarkup(
        keyboard=[
            [
                types.InlineKeyboardButton(
                    text=_("Обновить статус"),
                    callback_data="update_usage " + uuid
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=_("Продлить тариф"),
                    callback_data="user_pay_renew " + uuid
                ),
                types.InlineKeyboardButton(
                    text=_("Сменить тариф"),
                    callback_data="user_show_plans"
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=_("Ссылка на подписку"),
                    callback_data="user_send_sub " + uuid
                )
            ],
        ]
    )


def get_usage_msg(uuid, domain=None):
    user_data = get_common_data(uuid, 'multi')
    with app.app_context():

        user = user_data['user']
        expire_rel = user_data['expire_rel']
        reset_day = user_data['reset_day']
        plan_name = _display_plan_name(user)
        plan_name = user.plan.name if getattr(user, "plan", None) else _("Тариф не выбран")

        plan_name = _display_plan_name(user)
        domain = domain or Domain.get_domains()[0]
        user_link = hiddify.get_account_panel_link(user, domain.domain)
        with force_locale(user.lang or hconfig(ConfigEnum.lang)):
            msg = f"""{_('<a href="%(user_link)s"> %(user)s</a>',user_link=user_link ,user=user.name if user.name != "default" else "")}\n\n"""
            msg += f"""<b>{_('Тариф')}:</b> {plan_name}\n"""

            msg += f"""{_('user.home.usage.title')} {round(user.current_usage_GB, 3)}GB <b>{_('user.home.usage.from')}</b> {user.usage_limit_GB}GB  {_('user.home.usage.monthly') if user.monthly else ''}\n"""
            msg += f"""<b>{_('user.home.usage.expire')}</b> {expire_rel}"""

            if reset_day < 500:
                msg += f"""\n<b>{_('Reset Usage Time:')}</b> {reset_day} {_('days')}"""

            msg += f"""\n\n <a href="{user_link}">{_('Личный кабинет')}</a>  -  <a href="https://t.me/{bot.username}?start={user.uuid}">{_('Бот Telegram')}</a>"""
    return msg


@bot.callback_query_handler(func=lambda call: call.data.startswith("user_request_renew "))
def user_request_renew(call):
    uuid = call.data.split(" ", 1)[1] if " " in call.data else None
    if not uuid:
        return
    user = User.by_uuid(uuid)
    if not user:
        try:
            bot.answer_callback_query(call.id, text=_("Пользователь не найден"), show_alert=True, cache_time=1)
        except Exception:
            pass
        return
    plan_name = user.plan.name if getattr(user, "plan", None) else _("Тариф не выбран")
    _notify_admins_for_user(
        user,
        text=(
            f"Пользователь запросил продление тарифа\n"
            f"Телефон: {user.name}\n"
            f"UUID: {user.uuid}\n"
            f"Текущий тариф: {plan_name}"
        ),
    )
    try:
        bot.answer_callback_query(call.id, text=_("Запрос отправлен администратору"), show_alert=False, cache_time=1)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda call: call.data.startswith("user_send_sub "))
def user_send_sub(call):
    uuid = call.data.split(" ", 1)[1] if " " in call.data else None
    if not uuid:
        return
    user = User.by_uuid(uuid)
    if not user:
        try:
            bot.answer_callback_query(call.id, text=_("Пользователь не найден"), show_alert=True, cache_time=1)
        except Exception:
            pass
        return
    try:
        bot.send_message(
            call.message.chat.id,
            _subscription_links_message(user),
            reply_markup=_subscription_link_keyboard(user),
            disable_web_page_preview=True,
        )
        bot.answer_callback_query(call.id, text=_("Ссылка отправлена"), show_alert=False, cache_time=1)
    except Exception:
        try:
            bot.answer_callback_query(call.id, cache_time=1)
        except Exception:
            pass


_DEFAULT_EXPIRY_REMINDER_DAYS = "2,1"
_DEFAULT_EXPIRY_REMINDER_MESSAGE = "У вас заканчивается подписка через {days_left} дн. Не забудьте продлить тариф."


def _telegram_expiry_reminder_days() -> list[int]:
    raw = (hconfig(ConfigEnum.telegram_subscription_expiry_reminder_days) or "").strip() or _DEFAULT_EXPIRY_REMINDER_DAYS
    result = []
    for part in raw.split(","):
        part = part.strip()
        if not part or not part.isdigit():
            continue
        day = int(part)
        if day >= 0 and day not in result:
            result.append(day)
    return result


def _telegram_expiry_reminder_message_template() -> str:
    return (hconfig(ConfigEnum.telegram_subscription_expiry_reminder_message) or "").strip() or _DEFAULT_EXPIRY_REMINDER_MESSAGE


def _render_expiry_reminder_message(user: User) -> str:
    template = _telegram_expiry_reminder_message_template()
    plan_name = user.plan.name if getattr(user, "plan", None) else ""
    expire_rel = hutils.convert.format_timedelta(datetime.timedelta(days=user.remaining_days))
    try:
        return template.format(name=user.name or "", days_left=user.remaining_days, plan_name=plan_name, expire_rel=expire_rel)
    except Exception:
        return _DEFAULT_EXPIRY_REMINDER_MESSAGE.format(days_left=user.remaining_days)


@shared_task(ignore_result=False)
def send_expiry_reminders_task():
    token = telegram_bot_token()
    if not token:
        print("Telegram reminder skipped: telegram bot token is not configured")
        return {"sent": 0, "checked": 0, "days": [], "error": "missing_token"}
    bot.token = token
    reminder_days = _telegram_expiry_reminder_days()
    if not reminder_days:
        return {"sent": 0, "checked": 0, "days": []}
    today = datetime.date.today().isoformat()
    checked = 0
    sent = 0
    users = User.query.filter(User.telegram_id.isnot(None), User.telegram_id != 0, User.enable == True).all()
    for user in users:
        checked += 1
        days_left = int(user.remaining_days or 0)
        if days_left not in reminder_days:
            continue
        if not user.is_active:
            continue
        reminder_key = f"{today}:{days_left}"
        if (user.telegram_last_expiry_reminder_key or "") == reminder_key:
            continue
        try:
            bot.send_message(int(user.telegram_id), _render_expiry_reminder_message(user), disable_web_page_preview=True)
            user.telegram_last_expiry_reminder_key = reminder_key
            db.session.add(user)
            db.session.commit()
            sent += 1
        except Exception as exc:
            db.session.rollback()
            print(f"Failed to send expiry reminder to user {user.id}: {exc}")
    return {"sent": sent, "checked": checked, "days": reminder_days}
