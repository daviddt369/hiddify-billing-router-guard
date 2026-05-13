import ipaddress
import re
from dataclasses import asdict, dataclass
from typing import Any

from hiddifypanel.models import ConfigEnum
from hiddifypanel.models.commercial_routing_custom_rule import CommercialRoutingCustomRule
# Must be imported here so SQLAlchemy metadata includes commercial_routing_rule_source
# before any flush — CommercialRoutingCustomRule.source_id has a FK to it, and
# commercial_routing.py is loaded at BusinessAdmin module import time (before
# RoutingRuleSourceAdmin.py), so this is the earliest safe import point.
from hiddifypanel.models.commercial_routing_rule_source import CommercialRoutingRuleSource  # noqa: F401

PREFIX_MAP = {
    "domain": "domain_exact",
    "suffix": "domain_suffix",
    "wildcard": "domain_wildcard",
    "regex": "domain_regex",
    "ip": "ip",
    "cidr": "cidr",
    "cidr6": "cidr",
}


@dataclass
class BulkParseError:
    line_no: int
    raw: str
    error: str


@dataclass
class RouteSimulationResult:
    input_value: str
    normalized_input: str
    matched_rule: str | None
    source: str
    outbound: str
    note: str | None = None


# Stage A1 runtime contract:
# - DB stores custom RU rules, builtin RU suffixes and router-core flags.
# - BusinessAdmin writes routing UI state to the primary panel path and mirrors the
#   same JSON to the legacy /etc path.
# - Runtime apply still reads the legacy mirror intentionally. Stage A3 can switch
#   apply-time reads to the primary path after migration is complete.
COMMERCIAL_ROUTING_RUNTIME_CONFIG_PATH = "/etc/xray-router/config.json"
COMMERCIAL_ROUTING_UI_PRIMARY_PATH = "/opt/hiddify-manager/hiddify-panel/var/commercial-routing-ui.json"
COMMERCIAL_ROUTING_UI_LEGACY_PATH = "/etc/xray-router/commercial-routing-ui.json"


def commercial_routing_ui_primary_path() -> str:
    return COMMERCIAL_ROUTING_UI_PRIMARY_PATH


def commercial_routing_ui_legacy_path() -> str:
    return COMMERCIAL_ROUTING_UI_LEGACY_PATH


def commercial_routing_ui_read_paths() -> tuple[str, str]:
    return (COMMERCIAL_ROUTING_UI_PRIMARY_PATH, COMMERCIAL_ROUTING_UI_LEGACY_PATH)


def commercial_routing_runtime_ui_path() -> str:
    # Stage A3 switches runtime reads to the primary panel path while keeping the
    # legacy /etc mirror as a compatibility fallback.
    return COMMERCIAL_ROUTING_UI_PRIMARY_PATH



def _idna_host(host: str) -> str:
    labels = [p.encode("idna").decode("ascii") for p in host.split(".") if p]
    return ".".join(labels)


def normalize_domain_rule(rule_type: str, value: str) -> str:
    v = value.strip().lower()

    if rule_type == "domain_regex":
        re.compile(v)
        return v

    if rule_type == "domain_wildcard":
        if not v.startswith("*."):
            raise ValueError("wildcard rule must start with '*.'")
        v = v[2:]

    if rule_type == "domain_suffix" and v.startswith("."):
        v = v[1:]

    if not v:
        raise ValueError("empty domain value")

    return _idna_host(v)


def normalize_ip_rule(rule_type: str, value: str) -> str:
    v = value.strip().lower()
    if rule_type == "ip":
        return str(ipaddress.ip_address(v))
    if rule_type == "cidr":
        return str(ipaddress.ip_network(v, strict=False))
    raise ValueError("unsupported ip rule")


def validate_custom_rule(rule_type: str, value: str) -> tuple[bool, str | None, str | None]:
    if rule_type not in set(PREFIX_MAP.values()):
        return False, None, "unsupported rule type"
    try:
        if rule_type.startswith("domain_"):
            normalized = normalize_domain_rule(rule_type, value)
        else:
            normalized = normalize_ip_rule(rule_type, value)
    except Exception as exc:
        return False, None, str(exc)
    return True, normalized, None


def parse_bulk_rules(text: str) -> tuple[list[dict[str, Any]], list[BulkParseError]]:
    rules = []
    errors = []
    for idx, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            errors.append(BulkParseError(idx, raw, "missing ':' separator"))
            continue
        prefix, value = line.split(":", 1)
        prefix = prefix.strip().lower()
        value = value.strip()
        if prefix not in PREFIX_MAP:
            errors.append(BulkParseError(idx, raw, f"unknown prefix '{prefix}'"))
            continue
        rule_type = PREFIX_MAP[prefix]
        ok, normalized, err = validate_custom_rule(rule_type, value)
        if not ok:
            errors.append(BulkParseError(idx, raw, err or "invalid rule"))
            continue
        rules.append({
            "rule_type": rule_type,
            "value": value,
            "normalized_value": normalized,
            "enabled": True,
            "outbound_policy": "direct_ru",
        })
    return rules, errors


def parse_builtin_suffixes(raw_suffixes: str) -> list[str]:
    out = []
    seen = set()
    for raw in (raw_suffixes or "").split(","):
        item = raw.strip().lower()
        if not item:
            continue
        if item.startswith("."):
            item = item[1:]
        item = _idna_host(item)
        if item not in seen:
            out.append(item)
            seen.add(item)
    return out


def suffix_to_xray_tld_regex(suffix: str) -> str:
    return f"regexp:.*\\.{re.escape(suffix)}$"


def load_enabled_custom_rules() -> list[dict[str, Any]]:
    return [
        {
            "id": r.id,
            "rule_type": r.rule_type,
            "value": r.value,
            "normalized_value": r.normalized_value,
            "outbound_policy": r.outbound_policy,
            "enabled": r.enabled,
            "comment": r.comment,
        }
        for r in CommercialRoutingCustomRule.query.filter_by(enabled=True).order_by(CommercialRoutingCustomRule.id.asc()).all()
    ]




def _cfg(hconfigs: dict[str, Any], key: ConfigEnum, default: Any = None) -> Any:
    return hconfigs.get(key, default)




