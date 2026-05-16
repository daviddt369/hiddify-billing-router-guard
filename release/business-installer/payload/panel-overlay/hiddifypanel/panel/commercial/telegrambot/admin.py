from flask_babel import force_locale
from flask_babel import gettext as _
from telebot import types
import datetime

from hiddifypanel.database import db
from hiddifypanel.panel import hiddify
from hiddifypanel.models import *
from hiddifypanel import hutils
from hiddifypanel.commercial_logic import renew_user_package
from . import bot


def _admin_lang(admin: AdminUser | None) -> str:
    lang = getattr(admin, "lang", None) or hconfig(ConfigEnum.admin_lang) or "en"
    return str(lang).lower()


def _t(admin: AdminUser | None, ru: str, en: str) -> str:
    return ru if _admin_lang(admin).startswith("ru") else en


def _safe_answer(call, text: str, show_alert: bool = False):
    try:
        bot.answer_callback_query(call.id, text=text, show_alert=show_alert, cache_time=1)
    except Exception:
        return


def _refresh_admin_user_message(call, user: User, admin: AdminUser):
    try:
        bot.edit_message_text(
            _admin_user_summary(user, admin),
            call.message.chat.id,
            call.message.message_id,
            reply_markup=admin_user_actions_keyboard_v3(user, admin),
        )
    except Exception as exc:
        if "message is not modified" not in str(exc).lower():
            raise


