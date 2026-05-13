from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from hiddifypanel.hutils import commercial_routing


@dataclass
class RouterRenderResult:
    config: dict[str, Any]
    target_path: str
    service_name: str
    core_type: str


def _first(params: dict[str, list[str]], *names: str, default: str = "") -> str:
    for name in names:
        values = params.get(name)
        if values and values[0] is not None:
            return unquote(str(values[0]))
    return default


def _split_csv(value: str) -> list[str]:
    return [x.strip() for x in value.split(",") if x.strip()]


def _as_bool(value: str) -> bool:
    return str(value).lower() in {"1", "true", "yes", "on"}


def _uri_params(uri: str):
    parsed = urlparse(uri.strip())
    params = parse_qs(parsed.query, keep_blank_values=True)
    return parsed, params


def _port(parsed, default: int = 443) -> int:
    return int(parsed.port or default)


def _host(parsed) -> str:
    host = parsed.hostname
    if not host:
        raise ValueError("DE URI host is empty")
    return host


def _build_stream_settings(params: dict[str, list[str]], fallback_host: str, fallback_port: int) -> dict[str, Any]:
    network = _first(params, "type", "network", default="tcp").lower()
    if network == "h2":
        network = "http"

    security = _first(params, "security", default="tls" if fallback_port == 443 else "none").lower()

    stream: dict[str, Any] = {
        "network": network,
        "security": security,
    }

    sni = _first(params, "sni", "serverName", default=fallback_host)
    alpn = _split_csv(_first(params, "alpn", default=""))
    fp = _first(params, "fp", "fingerprint", default="")
    allow_insecure = _first(params, "allowInsecure", "allow_insecure", default="")

    if security == "tls":
        tls: dict[str, Any] = {}
        if sni:
            tls["serverName"] = sni
        if alpn:
            tls["alpn"] = alpn
        if fp:
            tls["fingerprint"] = fp
        if allow_insecure:
            tls["allowInsecure"] = _as_bool(allow_insecure)
        stream["tlsSettings"] = tls

    elif security == "reality":
        reality: dict[str, Any] = {}
        if sni:
            reality["serverName"] = sni
        if fp:
            reality["fingerprint"] = fp

        pbk = _first(params, "pbk", "publicKey", default="")
        sid = _first(params, "sid", "shortId", default="")
        spider_x = _first(params, "spx", "spiderX", default="")

        if pbk:
            reality["publicKey"] = pbk
        if sid:
            reality["shortId"] = sid
        if spider_x:
            reality["spiderX"] = spider_x

        stream["realitySettings"] = reality

    elif security == "none":
        pass

    else:
        raise ValueError(f"Unsupported security: {security}")

    path = _first(params, "path", default="")
    host_header = _first(params, "host", default="")
    service_name = _first(params, "serviceName", "service_name", default="")

    if network == "ws":
        ws: dict[str, Any] = {}
        if path:
            ws["path"] = path
        if host_header:
            ws["headers"] = {"Host": host_header}
        stream["wsSettings"] = ws

    elif network == "grpc":
        grpc: dict[str, Any] = {}
        if service_name:
            grpc["serviceName"] = service_name
        if host_header:
            grpc["authority"] = host_header
        stream["grpcSettings"] = grpc

    elif network == "httpupgrade":
        httpupgrade: dict[str, Any] = {}
        if path:
            httpupgrade["path"] = path
        if host_header:
            httpupgrade["host"] = host_header
        stream["httpupgradeSettings"] = httpupgrade

    elif network == "xhttp":
        xhttp: dict[str, Any] = {}
        if path:
            xhttp["path"] = path
        if host_header:
            xhttp["host"] = host_header
        stream["xhttpSettings"] = xhttp

    elif network in {"tcp", "http"}:
        pass

    else:
        raise ValueError(f"Unsupported network type: {network}")

    return stream


def _policy_to_outbound(policy: str, final_rule: dict[str, Any]) -> dict[str, Any]:
    """Map outbound_policy to the correct xray outbound target dict."""
    if policy == "direct_ru":
        return {"outboundTag": "direct-ru"}
    if policy == "block":
        return {"outboundTag": "block"}
    # to_upstream: use same target as the catch-all final rule (balancerTag or outboundTag)
    if "balancerTag" in final_rule:
        return {"balancerTag": final_rule["balancerTag"]}
    if "outboundTag" in final_rule:
        return {"outboundTag": final_rule["outboundTag"]}
    return {"outboundTag": "direct-ru"}


def _xray_custom_rule(rule: dict[str, Any], final_rule: dict[str, Any] | None = None) -> dict[str, Any]:
    rt = rule["rule_type"]
    nv = rule["normalized_value"]
    policy = str(rule.get("outbound_policy") or "direct_ru").strip()
    target = _policy_to_outbound(policy, final_rule or {"outboundTag": "direct-ru"})

    if rt == "domain_exact":
        return {"type": "field", "domain": [f"full:{nv}"], **target}
    if rt in ("domain_suffix", "domain_wildcard"):
        return {"type": "field", "domain": [f"domain:{nv}"], **target}
    if rt == "domain_regex":
        return {"type": "field", "domain": [f"regexp:{nv}"], **target}
    if rt in ("ip", "cidr"):
        return {"type": "field", "ip": [nv], **target}

    raise ValueError(f"Unsupported rule_type: {rt}")


