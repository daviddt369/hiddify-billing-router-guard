#!/usr/bin/env python3
"""vpn-watch: end-to-end monitoring of the RU entry node and its two exits (DE, NL). Python 3 standard library only.

What it watches (see README.md for the full table):
  RU    country canaries through the router entrances (what a user really gets: expected exit address per country), per-node probes of every VLESS/Trojan upstream
        (a temporary Xray client built from the LIVE router config), WireGuard tunnels, services, certificates, disk/memory, backups, router config invariants
  EXIT  services, public-port drift against a learned baseline, own egress, WireGuard peer, certificates, disk/memory, "the exit changed under us" fingerprints
Alerting: Telegram, one message per run, hysteresis (3 failing runs = critical, 5 = warning), repeat every N minutes while broken, recovery notice, daily summary.
Nothing here touches Hiddify, Xray configs or firewalls: it only reads, probes, and writes to /var/lib/vpn-watch.

  vpn_watch.py run [--role ru|exit]       fast checks (timer: every minute)
  vpn_watch.py nodes                      per-node probes (RU, timer: every 5 minutes)
  vpn_watch.py baseline                   (re)learn public ports and fingerprints from the CURRENT state
  vpn_watch.py status                     print the last results
  vpn_watch.py test-telegram              send a test message
Environment (for tests): VPN_WATCH_CONF, VPN_WATCH_STATE
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import glob
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

CONF_PATH = os.environ.get("VPN_WATCH_CONF", "/etc/vpn-watch/config.json")
STATE_DIR = os.environ.get("VPN_WATCH_STATE", "/var/lib/vpn-watch")
CRIT, WARN, INFO = "crit", "warn", "info"


# ------------------------------------------------------------------------------------------------------------------ helpers
def sh(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except Exception as e:  # noqa
        return 127, f"{type(e).__name__}: {e}"


def curl(url, socks_port=None, interface=None, timeout=10, extra=()):
    cmd = ["curl", "-s", "-m", str(timeout), "-w", "\n%{http_code}"]
    if socks_port:
        cmd += ["--socks5-hostname", f"127.0.0.1:{socks_port}"]
    if interface:
        cmd += ["--interface", interface]
    cmd += list(extra) + [url]
    rc, out = sh(cmd, timeout + 5)
    body, _, code = out.rpartition("\n")
    return rc, code.strip(), body.strip()


def extract(body: str, how: str):
    """how: 'text' (whole body), 'trace' (ip= line of a Cloudflare trace), 'json:KEY'"""
    if how.startswith("json:"):
        try:
            return str(json.loads(body).get(how[5:]))
        except Exception:
            return None
    if how == "trace":
        m = re.search(r"^ip=(\S+)", body, re.M)
        return m.group(1) if m else None
    return body.strip() or None


class R:
    """one check result"""
    __slots__ = ("id", "ok", "level", "detail", "event")

    def __init__(self, id, ok, level=CRIT, detail="", event=False):
        self.id, self.ok, self.level, self.detail, self.event = id, bool(ok), level, detail, event

    def as_dict(self):
        return {"id": self.id, "ok": self.ok, "level": self.level, "detail": self.detail}


# ------------------------------------------------------------------------------------------------------------------ checks
def check_services(cfg):
    out = []
    for u in cfg.get("services", []):
        rc, so = sh(["systemctl", "is-active", u])
        out.append(R(f"svc:{u}", so.strip() == "active", CRIT, f"{u}: {so.strip() or 'неизвестно'}"))
    for u in cfg.get("timers", []):
        rc, so = sh(["systemctl", "is-active", u])
        out.append(R(f"timer:{u}", so.strip() == "active", WARN, f"{u}: {so.strip() or 'неизвестно'}"))
    return out


def check_listening(cfg):
    out = []
    for p in cfg.get("router_ports", []):
        try:
            with socket.create_connection(("127.0.0.1", int(p)), timeout=1.5):
                ok = True
        except OSError:
            ok = False
        out.append(R(f"port:{p}", ok, CRIT, f"вход router 127.0.0.1:{p} {'слушает' if ok else 'НЕ слушает'}"))
    return out


def check_canaries(cfg):
    """what users really get: through each router entrance fetch the probe URL and compare the exit address / identity with the expectation"""
    out = []
    probe = cfg.get("probe", {"url": "https://1.1.1.1/cdn-cgi/trace", "extract": "trace"})
    for c in cfg.get("canaries", []):
        rc, code, body = curl(probe["url"], socks_port=c["port"], timeout=probe.get("timeout", 10))
        got = extract(body, probe.get("extract", "trace")) if code == "200" else None
        want = c.get("expect")
        want = [want] if isinstance(want, str) else (want or [])
        if got is None:
            out.append(R(f"canary:{c['name']}", False, CRIT, f"{c['name']}: нет ответа через вход {c['port']} (curl rc={rc}, http={code or '-'})"))
        elif want and got not in want:
            out.append(R(f"canary:{c['name']}", False, CRIT, f"{c['name']}: выход {got}, ожидался один из {want}: трафик пошёл не туда"))
        else:
            out.append(R(f"canary:{c['name']}", True, CRIT, f"{c['name']}: выход {got}"))
    return out


def check_router_config(cfg, state):
    """invariants of the live router config + 'the config changed' event"""
    path = cfg.get("router_config")
    if not path:
        return []
    out = []
    try:
        raw = open(path, "rb").read()
        conf = json.loads(raw)
    except Exception as e:
        return [R("routercfg:read", False, WARN, f"не удалось прочитать {path}: {type(e).__name__}")]
    sha = hashlib.sha256(raw).hexdigest()[:12]
    prev = state.setdefault("meta", {}).get("router_sha")
    if prev and prev != sha:
        out.append(R("routercfg:changed", False, INFO, f"конфиг router изменился ({prev} → {sha}); это нормально после «Применить»", event=True))
    state["meta"]["router_sha"] = sha
    bals = {b.get("tag"): b for b in conf.get("routing", {}).get("balancers", [])}
    for tag, b in bals.items():
        if str(tag).startswith("balancer-s"):
            ok = b.get("fallbackTag") == "block"
            out.append(R(f"routercfg:failclosed:{tag}", ok, WARN, f"{tag}: fallbackTag={b.get('fallbackTag')!r} (нужно 'block': иначе при падении страны трафик уйдёт в чужую)"))
        elif tag == "auto-director":
            ok = b.get("fallbackTag") == "block"
            out.append(R(f"routercfg:failclosed:{tag}", ok, WARN, f"AUTO-директор: fallbackTag={b.get('fallbackTag')!r} (нужно 'block')"))
        elif tag == "upstream-balancer" and cfg.get("auto_fail_closed", True):
            ok = b.get("fallbackTag") == "block"
            out.append(R(f"routercfg:failclosed:{tag}", ok, WARN, f"AUTO ({tag}): fallbackTag={b.get('fallbackTag')!r} (нужно 'block': иначе при отказе обеих стран зарубежный трафик уйдёт в первый узел файла)"))
    outs = [o.get("tag") for o in conf.get("outbounds", [])]
    out.append(R("routercfg:block", any(o.get("tag") == "block" and o.get("protocol") == "blackhole" for o in conf.get("outbounds", [])), WARN, "outbound block (blackhole) на месте"))
    return out


def check_director(cfg, state):
    """the AUTO director (country-level AUTO under hysteresis): the controller must be alive and applied, must not flap, and every switch is reported once"""
    d = cfg.get("auto_director")
    if not d:
        return []
    st_dir = d.get("state_dir", "/var/lib/auto-director")
    try:
        with open(os.path.join(st_dir, "status.json")) as f:
            st = json.load(f)
    except (OSError, ValueError):
        return [R("director:alive", False, WARN, "контроллер AUTO: нет файла статуса (служба auto-director не запущена?)")]
    out = []
    age = time.time() - float(st.get("ts", 0))
    out.append(R("director:alive", age <= d.get("max_age_s", 60), WARN, f"контроллер AUTO: статус обновлялся {int(age)} с назад (порог {d.get('max_age_s', 60)}) — если он молчит, AUTO работает по резервному выбору роутера"))
    cur = st.get("cur")
    out.append(R("director:applied", bool(st.get("applied", True)), WARN, f"контроллер AUTO: выбор {cur} не применён в router (API недоступен?)"))
    n = int(st.get("switches_last_hour", 0))
    lim = d.get("max_switches_per_hour", 4)
    out.append(R("director:flaps", n <= lim, WARN, f"AUTO переключался {n} раз за час (порог {lim}): проверьте задержки до выходов и параметры гистерезиса"))
    out.append(R("director:closed", cur != "NONE", CRIT, "AUTO ЗАКРЫТ: обе страны недоступны, зарубежный трафик заблокирован"))
    meta = state.setdefault("meta", {})
    ls = float(st.get("last_switch", 0))
    if meta.get("director_last_switch") is not None and ls and ls != meta["director_last_switch"]:
        try:
            with open(os.path.join(st_dir, "state.json")) as f:
                sw = json.load(f).get("switches", [])
            last = sw[-1] if sw else {}
            out.append(R("director:switch", False, INFO, f"AUTO: {last.get('from')} → {last.get('to')}: {last.get('reason', st.get('reason', ''))}", event=True))
        except (OSError, ValueError):
            out.append(R("director:switch", False, INFO, f"AUTO переключён на {cur}", event=True))
    meta["director_last_switch"] = ls
    return out


def check_router_restarts(cfg, state):
    unit = cfg.get("router_unit", "xray-router")
    rc, so = sh(["systemctl", "show", "-p", "ActiveEnterTimestampMonotonic,NRestarts,MainPID", unit])
    d = dict(l.split("=", 1) for l in so.splitlines() if "=" in l)
    meta = state.setdefault("meta", {})
    pid = d.get("MainPID", "0")
    out = []
    if meta.get("router_pid") and meta["router_pid"] != pid and pid != "0":
        out.append(R("router:restarted", False, INFO, f"{unit} перезапускался (PID {meta['router_pid']} → {pid})", event=True))
    meta["router_pid"] = pid
    return out


def _sys_counter(wcfg, iface, name):
    try:
        with open(f"{wcfg.get('sysfs_net', '/sys/class/net')}/{iface}/statistics/{name}") as f:
            return int(f.read())
    except (OSError, ValueError):
        return None


def _tun_growth(wcfg, meta, iface, now):
    """Xray's own WireGuard (RU) is a TUN device: wg(8) knows nothing about it, but the device counters do. A healthy tunnel is probed every minute
    by the router's observatory, so BOTH counters grow; "TX grows, RX does not" is exactly the dead-return-path signature (the .190 NAT defect)."""
    rx, tx = _sys_counter(wcfg, iface, "rx_packets"), _sys_counter(wcfg, iface, "tx_packets")
    if rx is None or tx is None:
        return [R(f"wg:{iface}:rx", False, CRIT, f"{iface}: интерфейса нет (счётчики недоступны)")]
    prev, window = meta.get(iface), wcfg.get("rx_window", 240)
    if not prev:
        meta[iface] = {"rx": rx, "tx": tx, "t": now}
        return []
    if now - prev["t"] < window:
        return []
    drx, dtx, need = rx - prev["rx"], tx - prev["tx"], wcfg.get("rx_min_packets", 3)
    meta[iface] = {"rx": rx, "tx": tx, "t": now}
    secs = int(now - prev["t"])
    if drx >= need:
        return [R(f"wg:{iface}:rx", True, CRIT, f"{iface}: туннель жив (принято +{drx}, отправлено +{dtx} пакетов за {secs} с)")]
    if dtx >= need:
        return [R(f"wg:{iface}:rx", False, CRIT, f"{iface}: отправлено +{dtx}, принято +{drx} пакетов за {secs} с: ОБРАТНЫЙ ПУТЬ МЁРТВ (туннель шлёт, но ничего не возвращается)")]
    return [R(f"wg:{iface}:rx", False, WARN, f"{iface}: туннель молчит (+{dtx} отправлено, +{drx} принято за {secs} с): узел не опрашивается или интерфейс простаивает")]