def _normalize_phone(value: str | None) -> str:
    raw = (value or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return raw
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    if not digits.startswith("7") and len(digits) == 10:
        digits = "7" + digits
    return f"+{digits}"


def _admin_reply_keyboard(admin: AdminUser | None = None):
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row(
        types.KeyboardButton(text=_t(admin, "Статистика", "Stats")),
        types.KeyboardButton(text=_t(admin, "Пользователи", "Users")),
    )
    keyboard.row(
        types.KeyboardButton(text=_t(admin, "Создать", "Create")),
        types.KeyboardButton(text=_t(admin, "Найти", "Find")),
    )
    return keyboard


def _find_user_by_phone_for_admin(admin: AdminUser, phone: str) -> User | None:
    normalized = _normalize_phone(phone)
    if not normalized:
        return None
    return (
        User.query.filter(
            User.added_by.in_(admin.recursive_sub_admins_ids()),
            ((User.name == normalized) | (User.username == normalized)),
        )
        .order_by(User.id.desc())
        .first()
    )


def _send_admin_user_with_subscription(chat_id: int, user: User, admin: AdminUser):
    from .Usage import _subscription_link_keyboard, _subscription_links_message

    bot.send_message(
        chat_id,
        _admin_user_summary(user, admin),
        reply_markup=admin_user_actions_keyboard_v3(user, admin),
    )
    bot.send_message(
        chat_id,
        _subscription_links_message(user),
        reply_markup=_subscription_link_keyboard(user),
    )
    bot.send_message(
        chat_id,
        _t(admin, "Используйте админское меню ниже.", "Use the admin menu below."),
        reply_markup=_admin_reply_keyboard(admin),
    )


def _create_trial_user_for_admin(admin: AdminUser, phone: str) -> User:
    from .Usage import TRIAL_MAX_IPS, TRIAL_PACKAGE_DAYS, TRIAL_USAGE_LIMIT_GB

    user = User(
        name=phone,
        username=phone,
        added_by=admin.id,
        enable=True,
        usage_limit=TRIAL_USAGE_LIMIT_GB * ONE_GIG,
        package_days=TRIAL_PACKAGE_DAYS,
        max_ips=TRIAL_MAX_IPS,
        mode=UserMode.no_reset,
        start_date=datetime.date.today(),
        last_reset_time=datetime.date.today(),
        comment="Telegram admin trial creation",
    )
    db.session.add(user)
    db.session.commit()
    hiddify.quick_apply_users()
    return user


@bot.message_handler(commands=['start'], func=lambda message: "admin" in message.text)
def send_welcome(message):
    text = message.text
    # print("dddd",text)
    uuid = text.split()[1].split("_")[1] if len(text.split()) > 1 else None
    if hutils.auth.is_uuid_valid(uuid):
        admin_user = AdminUser.by_uuid(uuid)
        if admin_user:
            admin_user.telegram_id = message.chat.id
            db.session.commit()
    else:
        admin_user = AdminUser.query.filter(AdminUser.telegram_id == message.chat.id).first()

    if admin_user:
        with force_locale(admin_user.lang or hconfig(ConfigEnum.admin_lang)):
            start_admin(message)
        return
    bot.reply_to(message, "error")


def start_admin(message):
    admin = get_admin_by_tgid(message)
    bot.reply_to(message, _("bot.admin_welcome"), reply_markup=_admin_reply_keyboard(admin))
    bot.send_message(message.chat.id, _("bot.admin_welcome"), reply_markup=admin_keyboard_main())


def get_admin_by_tgid(message):

    tgid = message.chat.id
    return AdminUser.query.filter(AdminUser.telegram_id == tgid).first()


def admin_keyboard_main():

    return types.InlineKeyboardMarkup(keyboard=[
        [
            types.InlineKeyboardButton(
                text="Статистика",
                callback_data="admin_stats"
            ),
            types.InlineKeyboardButton(
                text="Пользователи",
                callback_data="admin_recent_users"
            )
        ],
        [
            types.InlineKeyboardButton(
                text="Создать",
                callback_data='admin_help_create_user'
            ),
            types.InlineKeyboardButton(
                text="Найти",
                callback_data='admin_help_find_user'
            )
        ],
    ])


def admin_user_actions_keyboard(user: User, admin: AdminUser | None = None):
    return types.InlineKeyboardMarkup(keyboard=[
        [
            types.InlineKeyboardButton(
                text=_t(admin, "Продлить пакет", "Renew package"),
                callback_data=f"admin_renew_user {user.id}"
            )
        ],
        [
            types.InlineKeyboardButton(
                text=_t(admin, "Включить", "Enable"),
                callback_data=f"admin_enable_user {user.id}"
            ),
            types.InlineKeyboardButton(
                text=_t(admin, "Отключить", "Disable"),
                callback_data=f"admin_disable_user {user.id}"
            )
        ]
    ])


def admin_user_actions_keyboard_v2(user: User, admin: AdminUser | None = None):
    return types.InlineKeyboardMarkup(keyboard=[
        [
            types.InlineKeyboardButton(
                text=_t(admin, "Активировать тариф", "Activate plan"),
                callback_data=f"admin_activate_plan {user.id}"
            )
        ],
        [
            types.InlineKeyboardButton(
                text=_t(admin, "Активировать пакет", "Renew package"),
                callback_data=f"admin_renew_user {user.id}"
            )
        ],
        [
            types.InlineKeyboardButton(
                text=_t(admin, "Включить", "Enable"),
                callback_data=f"admin_enable_user {user.id}"
            ),
            types.InlineKeyboardButton(
                text=_t(admin, "Отключить", "Disable"),
                callback_data=f"admin_disable_user {user.id}"
            )
        ]
    ])


def admin_user_actions_keyboard_v3(user: User, admin: AdminUser | None = None):
    return types.InlineKeyboardMarkup(keyboard=[
        [
            types.InlineKeyboardButton(
                text=_t(admin, "Активировать выбранный тариф", "Activate selected plan"),
                callback_data=f"admin_activate_plan {user.id}"
            )
        ],
        [
            types.InlineKeyboardButton(
                text=_t(admin, "Продлить текущий тариф", "Renew current plan"),
                callback_data=f"admin_renew_user {user.id}"
            )
        ],
        [
            types.InlineKeyboardButton(
                text=_t(admin, "Включить", "Enable"),
                callback_data=f"admin_enable_user {user.id}"
            ),
            types.InlineKeyboardButton(
                text=_t(admin, "Отключить", "Disable"),
                callback_data=f"admin_disable_user {user.id}"
            )
        ]
    ])


def _format_gb_from_bytes(value: int | float) -> str:
    gb = float(value or 0) / (1024 * 1024 * 1024)
    return f"{gb:.2f} GB"


def _find_plan_for_input(plan_input: str, admin: AdminUser) -> CommercialPlan | None:
    value = (plan_input or "").strip().lower()
    if not value:
        return None

    base_query = CommercialPlan.query.filter(
        CommercialPlan.enable == True,
        CommercialPlan.added_by.in_(admin.recursive_sub_admins_ids()),
    )

    plan = None
    if hutils.convert.is_int(value):  # type: ignore
        numeric = int(value)
        plan = base_query.filter(CommercialPlan.id == numeric).first()
        if plan:
            return plan
        plan = base_query.filter(CommercialPlan.max_ips == numeric).order_by(CommercialPlan.usage_limit.asc()).first()
        if plan:
            return plan
        plan = base_query.filter(CommercialPlan.usage_limit == numeric * ONE_GIG).order_by(CommercialPlan.max_ips.asc()).first()
        if plan:
            return plan

    aliases = {
        "300": "300 GB / 30 days / 3 IP",
        "3": "300 GB / 30 days / 3 IP",
        "3ip": "300 GB / 30 days / 3 IP",
        "500": "500 GB / 30 days / 5 IP",
        "5": "500 GB / 30 days / 5 IP",
        "5ip": "500 GB / 30 days / 5 IP",
    }
    if value in aliases:
        plan = base_query.filter(CommercialPlan.name == aliases[value]).first()
        if plan:
            return plan

    return base_query.filter(CommercialPlan.name.ilike(f"%{plan_input.strip()}%")).order_by(CommercialPlan.id.asc()).first()


def _admin_stats_message(admin: AdminUser) -> str:
    stats = DailyUsage.get_daily_usage_stats(admin.id)
    return (
        f"<b>{_t(admin, 'Статистика администратора', 'Admin stats')}</b>\n"
        f"{_t(admin, 'Сегодня', 'Today')}: <b>{_format_gb_from_bytes(stats['today']['usage'])}</b> | {_t(admin, 'онлайн', 'online')} <b>{stats['today']['online']}</b>\n"
        f"{_t(admin, 'Вчера', 'Yesterday')}: <b>{_format_gb_from_bytes(stats['yesterday']['usage'])}</b> | {_t(admin, 'онлайн', 'online')} <b>{stats['yesterday']['online']}</b>\n"
        f"{_t(admin, 'За 30 дней', 'Last 30 days')}: <b>{_format_gb_from_bytes(stats['last_30_days']['usage'])}</b> | {_t(admin, 'онлайн', 'online')} <b>{stats['last_30_days']['online']}</b>\n"
        f"{_t(admin, 'Онлайн за 24ч', 'Last 24h online')}: <b>{stats['h24']['online']}</b>\n"
        f"{_t(admin, 'Онлайн за 5 мин', 'Last 5 min online')}: <b>{stats['m5']['online']}</b>\n"
        f"{_t(admin, 'Всего пользователей', 'Total users')}: <b>{stats['total']['users']}</b>"
    )


def _recent_users_for_admin(admin: AdminUser, limit: int = 10) -> list[User]:
    return (
        User.query.filter(User.added_by.in_(admin.recursive_sub_admins_ids()))
        .order_by(User.id.desc())
        .limit(limit)
        .all()
    )


def _find_users_for_admin(admin: AdminUser, query: str) -> list[User]:
    query = (query or "").strip()
    if not query:
        return []
    normalized = _normalize_phone(query)
    base_query = User.query.filter(User.added_by.in_(admin.recursive_sub_admins_ids()))
    if hutils.auth.is_uuid_valid(query):
        user = base_query.filter(User.uuid == query).first()
        return [user] if user else []
    if normalized:
        exact = (
            base_query.filter((User.name == normalized) | (User.username == normalized))
            .order_by(User.id.desc())
            .limit(5)
            .all()
        )
        if exact:
            return exact
    if hutils.convert.is_int(query):  # type: ignore
        user = base_query.filter(User.id == int(query)).first()
        return [user] if user else []
    like = f"%{query}%"
    users = (
        base_query.filter((User.name.ilike(like)) | (User.username.ilike(like)))
        .order_by(User.id.desc())
        .limit(5)
        .all()
    )
    if users:
        return users
    if normalized:
        like_normalized = f"%{normalized}%"
        return (
            base_query.filter(
                (User.name.ilike(like_normalized)) | (User.username.ilike(like_normalized))
            )
            .order_by(User.id.desc())
            .limit(5)
            .all()
        )
    return []


def _admin_user_summary(user: User, admin: AdminUser | None = None) -> str:
    plan_name = user.plan.name if user.plan else _t(admin, "Без тарифа", "No plan")
    remaining_days = user.remaining_days
    usage = round(user.current_usage_GB, 3)
    limit = round(user.usage_limit_GB, 3)
    status = _t(admin, "Активен", "active") if user.is_active else (user.inactive_reason or _t(admin, "Неактивен", "inactive"))
    return (
        f"<b>{user.name}</b>\n"
        f"UUID: <code>{user.uuid}</code>\n"
        f"{_t(admin, 'Тариф', 'Plan')}: <b>{plan_name}</b>\n"
        f"{_t(admin, 'Трафик', 'Traffic')}: <b>{usage} / {limit} GB</b>\n"
        f"{_t(admin, 'Дней осталось', 'Days left')}: <b>{remaining_days}</b>\n"
        f"{_t(admin, 'Макс. IP', 'Max IPs')}: <b>{user.max_ips}</b>\n"
        f"{_t(admin, 'Режим', 'Mode')}: <b>{user.mode}</b>\n"
        f"{_t(admin, 'Статус', 'Status')}: <b>{status}</b>"
    )


@bot.message_handler(commands=['finduser'])
def find_user_command(message):
    admin = get_admin_by_tgid(message)
    if not admin:
        return
    query = message.text.split(" ", 1)[1].strip() if " " in message.text else ""
    with force_locale(admin.lang or hconfig(ConfigEnum.admin_lang)):
        users = _find_users_for_admin(admin, query)
        if not users:
            bot.reply_to(message, _t(admin, "Пользователь не найден", "User not found"))
            return
        for user in users:
            bot.send_message(
                message.chat.id,
                _admin_user_summary(user, admin),
                reply_markup=admin_user_actions_keyboard_v3(user, admin),
            )


@bot.message_handler(commands=['stats'])
def admin_stats_command(message):
    admin = get_admin_by_tgid(message)
    if not admin:
        return
    with force_locale(admin.lang or hconfig(ConfigEnum.admin_lang)):
        bot.reply_to(message, _admin_stats_message(admin), reply_markup=_admin_reply_keyboard(admin))


@bot.message_handler(commands=['users'])
def admin_users_command(message):
    admin = get_admin_by_tgid(message)
    if not admin:
        return
    with force_locale(admin.lang or hconfig(ConfigEnum.admin_lang)):
        users = _recent_users_for_admin(admin)
        if not users:
            bot.reply_to(message, _t(admin, "Пользователи не найдены", "No users found"), reply_markup=admin_keyboard_main())
            return
        for user in users:
            bot.send_message(
                message.chat.id,
                _admin_user_summary(user, admin),
            reply_markup=admin_user_actions_keyboard_v3(user, admin),
            )


@bot.message_handler(commands=['newuser'])
def admin_new_user_command(message):
    admin = get_admin_by_tgid(message)
    if not admin:
        return
    text = (message.text or "").strip()
    parts = text.split(maxsplit=2)
    with force_locale(admin.lang or hconfig(ConfigEnum.admin_lang)):
        if len(parts) < 3:
            bot.reply_to(
                message,
                _t(admin, "Используй: /newuser <300|500|plan-id> <телефон>\nПример: /newuser 300 +79991234567", "Use: /newuser <300|500|plan-id> <name>\nExample: /newuser 300 ivan"),
                reply_markup=admin_keyboard_main(),
            )
            return

        plan_input = parts[1]
        user_name = _normalize_phone(parts[2].strip())
        if not user_name:
            bot.reply_to(message, _t(admin, "Нужно указать имя или телефон", "User name is required"), reply_markup=admin_keyboard_main())
            return

        plan = _find_plan_for_input(plan_input, admin)
        if not plan:
            bot.reply_to(message, _t(admin, "Тариф не найден", "Plan not found"), reply_markup=admin_keyboard_main())
            return

        user = User(
            name=user_name,
            username=user_name,
            added_by=admin.id,
            enable=True,
        )
        db.session.add(user)
        db.session.flush()
        renew_user_package(
            user,
            plan,
            created_by=admin.id,
            note=f"Telegram manual creation by admin #{admin.id}",
        )
        db.session.commit()
        hiddify.quick_apply_users()
        bot.send_message(
            message.chat.id,
            _admin_user_summary(user, admin),
            reply_markup=admin_user_actions_keyboard_v3(user, admin),
        )


def _prompt_admin_create_user(message, admin: AdminUser):
    prompt = _t(
        admin,
        "Введите номер телефона нового пользователя.\nПример: +79991234567",
        "Enter the new user's phone number.\nExample: +79991234567",
    )
    sent = bot.reply_to(message, prompt, reply_markup=_admin_reply_keyboard(admin))
    bot.register_next_step_handler(sent, _handle_admin_create_user_phone)


def _prompt_admin_find_user(message, admin: AdminUser):
    prompt = _t(
        admin,
        "Введите телефон, UUID, ID или имя пользователя.",
        "Enter a phone number, UUID, ID, or user name.",
    )
    sent = bot.reply_to(message, prompt, reply_markup=_admin_reply_keyboard(admin))
    bot.register_next_step_handler(sent, _handle_admin_find_user_query)


def _handle_admin_create_user_phone(message):
    admin = get_admin_by_tgid(message)
    if not admin:
        return
    with force_locale(admin.lang or hconfig(ConfigEnum.admin_lang)):
        phone = _normalize_phone(message.text)
        if not phone or len("".join(ch for ch in phone if ch.isdigit())) < 11:
            bot.reply_to(
                message,
                _t(admin, "Неверный номер. Используйте формат +79991234567", "Invalid phone number. Use format +79991234567"),
                reply_markup=_admin_reply_keyboard(admin),
            )
            return
        existing = _find_user_by_phone_for_admin(admin, phone)
        if existing:
            bot.reply_to(
                message,
                _t(admin, "Пользователь уже существует. Отправляю текущую ссылку.", "User already exists. Sending the current subscription link."),
                reply_markup=_admin_reply_keyboard(admin),
            )
            _send_admin_user_with_subscription(message.chat.id, existing, admin)
            return

        user = _create_trial_user_for_admin(admin, phone)
        bot.reply_to(
            message,
            _t(admin, "Создан trial-пользователь: 1 GB / 2 дня", "Trial user created: 1 GB / 2 days"),
            reply_markup=_admin_reply_keyboard(admin),
        )
        _send_admin_user_with_subscription(message.chat.id, user, admin)


def _handle_admin_find_user_query(message):
    admin = get_admin_by_tgid(message)
    if not admin:
        return
    with force_locale(admin.lang or hconfig(ConfigEnum.admin_lang)):
        users = _find_users_for_admin(admin, message.text)
        if not users:
            bot.reply_to(
                message,
                _t(admin, "Пользователь не найден", "User not found"),
                reply_markup=_admin_reply_keyboard(admin),
            )
            return
        bot.reply_to(
            message,
            _t(admin, "Нашёл пользователей:", "Found users:"),
            reply_markup=_admin_reply_keyboard(admin),
        )
        for user in users:
            bot.send_message(
                message.chat.id,
                _admin_user_summary(user, admin),
                reply_markup=admin_user_actions_keyboard_v3(user, admin),
            )


@bot.message_handler(func=lambda message: (message.text or "").strip() in {"Статистика", "Пользователи", "Создать", "Найти", "Stats", "Users", "Create", "Find"})
def admin_reply_keyboard_actions(message):
    admin = get_admin_by_tgid(message)
    if not admin:
        return
    text = (message.text or "").strip()
    with force_locale(admin.lang or hconfig(ConfigEnum.admin_lang)):
        if text in {"Статистика", "Stats"}:
            bot.reply_to(message, _admin_stats_message(admin), reply_markup=_admin_reply_keyboard(admin))
            return
        if text in {"Пользователи", "Users"}:
            users = _recent_users_for_admin(admin)
            if not users:
                bot.reply_to(message, _t(admin, "Пользователи не найдены", "No users found"), reply_markup=_admin_reply_keyboard(admin))
                return
            bot.reply_to(message, _t(admin, "Последние пользователи:", "Recent users:"), reply_markup=_admin_reply_keyboard(admin))
            for user in users:
                bot.send_message(
                    message.chat.id,
                    _admin_user_summary(user, admin),
                    reply_markup=admin_user_actions_keyboard_v3(user, admin),
                )
            return
        if text in {"Создать", "Create"}:
            _prompt_admin_create_user(message, admin)
            return
        if text in {"Найти", "Find"}:
            _prompt_admin_find_user(message, admin)
            return


@bot.callback_query_handler(func=lambda call: call.data == "admin_help_find_user")
def show_find_user_help(call):
    admin = get_admin_by_tgid(call.message)
    if not admin:
        return
    with force_locale(admin.lang or hconfig(ConfigEnum.admin_lang)):
        _safe_answer(call, _t(admin, "Используй /finduser <имя|uuid|id>", "Use /finduser <name|uuid|id>"), show_alert=True)


@bot.callback_query_handler(func=lambda call: call.data == "admin_help_create_user")
def show_create_user_help(call):
    admin = get_admin_by_tgid(call.message)
    if not admin:
        return
    with force_locale(admin.lang or hconfig(ConfigEnum.admin_lang)):
        _safe_answer(call, _t(admin, "Используй /newuser <300|500|plan-id> <телефон>\nПример: /newuser 500 +79991234567", "Use /newuser <300|500|plan-id> <name>\nExample: /newuser 500 client_1"), show_alert=True)


@bot.callback_query_handler(func=lambda call: call.data == "admin_stats")
def show_admin_stats(call):
    admin = get_admin_by_tgid(call.message)
    if not admin:
        return
    with force_locale(admin.lang or hconfig(ConfigEnum.admin_lang)):
        try:
            bot.edit_message_text(
                _admin_stats_message(admin),
                call.message.chat.id,
                call.message.message_id,
                reply_markup=admin_keyboard_main(),
            )
        except Exception as exc:
            if "message is not modified" not in str(exc).lower():
                raise
        _safe_answer(call, _t(admin, "Обновлено", "Updated"))


@bot.callback_query_handler(func=lambda call: call.data == "admin_recent_users")
def show_recent_users(call):
    admin = get_admin_by_tgid(call.message)
    if not admin:
        return
    with force_locale(admin.lang or hconfig(ConfigEnum.admin_lang)):
        users = _recent_users_for_admin(admin)
        if not users:
            _safe_answer(call, _t(admin, "Пользователи не найдены", "No users found"), show_alert=True)
            return
        _safe_answer(call, _t(admin, "Отправляю пользователей", "Sending recent users"))
        for user in users:
            bot.send_message(
                call.message.chat.id,
                _admin_user_summary(user, admin),
                reply_markup=admin_user_actions_keyboard_v3(user, admin),
            )


def _get_user_for_admin_action(admin: AdminUser, user_id: int) -> User | None:
    return User.query.filter(
        User.id == user_id,
        User.added_by.in_(admin.recursive_sub_admins_ids()),
    ).first()


@bot.callback_query_handler(func=lambda call: call.data.startswith("admin_renew_user "))
def admin_renew_user(call):
    admin = get_admin_by_tgid(call.message)
    if not admin:
        return
    user_id = int(call.data.split(" ", 1)[1])
    user = _get_user_for_admin_action(admin, user_id)
    if not user:
        _safe_answer(call, _t(admin, "Пользователь не найден", "User not found"), show_alert=True)
        return
    if not user.plan or not user.plan.enable:
        _safe_answer(call, _t(admin, "У пользователя нет активного тарифа", "User has no active plan"), show_alert=True)
        return

    with force_locale(admin.lang or hconfig(ConfigEnum.admin_lang)):
        renew_user_package(
            user,
            user.plan,
            created_by=admin.id,
            note=f"Telegram renewal by admin #{admin.id}",
        )
        db.session.commit()
        hiddify.quick_apply_users()
        _refresh_admin_user_message(call, user, admin)
        _safe_answer(call, _t(admin, "Пакет продлён", "Package renewed"))


@bot.callback_query_handler(func=lambda call: call.data.startswith("admin_activate_plan "))
def admin_activate_plan(call):
    admin = get_admin_by_tgid(call.message)
    if not admin:
        return
    user_id = int(call.data.split(" ", 1)[1])
    user = _get_user_for_admin_action(admin, user_id)
    if not user:
        _safe_answer(call, _t(admin, "Пользователь не найден", "User not found"), show_alert=True)
        return
    if not user.plan or not user.plan.enable:
        _safe_answer(call, _t(admin, "У пользователя не выбран тариф", "User has no selected plan"), show_alert=True)
        return
    with force_locale(admin.lang or hconfig(ConfigEnum.admin_lang)):
        renew_user_package(
            user,
            user.plan,
            created_by=admin.id,
            note=f"Telegram plan activation by admin #{admin.id}",
        )
        db.session.commit()
        hiddify.quick_apply_users()
        db.session.refresh(user)
        _refresh_admin_user_message(call, user, admin)
        _safe_answer(call, _t(admin, "Тариф активирован", "Plan activated"))


@bot.callback_query_handler(func=lambda call: call.data.startswith("admin_enable_user "))
def admin_enable_user(call):
    admin = get_admin_by_tgid(call.message)
    if not admin:
        return
    user_id = int(call.data.split(" ", 1)[1])
    user = _get_user_for_admin_action(admin, user_id)
    if not user:
        _safe_answer(call, _t(admin, "Пользователь не найден", "User not found"), show_alert=True)
        return
    with force_locale(admin.lang or hconfig(ConfigEnum.admin_lang)):
        user.enable = True
        db.session.commit()
        hiddify.quick_apply_users()
        db.session.refresh(user)
        _refresh_admin_user_message(call, user, admin)
        _safe_answer(call, _t(admin, "Пользователь включён", "User enabled"))


@bot.callback_query_handler(func=lambda call: call.data.startswith("admin_disable_user "))
def admin_disable_user(call):
    admin = get_admin_by_tgid(call.message)
    if not admin:
        return
    user_id = int(call.data.split(" ", 1)[1])
    user = _get_user_for_admin_action(admin, user_id)
    if not user:
        _safe_answer(call, _t(admin, "Пользователь не найден", "User not found"), show_alert=True)
        return
    with force_locale(admin.lang or hconfig(ConfigEnum.admin_lang)):
        user.enable = False
        db.session.commit()
        hiddify.quick_apply_users()
        db.session.refresh(user)
        _refresh_admin_user_message(call, user, admin)
        _safe_answer(call, _t(admin, "Пользователь отключён", "User disabled"))


def admin_keyboard_gig(old_action):
    def keyboard(gig):
        return types.InlineKeyboardButton(
            text=f"{gig} GB",
            callback_data=f"{old_action} {gig}"
        )
    return types.InlineKeyboardMarkup(keyboard=[
        [keyboard(i) for i in range(1, 5)],
        [keyboard(5 * i) for i in range(1, 5)],
        [keyboard(50 * i) for i in range(1, 5)]
    ]
    )


def admin_keyboard_days(old_action):
    def keyboard(days):
        return types.InlineKeyboardButton(
            text=f"{days}",
            callback_data=f"{old_action} {days}"
        )
    return types.InlineKeyboardMarkup(keyboard=[
        [keyboard(i) for i in range(1, 16, 3)],
        [keyboard(30 * i) for i in range(1, 5)]
    ]
    )


def admin_keyboard_count(old_action):
    def keyboard(count):
        return types.InlineKeyboardButton(
            text=f"{count}",
            callback_data=f"{old_action} {count}"
        )
    return types.InlineKeyboardMarkup(keyboard=[
        [keyboard(i) for i in range(1, 5)],
        [keyboard(i * 5) for i in range(1, 5)],
        [keyboard(i * 50) for i in range(1, 5)]
    ]
    )


def admin_keyboard_domain(old_action):
    def keyboard(domain):
        return types.InlineKeyboardButton(
            text=f"{domain.alias or domain.domain}",
            callback_data=f"{old_action} {domain.id}"
        )
    return types.InlineKeyboardMarkup(keyboard=[
        [keyboard(d)]
        for d in Domain.get_domains()
    ]
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith(f'create_package'))
def create_package(call):  # <- passes a CallbackQuery type object to your function
    admin = get_admin_by_tgid(call.message)
    if not admin:
        return
    with force_locale(admin.lang or hconfig(ConfigEnum.admin_lang)):
        _safe_answer(
            call,
            _t(admin, "Массовое создание отключено. Используй /newuser <300|500|plan-id> <телефон>", "Bulk package creation is disabled. Use /newuser <300|500|plan-id> <name>"),
            show_alert=True,
        )
