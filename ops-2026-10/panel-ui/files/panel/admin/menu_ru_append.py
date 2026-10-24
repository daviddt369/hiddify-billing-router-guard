

# ---------------------------------------------------------------------------------------------------------------- our own grouped menu (CP-16, text proposal)
def _link(label: str, endpoint: str, icon: str, **kw):
    """an item for an endpoint that may not exist on this panel (a layer that is not installed): None instead of an error"""
    try:
        return _item(label, hurl_for(endpoint, **kw), icon)
    except Exception:
        return None


def build_admin_v2_menu_ru() -> list[dict]:
    """Groups and Russian names for the whole panel: the old pages that exist here, in a RemnaWave-like order. Only text: the pages themselves are the old ones."""
    me = g.account.mode
    groups: list[dict] = []

    def group(label, items):
        items = [i for i in items if i]
        if items:
            groups.append({"label": label, "items": items})

    group("Управление", [
        _link("Пользователи", "flask.user.index_view", "pi pi-fw pi-users"),
        _link("Администраторы", "flask.adminuser.index_view", "pi pi-fw pi-user-edit"),
        _link("Домены", "flask.domain.index_view", "pi pi-fw pi-link") if me != "agent" else None,
        _link("Протоколы", "admin.ProxyAdmin:index", "pi pi-fw pi-sitemap") if me != "agent" else None,
    ])
    if me == "super_admin":
        group("Маршрутизация", [
            _link("Обзор маршрутизации", "admin.RoutingAdmin:index", "pi pi-fw pi-compass"),
            _link("Внешние ноды", "admin.RoutingAdmin:upstream_list", "pi pi-fw pi-server"),
            _link("Локации", "admin.RoutingAdmin:location_list", "pi pi-fw pi-globe"),
            _link("Источники правил", "admin.RoutingAdmin:rule_source_list", "pi pi-fw pi-list"),
        ])
        group("Подписка", [
            _link("Happ / INCY", "admin.RoutingAdmin:happ_page", "pi pi-fw pi-mobile"),
        ])
        group("Безопасность", [
            _link("Анти-шаринг", "admin.AntiShareAdmin:index", "pi pi-fw pi-shield"),
        ])
        group("Бизнес", [
            _link("Тарифы", "flask.plans.index_view", "pi pi-fw pi-credit-card"),
            _link("Telegram-бот", "admin.BusinessAdmin:index", "pi pi-fw pi-send", section="telegram"),
            _link("YooKassa", "admin.BusinessAdmin:index", "pi pi-fw pi-wallet", section="yookassa"),
        ])
        system = [
            _link("Настройки", "admin.SettingAdmin:index", "pi pi-fw pi-cog"),
            _link("Резервные копии", "admin.Backup:index", "pi pi-fw pi-save"),
            _link("Статус сервера", "admin.Actions:status", "pi pi-fw pi-chart-line"),
            _link("Журналы", "admin.Actions:viewlogs", "pi pi-fw pi-inbox"),
        ]
        try:
            system.append(_item("Статистика прокси", get_proxy_stats_url(), "pi pi-fw pi-chart-bar"))
        except Exception:
            pass
        try:
            system.append(_item("API", hurl_for("openapi.docs"), "pi pi-fw pi-code"))
        except Exception:
            pass
        for label, endpoint, icon in (("Применить конфигурацию", "admin.Actions:apply_configs", "pi pi-fw pi-bolt"), ("Обновить панель", "admin.Actions:update", "pi pi-fw pi-upload"),
                                      ("Переустановить", "admin.Actions:reinstall", "pi pi-fw pi-refresh"), ("Перезагрузить сервер", "admin.Actions:reset", "pi pi-fw pi-power-off")):
            try:
                system.append(_post_action(label, hurl_for(endpoint), icon))
            except Exception:
                pass
        group("Система", system)
    group("Помощь", [
        _item("Помощь", _wiki_support_url(), "pi pi-fw pi-question-circle", target="_blank"),
        _item("Ошибки и предложения", hutils.github_issue.generate_github_issue_link_for_admin_sidebar(), "pi pi-fw pi-exclamation-circle", target="_blank"),
    ])
    return groups


_PI_TO_FA = {
    "pi-users": "fa-solid fa-users", "pi-user-edit": "fa-solid fa-user-gear", "pi-link": "fa-solid fa-link", "pi-sitemap": "fa-solid fa-sitemap", "pi-compass": "fa-solid fa-compass",
    "pi-server": "fa-solid fa-server", "pi-globe": "fa-solid fa-globe", "pi-list": "fa-solid fa-list", "pi-mobile": "fa-solid fa-mobile-screen", "pi-shield": "fa-solid fa-shield-halved",
    "pi-credit-card": "fa-solid fa-credit-card", "pi-send": "fa-brands fa-telegram", "pi-wallet": "fa-solid fa-wallet", "pi-cog": "fa-solid fa-gear", "pi-save": "fa-solid fa-floppy-disk",
    "pi-chart-line": "fa-solid fa-signal", "pi-inbox": "fa-solid fa-inbox", "pi-chart-bar": "fa-solid fa-chart-column", "pi-code": "fa-solid fa-code", "pi-bolt": "fa-solid fa-bolt",
    "pi-upload": "fa-solid fa-arrow-up-from-bracket", "pi-refresh": "fa-solid fa-rotate", "pi-power-off": "fa-solid fa-power-off", "pi-question-circle": "fa-solid fa-circle-question",
    "pi-exclamation-circle": "fa-solid fa-bug", "pi-home": "fa-solid fa-house",
}


def _with_fa(groups: list[dict]) -> list[dict]:
    """the same groups for the old (Font Awesome) pages: every item also carries `fa`"""
    for grp in groups:
        for it in grp["items"]:
            pi = next((c for c in reversed(str(it.get("icon", "")).split()) if c.startswith("pi-") and c != "pi-fw"), "")
            it["fa"] = _PI_TO_FA.get(pi, "fa-solid fa-circle")
    return groups


def build_admin_v2_menu() -> list[dict]:
    try:
        return _with_fa(build_admin_v2_menu_ru())
    except Exception:                                   # never leave the panel without a menu
        return _build_admin_v2_menu_stock()


def admin_v2_layout_menu() -> dict:
    """for the old pages' sidebar (admin-layout.html): the same groups and the item that matches the current address best (exact, else the longest prefix)"""
    from urllib.parse import urlparse

    from flask import request
    groups = build_admin_v2_menu()

    def tail(p: str) -> str:                               # the first segment is the panel's secret path: the same page can be opened under another one
        parts = p.strip("/").split("/", 1)
        return parts[1].rstrip("/") if len(parts) > 1 else ""

    path = tail(request.path)
    best, best_len = None, -1
    for grp in groups:
        for it in grp["items"]:
            if it.get("method") == "post" or it.get("target") == "_blank":
                continue
            p = tail(urlparse(it["url"]).path)
            if p and (path == p or path.startswith(p + "/")) and len(p) > best_len:
                best, best_len = it["url"], len(p)
    return {"groups": groups, "active": best, "v2_url": hurl_for("admin.admin_v2")}