def check_wireguard(cfg, state):
    """RU: tun_only (Xray userspace WireGuard exposed as TUN: counters only). Exits: kernel WireGuard: fresh handshake (+ optional RX growth)."""
    wcfg = cfg.get("wireguard")
    if not wcfg:
        return []
    out = []
    now = time.time()
    meta = state.setdefault("meta", {}).setdefault("wg", {})
    if wcfg.get("tun_only"):
        for iface in wcfg.get("interfaces", []):
            out += _tun_growth(wcfg, meta, iface, now)
        return out
    rc, so = sh(["wg", "show", "all", "dump"])
    if rc != 0:
        return [R("wg:available", False, WARN, f"wg show недоступен: {so.strip()[:80]}")]
    seen = {}
    for ln in so.splitlines():
        p = ln.split("\t")
        if len(p) == 9:        # peer line: iface, pubkey, psk, endpoint, allowed-ips, handshake, rx, tx, keepalive
            seen.setdefault(p[0], []).append(int(p[5] or 0))
    for iface in (wcfg.get("interfaces") or list(seen)):
        peers = seen.get(iface)
        if not peers:
            out.append(R(f"wg:{iface}", False, CRIT, f"{iface}: интерфейса или пира нет"))
            continue
        hs = max(peers)
        age = int(now - hs) if hs else None
        max_age = wcfg.get("max_handshake_age", 240)
        out.append(R(f"wg:{iface}:handshake", age is not None and age <= max_age, CRIT, f"{iface}: рукопожатие {age if age is not None else 'никогда'} с назад (допустимо ≤ {max_age})"))
        if iface in wcfg.get("rx_growth_interfaces", []):
            out += _tun_growth(wcfg, meta, iface, now)
    return out


