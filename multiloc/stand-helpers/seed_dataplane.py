"""Stand-only, idempotent: commercial-routing settings, synthetic upstream rows (the two stand exits) and RU-direct rules.

Upstreams (VLESS-XHTTP/TLS, 2 paths per exit like prod):  exit A = 127.0.0.190 (shim) , exit B = 127.0.0.230 (shim)
RU-direct rules: domain suffix ru-site.test, cidr 12.0.0.0/24  (+ thousands of filler rules via --filler N, optional)
"""
import json
import os
import sys

import sqlalchemy
import hiddifypanel.panel.init_db as idb
idb.text = sqlalchemy.text
from hiddifypanel import create_app

FILLER = 0
for a in sys.argv[1:]:
    if a.startswith("--filler="):
        FILLER = int(a.split("=", 1)[1])

app = create_app(app_mode="cli")

UUID_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
UUID_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def vless_uri(uuid, ip, port, path, sni):
    return (f"vless://{uuid}@{ip}:{port}?type=xhttp&security=tls&sni={sni}&alpn=h2&path=%2F{path}"
            f"&fp=chrome&encryption=none")


UPSTREAMS = [
    # name, label, priority, uri
    ("exit-a-1", "Exit A path 1", 1, vless_uri(UUID_A, "127.0.0.190", 9443, "xa1", "exit-a.stand.test")),
    ("exit-a-2", "Exit A path 2", 2, vless_uri(UUID_A, "127.0.0.190", 9444, "xa2", "exit-a.stand.test")),
    ("exit-b-1", "Exit B path 1", 3, vless_uri(UUID_B, "127.0.0.230", 9443, "xb1", "exit-b.stand.test")),
    ("exit-b-2", "Exit B path 2", 4, vless_uri(UUID_B, "127.0.0.230", 9444, "xb2", "exit-b.stand.test")),
]
RULES = [("domain_suffix", "ru-site.test"), ("cidr", "12.0.0.0/24")]


def main():
    from hiddifypanel.database import db
    from hiddifypanel.models import ConfigEnum, set_hconfig
    from hiddifypanel.models.commercial_routing_upstream import CommercialRoutingUpstream
    from hiddifypanel.models.commercial_routing_custom_rule import CommercialRoutingCustomRule
    from hiddifypanel.hutils import commercial_routing

    cfg = {
        ConfigEnum.commercial_routing_enable: True,
        ConfigEnum.commercial_router_host: "127.0.0.1",
        ConfigEnum.commercial_router_port: "20808",
        ConfigEnum.commercial_router_protocol: "socks5",
        ConfigEnum.commercial_apply_to_xray: True,
        ConfigEnum.commercial_domestic_policy: "send_to_router",   # what the installer seeds (prod default)
        ConfigEnum.commercial_router_probe_url: "http://probe.stand.test:18080/probe",
        ConfigEnum.commercial_router_probe_interval: "3s",
        ConfigEnum.commercial_router_probe_tolerance: "0",
        ConfigEnum.dns_server: "12.0.0.53",
    }
    for k, v in cfg.items():
        set_hconfig(k, v, commit=False)
    db.session.commit()

    for name, label, prio, uri in UPSTREAMS:
        up = CommercialRoutingUpstream.query.filter_by(name=name).first()
        if up is None:
            up = CommercialRoutingUpstream(name=name)
            db.session.add(up)
        up.label = label
        up.enabled = True
        up.priority = prio
        up.tunnel_type = "vless"
        up.vless_uri = uri
    db.session.commit()

    for rt, val in RULES:
        ok, norm, err = commercial_routing.validate_custom_rule(rt, val)
        assert ok, err
        if not CommercialRoutingCustomRule.query.filter_by(rule_type=rt, normalized_value=norm).first():
            db.session.add(CommercialRoutingCustomRule(rule_type=rt, value=val, normalized_value=norm,
                                                       outbound_policy="direct_ru", enabled=True, comment="stand"))
    # optional filler to mimic the production rule count (15k) for performance/ordering checks
    if FILLER:
        existing = CommercialRoutingCustomRule.query.filter(CommercialRoutingCustomRule.comment == "stand-filler").count()
        for i in range(existing, FILLER):
            if i % 2:
                rt, val = "domain_suffix", f"filler{i}.example-ru.test"
            else:
                rt, val = "cidr", f"100.{(i // 65536) % 64 + 64}.{(i // 256) % 256}.{i % 256}/32"
            ok, norm, err = commercial_routing.validate_custom_rule(rt, val)
            if ok:
                db.session.add(CommercialRoutingCustomRule(rule_type=rt, value=val, normalized_value=norm,
                                                           outbound_policy="direct_ru", enabled=True, comment="stand-filler"))
        db.session.commit()
    db.session.commit()

    # UI JSON read by the routing layers (DNS servers, blocked domains) - synthetic
    ui = {
        "commercial_direct_dns_servers": "12.0.0.53",
        "commercial_proxy_dns_servers": "14.0.0.53",
        "commercial_blocked_domains": "blocked.stand.test",
    }
    for p in ("/opt/hiddify-manager/hiddify-panel/var/commercial-routing-ui.json",):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            json.dump(ui, f, indent=2)
    print("upstreams:", [(u.id, u.name) for u in CommercialRoutingUpstream.query.order_by(CommercialRoutingUpstream.id).all()])
    print("custom rules:", CommercialRoutingCustomRule.query.count())


with app.app_context():
    main()