def _xray_builtin_suffix_rules(hconfigs: dict[str, Any]) -> list[dict[str, Any]]:
    suffixes = commercial_routing.parse_builtin_suffixes(
        hconfigs.get("commercial_ru_domain_suffixes", "")
    )
    if not suffixes:
        return []

    return [{
        "type": "field",
        "domain": [commercial_routing.suffix_to_xray_tld_regex(s) for s in suffixes],
        "outboundTag": "direct-ru",
    }]


def _xray_geoip_rules(hconfigs: dict[str, Any]) -> list[dict[str, Any]]:
    if not bool(hconfigs.get("commercial_ru_geoip_enabled")):
        return []

    return [{
        "type": "field",
        "ip": ["geoip:ru"],
        "outboundTag": "direct-ru",
    }]


def _build_vless_outbound(uri: str) -> dict[str, Any]:
    if not uri or not uri.strip():
        raise ValueError("vless_uri is empty")

    parsed, params = _uri_params(uri)

    if parsed.scheme.lower() != "vless":
        raise ValueError("DE VLESS URI must start with vless://")

    user_id = unquote(parsed.username or "")
    if not user_id:
        raise ValueError("VLESS UUID is empty")

    host = _host(parsed)
    port = _port(parsed, 443)

    user: dict[str, Any] = {
        "id": user_id,
        "encryption": _first(params, "encryption", default="none"),
    }

    flow = _first(params, "flow", default="")
    if flow:
        user["flow"] = flow

    return {
        "tag": "to-de",
        "protocol": "vless",
        "settings": {
            "vnext": [
                {
                    "address": host,
                    "port": port,
                    "users": [user],
                }
            ]
        },
        "streamSettings": _build_stream_settings(params, host, port),
    }


def _build_trojan_outbound(uri: str) -> dict[str, Any]:
    if not uri or not uri.strip():
        raise ValueError("trojan_uri is empty")

    parsed, params = _uri_params(uri)

    if parsed.scheme.lower() != "trojan":
        raise ValueError("DE Trojan URI must start with trojan://")

    password = unquote(parsed.username or "")
    if not password:
        raise ValueError("Trojan password is empty")

    host = _host(parsed)
    port = _port(parsed, 443)

    return {
        "tag": "to-de",
        "protocol": "trojan",
        "settings": {
            "servers": [
                {
                    "address": host,
                    "port": port,
                    "password": password,
                }
            ]
        },
        "streamSettings": _build_stream_settings(params, host, port),
    }


def _read_secret_ref(ref: str) -> str:
    ref = (ref or "").strip()
    if not ref:
        raise ValueError("secret ref is empty")
    if ref.startswith("env:"):
        name = ref[4:].strip()
        value = os.environ.get(name, "")
        if not value:
            raise ValueError(f"Environment variable is empty: {name}")
        return value.strip()
    if ref.startswith("file:"):
        path = ref[5:].strip()
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    if os.path.exists(ref):
        with open(ref, "r", encoding="utf-8") as f:
            return f.read().strip()
    return ref


