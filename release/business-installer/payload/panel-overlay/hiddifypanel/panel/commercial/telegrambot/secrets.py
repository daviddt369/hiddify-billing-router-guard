import os

from hiddifypanel.models import ConfigEnum, hconfig

_SECRETS_FILE = "/etc/hiddify-panel/panel-secrets.env"


def _file_value(key: str) -> str:
    try:
        with open(_SECRETS_FILE, "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _env_or_file(primary: str, legacy: str = "") -> str:
    value = (os.environ.get(primary, "") or "").strip()
    if value:
        return value
    if legacy:
        value = (os.environ.get(legacy, "") or "").strip()
        if value:
            return value
    return _file_value(primary)


def _hconfig_value(key: ConfigEnum) -> str:
    try:
        return (hconfig(key) or "").strip()
    except RuntimeError:
        return ""


def _config_value(name: str) -> str:
    key = getattr(ConfigEnum, name, None)
    if key is None:
        return ""
    return _hconfig_value(key)


def telegram_bot_token() -> str:
    # UI/DB value wins when an app context is available; env/file remains fallback.
    return (
        _config_value("telegram_bot_token")
        or _env_or_file("HIDDIFY_TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_TOKEN")
    ).strip()


def telegram_payment_provider_token() -> str:
    # UI/DB value wins when an app context is available; env/file remains fallback.
    return (
        _config_value("telegram_payment_provider_token")
        or _env_or_file(
            "HIDDIFY_TELEGRAM_PAYMENT_PROVIDER_TOKEN",
            "TELEGRAM_PAYMENT_PROVIDER_TOKEN",
        )
    ).strip()
