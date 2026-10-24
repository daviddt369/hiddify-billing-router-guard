"""What changed between two xray-router configs - and does the owner's own change explain it? Pure functions (no I/O), used by hutils/multiloc_full.py before the running
config is replaced.

Why: the first full cycle that ever ran to the end on production (2026-10-13) rebuilt the router config from ALL layers and gave one that differed from the working one in
more than the owner's edit (no RU IP ranges, a DNS section with a dead tunnel address, another probe interval ...) - and it went live at once. Now the finished config is
compared with the running one; the differences the change does not explain are shown to the owner and NOTHING is replaced until he confirms.

A difference is "explained" when one of the plan parts that changed (rules / upstreams / settings / ui_files, plus `countries`) can produce it (EXPLAINED_BY)."""
from __future__ import annotations

import collections
import hashlib
import ipaddress
import json
from typing import Any

# which plan parts can explain which kind of difference
EXPLAINED_BY: dict[str, set[str]] = {
    "rules.custom": {"rules"},
    "rules.entries": {"rules", "settings", "ui_files", "upstreams", "countries"},       # plain `domain:x -> target` / `ip -> target` rules (own rules of any policy, the UI block, DNS addresses, per-slot copies)
    "rules.other": {"settings", "ui_files", "upstreams", "countries"},
    "dns": {"ui_files", "settings"},
    "dns.domains": {"rules", "ui_files", "settings"},       # the servers' domain lists are collected from the domain rules by the split-DNS layer
    "observatory": {"upstreams", "settings"},
    "outbounds": {"upstreams", "settings", "countries"},
    "balancers": {"upstreams", "countries", "settings"},
    "inbounds": {"countries", "settings"},
    "other": {"settings"},
}
_NOT_CUSTOM_PREFIXES = ("regexp:", "geoip:", "geosite:", "ext:", "keyword:")