def check_system(cfg):
    out = []
    for path in cfg.get("disks", ["/"]):
        try:
            u = shutil.disk_usage(path)
            free_pct = 100 * u.free / u.total
            out.append(R(f"disk:{path}", free_pct >= cfg.get("min_disk_free_pct", 12), WARN, f"диск {path}: свободно {free_pct:.0f}% ({u.free // 2**20} МБ)"))
        except OSError as e:
            out.append(R(f"disk:{path}", False, WARN, f"диск {path}: {e}"))
    try:
        mem = {k: int(v.split()[0]) for k, v in (l.split(":", 1) for l in open("/proc/meminfo"))}
        avail = mem.get("MemAvailable", 0) // 1024
        out.append(R("mem", avail >= cfg.get("min_mem_available_mb", 200), WARN, f"память: доступно {avail} МБ"))
    except Exception:
        pass
    try:
        l1 = os.getloadavg()[0]
        cores = os.cpu_count() or 1
        out.append(R("load", l1 <= cfg.get("max_load_per_core", 2.0) * cores, WARN, f"нагрузка {l1:.2f} на {cores} ядер"))
    except OSError:
        pass
    return out


def check_certs(cfg):
    """entries: "glob" or {"glob":..., "warn_days":..., "crit_days":...}. The IP-address certificates are short-lived (about 6 days, renewed every 2-3 days):
    give them their own thresholds, otherwise they would alarm all the time"""
    out = []
    for ent in cfg.get("certs", []):
        spec = ent if isinstance(ent, dict) else {"glob": ent}
        warn = spec.get("warn_days", cfg.get("cert_warn_days", 14))
        crit = spec.get("crit_days", cfg.get("cert_crit_days", 5))
        for f in sorted(glob.glob(spec["glob"])):
            if any(os.path.basename(f) == x or x in f for x in spec.get("exclude", [])):          # exclude: a file name or a part of the path
                continue
            rc, so = sh(["openssl", "x509", "-noout", "-enddate", "-in", f])
            m = re.search(r"notAfter=(.+)", so)
            if rc != 0 or not m:
                continue
            try:
                end = datetime.datetime.strptime(m.group(1).strip(), "%b %d %H:%M:%S %Y %Z").replace(tzinfo=datetime.timezone.utc)
            except ValueError:
                continue
            days = (end - datetime.datetime.now(datetime.timezone.utc)).total_seconds() / 86400
            level = CRIT if days < crit else WARN
            name = os.path.basename(f)
            if name.startswith("fullchain"):                       # acme.sh stores every certificate as <domain>_ecc/fullchain.cer: the directory is the name
                name = os.path.basename(os.path.dirname(f)) + "/" + name
            out.append(R(f"cert:{name}", days >= warn, level, f"сертификат {name}: осталось {days:.1f} дн. (порог {warn})"))
    return out