def custom_rules_to_bulk_text(custom_rules: list[dict[str, Any]]) -> str:
    prefix_map = {
        "domain_exact": "domain",
        "domain_suffix": "suffix",
        "domain_wildcard": "wildcard",
        "domain_regex": "regex",
        "ip": "ip",
        "cidr": "cidr",
    }

    lines: list[str] = []
    seen: set[tuple[str, str]] = set()

    for rule in custom_rules or []:
        # Only include direct_ru rules in bulk text.
        # Rules with to_upstream/block policy are managed exclusively via
        # Sources (commercial_routing_rule_source) and must not appear here:
        # BusinessAdmin.save() deletes all rules and recreates from this text,
        # so showing non-direct_ru rules would corrupt their outbound_policy.
        policy = str(rule.get("outbound_policy") or "direct_ru").strip()
        if policy != "direct_ru":
            continue

        rule_type = str(rule.get("rule_type") or "").strip()
        normalized_value = str(rule.get("normalized_value") or "").strip()

        if not rule_type or not normalized_value:
            continue

        key = (rule_type, normalized_value)
        if key in seen:
            continue
        seen.add(key)

        prefix = prefix_map.get(rule_type)
        if not prefix:
            continue

        lines.append(f"{prefix}:{normalized_value}")

    return "\n".join(lines)

def build_preview(hconfigs: dict[str, Any], custom_rules: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "layer1_enabled": bool(_cfg(hconfigs, ConfigEnum.commercial_routing_enable)),
        "router_core_type": _cfg(hconfigs, ConfigEnum.commercial_router_core_type, "xray"),
        "router_target": COMMERCIAL_ROUTING_RUNTIME_CONFIG_PATH,
        "custom_rules_total": len(custom_rules),
        "builtin_ru_suffixes": parse_builtin_suffixes(_cfg(hconfigs, ConfigEnum.commercial_ru_domain_suffixes, "")),
        "geoip_enabled": bool(_cfg(hconfigs, ConfigEnum.commercial_ru_geoip_enabled)),
        # apply result is shown via UI flashes from BusinessAdmin.post
        "apply_required": False,
        "apply_notice": "",
    }


def simulate_route_match(input_value: str, hconfigs: dict[str, Any], custom_rules: list[dict[str, Any]]) -> RouteSimulationResult:
    normalized = input_value.strip().lower()
    try:
        ip = str(ipaddress.ip_address(normalized))
        for rule in custom_rules:
            if not rule.get("enabled"):
                continue
            if rule["rule_type"] == "ip" and rule["normalized_value"] == ip:
                return RouteSimulationResult(input_value, ip, ip, "custom_ru_ip", "direct-ru")
            if rule["rule_type"] == "cidr" and ipaddress.ip_address(ip) in ipaddress.ip_network(rule["normalized_value"], strict=False):
                return RouteSimulationResult(input_value, ip, rule["normalized_value"], "custom_ru_ip", "direct-ru")
        return RouteSimulationResult(
            input_value, ip, None, "geoip_runtime_not_simulated", "to-de",
            "GeoIP match is not simulated in UI; requires runtime router test."
        )
    except ValueError:
        pass

    host = _idna_host(normalized.lstrip("."))
    for rule in custom_rules:
        if not rule.get("enabled"):
            continue
        rt = rule["rule_type"]
        nv = rule["normalized_value"]
        if rt == "domain_exact" and host == nv:
            return RouteSimulationResult(input_value, host, nv, "custom_ru_domain", "direct-ru")
        if rt in ("domain_suffix", "domain_wildcard") and (host == nv or host.endswith("." + nv)):
            return RouteSimulationResult(input_value, host, nv, "custom_ru_domain", "direct-ru")
        if rt == "domain_regex" and re.search(nv, host):
            return RouteSimulationResult(input_value, host, nv, "custom_ru_domain", "direct-ru")

    for suffix in parse_builtin_suffixes(_cfg(hconfigs, ConfigEnum.commercial_ru_domain_suffixes, "")):
        if host == suffix or host.endswith("." + suffix):
            return RouteSimulationResult(input_value, host, suffix, "builtin_ru_suffix", "direct-ru")

    return RouteSimulationResult(input_value, host, None, "default_global", "to-de")


@dataclass
class RouterApplyResult:
    target_path: str
    service_name: str
    backup_path: str | None
    xray_binary: str
    custom_rules_total: int


def _router_apply_xray_binary() -> str:
    import shutil
    return shutil.which("xray") or "/usr/bin/xray"


def _write_router_json(path, data: dict[str, Any]) -> None:
    import json
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")




def _commercial_router_get_ru_suffixes() -> list[str]:
    import subprocess

    fallback = [".ru", ".su", ".xn--p1ai"]

    try:
        result = subprocess.run(
            [
                "mysql",
                "-N",
                "-B",
                "hiddifypanel",
                "-e",
                "SELECT value FROM str_config WHERE `key`='commercial_ru_domain_suffixes' LIMIT 1;",
            ],
            text=True,
            capture_output=True,
            timeout=5,
        )

        value = (result.stdout or "").strip()
        if value:
            suffixes = []
            for item in value.replace("\n", ",").split(","):
                item = item.strip()
                if not item:
                    continue
                if not item.startswith("."):
                    item = "." + item
                suffixes.append(item)

            if suffixes:
                return suffixes
    except Exception:
        pass

    return fallback


def _commercial_router_suffix_to_regex(suffix: str) -> str:
    import re

    suffix = suffix.strip()
    if suffix.startswith("."):
        suffix = suffix[1:]

    return "regexp:.*\\." + re.escape(suffix) + "$"


def _is_final_global_rule(rule: dict) -> bool:
    """Detect the catch-all routing rule that sends traffic to upstream/global.
    Works with legacy to-de, single upstream-{id}, and balancer configs."""
    if not isinstance(rule, dict) or rule.get("type") != "field":
        return False
    if rule.get("domain") or rule.get("ip"):
        return False
    if rule.get("network") != "tcp,udp":
        return False
    outbound_tag = str(rule.get("outboundTag") or "")
    return (
        outbound_tag == "to-de"
        or outbound_tag.startswith("upstream-")
        or rule.get("balancerTag") == "upstream-balancer"
    )


def _upstream_routing_target(data: dict) -> dict:
    """Return routing rule target for upstream-bound traffic in the given config.
    Returns e.g. {"balancerTag": "upstream-balancer"}, {"outboundTag": "upstream-2"},
    or {"outboundTag": "to-de"} for legacy configs."""
    routing = data.get("routing", {}) if isinstance(data, dict) else {}
    if routing.get("balancers"):
        return {"balancerTag": "upstream-balancer"}
    outbounds = data.get("outbounds", []) if isinstance(data, dict) else []
    for ob in outbounds or []:
        tag = str((ob or {}).get("tag") or "")
        if tag.startswith("upstream-"):
            return {"outboundTag": tag}
    return {"outboundTag": "to-de"}


