import json
import os
import re
from urllib.parse import urlencode
from uuid import UUID

import wtforms as wtf
from flask import redirect, render_template, request
from flask_babel import lazy_gettext as _
from flask_classful import FlaskView
from flask_wtf import FlaskForm
from wtforms.validators import ValidationError

from hiddifypanel import hutils
from hiddifypanel.auth import login_required
from hiddifypanel.database import db
from hiddifypanel.models import ConfigEnum, Role, get_hconfigs, hconfig, set_hconfig
from hiddifypanel.panel import hiddify
from hiddifypanel.panel.commercial import capabilities
try:
    from hiddifypanel.panel.commercial.telegrambot.secrets import telegram_bot_token, telegram_payment_provider_token
except Exception:
    def telegram_bot_token():
        return (hconfig(ConfigEnum.telegram_bot_token) or "").strip()

    def telegram_payment_provider_token():
        return (hconfig(ConfigEnum.telegram_payment_provider_token) or "").strip()

# BEGIN COMMERCIAL ROUTING EDITABLE UI CONFIG
COMMERCIAL_ROUTING_DIRECT_DNS_KEY = "commercial_direct_dns_servers"
COMMERCIAL_ROUTING_PROXY_DNS_KEY = "commercial_proxy_dns_servers"
COMMERCIAL_ROUTING_BLOCKED_DOMAINS_KEY = "commercial_blocked_domains"
COMMERCIAL_ROUTING_UI_PRIMARY_PATH = "/opt/hiddify-manager/hiddify-panel/var/commercial-routing-ui.json"
COMMERCIAL_ROUTING_UI_LEGACY_PATH = "/etc/xray-router/commercial-routing-ui.json"

DEFAULT_COMMERCIAL_ROUTING_DIRECT_DNS = "77.88.8.8\n77.88.8.1"
DEFAULT_COMMERCIAL_ROUTING_PROXY_DNS = "1.1.1.1\n1.0.0.1\n8.8.8.8\n8.8.4.4"
DEFAULT_COMMERCIAL_ROUTING_BLOCKED_DOMAINS = "gosuslugi.ru\ngslb.gosuslugi.ru\ngu-st.ru\nnalog.ru\nnalog.gov.ru"


def _routing_modules():
    try:
        from hiddifypanel.models.commercial_routing_custom_rule import CommercialRoutingCustomRule
        from hiddifypanel.hutils import commercial_routing
        return CommercialRoutingCustomRule, commercial_routing
    except Exception:
        return None, None


def _require_routing_modules():
    CommercialRoutingCustomRule, commercial_routing = _routing_modules()
    if not CommercialRoutingCustomRule or not commercial_routing:
        raise RuntimeError("routing addon is not installed")
    return CommercialRoutingCustomRule, commercial_routing


def _commercial_routing_config_text(key, default):
    _, commercial_routing = _routing_modules()
    if not commercial_routing:
        return default
    try:
        from pathlib import Path

        routing_ui_paths = tuple(Path(p) for p in commercial_routing.commercial_routing_ui_read_paths())

        for routing_ui_path in routing_ui_paths:
            if not routing_ui_path.exists():
                continue

            try:
                data = json.loads(routing_ui_path.read_text(encoding="utf-8"))
            except Exception:
                continue

            if not isinstance(data, dict):
                continue

            value = data.get(key)
            value = "" if value is None else str(value)
            value = value.replace("\r\n", "\n").replace("\r", "\n")

            if value.strip():
                return value
    except Exception:
        pass

    return default
# END COMMERCIAL ROUTING EDITABLE UI CONFIG