def check_backups(cfg):
    out = []
    b = cfg.get("backup_dir")
    if b and os.path.isdir(b):
        files = [os.path.join(b, x) for x in os.listdir(b)]
        if files:
            age_h = (time.time() - max(os.path.getmtime(x) for x in files)) / 3600
            out.append(R("backup:age", age_h <= cfg.get("max_backup_age_h", 8), WARN, f"новейший бэкап панели старше {age_h:.1f} ч"))
        else:
            out.append(R("backup:age", False, WARN, "каталог бэкапов пуст"))
    return out


def public_listeners(ignore_ports=(), ignore_procs=(), ephemeral=()):
    rc, so = sh(["ss", "-Htulnp"])
    res = {}
    for ln in so.splitlines():
        p = ln.split()
        if len(p) < 5:
            continue
        proto, local = p[0], p[4]
        host, _, port = local.rpartition(":")
        host = host.strip("[]")
        if host.startswith("127.") or host in ("::1",) or host.startswith("127.0.0.5") or "%lo" in host:
            continue
        m = re.search(r'\(\("([^"]+)"', ln)
        proc = m.group(1) if m else "-"
        if int(port) in ignore_ports or proc in ignore_procs:
            continue
        if any(proc == e.get("proc") and proto.startswith(e.get("proto", "udp")) and int(port) >= int(e.get("min_port", 30000)) for e in ephemeral):
            continue                      # e.g. the random UDP source ports of Xray's WireGuard outbounds: they change at every router restart
        res[f"{proto}/{port}"] = proc
    return res


def check_ports(cfg, state, learn=False):
    """new public listener = critical (the F-04/F-06 class of problems); a vanished one = warning"""
    ign = cfg.get("ignore_ports", [])
    cur = public_listeners(ign, cfg.get("ignore_procs", []), cfg.get("ignore_ephemeral", []))
    meta = state.setdefault("meta", {})
    base = meta.get("ports_baseline")
    if base is None or learn:
        meta["ports_baseline"] = cur
        return [R("ports:baseline", False, INFO, f"запомнен эталон публичных портов ({len(cur)} шт.)", event=True)] if base is None or learn else []
    new = {k: v for k, v in cur.items() if k not in base}
    gone = [k for k in base if k not in cur]
    out = [R("ports:new", not new, CRIT, "новые публичные порты: " + ", ".join(f"{k} ({v})" for k, v in sorted(new.items())) if new else "новых публичных портов нет")]
    out.append(R("ports:gone", not gone, WARN, "исчезли порты: " + ", ".join(sorted(gone)) if gone else "все ожидаемые порты на месте"))
    return out


def check_egress(cfg):
    e = cfg.get("egress")
    if not e:
        return []
    rc, code, body = curl(e.get("url", "https://1.1.1.1/cdn-cgi/trace"), timeout=8)
    got = extract(body, e.get("extract", "trace")) if code == "200" else None
    ok = got is not None and (not e.get("expect") or got == e["expect"])
    return [R("egress:own", ok, CRIT, f"собственный выход в интернет: {got or 'нет ответа'}" + (f" (ожидался {e['expect']})" if got and e.get("expect") and got != e["expect"] else ""))]


def fingerprint_of(spec):
    mode = spec.get("mode", "file")
    h = hashlib.sha256()
    files = sorted(glob.glob(spec["glob"])) if "glob" in spec else []
    for f in files:
        try:
            if mode == "keys":        # only the first token of each line (map keys = URL paths); values may legitimately move
                for ln in sorted(l.split()[0] for l in open(f, errors="replace") if l.strip() and not l.startswith("#")):
                    h.update(ln.encode())
            else:
                h.update(open(f, "rb").read())
        except OSError:
            pass
    if "cmd" in spec:
        rc, so = sh(spec["cmd"], 20)
        h.update(so.strip().encode())
    if "json_summary" in spec:       # structural summary of a JSON file: protocol/network/security/decryption-kind per inbound
        try:
            d = json.load(open(spec["json_summary"]))
            items = []
            for ib in d.get("inbounds", []):
                ss = ib.get("streamSettings") or {}
                dec = (ib.get("settings") or {}).get("decryption")
                items.append((ib.get("protocol"), ss.get("network"), ss.get("security"), "none" if dec in (None, "none") else "mlkem"))
            h.update(json.dumps(sorted(map(str, items))).encode())
        except Exception:
            pass
    return h.hexdigest()[:12]


EMPTY_FP = hashlib.sha256(b"").hexdigest()[:12]


def check_fingerprints(cfg, state):
    out = []
    store = state.setdefault("meta", {}).setdefault("fingerprints", {})
    for spec in cfg.get("fingerprints", []):
        if spec.get("enabled", True) is False:
            continue
        name = spec["name"]
        cur = fingerprint_of(spec)
        prev = store.get(name)
        if cur == EMPTY_FP:            # the file was missing/empty at this very moment (Hiddify rewrites the configs on "Apply"): not a change, look again next minute
            continue
        if prev is None:
            store[name] = cur
        elif prev != cur:
            out.append(R(f"drift:{name}", False, WARN, f"изменилось «{name}» ({prev} → {cur}): {spec.get('why', 'выход изменился без вашего участия?')}", event=True))
            store[name] = cur
    return out


def check_db_setting(cfg):
    out = []
    for s in cfg.get("db_settings", []):
        if s.get("enabled", True) is False:
            continue
        rc, so = sh(["mysql", "-N", "-B", s.get("db", "hiddifypanel"), "-e", s["sql"]])
        val = so.strip()
        out.append(R(f"setting:{s['name']}", val == str(s["expect"]), WARN, f"{s['name']} = {val or '?'} (ожидалось {s['expect']}): {s.get('why', '')}"))
    return out


