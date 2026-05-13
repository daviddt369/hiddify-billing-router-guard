import os

from hiddifypanel.models import ConfigEnum, get_hconfigs, hconfig

_INSTALL_DIR = os.environ.get("HIDDIFY_INSTALL_DIR", "/opt/hiddify-manager")
_ROUTING_MANIFEST = os.path.join(_INSTALL_DIR, "routing-addon.manifest")
_ANTI_SHARE_MANIFEST = os.path.join(_INSTALL_DIR, "anti-share-addon.manifest")
_BUSINESS_MANIFEST = os.path.join(_INSTALL_DIR, "business-addon.manifest")


def _bool_value(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _config_member(name: str):
    return getattr(ConfigEnum, name, None)


def _hconfig_value(name: str):
    key = _config_member(name)
    if key is None:
        return None
    try:
        return hconfig(key)
    except Exception:
        return None


def business_enabled(hconfigs=None) -> bool:
    if hconfigs is None:
        hconfigs = get_hconfigs()
    business_key = _config_member("business_enabled")
    if business_key is not None and _bool_value(hconfigs.get(business_key)):
        return True
    return os.path.exists(_BUSINESS_MANIFEST)


def routing_enabled(hconfigs=None) -> bool:
    if hconfigs is None:
        hconfigs = get_hconfigs()
    marker_key = _config_member("commercial_routing_installed")
    marker = hconfigs.get(marker_key) if marker_key is not None else None
    if marker is not None:
        return _bool_value(marker)
    if os.path.exists(_ROUTING_MANIFEST):
        return True
    return bool(
        hconfigs.get(_config_member("commercial_routing_enable"))
        or hconfigs.get(_config_member("commercial_router_core_type"))
        or hconfigs.get(_config_member("commercial_router_host"))
        or hconfigs.get(_config_member("commercial_router_port"))
    )


def antishare_enabled(hconfigs=None) -> bool:
    if hconfigs is None:
        hconfigs = get_hconfigs()
    marker_key = _config_member("commercial_antishare_installed")
    marker = hconfigs.get(marker_key) if marker_key is not None else None
    if marker is not None:
        return _bool_value(marker)
    return os.path.exists(_ANTI_SHARE_MANIFEST)


def telegram_proxy_enabled() -> bool:
    return _bool_value(_hconfig_value("telegram_api_proxy_enable"))