def _secret_file_value(key: str) -> str:
    try:
        with open("/etc/hiddify-panel/panel-secrets.env", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _telegram_webhook_domain_value() -> str:
    value = (hconfig(ConfigEnum.telegram_webhook_domain) or "").strip()
    if value:
        return value
    env_val = (os.environ.get("HIDDIFY_TELEGRAM_WEBHOOK_DOMAIN", "") or os.environ.get("TELEGRAM_WEBHOOK_DOMAIN", "")).strip()
    if env_val:
        return env_val
    return _secret_file_value("HIDDIFY_TELEGRAM_WEBHOOK_DOMAIN")


def _support_url_value() -> str:
    value = (hconfig(ConfigEnum.support_url) or "").strip()
    if value:
        return value
    env_val = (os.environ.get("HIDDIFY_SUPPORT_URL", "") or "").strip()
    if env_val:
        return env_val
    return _secret_file_value("HIDDIFY_SUPPORT_URL")


TELEGRAM_UI_PRIMARY_PATH = "/opt/hiddify-manager/hiddify-panel/var/business-telegram-ui.json"


def _telegram_ui_config() -> dict:
    try:
        with open(TELEGRAM_UI_PRIMARY_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _telegram_ui_value(key: str, default: str = "") -> str:
    value = _telegram_ui_config().get(key, default)
    return "" if value is None else str(value)


def _write_telegram_ui_value(key: str, value: str) -> None:
    data = _telegram_ui_config()
    data[key] = value
    os.makedirs(os.path.dirname(TELEGRAM_UI_PRIMARY_PATH), exist_ok=True)
    tmp_path = TELEGRAM_UI_PRIMARY_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp_path, TELEGRAM_UI_PRIMARY_PATH)


def _telegram_registration_mode_value() -> str:
    value = (_telegram_ui_value("telegram_registration_mode", "") or _secret_file_value("HIDDIFY_TELEGRAM_REGISTRATION_MODE") or os.environ.get("HIDDIFY_TELEGRAM_REGISTRATION_MODE", "") or "admin_only").strip().lower()
    return value if value in {"auto", "admin_only"} else "admin_only"


def _normalize_multiline_text(value: str) -> str:
    return (value or "").replace("\r\n", "\n").replace("\r", "\n")


def _validate_telegram_proxy_url(form, field):
    value = (field.data or "").strip()
    if not value:
        return
    if not re.match(r"^(socks5h|socks5|http|https)://.+", value, flags=re.IGNORECASE):
        raise ValidationError("Proxy URL must start with socks5h://, socks5://, http://, or https://")


def _format_routing_apply_error(msg: str) -> str:
    text = (msg or "").strip()
    if not text:
        return "неизвестная ошибка"
    if "No such command 'commercial-routing-apply'" in text:
        return "на сервере не установлена команда commercial-routing-apply"
    if "invalid UUID" in text:
        return "в VLESS URI внешней ноды указан невалидный UUID"
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1][-300:] if lines else "неизвестная ошибка"