def check_router_log(cfg):
    unit = cfg.get("router_unit", "xray-router")
    win = cfg.get("dns_error_window_min", 10)
    rc, so = sh(f"journalctl -u {unit} --since '{win} minutes ago' --no-pager 2>/dev/null | grep -c 'closed pipe\\|dns.*fail'", 20)
    try:
        n = int(so.strip() or 0)
    except ValueError:
        n = 0
    lim = cfg.get("dns_error_limit", 40)
    return [R("router:dns-errors", n <= lim, WARN, f"ошибки DNS в журнале router за {win} мин: {n} (порог {lim})")]


def check_tcp_targets(cfg):
    out = []
    for t in cfg.get("tcp_targets", []):
        try:
            with socket.create_connection((t["host"], int(t["port"])), timeout=4):
                ok = True
        except OSError:
            ok = False
        out.append(R(f"tcp:{t['name']}", ok, CRIT, f"{t['name']} {t['host']}:{t['port']} {'доступен' if ok else 'НЕ доступен'}"))
    return out


# ------------------------------------------------------------------------------------------------------------------ per-node probes (RU)
def router_nodes(conf):
    """upstream outbounds that a temporary client can test: vless/trojan/shadowsocks (WireGuard is checked through its interface)"""
    return [o for o in conf.get("outbounds", []) if str(o.get("tag", "")).startswith("upstream-") and o.get("protocol") in ("vless", "vmess", "trojan", "shadowsocks")]


def node_country(conf, cfg):
    """upstream tag -> country name by the balancer selectors (balancer-s<N> -> cfg['slots'][N])"""
    res = {}
    names = cfg.get("slots", {})
    for b in conf.get("routing", {}).get("balancers", []):
        m = re.match(r"balancer-s(\d+)$", str(b.get("tag")))
        if m:
            for sel in b.get("selector", []):
                res[sel] = names.get(m.group(1), f"slot{m.group(1)}")
    for r in conf.get("routing", {}).get("rules", []):          # single-node countries are plain outboundTag rules of the slot entrance
        for t in r.get("inboundTag", []) or []:
            m = re.match(r"from-hiddify-s(\d+)$", str(t))
            if m and r.get("outboundTag", "").startswith("upstream-"):
                res.setdefault(r["outboundTag"], names.get(m.group(1), f"slot{m.group(1)}"))
    return res


def probe_nodes(cfg, state):
    path = cfg["router_config"]
    conf = json.load(open(path))
    nodes = router_nodes(conf)
    if not nodes:
        return []
    probe = cfg.get("probe", {"url": "https://1.1.1.1/cdn-cgi/trace", "extract": "trace"})
    base = int(cfg.get("node_probe_base_port", 21900))
    xray = shutil.which("xray") or "/usr/bin/xray"
    country = node_country(conf, cfg)
    expect_by_country = {c["name"]: c.get("expect") for c in cfg.get("canaries", [])}
    inbounds, rules, outs, ports = [], [], [], {}
    referenced = set()
    for i, o in enumerate(nodes):
        tag = o["tag"]
        port = base + i
        ports[tag] = port
        inbounds.append({"tag": f"p-{tag}", "listen": "127.0.0.1", "port": port, "protocol": "socks", "settings": {"auth": "noauth", "udp": False}})
        rules.append({"type": "field", "inboundTag": [f"p-{tag}"], "outboundTag": tag})
        outs.append(o)
        dp = ((o.get("streamSettings") or {}).get("sockopt") or {}).get("dialerProxy")
        if dp:
            referenced.add(dp)
    for o in conf.get("outbounds", []):
        if o.get("tag") in referenced:
            outs.append(o)
    outs.append({"tag": "block", "protocol": "blackhole"})
    pconf = {"log": {"loglevel": "none"}, "inbounds": inbounds, "outbounds": outs, "routing": {"domainStrategy": "AsIs", "rules": rules}}
    tmp = tempfile.mkdtemp(prefix="vpnwatch-")
    cfgfile = os.path.join(tmp, "probe.json")
    with open(cfgfile, "w") as f:
        json.dump(pconf, f)
    os.chmod(cfgfile, 0o600)
    proc = subprocess.Popen([xray, "run", "-c", cfgfile], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    results = []
    try:
        t0 = time.time()
        while time.time() - t0 < 8:
            try:
                with socket.create_connection(("127.0.0.1", ports[nodes[-1]["tag"]]), timeout=0.3):
                    break
            except OSError:
                time.sleep(0.2)
        else:
            return [R("nodes:probe-client", False, WARN, "временный клиент Xray не поднялся")]

        def one(o):
            tag = o["tag"]
            rc, code, body = curl(probe["url"], socks_port=ports[tag], timeout=probe.get("timeout", 12))
            got = extract(body, probe.get("extract", "trace")) if code == "200" else None
            return tag, got, rc, code
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
            for tag, got, rc, code in ex.map(one, nodes):
                cn = country.get(tag, "AUTO")
                want = expect_by_country.get(cn)
                want = [want] if isinstance(want, str) else (want or [])
                nid = tag.split("-", 1)[1]
                if nid in cfg.get("expected_dead_nodes", []):
                    results.append(R(f"node:{nid}", True, INFO, f"узел {nid} ({cn}): в списке ожидаемо мёртвых — не оценивается"))
                elif got is None:
                    results.append(R(f"node:{nid}", False, WARN, f"узел {nid} ({cn}): не отвечает (curl rc={rc}, http={code or '-'})"))
                elif want and got not in want:
                    results.append(R(f"node:{nid}", False, CRIT, f"узел {nid} ({cn}): выход {got}, ожидался {want}"))
                else:
                    results.append(R(f"node:{nid}", True, WARN, f"узел {nid} ({cn}): ок, выход {got}"))
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)
    # country summary: how many nodes of the country are alive (a country with a single live node has no reserve)
    byc = {}
    for r in results:
        nid = r.id.split(":", 1)[1]
        cn = country.get(f"upstream-{nid}", "AUTO")
        byc.setdefault(cn, []).append(r)
    for cn, rs in byc.items():
        live = sum(1 for r in rs if r.ok and r.level != INFO)
        tot = sum(1 for r in rs if r.level != INFO or not r.ok)
        if tot >= 2:
            results.append(R(f"nodes:reserve:{cn}", live >= 2, WARN, f"{cn}: живых узлов {live} из {tot}" + ("" if live >= 2 else " — резерва внутри страны нет")))
    return results


