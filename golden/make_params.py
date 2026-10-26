#!/usr/bin/env python3
"""Fill in the parameters file of a NEW node (the values that golden/overlay has as @@NAME@@ placeholders).
Asks a few questions in the terminal, or takes everything from the options (--yes = never ask, use the defaults / options).
The result is written with mode 600 and must stay out of git.

usage: make_params.py --out FILE [--yes] [--name TEXT] [--domain HOST] [--support-handle NAME] [--admin-ip IP] [--features "routing_links dr_backup"]"""
import argparse
import os
import re
import secrets
import socket
import subprocess
import sys

ap = argparse.ArgumentParser()
ap.add_argument("--out", required=True)
ap.add_argument("--yes", action="store_true", help="do not ask, use defaults and options")
ap.add_argument("--name", default="")
ap.add_argument("--domain", default="")
ap.add_argument("--support-handle", default="")
ap.add_argument("--admin-ip", default="")
ap.add_argument("--features", default=None)
ap.add_argument("--yookassa-shop-id", default="0")
a = ap.parse_args()

INTERACTIVE = not a.yes and sys.stdin.isatty()
HOST_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
IP_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{3,32}$")


def ask(question, default="", check=None, allow_empty=True):
    """Returns the answer; asks again until it passes `check` (a failing option is an error, not a question)."""
    while True:
        if not INTERACTIVE:
            value = default
        else:
            shown = f" [{default}]" if default else ""
            value = input(f"{question}{shown}: ").strip() or default
        if not value and allow_empty:
            return ""
        if check is None or check(value):
            return value
        if not INTERACTIVE:
            sys.exit(f"bad value for: {question}")
        print("  это не подходит, попробуйте ещё раз")


def public_ip():
    for cmd in (["curl", "-4", "-s", "-m", "6", "https://api.ipify.org"],):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
            if IP_RE.match(out):
                return out
        except Exception:
            pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("1.1.1.1", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "192.0.2.1"


def ssh_client_ip():
    for var in ("SSH_CONNECTION", "SSH_CLIENT"):
        v = os.environ.get(var, "").split()
        if v and IP_RE.match(v[0]):
            return v[0]
    return ""


ru_ip = public_ip()
name = ask("Название продукта в панели", a.name or "Чебурнатор", lambda v: len(v) <= 40 and not re.search(r"""[\\'"&<>`$]""", v), allow_empty=False)
domain = ask("Домен этого сервера (A-запись уже указывает сюда; Enter - пока без домена)", a.domain, lambda v: bool(HOST_RE.match(v.lower())))
domain = domain.lower()
handle = ask("Контакт поддержки в Telegram, без @", a.support_handle or "support", lambda v: bool(HANDLE_RE.match(v)), allow_empty=False)
admin_ip = ask("Ваш IP, который fail2ban не должен банить (Enter - пропустить)", a.admin_ip or ssh_client_ip(), lambda v: bool(IP_RE.match(v)))
if a.features is not None:
    features = a.features
else:
    features = []
    if domain and ask(f"Страница быстрого импорта в Happ на routing.{domain}? Нужна DNS-запись routing.{domain} (y/N)", "n").lower().startswith("y"):
        features.append("routing_links")
    features = " ".join(features)

# fixed, harmless defaults: documentation addresses (RFC 5737), they only appear as examples in the anti-share form
values = {
    "RU_IP": ru_ip,
    "ADMIN_IP_1": admin_ip or "198.51.100.1",
    "ADMIN_IP_2": "198.51.100.2",
    "ADMIN_IP_3": "198.51.100.3",
    "ROUTING_HOST": f"routing.{domain}" if domain else "routing.invalid",
    "BRAND_DOMAIN": domain or "domain.invalid",
    "PRODUCT_NAME": name,
    "SUPPORT_HANDLE": handle,
    "SUPPORT_DOMAIN": domain or "support.invalid",
    "GCORE_EDGE_1": "203.0.113.51",
    "GCORE_EDGE_NET": "203.0.113.0",
    # the secret path of the CDN entrance (the CDN origin has to be told this path); a new random one for every node
    "CDN_XHTTP_PATH": secrets.token_hex(16),
    # YooKassa shop number for the pay form of the instruction page (0 = no form, only the pay link); set it in /opt/routing-links/clients.json
    "YOOKASSA_SHOP_ID": re.sub(r"[^0-9]", "", a.yookassa_shop_id) or "0",
}
fd = os.open(a.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as f:
    f.write("# Parameters of this node (made by golden/make_params.py). Private: keep out of git.\n")
    for k, v in values.items():
        f.write(f"{k}={v}\n")
    f.write(f"FEATURES={features}\n")
os.chmod(a.out, 0o600)
print(f"параметры записаны: {a.out}")