def _canon(x: Any) -> str:
    return json.dumps(x, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _list(rule: dict, key: str) -> list:
    v = rule.get(key)
    return v if isinstance(v, list) else []


def is_custom(rule: dict) -> bool:
    """one entry list of the owner's custom rules (plain domain: / full: / ip / cidr entries, outbound direct-ru and nothing else in the rule)"""
    if rule.get("outboundTag") != "direct-ru" or (set(rule) - {"type", "domain", "ip", "outboundTag"}):
        return False
    if ("domain" in rule) == ("ip" in rule):
        return False
    ents = _list(rule, "domain") + _list(rule, "ip")
    return bool(ents) and not any(str(e).startswith(_NOT_CUSTOM_PREFIXES) for e in ents)


def _is_entry_rule(rule: dict) -> bool:
    """a rule that is only plain entries (domain: / full: / ip / cidr) and a target (+ the entrance it belongs to) - what a custom rule of ANY policy turns into"""
    if not isinstance(rule, dict) or (set(rule) - {"type", "domain", "ip", "outboundTag", "balancerTag", "inboundTag"}):
        return False
    if ("domain" in rule) == ("ip" in rule) or not (rule.get("outboundTag") or rule.get("balancerTag")):
        return False
    ents = _list(rule, "domain") + _list(rule, "ip")
    return bool(ents) and not any(str(e).startswith(_NOT_CUSTOM_PREFIXES) for e in ents)


def _label(rule: dict) -> str:
    """a short human name of a (not custom) routing rule"""
    doms, ips = [str(x) for x in _list(rule, "domain")], [str(x) for x in _list(rule, "ip")]
    if rule.get("outboundTag") == "block" and doms:
        return "блокировка доменов"
    if ips and all(i.startswith("geoip:") for i in ips):
        return "правило geoip"
    if doms and any(d.startswith("regexp:") for d in doms):
        return "маски доменов (regexp)"
    if ips and not doms and len(ips) <= 8 and all("/" not in i for i in ips):
        return "адреса DNS-серверов"
    if "inboundTag" in rule and not doms and not ips:
        return "правила входов (страны)"
    if ("network" in rule or "port" in rule) and not doms and not ips:
        return "правило по умолчанию"
    return "прочие правила"


def summarize(cfg: dict) -> dict:
    routing = cfg.get("routing") or {}
    rules = [r for r in (routing.get("rules") or []) if isinstance(r, dict)]
    from hiddifypanel.hutils import router_compact
    custom = [r for r in rules if is_custom(r)]
    other = [r for r in rules if not is_custom(r)]
    dom = [r for r in custom if "domain" in r]
    ipr = [r for r in custom if "ip" in r]
    rest = {k: v for k, v in cfg.items() if k not in ("routing", "dns", "observatory", "burstObservatory", "outbounds", "inbounds")}
    rest["routing.domainStrategy"] = routing.get("domainStrategy")
    rest["routing.other"] = {k: v for k, v in routing.items() if k not in ("rules", "balancers", "domainStrategy")}
    return {
        "custom": {"domain_rules": len(dom), "domain_entries": sum(len(_list(r, "domain")) for r in dom), "ip_rules": len(ipr), "ip_entries": sum(len(_list(r, "ip")) for r in ipr),
                   "hash": hashlib.sha256(_canon(router_compact.flat_entries(custom)).encode()).hexdigest()},      # by the ENTRIES in order: a compacted and a plain list of the same rules are equal
        "other": other,
        "dns": cfg.get("dns"),
        "observatory": cfg.get("observatory") if cfg.get("observatory") is not None else cfg.get("burstObservatory"),
        "outbounds": {str(o.get("tag")): _canon(o) for o in (cfg.get("outbounds") or []) if isinstance(o, dict)},
        "balancers": {str(b.get("tag")): _canon(b) for b in (routing.get("balancers") or []) if isinstance(b, dict)},
        "inbounds": {str(i.get("tag")): _canon(i) for i in (cfg.get("inbounds") or []) if isinstance(i, dict)},
        "rest": rest,
    }


def _dns_addresses(dns: Any) -> list[str]:
    out = []
    for s in (dns or {}).get("servers", []) if isinstance(dns, dict) else []:
        out.append(str(s if isinstance(s, str) else s.get("address")))
    return out


def _interval(obs: Any) -> str | None:
    if not isinstance(obs, dict):
        return None
    return str(obs.get("probeInterval") or (obs.get("pingConfig") or {}).get("interval") or "") or None


def _without_domains(dns: Any) -> str:
    """the DNS section with the per-server `domains` lists cut out (canonical text)"""
    if not isinstance(dns, dict):
        return _canon(dns)
    c = dict(dns)
    c["servers"] = [({k: v for k, v in s.items() if k != "domains"} if isinstance(s, dict) else s) for s in dns.get("servers", [])]
    return _canon(c)


def _tags(d: dict) -> str:
    return ", ".join(sorted(d)[:8]) + (" …" if len(d) > 8 else "")


def compare(old: dict, new: dict) -> list[dict]:
    """[{"key": category, "title": ..., "lines": [Russian sentences], "parts": [plan parts that could explain it]}] - empty when nothing differs"""
    a, b = summarize(old), summarize(new)
    out: list[dict] = []

    def add(key: str, title: str, lines: list[str]) -> None:
        out.append({"key": key, "title": title, "lines": lines, "parts": sorted(EXPLAINED_BY[key])})

    keep = ("domain_entries", "ip_entries", "hash")                                       # not how many RULES they are grouped into
    ca, cb = {k: a["custom"][k] for k in keep}, {k: b["custom"][k] for k in keep}
    if ca != cb:
        if (ca["domain_entries"], ca["ip_entries"]) != (cb["domain_entries"], cb["ip_entries"]):
            add("rules.custom", "Правила «напрямую (РФ)»", [f"домены: {ca['domain_entries']} → {cb['domain_entries']}; IP и подсети: {ca['ip_entries']} → {cb['ip_entries']}"])
        else:
            add("rules.custom", "Правила «напрямую (РФ)»", ["то же количество, но изменилось содержимое или порядок"])

    for key, title, pick in (("rules.entries", "Правила по адресам и доменам (свои, блокировка, DNS)", _is_entry_rule), ("rules.other", "Прочие правила маршрутизации", lambda r: not _is_entry_rule(r))):
        la, lb = [r for r in a["other"] if pick(r)], [r for r in b["other"] if pick(r)]
        oa = collections.Counter(_canon(r) for r in la)
        ob = collections.Counter(_canon(r) for r in lb)
        if oa != ob:
            lab = {_canon(r): _label(r) for r in la + lb}
            added = collections.Counter(lab[k] for k in (ob - oa).elements())
            removed = collections.Counter(lab[k] for k in (oa - ob).elements())
            fmt = lambda c: ", ".join(f"{n} × {name}" for name, n in c.most_common())
            lines = []
            if added:
                lines.append(f"добавлено правил: {sum(added.values())} ({fmt(added)})")
            if removed:
                lines.append(f"убрано правил: {sum(removed.values())} ({fmt(removed)})")
            add(key, title, lines)
        elif [_canon(r) for r in la] != [_canon(r) for r in lb]:
            add(key, title, ["тот же набор, но изменился порядок"])

    if _canon(a["dns"]) != _canon(b["dns"]):
        da, db_ = _dns_addresses(a["dns"]), _dns_addresses(b["dns"])
        if not a["dns"] and b["dns"]:
            add("dns", "Секция DNS", [f"сейчас не задана, станет {len(db_)} сервера(ов): {', '.join(db_)}"])
        elif a["dns"] and not b["dns"]:
            add("dns", "Секция DNS", [f"сейчас {len(da)} сервера(ов): {', '.join(da)}; будет убрана"])
        elif da != db_:
            add("dns", "Секция DNS", [f"серверы: {', '.join(da)} → {', '.join(db_)}"])
        elif _without_domains(a["dns"]) == _without_domains(b["dns"]):
            add("dns.domains", "Секция DNS", ["изменился список российских доменов у DNS-серверов (он собирается из правил «напрямую»)"])
        else:
            add("dns", "Секция DNS", ["изменились параметры запросов"])

    if _canon(a["observatory"]) != _canon(b["observatory"]):
        lines = []
        ia, ib = _interval(a["observatory"]), _interval(b["observatory"])
        if ia != ib:
            lines.append(f"интервал проверки узлов: {ia or 'не задан'} → {ib or 'не задан'}")
        sa = (a["observatory"] or {}).get("subjectSelector") if isinstance(a["observatory"], dict) else None
        sb = (b["observatory"] or {}).get("subjectSelector") if isinstance(b["observatory"], dict) else None
        if sa != sb:
            lines.append("изменился список проверяемых узлов")
        add("observatory", "Проверка узлов (observatory)", lines or ["изменились параметры проверки"])

    for key, title in (("outbounds", "Исходящие подключения"), ("balancers", "Балансировщики"), ("inbounds", "Входы router")):
        da, db_ = a[key], b[key]
        if da != db_:
            gone, came = sorted(set(da) - set(db_)), sorted(set(db_) - set(da))
            changed = sorted(t for t in set(da) & set(db_) if da[t] != db_[t])
            lines = []
            if came:
                lines.append("добавлено: " + ", ".join(came[:10]))
            if gone:
                lines.append("убрано: " + ", ".join(gone[:10]))
            if changed:
                lines.append("изменено: " + ", ".join(changed[:10]))
            add(key, title, lines)

    if _canon(a["rest"]) != _canon(b["rest"]):
        keys = sorted(k for k in set(a["rest"]) | set(b["rest"]) if _canon(a["rest"].get(k)) != _canon(b["rest"].get(k)))
        add("other", "Прочие параметры конфигурации", ["изменено: " + ", ".join(keys[:10])])
    return out


def warnings_for(new: dict) -> list[str]:
    """things in the NEW config that are likely to hurt, whatever changed"""
    out = []
    for addr in _dns_addresses(new.get("dns")):
        host = addr.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            continue
        if ip.is_private:
            out.append(f"DNS-сервер {host} - адрес внутри частной сети / туннеля: через другие узлы он недоступен, и каждый новый сайт будет ждать ≈4 с (если узел с этим туннелем не работает, так будет всегда)")
    return out


def review(old: dict, new: dict, expected: set[str] | None) -> dict:
    """{"diffs", "explained", "unexplained", "warnings"}: a diff is explained when one of the changed plan parts (`expected`) can produce it"""
    exp = set(expected or ())
    diffs = compare(old, new)
    explained = [d for d in diffs if exp & set(EXPLAINED_BY[d["key"]])]
    unexplained = [d for d in diffs if d not in explained]
    return {"diffs": diffs, "explained": explained, "unexplained": unexplained, "warnings": warnings_for(new)}