class BusinessSettingsForm(FlaskForm):
    telegram_bot_token = wtf.StringField(_("Токен Telegram бота"), validators=[wtf.validators.Optional(), wtf.validators.Regexp(r"^([0-9]{8,12}:[a-zA-Z0-9_-]{30,40})$", re.IGNORECASE, _("config.Invalid_telegram_bot_token"))], description=_("Токен из @BotFather для работы коммерческого Telegram-бота."), render_kw={"class": "ltr", "placeholder": "123456789:AA..."})
    telegram_webhook_domain = wtf.StringField(_("Домен Telegram webhook"), validators=[wtf.validators.Optional(), wtf.validators.Regexp(r"^([A-Za-z0-9.-]+\.[A-Za-z]{2,})$", re.IGNORECASE, _("config.Invalid_domain"))], description=_("Фиксированный домен для webhook. Если пусто - используется домен панели (полезно для стабильности webhook при нескольких direct-доменах)."), render_kw={"class": "ltr", "placeholder": "tgbot.example.com"})
    telegram_payment_provider_token = wtf.StringField(_("Токен YooKassa для Telegram Payments"), validators=[wtf.validators.Optional(), wtf.validators.Regexp(r"^([0-9]{5,}:[A-Za-z0-9_:-]+)$", re.IGNORECASE, _("Invalid YooKassa/Telegram provider token"))], description=_("Токен провайдера Telegram Payments для YooKassa, который выдаётся через BotFather."), render_kw={"class": "ltr"})
    support_url = wtf.StringField(_("Ссылка поддержки"), validators=[wtf.validators.Optional(), wtf.validators.Regexp(r"^(https?://|tg://|mailto:|tel:).+", re.IGNORECASE, _("Invalid support URL"))], description=_("Ссылка поддержки для пользователя, которая есть у него (например в меню или кнопке обращения к администратору)."), render_kw={"class": "ltr"})
    telegram_registration_mode = wtf.SelectField(_("Регистрация пользователей в Telegram-боте"), choices=[("auto", "Автоматическая"), ("admin_only", "Ручная")], validate_choice=False, description=_("Автоматическая - бот сам регистрирует новых пользователей. Ручная - только после действий администратора."))
    telegram_instruction_button_text = wtf.StringField(_("Текст кнопки инструкции"), validators=[wtf.validators.Optional(), wtf.validators.Length(max=64)], description=_("Текст reply-кнопки, которая отправляет отдельное сообщение с инструкцией."))
    telegram_welcome_message = wtf.TextAreaField(_("Приветственное сообщение"), validators=[wtf.validators.Optional(), wtf.validators.Length(max=4000)], description=_("Первое сообщение для нового пользователя Telegram при регистрации и выдаче ссылки на подписку. Поддерживается HTML и ссылки."), render_kw={"rows": 6})
    telegram_instruction_message = wtf.TextAreaField(_("Инструкция"), validators=[wtf.validators.Optional(), wtf.validators.Length(max=4000)], description=_("Отдельное сообщение, которое отправляется по кнопке Инструкция. Если поле пустое, используется приветственное сообщение."), render_kw={"rows": 6})
    telegram_subscription_expiry_reminder_days = wtf.StringField(_("Дни до напоминания о продлении"), validators=[wtf.validators.Optional(), wtf.validators.Length(max=64)], description=_("Список через запятую, например 2,1. Бот напомнит за столько дней до окончания подписки."))
    telegram_subscription_expiry_reminder_message = wtf.TextAreaField(_("Текст напоминания о продлении"), validators=[wtf.validators.Optional(), wtf.validators.Length(max=4000)], description=_("Текст автоматического напоминания в Telegram. Доступен плейсхолдер {days_left}."), render_kw={"rows": 5})

    telegram_api_proxy_enable = wtf.BooleanField(_("Включить прокси Telegram API"))
    telegram_api_proxy_url = wtf.StringField(
        _("Прокси Telegram API"),
        validators=[wtf.validators.Optional(), _validate_telegram_proxy_url],
        description=_("URL прокси для исходящих запросов Telegram Bot API. Не влияет на входящий webhook и не использует systemd HTTP_PROXY/HTTPS_PROXY."),
        render_kw={"class": "ltr", "placeholder": "socks5h://127.0.0.1:20808"},
    )
    commercial_routing_enable = wtf.BooleanField("Включить коммерческую маршрутизацию")
    commercial_router_host = wtf.StringField(_("Хост router-core"), validators=[wtf.validators.Optional(), wtf.validators.Length(max=255)])
    commercial_router_port = wtf.StringField(_("Порт router-core"), validators=[wtf.validators.Optional(), wtf.validators.Length(max=8)])
    commercial_router_protocol = wtf.SelectField(_("Протокол router-core"), choices=[("socks5", "socks5")], validate_choice=False)
    commercial_apply_to_xray = wtf.BooleanField(_("Применять к Xray"))
    commercial_apply_to_singbox = wtf.BooleanField(_("Применять к sing-box"))
    commercial_domestic_policy = wtf.SelectField("Политика для внутреннего трафика", choices=[("keep_hiddify", "Оставить как у Hiddify"), ("send_to_router", "Передать в router-core"), ("direct_ru", "Оставить на текущей ноде"), ("block", "Блокировать")], validate_choice=False)
    commercial_udp443_policy = wtf.SelectField("Политика UDP/443", choices=[("keep_block", "Блокировать"), ("allow_to_router", "Передавать в router-core")], validate_choice=False)
    commercial_legacy_geosite_to_router = wtf.BooleanField(_("Направлять legacy geosite Hiddify в router-core"))
    commercial_drop_bittorrent = wtf.BooleanField(_("Блокировать BitTorrent-трафик"))

    commercial_ru_domain_suffixes = wtf.StringField("Встроенные суффиксы текущей ноды")
    commercial_ru_geoip_enabled = wtf.BooleanField("Включить geoip:ru для трафика текущей страны")
    commercial_default_global_policy = wtf.SelectField("Куда отправлять остальной трафик", choices=[("to_de", "Внешняя нода / Upstream")], validate_choice=False)
    commercial_router_core_type = wtf.SelectField("Движок маршрутизации", choices=[("xray", "Xray")], validate_choice=False)
    commercial_router_probe_url = wtf.SelectField("URL для проверки нод", choices=[
        ("https://1.1.1.1/", "https://1.1.1.1/ (Cloudflare)"),
        ("https://captive.apple.com/", "https://captive.apple.com/ (Apple)"),
        ("https://www.google.com/generate_204", "https://www.google.com/generate_204 (Google)"),
    ], validate_choice=False)
    commercial_router_probe_interval = wtf.SelectField("Интервал проверки нод", choices=[
        ("30s", "Каждые 30 секунд"),
        ("1m",  "Каждую минуту"),
        ("3m",  "Каждые 3 минуты"),
        ("5m",  "Каждые 5 минут"),
    ], validate_choice=False)
    commercial_router_probe_tolerance = wtf.StringField("Допустимое отклонение (мс)", validators=[wtf.validators.Optional()])


    # BEGIN COMMERCIAL ROUTING EDITABLE UI FIELDS
    commercial_blocked_domains = wtf.TextAreaField(
        _("Заблокированные чувствительные домены"),
        validators=[wtf.validators.Optional()],
        description="Домены для блокировки через blackhole. Один домен на строку. Можно писать gosuslugi.ru или domain:gosuslugi.ru.",
        render_kw={"rows": 6},
    )
    commercial_direct_dns_servers = wtf.TextAreaField(
        _("Direct DNS серверы"),
        validators=[wtf.validators.Optional()],
        description="DNS для direct-маршрута текущей ноды. Один IP на строку.",
        render_kw={"rows": 4},
    )
    commercial_proxy_dns_servers = wtf.TextAreaField(
        _("Proxy / Global DNS серверы"),
        validators=[wtf.validators.Optional()],
        description="DNS для global/to-de маршрута. Один IP на строку.",
        render_kw={"rows": 4},
    )
    # END COMMERCIAL ROUTING EDITABLE UI FIELDS

    custom_ru_rules_bulk = wtf.TextAreaField("Custom current-node rules bulk import", render_kw={"rows": 6})
    test_route_input = wtf.StringField("Test route input")
    submit = wtf.SubmitField(_("Сохранить"))