def wg_iface_by_address(addresses):
    """interface that carries one of the outbound's addresses (Xray's TUN devices keep the tunnel address, e.g. 10.66.66.2/32 on wg0)"""
    rc, so = sh(["ip", "-4", "-o", "addr", "show"])
    for ln in so.splitlines():
        p = ln.split()
        if len(p) >= 4 and p[3].split("/")[0] in addresses:
            return p[1].rstrip(":")
    return None


def probe_wg_nodes(cfg):
    """WireGuard upstreams: find the interface by the tunnel address (TUN) or by the peer endpoint (kernel WireGuard) and fetch the probe URL bound to it"""
    path = cfg.get("router_config")
    if not path or not cfg.get("wg_nodes"):
        return []
    with open(path) as f:
        conf = json.load(f)
    rc, so = sh(["wg", "show", "all", "endpoints"])
    ep2if = {}
    for ln in so.splitlines():
        p = ln.split()
        if len(p) == 3:
            ep2if[p[2]] = p[0]
    country = node_country(conf, cfg)
    expect_by_country = {c["name"]: c.get("expect") for c in cfg.get("canaries", [])}
    probe = cfg.get("probe", {"url": "https://1.1.1.1/cdn-cgi/trace", "extract": "trace"})
    out = []
    for o in conf.get("outbounds", []):
        if o.get("protocol") != "wireguard" or not str(o.get("tag", "")).startswith("upstream-"):
            continue
        nid = o["tag"].split("-", 1)[1]
        st = o.get("settings") or {}
        peers = st.get("peers") or []
        ep = peers[0].get("endpoint") if peers else None
        addrs = [a.split("/")[0] for a in (st.get("address") or [])]
        iface = wg_iface_by_address(addrs) or ep2if.get(ep)
        cn = country.get(o["tag"], "AUTO")
        if nid in cfg.get("expected_dead_nodes", []):
            out.append(R(f"node:{nid}", True, INFO, f"WG-узел {nid} ({cn}): в списке ожидаемо мёртвых — не оценивается"))
            continue
        if not iface:
            out.append(R(f"node:{nid}", False, WARN, f"WG-узел {nid} ({cn}): интерфейс туннеля не найден (адрес {addrs}, endpoint {ep})"))
            continue
        rc, code, body = curl(probe["url"], interface=iface, timeout=probe.get("timeout", 12))
        got = extract(body, probe.get("extract", "trace")) if code == "200" else None
        want = expect_by_country.get(cn)
        want = [want] if isinstance(want, str) else (want or [])
        if got is None:
            out.append(R(f"node:{nid}", False, WARN, f"WG-узел {nid} ({cn}, {iface}): нет ответа через туннель (curl rc={rc}, http={code or '-'})"))
        elif want and got not in want:
            out.append(R(f"node:{nid}", False, CRIT, f"WG-узел {nid} ({cn}, {iface}): выход {got}, ожидался {want}"))
        else:
            out.append(R(f"node:{nid}", True, WARN, f"WG-узел {nid} ({cn}, {iface}): ок, выход {got}"))
    return out


# ------------------------------------------------------------------------------------------------------------------ alerting
def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise OSError("socks5: connection closed by the proxy")
        buf += chunk
    return buf