def _commercial_router_is_final_to_de(rule: dict) -> bool:
    return _is_final_global_rule(rule)


def _commercial_router_is_base_ru_rule(rule: dict) -> bool:
    if rule.get("type") != "field":
        return False

    if rule.get("outboundTag") != "direct-ru":
        return False

    domains = rule.get("domain") or []
    ips = rule.get("ip") or []

    domain_text = "\n".join(str(x) for x in domains).lower()
    ip_text = "\n".join(str(x) for x in ips).lower()

    if "geoip:ru" in ip_text:
        return True

    if "xn--p1ai" in domain_text:
        return True

    if "\\.ru$" in domain_text or ".ru$" in domain_text:
        return True

    if "\\.su$" in domain_text or ".su$" in domain_text:
        return True

    return False


def _commercial_router_ensure_base_direct_ru_rules(data: dict) -> dict:
    routing = data.setdefault("routing", {})
    rules = routing.setdefault("rules", [])

    rules = [
        rule for rule in rules
        if not _commercial_router_is_base_ru_rule(rule)
    ]

    suffixes = _commercial_router_get_ru_suffixes()
    domain_rule = {
        "type": "field",
        "domain": [_commercial_router_suffix_to_regex(suffix) for suffix in suffixes],
        "outboundTag": "direct-ru",
    }

    geoip_rule = {
        "type": "field",
        "ip": ["geoip:ru"],
        "outboundTag": "direct-ru",
    }

    insert_at = len(rules)
    for index, rule in enumerate(rules):
        if _commercial_router_is_final_to_de(rule):
            insert_at = index
            break

    rules[insert_at:insert_at] = [domain_rule, geoip_rule]
    routing["rules"] = rules
    data["routing"] = routing

    return data


