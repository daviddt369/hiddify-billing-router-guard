"""Stand-only, idempotent synthetic baseline seed (NO real data).

Reproduces the *shape* of the production subscription (15 profile lines on 3 domain groups) on the stand DB:
  RU_GLOBAL      (direct domain)    : vless xhttp tls_h2 dl=h2, vless WS, trojan WS, vless httpupgrade,
                                      vless xhttp tls dl=h3, vless xhttp h3_quic dl=h3, TUIC, Hysteria2,
                                      NaiveTLS, NaiveQuic, MieruTCP
  RU_GLOBAL_TIME (reality domain)   : vless grpc reality
  RU_GLOBAL_GCDN (CDN domain)       : vless xhttp tls_h2 dl=h2, vless httpupgrade, vless xhttp h3_quic dl=h3
Deterministic paths/UUIDs so golden files are comparable across clean runs. Domains are *.stand.test.
"""
import datetime
import os
import sys
import uuid

import sqlalchemy
import hiddifypanel.panel.init_db as idb
idb.text = sqlalchemy.text  # latent bug workaround for empty DBs only (see stand_bootstrap_db.py)

from hiddifypanel import create_app

app = create_app(app_mode="cli")

# ---- deterministic synthetic constants (not secrets) -------------------------------------------
PATHS = {
    "proxy_path": "stPx0Main1234",
    "proxy_path_admin": "stAdm0Path12345",
    "proxy_path_client": "stCli0Path1234567890abcdef",
    "path_vless": "stVl0Path1",
    "path_trojan": "stTr0Path1",
    "path_vmess": "stVm0Path1",
    "path_ss": "stSs0Path1",
    "path_grpc": "stGr0Path1",
    "path_tcp": "stTc0Path1",
    "path_ws": "stWs0Path1",
    "path_httpupgrade": "stHu0Path1",
    "path_xhttp": "stXh0Path1",
}
USERS = [
    # name, uuid, kind
    ("u-active",  "11111111-1111-4111-8111-111111111111", "active"),
    ("u-tester",  "22222222-2222-4222-8222-222222222222", "active"),
    ("u-expired", "33333333-3333-4333-8333-333333333333", "expired"),
    ("u-overlimit", "44444444-4444-4444-8444-444444444444", "overlimit"),
    ("u-disabled", "55555555-5555-4555-8555-555555555555", "disabled"),
]
DOMAINS = [
    ("ru.stand.test",   "direct",              "RU_GLOBAL"),
    ("help.stand.test", "special_reality_grpc", "RU_GLOBAL_TIME"),
    ("gcdn.stand.test", "cdn",                "RU_GLOBAL_GCDN"),
]
ENABLED_PROXY_NAMES = {
    # direct domain
    ("vless", "tls_h2 xhttp direct vless dl=h2"),
    ("vless", "tls WS direct vless"),
    ("trojan", "tls WS direct trojan"),
    ("vless", "tls httpupgrade direct vless"),
    ("vless", "tls xhttp direct vless dl=h3"),
    ("vless", "h3_quic xhttp direct vless dl=h3"),
    ("tuic", "TUIC"), ("hysteria2", "Hysteria2"), ("naive", "NaiveTLS"), ("naive", "NaiveQuic"), ("mieru", "MieruTCP"),
    # reality domain
    ("vless", "reality grpc direct vless"),
    # cdn domain
    ("vless", "tls_h2 xhttp CDN vless dl=h2"),
    ("vless", "tls httpupgrade CDN vless"),
    ("vless", "h3_quic xhttp CDN vless dl=h3"),
}
BOOL_TOGGLES = {
    "ws_enable": True, "trojan_enable": True, "vless_enable": True, "vmess_enable": False,
    "wireguard_enable": False, "ssh_server_enable": False, "reality_enable": True, "grpc_enable": True,
    "httpupgrade_enable": True, "xhttp_enable": True, "tuic_enable": True, "hysteria_enable": True,
    "naive_enable": True, "mieru_enable": True, "quic_enable": True, "h2_enable": True,
    "first_setup": False, "tls_mixed_case": False,
}


def main():
    from hiddifypanel.database import db
    from hiddifypanel.models import (ConfigEnum, set_hconfig, hconfig, Domain, DomainType, Proxy, User, UserMode,
                                     BoolConfig, StrConfig)

    # 1) hconfig toggles + deterministic paths
    for k, v in BOOL_TOGGLES.items():
        try:
            set_hconfig(ConfigEnum[k], v, commit=False)
        except Exception as e:  # noqa
            print("WARN toggle", k, e)
    for k, v in PATHS.items():
        try:
            set_hconfig(ConfigEnum[k], v, commit=False)
        except Exception as e:  # noqa
            print("WARN path", k, e)
    db.session.commit()

    # 2) domains: replace the bootstrap defaults by the three synthetic groups
    want = {d for d, _, _ in DOMAINS}
    for dom in Domain.query.all():
        if dom.domain not in want:
            db.session.delete(dom)
    db.session.commit()
    for name, mode, alias in DOMAINS:
        d = Domain.query.filter(Domain.domain == name).first()
        if d is None:
            d = Domain(domain=name, mode=DomainType[mode], alias=alias, child_id=0)
            db.session.add(d)
        else:
            d.mode = DomainType[mode]
            d.alias = alias
        if mode.startswith("special_reality"):
            d.servernames = "help.stand.test"
    db.session.commit()

    # 3) proxy templates: enable exactly the prod-like subset
    n_on = 0
    for p in Proxy.query.all():
        on = (str(p.proto), p.name) in ENABLED_PROXY_NAMES
        p.enable = on
        n_on += int(on)
    db.session.commit()
    print("proxy templates enabled:", n_on, "(expected", len(ENABLED_PROXY_NAMES), ")")

    # 4) users
    today = datetime.date.today()
    for name, u, kind in USERS:
        usr = User.query.filter(User.uuid == u).first()
        if usr is None:
            usr = User(name=name, uuid=u, usage_limit_GB=100, package_days=30, mode=UserMode.no_reset, added_by=1)
            db.session.add(usr)
        usr.name = name
        usr.enable = kind != "disabled"
        usr.usage_limit_GB = 100
        usr.current_usage = 0
        usr.package_days = 30
        usr.start_date = today
        usr.max_ips = 5
        if kind == "expired":
            usr.package_days = 3
            usr.start_date = today - datetime.timedelta(days=40)
        if kind == "overlimit":
            usr.current_usage_GB = 101
    # the panel's admin flow expects a user with id=1 (it exists on prod): keep the bootstrap 'default' user but disabled
    usr = User.query.filter(User.name == "default").first() or User.query.filter(User.id == 1).first()
    if usr is None:
        usr = User(id=1, name="default", uuid="00000000-0000-4000-8000-000000000001", usage_limit_GB=1, package_days=1,
                   mode=UserMode.no_reset, added_by=1)
        db.session.add(usr)
    usr.name = "default"
    usr.enable = False
    usr.start_date = today - datetime.timedelta(days=400)
    usr.package_days = 1
    db.session.commit()
    print("users:", [(x.name, str(x.uuid), x.is_active) for x in User.query.order_by(User.id).all()])


with app.app_context():
    main()