def socks5_post(url, data, proxy, timeout=10):
    """POST form data through a SOCKS5 proxy (stdlib only, the host name is resolved by the proxy = socks5h). Returns the HTTP status code.
    The RU node cannot reach api.telegram.org directly: the panel's own bot goes through the router entrance, so do we."""
    u = urllib.parse.urlsplit(url)
    host = u.hostname
    port = u.port or (443 if u.scheme == "https" else 80)
    p = urllib.parse.urlsplit(proxy)
    s = socket.create_connection((p.hostname, p.port or 1080), timeout=timeout)
    try:
        s.settimeout(timeout)
        s.sendall(b"\x05\x01\x00")                                    # version 5, one method: no authentication
        if _recv_exact(s, 2) != b"\x05\x00":
            raise OSError("socks5: the proxy wants authentication")
        hb = host.encode("idna")
        s.sendall(b"\x05\x01\x00\x03" + bytes([len(hb)]) + hb + port.to_bytes(2, "big"))
        head = _recv_exact(s, 4)                                      # VER REP RSV ATYP
        if head[0] != 5 or head[1] != 0:
            raise OSError(f"socks5: the proxy refused the connection (reply {head[1]})")
        if head[3] == 3:
            _recv_exact(s, _recv_exact(s, 1)[0] + 2)
        else:
            _recv_exact(s, (4 if head[3] == 1 else 16) + 2)
        if u.scheme == "https":
            s = ssl.create_default_context().wrap_socket(s, server_hostname=host)
        path = u.path + (("?" + u.query) if u.query else "")
        s.sendall((f"POST {path} HTTP/1.1\r\nHost: {u.netloc}\r\nContent-Type: application/x-www-form-urlencoded\r\n"
                   f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n").encode() + data)
        resp = b""
        while b"\r\n" not in resp and len(resp) < 8192:
            chunk = s.recv(4096)
            if not chunk:
                break
            resp += chunk
        return int(resp.split(b"\r\n", 1)[0].split()[1])
    finally:
        s.close()


class Telegram:
    def __init__(self, cfg):
        t = cfg.get("telegram", {})
        self.api = t.get("api_base", "https://api.telegram.org")
        self.env = {}
        envf = t.get("env_file", "/etc/vpn-watch/telegram.env")
        try:
            for ln in open(envf):
                if "=" in ln and not ln.strip().startswith("#"):
                    k, v = ln.strip().split("=", 1)
                    self.env[k] = v.strip().strip('"').strip("'")
        except OSError:
            pass
        self.token = os.environ.get("VPN_WATCH_TG_TOKEN") or self.env.get("TG_TOKEN")
        self.chat = os.environ.get("VPN_WATCH_TG_CHAT") or self.env.get("TG_CHAT")
        self.proxy = os.environ.get("VPN_WATCH_TG_PROXY") or self.env.get("TG_PROXY") or t.get("proxy") or ""   # socks5h://host:port or http://host:port
        self.host = cfg.get("host_label", socket.gethostname())

    def _post(self, url, data):
        if self.proxy.startswith("socks5"):
            return socks5_post(url, data, self.proxy)
        if self.proxy:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy}))
            with opener.open(urllib.request.Request(url, data=data), timeout=10) as r:
                return r.status
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10) as r:
            return r.status

    def send(self, text, silent=False):
        if not self.token or not self.chat:
            return False
        url = f"{self.api}/bot{self.token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": self.chat, "text": f"[{self.host}] {text}"[:3900], "disable_notification": "true" if silent else "false"}).encode()
        for _ in range(2):
            try:
                if self._post(url, data) == 200:
                    return True
            except Exception:                  # never print the exception: it may contain the URL with the bot token
                pass
            time.sleep(1)
        return False


class Monitor:
    def __init__(self, cfg, state_dir=STATE_DIR, now=time.time, tg=None):
        self.cfg, self.now, self.dir = cfg, now, state_dir
        os.makedirs(state_dir, exist_ok=True)
        self.path = os.path.join(state_dir, "state.json")
        try:
            self.state = json.load(open(self.path))
        except Exception:
            self.state = {}
        self.state.setdefault("checks", {})
        self.tg = tg or Telegram(cfg)

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.state, f, indent=1, ensure_ascii=False)
        os.replace(tmp, self.path)

    def apply(self, results, namespace, crit_after=None, warn_after=None):
        """update per-check counters; returns (alerts, recoveries)"""
        th = self.cfg.get("thresholds", {})
        crit_after = crit_after or th.get("crit_runs", 3)
        warn_after = warn_after or th.get("warn_runs", 5)
        repeat = th.get("repeat_minutes", 180) * 60
        # what is SENT to Telegram: only these levels (config "notify_levels", default all) plus the check ids that start with one of "notify_always".
        # Everything else is still evaluated and kept (status, the daily summary), it just does not page anybody.
        levels = set(self.cfg.get("notify_levels", [CRIT, WARN, INFO]))
        always = tuple(self.cfg.get("notify_always", []))
        only = tuple(self.cfg["notify_only"]) if "notify_only" in self.cfg else None
        slow_map = self.cfg.get("notify_slow", {})          # id (prefix) -> runs (about minutes) of continuous failure before it is "important enough"
        alerts, recov, t = [], [], self.now()
        for r in results:
            sendable = r.level in levels or (bool(always) and r.id.startswith(always))
            icon_override, need_override = None, None
            if only is not None:                       # allow-list mode: ONLY these checks may page anybody, nothing else (events included)
                slow = next((k for k in slow_map if r.id == k or r.id.startswith(k)), None)
                sendable = bool(only) and r.id.startswith(only) or slow is not None
                if slow is not None:
                    icon_override, need_override = "🟠 ВАЖНО:", int(slow_map[slow])
                elif sendable:
                    icon_override = "🔴 КРИТИЧНО:"
            st = self.state["checks"].setdefault(r.id, {"fails": 0, "alerted": False, "last_alert": 0, "since": t})
            st["detail"], st["level"], st["ok"], st["ns"], st["seen"], st["event"] = r.detail, r.level, r.ok, namespace, t, bool(r.event)
            if r.event:
                if not r.ok and sendable:
                    alerts.append((r.level, r.detail))
                continue
            if r.ok:
                if st["alerted"]:
                    recov.append(f"✅ восстановлено: {r.detail}")
                st.update(fails=0, alerted=False, since=t)
                continue
            if st["fails"] == 0:
                st["since"] = t
            st["fails"] += 1
            need = need_override or (crit_after if r.level == CRIT else warn_after)
            if r.level == INFO or not sendable:
                continue
            if st["fails"] >= need and (not st["alerted"] or t - st["last_alert"] >= repeat):
                icon = icon_override or ("🔴" if r.level == CRIT else "🟠")
                dur = int((t - st["since"]) / 60)
                alerts.append((r.level, f"{icon} {r.detail}" + (f" (уже {dur} мин)" if st["alerted"] else "")))
                st["alerted"], st["last_alert"] = True, t
        # checks of this namespace that were not reported any more (e.g. a node that disappeared) are dropped quietly after a day
        for cid in [c for c, v in self.state["checks"].items() if v.get("ns") == namespace and t - v.get("seen", t) > 86400]:
            self.state["checks"].pop(cid, None)
        return alerts, recov

    def notify(self, alerts, recov):
        msgs = [m if m.startswith(("🔴", "🟠", "ℹ️")) else f"ℹ️ {m}" for _, m in alerts] + recov
        t = self.now()
        pend = [{"t": t, "m": x} if isinstance(x, str) else x for x in self.state.get("pending", [])]
        if msgs:
            pend.append({"t": t, "m": "\n".join(msgs)})
        sent = []
        for item in pend[-20:]:                    # a message that could not be delivered (Telegram or the exits down) is retried on the next run
            late = t - item["t"]
            text = item["m"] if late < 120 else f"⏳ событие в {time.strftime('%H:%M', time.localtime(item['t']))}, доставлено с опозданием {int(late / 60)} мин\n{item['m']}"
            if self.tg.send(text):
                sent.append(item)
            else:
                break
        self.state["pending"] = [it for it in pend if it not in sent][-20:]

    def daily(self):
        h = self.cfg.get("daily_summary_hour")
        if h is None:
            return
        today = datetime.date.fromtimestamp(self.now()).isoformat()
        meta = self.state.setdefault("meta", {})
        if meta.get("daily_sent") == today or datetime.datetime.fromtimestamp(self.now()).hour < int(h):
            return
        bad = [v["detail"] for v in self.state["checks"].values() if not v.get("ok") and v.get("level") != INFO and not v.get("event")]
        total = len(self.state["checks"])
        txt = f"📋 ежедневная сводка: проверок {total}, проблем {len(bad)}" + ("\n" + "\n".join("• " + b for b in bad[:12]) if bad else " — всё в порядке")
        known = [v["detail"] for v in self.state["checks"].values() if v.get("level") == INFO and v.get("ok") and "ожидаемо мёртв" in v.get("detail", "")]
        if known:
            txt += "\nℹ️ известные неисправности (в списке ожидаемых, не оцениваются):\n" + "\n".join("• " + k for k in known[:8])
        if self.tg.send(txt, silent=not bad):
            meta["daily_sent"] = today