def _get_attr(obj: Any, key: str, default: Any = "") -> Any:
    """Read attribute from model object or dict."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _build_upstream_outbound(upstream: Any, tag: str) -> dict[str, Any]:
    """Build an xray outbound dict from a CommercialRoutingUpstream model object or dict.
    Uses the upstream's dedicated fields directly (not the hconfigs de_* mapping hack for WireGuard).
    """
    tunnel_type = (_get_attr(upstream, "tunnel_type") or "test_blackhole").strip().lower()

    if tunnel_type == "test_blackhole":
        return {"tag": tag, "protocol": "blackhole"}

    if tunnel_type == "vless":
        uri = (_get_attr(upstream, "vless_uri") or "").strip()
        outbound = _build_vless_outbound(uri)
        outbound["tag"] = tag
        return outbound

    if tunnel_type == "trojan":
        uri = (_get_attr(upstream, "trojan_uri") or "").strip()
        outbound = _build_trojan_outbound(uri)
        outbound["tag"] = tag
        return outbound

    if tunnel_type == "wireguard":
        endpoint = (_get_attr(upstream, "wg_endpoint") or "").strip()
        public_key = (_get_attr(upstream, "wg_public_key") or "").strip()
        private_key_ref = (_get_attr(upstream, "wg_private_key_ref") or "").strip()
        addresses_raw = (_get_attr(upstream, "wg_addresses") or "").strip()
        mtu = int(_get_attr(upstream, "wg_mtu") or 1280)

        if not endpoint:
            raise ValueError(f"wg_endpoint required for wireguard upstream '{tag}'")
        if not public_key:
            raise ValueError(f"wg_public_key required for wireguard upstream '{tag}'")

        private_key = _read_secret_ref(private_key_ref)
        addresses = _split_csv(addresses_raw) if addresses_raw else ["10.66.66.2/32"]

        return {
            "tag": tag,
            "protocol": "wireguard",
            "settings": {
                "secretKey": private_key,
                "address": addresses,
                "peers": [{
                    "publicKey": public_key,
                    "endpoint": endpoint,
                    "allowedIPs": ["0.0.0.0/0", "::/0"],
                }],
                "mtu": mtu,
            },
        }

    raise ValueError(f"Unsupported tunnel_type '{tunnel_type}' for upstream '{tag}'")


def render_xray_router_config(
    hconfigs: dict[str, Any],
    custom_rules: list[dict[str, Any]],
    upstreams: list[Any] | None = None,
) -> dict[str, Any]:
    """Render the xray-router config.

    Upstreams are managed via the CommercialRoutingUpstream DB table.
      - Each upstream gets outbound tag upstream-{id}.
      - test_blackhole upstreams are included as outbounds but excluded from balancer.
      - If >=2 real (non-blackhole) upstreams: add routing.balancers + observatory for auto-failover.
      - If exactly 1 real upstream: use outboundTag directly (no balancer).
      - If 0 real upstreams: all non-RU traffic is blocked (blackhole).
        Add external nodes via Routing → External Nodes to route traffic out.
    """
    # Build upstream outbound list first so we know the final_rule target
    # before rendering custom_rules (to_upstream policy needs to reference it).
    upstream_outbounds: list[dict[str, Any]] = []
    if upstreams:
        for up in upstreams:
            up_id = _get_attr(up, "id") or ""
            tag = f"upstream-{up_id}"
            try:
                outbound = _build_upstream_outbound(up, tag)
                upstream_outbounds.append(outbound)
            except Exception:
                pass

    real_outbounds = [ob for ob in upstream_outbounds if ob.get("protocol") != "blackhole"]
    use_balancer = len(real_outbounds) >= 2

    if real_outbounds:
        all_outbounds = upstream_outbounds
        if use_balancer:
            selector = [ob["tag"] for ob in real_outbounds]
            final_rule: dict[str, Any] = {"type": "field", "network": "tcp,udp", "balancerTag": "upstream-balancer"}
        else:
            selector = []
            final_rule = {"type": "field", "network": "tcp,udp", "outboundTag": real_outbounds[0]["tag"]}
    else:
        # No upstreams configured — block non-RU traffic until nodes are added.
        all_outbounds = []
        selector = []
        final_rule = {"type": "field", "network": "tcp,udp", "outboundTag": "block"}

    # Now render custom rules with correct outbound targets
    rules: list[dict[str, Any]] = []
    for rule in custom_rules:
        if rule.get("enabled"):
            rules.append(_xray_custom_rule(rule, final_rule))

    rules.extend(_xray_builtin_suffix_rules(hconfigs))
    rules.extend(_xray_geoip_rules(hconfigs))
    rules.append(final_rule)

    routing: dict[str, Any] = {
        "domainStrategy": "IPIfNonMatch",
        "rules": rules,
    }

    if use_balancer:
        tolerance_ms = int(hconfigs.get("commercial_router_probe_tolerance") or 0)
        balancer_strategy: dict = {"type": "leastPing"}
        if tolerance_ms > 0:
            balancer_strategy["settings"] = {"baselines": [f"{tolerance_ms}ms"]}
        routing["balancers"] = [{
            "tag": "upstream-balancer",
            "selector": selector,
            "strategy": balancer_strategy,
        }]

    config: dict[str, Any] = {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "tag": "from-hiddify",
                "listen": "127.0.0.1",
                "port": int(hconfigs.get("commercial_router_port", 20808)),
                "protocol": "socks",
                "settings": {
                    "auth": "noauth",
                    "udp": True,
                    "ip": "127.0.0.1",
                },
                "sniffing": {
                    "enabled": True,
                    "destOverride": ["http", "tls", "quic"],
                    "routeOnly": True,
                },
            }
        ],
        "outbounds": all_outbounds + [
            {"tag": "direct-ru", "protocol": "freedom"},
            {"tag": "block", "protocol": "blackhole"},
        ],
        "routing": routing,
    }

    if use_balancer:
        probe_url = (hconfigs.get("commercial_router_probe_url") or "https://1.1.1.1/").strip()
        probe_interval = (hconfigs.get("commercial_router_probe_interval") or "1m").strip()
        config["observatory"] = {
            "subjectSelector": selector,
            "probeUrl": probe_url,
            "probeInterval": probe_interval,
            "enableConcurrency": True,
        }

    return config


def render_desired_config(
    hconfigs: dict[str, Any],
    custom_rules: list[dict[str, Any]],
    upstreams: list[Any] | None = None,
) -> RouterRenderResult:
    hconfigs = {getattr(k, "name", k): v for k, v in (hconfigs or {}).items()}

    core_type = (hconfigs.get("commercial_router_core_type") or "xray").strip().lower()

    if core_type != "xray":
        raise NotImplementedError("singbox-router generator is not implemented in first stage")

    return RouterRenderResult(
        config=render_xray_router_config(hconfigs, custom_rules, upstreams=upstreams),
        target_path="/etc/xray-router/config.json",
        service_name="xray-router",
        core_type="xray",
    )