def apply_router_core_config() -> RouterApplyResult:
    import datetime
    import os
    import shutil
    import subprocess
    from pathlib import Path

    from hiddifypanel.models import get_hconfigs
    from hiddifypanel.hutils.proxy import router_core

    hconfigs = get_hconfigs()
    custom_rules = load_enabled_custom_rules()
    upstreams = None
    try:
        from hiddifypanel.models.commercial_routing_upstream import CommercialRoutingUpstream
        upstreams = CommercialRoutingUpstream.get_active_ordered() or None
    except Exception:
        upstreams = None

    rendered = router_core.render_desired_config(hconfigs, custom_rules, upstreams=upstreams)

    target = Path(rendered.target_path)
    tmp = target.with_name(target.name + ".tmp.json")
    backup = None

    target.parent.mkdir(parents=True, exist_ok=True)

    _write_router_json(tmp, rendered.config)

    xray_bin = _router_apply_xray_binary()
    try:
        test = subprocess.run(
            [xray_bin, "run", "-test", "-config", str(tmp)],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        if tmp.exists():
            tmp.unlink()
        raise RuntimeError("xray run -test timed out after 30s (apply_router_core_config)")

    if test.returncode != 0:
        if tmp.exists():
            tmp.unlink()
        msg = (test.stderr or test.stdout or "xray config test failed").strip()
        raise RuntimeError(msg)

    if target.exists():
        backup = Path(str(target) + ".bak." + datetime.datetime.utcnow().strftime("%F_%H-%M-%S"))
        shutil.copy2(target, backup)

    os.replace(tmp, target)

    try:
        restart = subprocess.run(
            ["systemctl", "restart", rendered.service_name],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("systemctl restart %s timed out after 30s" % rendered.service_name)

    try:
        active = subprocess.run(
            ["systemctl", "is-active", rendered.service_name],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("systemctl is-active %s timed out after 30s" % rendered.service_name)

    if restart.returncode != 0 or active.stdout.strip() != "active":
        if backup and backup.exists():
            shutil.copy2(backup, target)
            subprocess.run(["systemctl", "restart", rendered.service_name], capture_output=True, text=True, timeout=30)
        msg = ((restart.stderr or "") + "\n" + (restart.stdout or "") + "\n" + (active.stderr or "") + "\n" + (active.stdout or "")).strip()
        raise RuntimeError("router-core restart failed, rollback attempted: " + msg)

    return RouterApplyResult(
        target_path=str(target),
        service_name=rendered.service_name,
        backup_path=str(backup) if backup else None,
        xray_binary=xray_bin,
        custom_rules_total=len(custom_rules),
    )


# BEGIN HIDDIFY COMMERCIAL ROUTING BASE RU POSTFIX

def _commercial_router_postfix_get_ru_suffixes():
    import subprocess

    fallback = [".ru", ".su", ".xn--p1ai"]

    try:
        result = subprocess.run(
            [
                "mysql",
                "-N",
                "-B",
                "hiddifypanel",
                "-e",
                "SELECT value FROM str_config WHERE `key`='commercial_ru_domain_suffixes' LIMIT 1;",
            ],
            text=True,
            capture_output=True,
            timeout=5,
        )

        value = (result.stdout or "").strip()
        if not value:
            return fallback

        suffixes = []
        for item in value.replace("\n", ",").split(","):
            item = item.strip()
            if not item:
                continue
            if not item.startswith("."):
                item = "." + item
            suffixes.append(item)

        return suffixes or fallback
    except Exception:
        return fallback


def _commercial_router_postfix_suffix_to_regex(suffix):
    import re

    suffix = str(suffix).strip()
    if suffix.startswith("."):
        suffix = suffix[1:]

    return "regexp:.*\\." + re.escape(suffix) + "$"


def _commercial_router_postfix_is_final_to_de(rule):
    return _is_final_global_rule(rule)


def _commercial_router_postfix_is_base_ru_rule(rule):
    if not isinstance(rule, dict):
        return False

    if rule.get("type") != "field":
        return False

    if rule.get("outboundTag") != "direct-ru":
        return False

    domains = rule.get("domain") or []
    ips = rule.get("ip") or []

    domain_text = "\n".join(str(x) for x in domains).lower()
    ip_text = "\n".join(str(x) for x in ips).lower()

    if "geoip:ru" in ip_text:
        return True

    if "xn--p1ai" in domain_text:
        return True

    if "\\.ru$" in domain_text or ".ru$" in domain_text:
        return True

    if "\\.su$" in domain_text or ".su$" in domain_text:
        return True

    return False


def _commercial_router_postfix_apply_base_ru_rules():
    import json
    import os
    import shutil
    import subprocess
    from datetime import datetime
    from pathlib import Path

    target = Path(COMMERCIAL_ROUTING_RUNTIME_CONFIG_PATH)
    if not target.exists():
        raise RuntimeError("Router config not found: /etc/xray-router/config.json")

    data = json.loads(target.read_text())

    routing = data.setdefault("routing", {})
    rules = routing.setdefault("rules", [])

    rules = [
        rule for rule in rules
        if not _commercial_router_postfix_is_base_ru_rule(rule)
    ]

    suffixes = _commercial_router_postfix_get_ru_suffixes()

    base_domain_rule = {
        "type": "field",
        "domain": [_commercial_router_postfix_suffix_to_regex(suffix) for suffix in suffixes],
        "outboundTag": "direct-ru",
    }

    base_geoip_rule = {
        "type": "field",
        "ip": ["geoip:ru"],
        "outboundTag": "direct-ru",
    }

    insert_at = len(rules)
    for index, rule in enumerate(rules):
        if _commercial_router_postfix_is_final_to_de(rule):
            insert_at = index
            break

    rules[insert_at:insert_at] = [base_domain_rule, base_geoip_rule]
    routing["rules"] = rules
    data["routing"] = routing

    data.setdefault("log", {})
    data["log"]["access"] = "none"
    data["log"]["loglevel"] = "warning"
    data["log"]["dnsLog"] = False

    backup_dir = Path("/root/xray-router-backups")
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / ("config.json." + datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + ".postfix-base-ru.bak")
    shutil.copy2(target, backup)

    tmp = target.with_name(target.name + ".tmp.json")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    xray_bin = shutil.which("xray") or "/usr/bin/xray"
    try:
        result = subprocess.run(
            [xray_bin, "run", "-test", "-config", str(tmp)],
            text=True,
            capture_output=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        try:
            tmp.unlink()
        except Exception:
            pass
        raise RuntimeError("xray run -test timed out after 30s (base-ru postfix)")

    if result.returncode != 0:
        try:
            tmp.unlink()
        except Exception:
            pass

        msg = (result.stdout or "") + "\n" + (result.stderr or "")
        raise RuntimeError("Generated router config validation failed:\n" + msg)

    os.replace(tmp, target)
    try:
        subprocess.run(["systemctl", "reset-failed", "xray-router"], check=False, timeout=10)
    except subprocess.TimeoutExpired:
        pass
    subprocess.run(["systemctl", "restart", "xray-router"], check=True, timeout=30)

    return str(backup)


if "_HIDDIFY_ORIGINAL_APPLY_ROUTER_CORE_CONFIG" not in globals():
    _HIDDIFY_ORIGINAL_APPLY_ROUTER_CORE_CONFIG = apply_router_core_config


def apply_router_core_config(*args, **kwargs):
    result = _HIDDIFY_ORIGINAL_APPLY_ROUTER_CORE_CONFIG(*args, **kwargs)
    backup = _commercial_router_postfix_apply_base_ru_rules()
    print("Postfixed base RU routing rules and restarted xray-router backup=" + backup)
    return result

# END HIDDIFY COMMERCIAL ROUTING BASE RU POSTFIX

# BEGIN HIDDIFY COMMERCIAL ROUTING PRESERVE TO-DE OUTBOUND

def _commercial_router_preserve_to_de_find(outbounds):
    for outbound in outbounds or []:
        if outbound.get("tag") == "to-de":
            return outbound
    return None


def _commercial_router_preserve_to_de_is_good(outbound):
    return bool(outbound and outbound.get("tag") == "to-de" and outbound.get("protocol") and outbound.get("protocol") != "blackhole")


def _commercial_router_preserve_to_de_patch_config(target_path, preserved_to_de):
    import json
    import os
    import subprocess
    import shutil
    from pathlib import Path

    target = Path(target_path)

    if not _commercial_router_preserve_to_de_is_good(preserved_to_de):
        return False

    if not target.exists():
        return False

    data = json.loads(target.read_text())
    outbounds = data.setdefault("outbounds", [])

    current_to_de = _commercial_router_preserve_to_de_find(outbounds)

    if _commercial_router_preserve_to_de_is_good(current_to_de):
        return False

    replaced = False

    for i, outbound in enumerate(outbounds):
        if outbound.get("tag") == "to-de":
            outbounds[i] = preserved_to_de
            replaced = True
            break

    if not replaced:
        outbounds.insert(0, preserved_to_de)

    tmp = target.with_name(target.name + ".preserve-to-de.tmp.json")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    xray_bin = shutil.which("xray") or "/usr/bin/xray"
    try:
        result = subprocess.run(
            [xray_bin, "run", "-test", "-config", str(tmp)],
            text=True,
            capture_output=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("xray run -test timed out after 30s (preserve-to-de postfix)")

    if result.returncode != 0:
        msg = (result.stdout or "") + "\n" + (result.stderr or "")
        raise RuntimeError("preserve to-de validation failed: " + msg)

    backup_dir = Path("/root/xray-router-backups")
    backup_dir.mkdir(parents=True, exist_ok=True)

    import datetime
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup = backup_dir / f"config.json.{stamp}.before-preserve-to-de.bak"

    try:
        backup.write_text(target.read_text())
    except Exception:
        pass

    os.replace(tmp, target)

    try:
        subprocess.run(["systemctl", "restart", "xray-router"], check=False, timeout=30)
    except subprocess.TimeoutExpired:
        pass

    print(f"Preserved good to-de outbound and restarted xray-router backup={backup}")

    return True


if "_commercial_router_preserve_to_de_original_apply_router_core_config" not in globals():
    _commercial_router_preserve_to_de_original_apply_router_core_config = apply_router_core_config


def apply_router_core_config(*args, **kwargs):
    import json
    from pathlib import Path

    target = Path(str(
        kwargs.get("target_path")
        or kwargs.get("target")
        or kwargs.get("path")
        or COMMERCIAL_ROUTING_RUNTIME_CONFIG_PATH
    ))

    preserved_to_de = None

    try:
        if target.exists():
            before = json.loads(target.read_text())
            old_to_de = _commercial_router_preserve_to_de_find(before.get("outbounds", []))
            if _commercial_router_preserve_to_de_is_good(old_to_de):
                preserved_to_de = old_to_de
    except Exception:
        preserved_to_de = None

    result = _commercial_router_preserve_to_de_original_apply_router_core_config(*args, **kwargs)

    try:
        _commercial_router_preserve_to_de_patch_config(target, preserved_to_de)
    except Exception as e:
        raise RuntimeError(f"commercial routing apply wrote invalid to-de outbound and preserve failed: {e}") from e

    return result

# END HIDDIFY COMMERCIAL ROUTING PRESERVE TO-DE OUTBOUND

# BEGIN HIDDIFY COMMERCIAL ROUTING EDITABLE CONFIG HELPERS

def _commercial_router_get_config_text(key, default):
    try:
        from hiddifypanel.models import get_hconfigs
        configs = get_hconfigs() or {}
        value = configs.get(key)
    except Exception:
        value = None

    value = "" if value is None else str(value)
    return value if value.strip() else default


def _commercial_router_split_config_tokens(value):
    import re
    out = []
    for line in str(value or "").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        for item in re.split(r"[,\s]+", line):
            item = item.strip()
            if item:
                out.append(item)
    return out


def _commercial_router_parse_dns_ip_list(key, default_list):
    import ipaddress

    default_text = "\n".join(default_list)
    raw = _commercial_router_get_config_text(key, default_text)

    result = []
    for item in _commercial_router_split_config_tokens(raw):
        item = item.strip()
        item = item.replace("tcp://", "").replace("udp://", "")
        item = item.split("/", 1)[0].strip()

        try:
            ipaddress.ip_address(item)
        except Exception:
            continue

        if item not in result:
            result.append(item)

    return result or list(default_list)


def _commercial_router_dns_tcp_address(ip):
    ip = str(ip).strip()
    if ip.startswith("tcp://"):
        return ip
    return "tcp://" + ip


def _commercial_router_get_block_domains():
    default_domains = [
        "gosuslugi.ru",
        "gslb.gosuslugi.ru",
        "gu-st.ru",
        "nalog.ru",
        "nalog.gov.ru",
    ]

    raw = _commercial_router_get_config_text(
        "commercial_blocked_domains",
        "\n".join(default_domains),
    )

    result = []
    for item in _commercial_router_split_config_tokens(raw):
        item = item.strip().lower()
        if not item:
            continue

        if "://" in item:
            item = item.split("://", 1)[1]
        item = item.split("/", 1)[0].strip()
        item = item.strip("*.")

        if not item:
            continue

        if item.startswith(("domain:", "regexp:", "geosite:", "full:", "keyword:")):
            normalized = item
        else:
            normalized = "domain:" + item

        if normalized not in result:
            result.append(normalized)

    return result or ["domain:" + d for d in default_domains]


def _commercial_router_get_block_domain_plain_set():
    out = set()
    for item in _commercial_router_get_block_domains():
        item = str(item).lower()
        if item.startswith("domain:"):
            item = item.replace("domain:", "", 1)
        out.add(item)
    return out

# END HIDDIFY COMMERCIAL ROUTING EDITABLE CONFIG HELPERS


# BEGIN HIDDIFY COMMERCIAL ROUTING UI JSON HELPERS

def _commercial_router_ui_json_value(key, default=""):
    try:
        import json
        from pathlib import Path

        for ui_path in commercial_routing_ui_read_paths():
            p = Path(ui_path)
            if not p.exists():
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            value = data.get(key, default)
            if value is None:
                continue
            value = str(value)
            if value.strip():
                return value
        return default
    except Exception:
        return default


def _commercial_router_ui_lines(key, default_lines):
    raw = _commercial_router_ui_json_value(key, "\n".join(default_lines))
    result = []
    seen = set()
    for line in str(raw).replace("\r", "\n").replace(",", "\n").splitlines():
        item = line.strip()
        if not item or item.startswith("#"):
            continue
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result or list(default_lines)

# END HIDDIFY COMMERCIAL ROUTING UI JSON HELPERS

# BEGIN HIDDIFY COMMERCIAL ROUTING SPLIT DNS POSTFIX

def _commercial_router_split_dns_is_final_to_de_rule(rule):
    return _is_final_global_rule(rule)


def _commercial_router_split_dns_same_ip_rule(rule, ips, outbound_tag):
    return (
        isinstance(rule, dict)
        and rule.get("type") == "field"
        and rule.get("outboundTag") == outbound_tag
        and sorted(str(x) for x in (rule.get("ip") or [])) == sorted(str(x) for x in ips)
        and not rule.get("domain")
    )


def _commercial_router_split_dns_collect_ru_domains(rules):
    ru_domains = []
    seen = set()

    for rule in rules or []:
        if not isinstance(rule, dict):
            continue

        if rule.get("outboundTag") != "direct-ru":
            continue

        for domain in rule.get("domain") or []:
            domain = str(domain).strip()
            if not domain:
                continue
            if domain in seen:
                continue
            seen.add(domain)
            ru_domains.append(domain)

    for domain in [
        "regexp:.*\\.ru$",
        "regexp:.*\\.su$",
        "regexp:.*\\.xn\\-\\-p1ai$",
    ]:
        if domain not in seen:
            seen.add(domain)
            ru_domains.append(domain)

    return ru_domains


def _commercial_router_split_dns_patch_config(target_path=COMMERCIAL_ROUTING_RUNTIME_CONFIG_PATH):
    import json
    import os
    import shutil
    import subprocess
    from datetime import datetime
    from pathlib import Path

    target = Path(str(target_path))

    if not target.exists():
        return False

    data = json.loads(target.read_text())

    routing = data.setdefault("routing", {})
    rules = routing.setdefault("rules", [])

    routing["domainStrategy"] = "IPIfNonMatch"

    ru_dns_ips = _commercial_router_ui_lines("commercial_direct_dns_servers", ["77.88.8.8", "77.88.8.1"])
    global_dns_ips = _commercial_router_ui_lines("commercial_proxy_dns_servers", ["1.1.1.1", "1.0.0.1", "8.8.8.8", "8.8.4.4"])

    def _is_upstream_dns_ip_rule(rule, ips):
        """Match a DNS routing rule for given IPs going to any upstream (to-de, upstream-*, balancerTag)."""
        if not isinstance(rule, dict) or rule.get("domain"):
            return False
        rule_ips = sorted(str(x) for x in (rule.get("ip") or []))
        if rule_ips != sorted(str(x) for x in ips):
            return False
        outbound = str(rule.get("outboundTag") or "")
        return (
            outbound == "to-de"
            or outbound.startswith("upstream-")
            or rule.get("balancerTag") == "upstream-balancer"
        )

    rules = [
        rule for rule in rules
        if not _commercial_router_split_dns_same_ip_rule(rule, ru_dns_ips, "direct-ru")
        and not _is_upstream_dns_ip_rule(rule, global_dns_ips)
    ]

    ru_domains = _commercial_router_split_dns_collect_ru_domains(rules)

    data["dns"] = {
        "queryStrategy": "UseIPv4",
        "servers": [
            {
                "address": ru_dns_ips[0],
                "port": 53,
                "domains": ru_domains,
                "skipFallback": True,
                "queryStrategy": "UseIPv4",
            },
            {
                "address": (ru_dns_ips[1] if len(ru_dns_ips) > 1 else ru_dns_ips[0]),
                "port": 53,
                "domains": ru_domains,
                "skipFallback": True,
                "queryStrategy": "UseIPv4",
            },
            {
                "address": _commercial_router_dns_tcp_address(global_dns_ips[0]),
                "queryStrategy": "UseIPv4",
            },
            {
                "address": _commercial_router_dns_tcp_address(global_dns_ips[1] if len(global_dns_ips) > 1 else global_dns_ips[0]),
                "queryStrategy": "UseIPv4",
            },
        ],
    }

    upstream_target = _upstream_routing_target(data)
    dns_route_rules = [
        {
            "type": "field",
            "ip": ru_dns_ips,
            "outboundTag": "direct-ru",
        },
        {
            "type": "field",
            "ip": global_dns_ips,
            **upstream_target,
        },
    ]

    insert_at = len(rules)
    for index, rule in enumerate(rules):
        if _commercial_router_split_dns_is_final_to_de_rule(rule):
            insert_at = index
            break

    rules[insert_at:insert_at] = dns_route_rules
    routing["rules"] = rules
    data["routing"] = routing

    for outbound in data.get("outbounds", []):
        if outbound.get("tag") == "direct-ru" and outbound.get("protocol") == "freedom":
            settings = outbound.setdefault("settings", {})
            settings["domainStrategy"] = "UseIPv4"

    tmp = target.with_name(target.name + ".split-dns.tmp.json")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    xray_bin = shutil.which("xray") or "/usr/bin/xray"
    try:
        result = subprocess.run(
            [xray_bin, "run", "-test", "-config", str(tmp)],
            text=True,
            capture_output=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        try:
            tmp.unlink()
        except Exception:
            pass
        raise RuntimeError("xray run -test timed out after 30s (split-dns postfix)")

    if result.returncode != 0:
        try:
            tmp.unlink()
        except Exception:
            pass

        msg = (result.stdout or "") + "\n" + (result.stderr or "")
        raise RuntimeError("split DNS config validation failed:\n" + msg)

    backup_dir = Path("/root/xray-router-backups")
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup = backup_dir / f"config.json.{stamp}.before-split-dns.bak"

    shutil.copy2(target, backup)
    os.replace(tmp, target)

    try:
        subprocess.run(["systemctl", "reset-failed", "xray-router"], check=False, timeout=10)
    except subprocess.TimeoutExpired:
        pass
    subprocess.run(["systemctl", "restart", "xray-router"], check=True, timeout=30)

    try:
        active = subprocess.run(
            ["systemctl", "is-active", "xray-router"],
            text=True,
            capture_output=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        shutil.copy2(backup, target)
        try:
            subprocess.run(["systemctl", "restart", "xray-router"], check=False, timeout=30)
        except subprocess.TimeoutExpired:
            pass
        raise RuntimeError("systemctl is-active xray-router timed out after 30s (split-dns postfix)")

    if active.stdout.strip() != "active":
        shutil.copy2(backup, target)
        try:
            subprocess.run(["systemctl", "restart", "xray-router"], check=False, timeout=30)
        except subprocess.TimeoutExpired:
            pass
        raise RuntimeError("xray-router is not active after split DNS patch, rollback attempted")

    print(
        "Applied split DNS postfix: "
        + f"ru_domains={len(ru_domains)} "
        + f"backup={backup}"
    )

    return True


if "_commercial_router_split_dns_original_apply_router_core_config" not in globals():
    _commercial_router_split_dns_original_apply_router_core_config = apply_router_core_config


def apply_router_core_config(*args, **kwargs):
    result = _commercial_router_split_dns_original_apply_router_core_config(*args, **kwargs)

    target = kwargs.get("target_path") or kwargs.get("target") or kwargs.get("path") or COMMERCIAL_ROUTING_RUNTIME_CONFIG_PATH
    _commercial_router_split_dns_patch_config(target)

    return result

# END HIDDIFY COMMERCIAL ROUTING SPLIT DNS POSTFIX

# BEGIN HIDDIFY COMMERCIAL ROUTING GOV BLOCK POSTFIX

def _commercial_router_gov_block_rule():
    return {
        "type": "field",
        "domain": _commercial_router_get_block_domains(),
        "outboundTag": "block",
    }


def _commercial_router_gov_block_is_same_rule(rule):
    if not isinstance(rule, dict):
        return False

    if rule.get("outboundTag") != "block":
        return False

    domains = [str(x).lower() for x in (rule.get("domain") or [])]
    text = "\n".join(domains)

    markers = (
        "gosuslugi.ru",
        "gslb.gosuslugi.ru",
        "gu-st.ru",
        "nalog.ru",
        "nalog.gov.ru",
    )

    return any(marker in text for marker in markers)


def _commercial_router_gov_block_patch_config(target_path=COMMERCIAL_ROUTING_RUNTIME_CONFIG_PATH):
    import json
    import os
    import shutil
    import subprocess
    from pathlib import Path
    from datetime import datetime

    target = Path(target_path)

    if not target.exists():
        return None

    data = json.loads(target.read_text())
    routing = data.setdefault("routing", {})
    rules = routing.setdefault("rules", [])

    # Remove old generated gov block copies, then insert a fresh one as rule #1.
    rules = [
        rule for rule in rules
        if not _commercial_router_gov_block_is_same_rule(rule)
    ]

    rules.insert(0, _commercial_router_gov_block_rule())
    routing["rules"] = rules
    data["routing"] = routing

    backup_dir = Path("/root/xray-router-backups")
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / ("config.json." + datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + ".before-gov-block.bak")
    shutil.copy2(target, backup)

    tmp = target.with_name(target.name + ".gov-block.tmp.json")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    xray_bin = shutil.which("xray") or "/usr/bin/xray"
    try:
        result = subprocess.run(
            [xray_bin, "run", "-test", "-config", str(tmp)],
            text=True,
            capture_output=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        try:
            tmp.unlink()
        except Exception:
            pass
        raise RuntimeError("xray run -test timed out after 30s (gov-block postfix)")

    if result.returncode != 0:
        try:
            tmp.unlink()
        except Exception:
            pass

        msg = (result.stdout or "") + "\n" + (result.stderr or "")
        raise RuntimeError("gov block config validation failed:\n" + msg)

    os.replace(tmp, target)
    try:
        subprocess.run(["systemctl", "reset-failed", "xray-router"], check=False, timeout=10)
    except subprocess.TimeoutExpired:
        pass
    subprocess.run(["systemctl", "restart", "xray-router"], check=True, timeout=30)

    return str(backup)


if "_commercial_router_gov_block_original_apply_router_core_config" not in globals():
    _commercial_router_gov_block_original_apply_router_core_config = apply_router_core_config


def apply_router_core_config(*args, **kwargs):
    result = _commercial_router_gov_block_original_apply_router_core_config(*args, **kwargs)
    backup = _commercial_router_gov_block_patch_config(COMMERCIAL_ROUTING_RUNTIME_CONFIG_PATH)
    print("Applied gov/tax blackhole postfix backup=" + str(backup))
    return result

# END HIDDIFY COMMERCIAL ROUTING GOV BLOCK POSTFIX

# BEGIN HIDDIFY JSON UI ROUTING APPLY FIX V2

def _hiddify_json_ui_routing_read_v2():
    import json
    from pathlib import Path

    last_exc = None
    for ui_path in commercial_routing_ui_read_paths():
        p = Path(ui_path)
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception as exc:
            last_exc = exc
            continue

    if last_exc:
        print("WARNING: cannot read commercial-routing-ui.json:", last_exc)
    return {}


def _hiddify_json_ui_parse_lines_v2(raw):
    text = "" if raw is None else str(raw)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace(",", "\n").replace(";", "\n")

    result = []
    for line in text.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "#" in line:
            line = line.split("#", 1)[0].strip()
        if line and line not in result:
            result.append(line)
    return result


def _hiddify_json_ui_parse_domains_v2(raw, defaults):
    result = []

    for item in _hiddify_json_ui_parse_lines_v2(raw):
        item = item.strip().lower()
        item = item.replace("https://", "").replace("http://", "")
        item = item.split("/", 1)[0].strip()

        if item.startswith("*."):
            item = item[2:]

        if item.startswith(("domain:", "full:", "regexp:", "geosite:")):
            token = item
        else:
            token = "domain:" + item

        if token not in result:
            result.append(token)

    if not result:
        for item in defaults:
            item = str(item).strip().lower().replace("domain:", "")
            token = "domain:" + item
            if token not in result:
                result.append(token)

    return result


def _hiddify_json_ui_parse_ips_v2(raw, defaults):
    import ipaddress

    result = []

    for item in _hiddify_json_ui_parse_lines_v2(raw):
        item = item.strip().lower()
        item = item.replace("tcp://", "").replace("udp://", "").replace("https://", "").replace("tls://", "")
        item = item.split("/", 1)[0].strip()

        try:
            ipaddress.ip_address(item)
        except Exception:
            continue

        if item not in result:
            result.append(item)

    if not result:
        result = list(defaults)

    return result


def _hiddify_json_ui_dns_tcp_addr_v2(ip):
    ip = str(ip).strip()
    if ip.startswith(("tcp://", "https://", "tls://")):
        return ip
    return "tcp://" + ip


def _hiddify_json_ui_norm_domain_v2(token):
    token = str(token or "").strip().lower()
    for prefix in ("domain:", "full:", "regexp:", "geosite:"):
        if token.startswith(prefix):
            token = token[len(prefix):]
    if token.startswith("*."):
        token = token[2:]
    return token


def _hiddify_json_ui_is_sensitive_block_rule_v2(rule, new_domains):
    if not isinstance(rule, dict):
        return False

    if rule.get("outboundTag") != "block":
        return False

    domains = rule.get("domain") or []
    if not isinstance(domains, list):
        return False

    watched = {
        "gosuslugi.ru",
        "gslb.gosuslugi.ru",
        "gu-st.ru",
        "nalog.ru",
        "nalog.gov.ru",
    }

    for d in new_domains:
        watched.add(_hiddify_json_ui_norm_domain_v2(d))

    for d in domains:
        nd = _hiddify_json_ui_norm_domain_v2(d)
        if nd in watched:
            return True
        if "gosuslugi" in nd or "nalog" in nd or nd == "gu-st.ru":
            return True

    return False


def _hiddify_json_ui_norm_dns_addr_v2(addr):
    addr = str(addr or "").strip().lower()
    for prefix in ("tcp://", "udp://", "https://", "tls://"):
        if addr.startswith(prefix):
            addr = addr[len(prefix):]
    return addr.split("/", 1)[0].strip()


def _hiddify_json_ui_is_dns_route_rule_v2(rule, dns_ips):
    if not isinstance(rule, dict):
        return False

    outbound = str(rule.get("outboundTag") or "")
    has_balancer = rule.get("balancerTag") == "upstream-balancer"
    is_valid_target = (
        outbound == "direct-ru"
        or outbound == "to-de"
        or outbound.startswith("upstream-")
        or has_balancer
    )
    if not is_valid_target:
        return False

    ips = rule.get("ip") or []
    if not isinstance(ips, list):
        return False

    for ip in ips:
        if str(ip).strip() in dns_ips:
            return True

    return False


def _hiddify_json_ui_apply_override_v2(config_path=COMMERCIAL_ROUTING_RUNTIME_CONFIG_PATH):
    import json
    import subprocess
    from datetime import datetime
    from pathlib import Path

    p = Path(config_path)
    if not p.exists():
        return

    ui = _hiddify_json_ui_routing_read_v2()

    default_blocked = [
        "gosuslugi.ru",
        "gslb.gosuslugi.ru",
        "gu-st.ru",
        "nalog.ru",
        "nalog.gov.ru",
    ]

    default_direct_dns = ["77.88.8.8", "77.88.8.1"]
    default_proxy_dns = ["1.1.1.1", "1.0.0.1", "8.8.8.8", "8.8.4.4"]

    blocked_domains = _hiddify_json_ui_parse_domains_v2(
        ui.get("commercial_blocked_domains"),
        default_blocked,
    )

    direct_dns = _hiddify_json_ui_parse_ips_v2(
        ui.get("commercial_direct_dns_servers"),
        default_direct_dns,
    )

    proxy_dns = _hiddify_json_ui_parse_ips_v2(
        ui.get("commercial_proxy_dns_servers"),
        default_proxy_dns,
    )

    data = json.loads(p.read_text(encoding="utf-8"))

    routing = data.setdefault("routing", {})
    rules = routing.setdefault("rules", [])

    dns = data.setdefault("dns", {})
    servers = dns.get("servers") or []

    ru_domains = []
    for s in servers:
        if isinstance(s, dict) and s.get("domains"):
            ru_domains = s.get("domains") or []
            break

    old_dns_ips = set(default_direct_dns + default_proxy_dns + direct_dns + proxy_dns)
    for s in servers:
        if isinstance(s, dict):
            a = _hiddify_json_ui_norm_dns_addr_v2(s.get("address"))
            if a:
                old_dns_ips.add(a)

    new_rules = []
    for r in rules:
        if _hiddify_json_ui_is_sensitive_block_rule_v2(r, blocked_domains):
            continue
        if _hiddify_json_ui_is_dns_route_rule_v2(r, old_dns_ips):
            continue
        new_rules.append(r)

    # Самое первое правило - дроп чувствительных доменов.
    new_rules.insert(0, {
        "type": "field",
        "domain": blocked_domains,
        "outboundTag": "block",
    })

    # DNS IP rules ставим сразу после block, чтобы DNS не уезжал не туда.
    new_rules.insert(1, {
        "type": "field",
        "ip": direct_dns,
        "outboundTag": "direct-ru",
    })

    new_rules.insert(2, {
        "type": "field",
        "ip": proxy_dns,
        **_upstream_routing_target(data),
    })

    routing["rules"] = new_rules
    data["routing"] = routing

    dns["queryStrategy"] = "UseIPv4"

    dns_servers = [
        {
            "address": direct_dns[0],
            "domains": ru_domains,
            "expectIPs": ["geoip:ru"],
            "skipFallback": True,
        },
        {
            "address": direct_dns[1] if len(direct_dns) > 1 else direct_dns[0],
            "domains": ru_domains,
            "expectIPs": ["geoip:ru"],
            "skipFallback": True,
        },
        {
            "address": _hiddify_json_ui_dns_tcp_addr_v2(proxy_dns[0]),
        },
        {
            "address": _hiddify_json_ui_dns_tcp_addr_v2(proxy_dns[1] if len(proxy_dns) > 1 else proxy_dns[0]),
        },
    ]

    dns["servers"] = dns_servers
    data["dns"] = dns

    backup_dir = Path("/root/xray-router-backups")
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / ("config.json." + datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + ".before-json-ui-routing-override-v2.bak")

    try:
        backup.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    except Exception:
        pass

    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(p)

    subprocess.run(["xray", "run", "-test", "-config", str(p)], check=True, timeout=30)
    try:
        subprocess.run(["systemctl", "reset-failed", "xray-router"], check=False, timeout=10)
    except subprocess.TimeoutExpired:
        pass
    try:
        proc = subprocess.run(
            ["systemctl", "restart", "xray-router"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("systemctl restart xray-router timed out after 30s (json-ui-override-v2)")
    if proc.returncode != 0:
        try:
            subprocess.run(["systemctl", "reset-failed", "xray-router"], check=False, timeout=10)
        except subprocess.TimeoutExpired:
            pass
        subprocess.run(["systemctl", "start", "xray-router"], check=True, timeout=30)

    print(
        "Applied JSON UI routing override V2: "
        + "blocked_domains="
        + str(len(blocked_domains))
        + " direct_dns="
        + ",".join(direct_dns)
        + " proxy_dns="
        + ",".join(proxy_dns)
        + " backup="
        + str(backup)
    )


try:
    _hiddify_json_ui_routing_previous_apply_router_core_config_v2
except NameError:
    _hiddify_json_ui_routing_previous_apply_router_core_config_v2 = apply_router_core_config

    def apply_router_core_config(*args, **kwargs):
        result = _hiddify_json_ui_routing_previous_apply_router_core_config_v2(*args, **kwargs)
        _hiddify_json_ui_apply_override_v2()
        return result

# END HIDDIFY JSON UI ROUTING APPLY FIX V2

# BEGIN HIDDIFY COMMERCIAL ROUTING STAGE A2 PIPELINE

def _commercial_router_resolve_target_path_from_kwargs(kwargs):
    return str(
        kwargs.get("target_path")
        or kwargs.get("target")
        or kwargs.get("path")
        or COMMERCIAL_ROUTING_RUNTIME_CONFIG_PATH
    )


def _commercial_router_capture_existing_good_to_de(target_path):
    import json
    from pathlib import Path

    target = Path(str(target_path))
    if not target.exists():
        return None

    try:
        before = json.loads(target.read_text())
    except Exception:
        return None

    old_to_de = _commercial_router_preserve_to_de_find(before.get("outbounds", []))
    if _commercial_router_preserve_to_de_is_good(old_to_de):
        return old_to_de
    return None


def _commercial_router_apply_pipeline(*args, **kwargs):
    target_path = _commercial_router_resolve_target_path_from_kwargs(kwargs)
    preserved_to_de = _commercial_router_capture_existing_good_to_de(target_path)

    result = _HIDDIFY_ORIGINAL_APPLY_ROUTER_CORE_CONFIG(*args, **kwargs)
    _commercial_router_postfix_apply_base_ru_rules()

    try:
        _commercial_router_preserve_to_de_patch_config(target_path, preserved_to_de)
    except Exception as e:
        raise RuntimeError(f"commercial routing apply wrote invalid to-de outbound and preserve failed: {e}") from e

    _commercial_router_split_dns_patch_config(target_path)
    _commercial_router_gov_block_patch_config(target_path)
    _hiddify_json_ui_apply_override_v2(target_path)

    return result


def apply_router_core_config(*args, **kwargs):
    return _commercial_router_apply_pipeline(*args, **kwargs)

# END HIDDIFY COMMERCIAL ROUTING STAGE A2 PIPELINE