class BusinessAdmin(FlaskView):
    route_base = "/business-admin"
    decorators = [login_required(roles={Role.super_admin})]

    def _default_section(self):
        return "telegram"

    @staticmethod
    def _routing_available(hconfigs=None):
        return capabilities.routing_enabled(get_hconfigs() if hconfigs is None else hconfigs)

    def _active_section(self, hconfigs=None):
        default = self._default_section()
        section = (request.args.get("section") or request.form.get("section") or default).strip().lower()
        if section == "routing" and not self._routing_available(hconfigs):
            return default
        return section if section in {"telegram", "yookassa", "routing"} else default

    @staticmethod
    def _routing_summary(preview):
        preview = preview or {}
        builtin_suffixes = [str(item).strip() for item in (preview.get("builtin_ru_suffixes") or []) if str(item).strip()]
        return {
            "apply_notice": str(preview.get("apply_notice") or "").strip(),
            "apply_required": bool(preview.get("apply_required")),
            "builtin_ru_suffixes": builtin_suffixes,
            "custom_rules_total": int(preview.get("custom_rules_total") or 0),
            "geoip_enabled": bool(preview.get("geoip_enabled")),
            "layer1_enabled": bool(preview.get("layer1_enabled")),
            "router_core_type": str(preview.get("router_core_type") or "xray"),
            "router_target": str(preview.get("router_target") or "/etc/xray-router/config.json"),
        }

    def _render_settings(self, form, commercial_routing_notice=None, test_result=None):
        hconfigs = get_hconfigs()
        active_section = self._active_section(hconfigs)
        custom_rules = []
        preview = {}
        if active_section == "routing":
            _, commercial_routing = _routing_modules()
            if commercial_routing:
                custom_rules = commercial_routing.load_enabled_custom_rules()
                preview = commercial_routing.build_preview(hconfigs, custom_rules)
        routing_available = self._routing_available(hconfigs)
        return render_template(
            "business-settings.html",
            form=form,
            custom_rules=custom_rules,
            commercial_routing_preview=preview,
            commercial_routing_summary=self._routing_summary(preview),
            test_result=test_result,
            commercial_routing_notice=commercial_routing_notice,
            active_section=active_section,
            routing_available=routing_available,
            routing_section_url=f"{request.path}?{urlencode({'section': 'routing'})}",
            telegram_section_url=f"{request.path}?{urlencode({'section': 'telegram'})}",
            yookassa_section_url=f"{request.path}?{urlencode({'section': 'yookassa'})}",
            page_title={"telegram": "Бизнес: Telegram", "yookassa": "Бизнес: YooKassa", "routing": "Маршрутизация"}.get(active_section, "Бизнес"),
            show_section_tabs=False,
        )

    def _build_form(self):
        form = BusinessSettingsForm(
            telegram_bot_token=telegram_bot_token(),
            telegram_webhook_domain=_telegram_webhook_domain_value() or "",
            telegram_payment_provider_token=telegram_payment_provider_token(),
            support_url=_support_url_value() or "",
            telegram_registration_mode=_telegram_registration_mode_value(),
            telegram_instruction_button_text=hconfig(ConfigEnum.telegram_instruction_button_text) or "Инструкция",
            telegram_welcome_message=hconfig(ConfigEnum.telegram_welcome_message) or "",
            telegram_instruction_message=hconfig(ConfigEnum.telegram_instruction_message) or "",
            telegram_subscription_expiry_reminder_days=hconfig(ConfigEnum.telegram_subscription_expiry_reminder_days) or "2,1",
            telegram_subscription_expiry_reminder_message=hconfig(ConfigEnum.telegram_subscription_expiry_reminder_message) or "У вас заканчивается подписка через {days_left} дн. Не забудьте продлить тариф.",
            telegram_api_proxy_enable=bool(hconfig(ConfigEnum.telegram_api_proxy_enable)),
            telegram_api_proxy_url=hconfig(ConfigEnum.telegram_api_proxy_url) or "",
            commercial_routing_enable=bool(hconfig(ConfigEnum.commercial_routing_enable)),
            commercial_router_host=hconfig(ConfigEnum.commercial_router_host) or "127.0.0.1",
            commercial_router_port=hconfig(ConfigEnum.commercial_router_port) or "20808",
            commercial_router_protocol=hconfig(ConfigEnum.commercial_router_protocol) or "socks5",
            commercial_apply_to_xray=bool(hconfig(ConfigEnum.commercial_apply_to_xray)),
            commercial_apply_to_singbox=bool(hconfig(ConfigEnum.commercial_apply_to_singbox)),
            commercial_domestic_policy=hconfig(ConfigEnum.commercial_domestic_policy) or "keep_hiddify",
            commercial_udp443_policy=hconfig(ConfigEnum.commercial_udp443_policy) or "keep_block",
            commercial_legacy_geosite_to_router=bool(hconfig(ConfigEnum.commercial_legacy_geosite_to_router)) if hconfig(ConfigEnum.commercial_legacy_geosite_to_router) is not None else True,
            commercial_drop_bittorrent=bool(hconfig(ConfigEnum.commercial_drop_bittorrent)) if hconfig(ConfigEnum.commercial_drop_bittorrent) is not None else True,
            commercial_ru_domain_suffixes=hconfig(ConfigEnum.commercial_ru_domain_suffixes) or ".ru,.su,.xn--p1ai",
            commercial_ru_geoip_enabled=bool(hconfig(ConfigEnum.commercial_ru_geoip_enabled)),
            commercial_default_global_policy=hconfig(ConfigEnum.commercial_default_global_policy) or "to_de",
            commercial_router_core_type=hconfig(ConfigEnum.commercial_router_core_type) or "xray",
            commercial_router_probe_url=hconfig(ConfigEnum.commercial_router_probe_url) or "https://1.1.1.1/",
            commercial_router_probe_interval=hconfig(ConfigEnum.commercial_router_probe_interval) or "1m",
            commercial_router_probe_tolerance=hconfig(ConfigEnum.commercial_router_probe_tolerance) or "0",
            commercial_blocked_domains=_commercial_routing_config_text(COMMERCIAL_ROUTING_BLOCKED_DOMAINS_KEY, DEFAULT_COMMERCIAL_ROUTING_BLOCKED_DOMAINS),
            commercial_direct_dns_servers=_commercial_routing_config_text(COMMERCIAL_ROUTING_DIRECT_DNS_KEY, DEFAULT_COMMERCIAL_ROUTING_DIRECT_DNS),
            commercial_proxy_dns_servers=_commercial_routing_config_text(COMMERCIAL_ROUTING_PROXY_DNS_KEY, DEFAULT_COMMERCIAL_ROUTING_PROXY_DNS),
        )
        if self._active_section(get_hconfigs()) == "routing":
            _, commercial_routing = _routing_modules()
            if commercial_routing:
                form.custom_ru_rules_bulk.data = commercial_routing.custom_rules_to_bulk_text(commercial_routing.load_enabled_custom_rules())
        return form

    def index(self):
        form = self._build_form()
        test_result = None
        if request.args.get("test_route"):
            _, commercial_routing = _routing_modules()
            if commercial_routing:
                test_result = commercial_routing.simulate_route_match(request.args.get("test_route"), get_hconfigs(), commercial_routing.load_enabled_custom_rules())
        return self._render_settings(form, commercial_routing_notice=None, test_result=test_result)

    def post(self):
        form = BusinessSettingsForm()
        old_configs = get_hconfigs()
        if not form.validate_on_submit():
            hutils.flask.flash(_("config.validation-error"), "danger")
            return self._render_settings(form, commercial_routing_notice=None, test_result=None)

        if (form.commercial_router_port.data or "").strip():
            try:
                port = int((form.commercial_router_port.data or "").strip())
                if not (1 <= port <= 65535):
                    raise ValueError
            except Exception:
                hutils.flask.flash("Некорректный порт router-core.", "danger")
                return self._render_settings(form, commercial_routing_notice=None, test_result=None)

        if self._active_section(old_configs) == "routing":
            if not self._routing_available(old_configs):
                hutils.flask.flash("Модуль «Маршрутизация» не установлен.", "danger")
                return self._render_settings(form, commercial_routing_notice=None, test_result=None)
            upstream_error = _validate_routing_upstream_form(form)
            if upstream_error:
                hutils.flask.flash(upstream_error, "danger")
                return self._render_settings(form, commercial_routing_notice=None, test_result=None)

        telegram_registration_mode = (form.telegram_registration_mode.data or "admin_only").strip().lower()
        if telegram_registration_mode not in {"auto", "admin_only"}:
            telegram_registration_mode = "admin_only"

        submitted = {
            ConfigEnum.telegram_bot_token: (form.telegram_bot_token.data or "").strip(),
            ConfigEnum.telegram_webhook_domain: (form.telegram_webhook_domain.data or "").strip().lower(),
            ConfigEnum.telegram_payment_provider_token: (form.telegram_payment_provider_token.data or "").strip(),
            ConfigEnum.support_url: (form.support_url.data or "").strip(),
            ConfigEnum.telegram_instruction_button_text: (form.telegram_instruction_button_text.data or "").strip() or "Инструкция",
            ConfigEnum.telegram_welcome_message: _normalize_multiline_text(form.telegram_welcome_message.data or ""),
            ConfigEnum.telegram_instruction_message: _normalize_multiline_text(form.telegram_instruction_message.data or ""),
            ConfigEnum.telegram_subscription_expiry_reminder_days: (form.telegram_subscription_expiry_reminder_days.data or "").strip() or "2,1",
            ConfigEnum.telegram_subscription_expiry_reminder_message: _normalize_multiline_text(form.telegram_subscription_expiry_reminder_message.data or ""),
            ConfigEnum.telegram_api_proxy_enable: bool(form.telegram_api_proxy_enable.data),
            ConfigEnum.telegram_api_proxy_url: (form.telegram_api_proxy_url.data or "").strip(),
            ConfigEnum.commercial_routing_enable: bool(form.commercial_routing_enable.data),
            ConfigEnum.commercial_router_host: (form.commercial_router_host.data or "").strip() or "127.0.0.1",
            ConfigEnum.commercial_router_port: (form.commercial_router_port.data or "").strip() or "20808",
            ConfigEnum.commercial_router_protocol: (form.commercial_router_protocol.data or "").strip() or "socks5",
            ConfigEnum.commercial_apply_to_xray: bool(form.commercial_apply_to_xray.data),
            ConfigEnum.commercial_apply_to_singbox: bool(form.commercial_apply_to_singbox.data),
            ConfigEnum.commercial_domestic_policy: (form.commercial_domestic_policy.data or "keep_hiddify").strip(),
            ConfigEnum.commercial_udp443_policy: (form.commercial_udp443_policy.data or "keep_block").strip(),
            ConfigEnum.commercial_legacy_geosite_to_router: bool(form.commercial_legacy_geosite_to_router.data),
            ConfigEnum.commercial_drop_bittorrent: bool(form.commercial_drop_bittorrent.data),
            ConfigEnum.commercial_ru_domain_suffixes: (form.commercial_ru_domain_suffixes.data or "").strip() or ".ru,.su,.xn--p1ai",
            ConfigEnum.commercial_ru_geoip_enabled: bool(form.commercial_ru_geoip_enabled.data),
            ConfigEnum.commercial_default_global_policy: (form.commercial_default_global_policy.data or "to_de").strip(),
            ConfigEnum.commercial_router_core_type: (form.commercial_router_core_type.data or "xray").strip(),
            ConfigEnum.commercial_router_probe_url: (form.commercial_router_probe_url.data or "https://1.1.1.1/").strip(),
            ConfigEnum.commercial_router_probe_interval: (form.commercial_router_probe_interval.data or "1m").strip(),
            ConfigEnum.commercial_router_probe_tolerance: str(max(0, int((form.commercial_router_probe_tolerance.data or "0").strip() or "0"))),
            COMMERCIAL_ROUTING_BLOCKED_DOMAINS_KEY: (form.commercial_blocked_domains.data or "").strip(),
            COMMERCIAL_ROUTING_DIRECT_DNS_KEY: (form.commercial_direct_dns_servers.data or "").strip(),
            COMMERCIAL_ROUTING_PROXY_DNS_KEY: (form.commercial_proxy_dns_servers.data or "").strip(),
        }
        active_section = self._active_section(old_configs)
        telegram_keys = {
            ConfigEnum.telegram_bot_token,
            ConfigEnum.telegram_webhook_domain,
            ConfigEnum.support_url,
            ConfigEnum.telegram_instruction_button_text,
            ConfigEnum.telegram_welcome_message,
            ConfigEnum.telegram_instruction_message,
            ConfigEnum.telegram_subscription_expiry_reminder_days,
            ConfigEnum.telegram_subscription_expiry_reminder_message,
            ConfigEnum.telegram_api_proxy_enable,
            ConfigEnum.telegram_api_proxy_url,
        }
        yookassa_keys = {
            ConfigEnum.telegram_payment_provider_token,
        }
        routing_keys = {
            ConfigEnum.commercial_routing_enable,
            ConfigEnum.commercial_router_host,
            ConfigEnum.commercial_router_port,
            ConfigEnum.commercial_router_protocol,
            ConfigEnum.commercial_apply_to_xray,
            ConfigEnum.commercial_apply_to_singbox,
            ConfigEnum.commercial_domestic_policy,
            ConfigEnum.commercial_udp443_policy,
            ConfigEnum.commercial_legacy_geosite_to_router,
            ConfigEnum.commercial_drop_bittorrent,
            ConfigEnum.commercial_ru_domain_suffixes,
            ConfigEnum.commercial_ru_geoip_enabled,
            ConfigEnum.commercial_default_global_policy,
            ConfigEnum.commercial_router_core_type,
            ConfigEnum.commercial_router_probe_url,
            ConfigEnum.commercial_router_probe_interval,
            ConfigEnum.commercial_router_probe_tolerance,
            COMMERCIAL_ROUTING_BLOCKED_DOMAINS_KEY,
            COMMERCIAL_ROUTING_DIRECT_DNS_KEY,
            COMMERCIAL_ROUTING_PROXY_DNS_KEY,
        }

        if active_section == "telegram":
            for k in list(routing_keys):
                submitted.pop(k, None)
            for k in list(yookassa_keys):
                submitted.pop(k, None)
        elif active_section == "yookassa":
            for k in list(telegram_keys):
                submitted.pop(k, None)
            for k in list(routing_keys):
                submitted.pop(k, None)
        elif active_section == "routing":
            for k in list(telegram_keys):
                submitted.pop(k, None)
            for k in list(yookassa_keys):
                submitted.pop(k, None)


        # BEGIN HIDDIFY ROUTING UI JSON SAVE
        routing_ui_submitted = {}
        for _routing_ui_key in (
            COMMERCIAL_ROUTING_BLOCKED_DOMAINS_KEY,
            COMMERCIAL_ROUTING_DIRECT_DNS_KEY,
            COMMERCIAL_ROUTING_PROXY_DNS_KEY,
        ):
            if _routing_ui_key in submitted:
                routing_ui_submitted[_routing_ui_key] = submitted.pop(_routing_ui_key)

        for key, value in submitted.items():
            if old_configs.get(key) != value:
                set_hconfig(key, value, commit=False)

        if active_section == "routing" and routing_ui_submitted:
            from pathlib import Path
            import json

            # Stage A1 keeps panel behavior unchanged: write primary JSON first,
            # then mirror the same payload to the legacy /etc path used by runtime apply.
            primary_routing_ui_path = Path(COMMERCIAL_ROUTING_UI_PRIMARY_PATH)
            legacy_routing_ui_path = Path(COMMERCIAL_ROUTING_UI_LEGACY_PATH)

            primary_routing_ui_path.parent.mkdir(parents=True, exist_ok=True)

            current_routing_ui = {}
            for _candidate_path in (primary_routing_ui_path, legacy_routing_ui_path):
                try:
                    if _candidate_path.exists():
                        _loaded = json.loads(_candidate_path.read_text(encoding="utf-8"))
                        if isinstance(_loaded, dict):
                            current_routing_ui = _loaded
                            break
                except Exception:
                    pass

            for _routing_ui_key, _routing_ui_value in routing_ui_submitted.items():
                current_routing_ui[str(_routing_ui_key)] = "" if _routing_ui_value is None else str(_routing_ui_value)

            _serialized_routing_ui = json.dumps(current_routing_ui, indent=2, ensure_ascii=False) + "\n"

            primary_routing_ui_path.write_text(_serialized_routing_ui, encoding="utf-8")

            try:
                legacy_routing_ui_path.parent.mkdir(parents=True, exist_ok=True)
                legacy_routing_ui_path.write_text(_serialized_routing_ui, encoding="utf-8")
            except Exception:
                pass
        # END HIDDIFY ROUTING UI JSON SAVE

        commercial_routing_notice = None
        if active_section == "routing":
            CommercialRoutingCustomRule, commercial_routing = _require_routing_modules()
            bulk_text = (form.custom_ru_rules_bulk.data or "").strip()
            if bulk_text:
                rules, errors = commercial_routing.parse_bulk_rules(bulk_text)
            else:
                rules, errors = [], []

            if errors:
                for err in errors:
                    hutils.flask.flash(f"Ошибка в строке {err.line_no} списка правил: {err.error}", "danger")
                return self._render_settings(form, commercial_routing_notice=None, test_result=None)

            unique_rules = {}
            for rule in rules:
                unique_rules[(rule["rule_type"], rule["normalized_value"])] = rule

            CommercialRoutingCustomRule.query.delete()
            for rule in unique_rules.values():
                db.session.add(CommercialRoutingCustomRule(**rule))

            db.session.commit()

            try:
                import subprocess
                apply_proc = subprocess.run(
                    ["sudo", "-n", "/opt/hiddify-manager/common/commander.py", "commercial-routing-apply"],
                    capture_output=True,
                    text=True,
                    timeout=90,
                )
                if apply_proc.returncode == 0:
                    commercial_routing_notice = "Конфигурация router-core применена, xray-router перезапущен."
                    hutils.flask.flash(commercial_routing_notice, "success")
                else:
                    msg = ((apply_proc.stderr or "") + "\n" + (apply_proc.stdout or "")).strip()
                    commercial_routing_notice = "Настройки сохранены, но router-core config не применён: " + _format_routing_apply_error(msg)
                    hutils.flask.flash(commercial_routing_notice, "danger")
            except Exception as exc:
                commercial_routing_notice = "Настройки сохранены, но router-core config не применён: " + _format_routing_apply_error(str(exc))
                hutils.flask.flash(commercial_routing_notice, "danger")
        else:
            db.session.commit()

        _write_telegram_ui_value("telegram_registration_mode", telegram_registration_mode)
        os.environ["HIDDIFY_TELEGRAM_REGISTRATION_MODE"] = telegram_registration_mode

        telegram_related_keys = {
            ConfigEnum.telegram_bot_token,
            ConfigEnum.telegram_webhook_domain,
            ConfigEnum.telegram_payment_provider_token,
        }
        if any(old_configs.get(k) != submitted.get(k) for k in telegram_related_keys):
            from hiddifypanel.panel.commercial.telegrambot import register_bot
            register_bot(set_hook=True)

        reset_action = hiddify.check_need_reset(old_configs)
        hutils.flask.flash(_("config.configs_have_been_updated"), "success")
        default_notice = {
            "telegram": "Настройки Telegram сохранены.",
            "yookassa": "Настройки YooKassa сохранены.",
            "routing": "Настройки маршрутизации сохранены.",
        }.get(active_section, "Настройки сохранены.")
        notice = commercial_routing_notice or default_notice
        if reset_action:
            return reset_action
        return redirect(f"{request.path}?{urlencode({'section': active_section})}")


class RoutingAdmin(BusinessAdmin):
    route_base = "/routing-admin"

    def _default_section(self):
        return "routing"