def load_cfg(path=CONF_PATH):
    return json.load(open(path))


def run_fast(cfg, mon, learn=False):
    role = cfg.get("role", "exit")
    res = []
    res += check_services(cfg)
    if role == "ru":
        res += check_listening(cfg) + check_canaries(cfg) + check_router_config(cfg, mon.state) + check_router_restarts(cfg, mon.state) + check_router_log(cfg)
        res += check_director(cfg, mon.state)
        res += check_backups(cfg)
    res += check_wireguard(cfg, mon.state)
    res += check_system(cfg) + check_certs(cfg)
    res += check_ports(cfg, mon.state, learn) + check_egress(cfg) + check_fingerprints(cfg, mon.state) + check_db_setting(cfg) + check_tcp_targets(cfg)
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["run", "nodes", "baseline", "status", "test-telegram"])
    ap.add_argument("--conf", default=CONF_PATH)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    cfg = load_cfg(a.conf)
    os.makedirs(STATE_DIR, exist_ok=True)
    import fcntl
    lock = open(os.path.join(STATE_DIR, "lock"), "w")
    t0 = time.time()
    while True:                                    # the fast and the node timers share one state file: serialize them (wait up to 150 s)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if time.time() - t0 > 150:
                print("vpn-watch: другой запуск занят дольше 150 с, пропуск", file=sys.stderr)
                return 0
            time.sleep(1)
    mon = Monitor(cfg)
    if a.cmd == "test-telegram":
        ok = mon.tg.send("тестовое сообщение vpn-watch: канал оповещений работает")
        print("отправлено" if ok else "НЕ отправлено (проверьте токен/чат и доступ к api.telegram.org)")
        return 0 if ok else 1
    if a.cmd == "status":
        for cid, v in sorted(mon.state["checks"].items()):
            print(f"{'OK  ' if v.get('ok') else 'FAIL'} {v.get('level', ''):<5} fails={v.get('fails', 0):<3} {cid:<34} {v.get('detail', '')}")
        return 0
    if a.cmd == "baseline":
        res = check_ports(cfg, mon.state, learn=True) + check_fingerprints(cfg, mon.state)
        for spec in cfg.get("fingerprints", []):
            mon.state["meta"].setdefault("fingerprints", {})[spec["name"]] = fingerprint_of(spec)
        mon.save()
        pb = mon.state["meta"].get("ports_baseline", {})
        print("эталон портов и отпечатков запомнен:", len(pb), "публичных портов,", len(mon.state["meta"].get("fingerprints", {})), "отпечатков")
        print("ПРОВЕРЬТЕ список глазами: всё, что здесь лишнее, потом считается «нормой» (новые публичные порты после этого вызовут оповещение).")
        for k, v in sorted(pb.items(), key=lambda kv: (kv[0].split("/")[0], int(kv[0].split("/")[1]))):
            print(f"  {k:<12} {v}")
        return 0
    if a.cmd == "nodes":
        res = probe_nodes(cfg, mon.state) + probe_wg_nodes(cfg)
        th = cfg.get("thresholds", {})
        alerts, recov = mon.apply(res, "nodes", crit_after=th.get("node_crit_runs", 3), warn_after=th.get("node_warn_runs", 3))
    else:
        res = run_fast(cfg, mon)
        alerts, recov = mon.apply(res, "fast")
    mon.notify(alerts, recov)
    mon.daily()
    mon.save()
    if not a.quiet:
        bad = [r for r in res if not r.ok and r.level != INFO]
        print(f"vpn-watch {a.cmd}: проверок {len(res)}, не в порядке {len(bad)}; оповещений {len(alerts)}, восстановлений {len(recov)}")
        for r in bad:
            print(f"  {r.level.upper():<4} {r.detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
