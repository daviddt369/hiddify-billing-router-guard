"""
Parser for routing rule sources.

Supports domain and subnet families with multiple input formats:
  - plain_text: one rule per line, auto-detected prefixes
  - sing_box_source_json: sing-box ruleset JSON with domain_suffix/ip_cidr arrays
  - sing_box_binary_srs: not implemented in Stage 2E (returns stub error)
  - auto: detect format from URL/path extension or content sniff

Safety limits: 512 KB, 50 000 lines, 15 s fetch timeout.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

MAX_LINES = 50_000
MAX_BYTES = 512 * 1024          # 512 KB
FETCH_TIMEOUT = 15              # seconds
ALLOWED_LOCAL_DIR = "/opt/hiddify-manager/routing-lists/"

# Formats
FMT_AUTO = "auto"
FMT_PLAIN = "plain_text"
FMT_SINGBOX_JSON = "sing_box_source_json"
FMT_SRS = "sing_box_binary_srs"

_SRS_MAGIC = b"SRS\x00"        # first 4 bytes of .srs files


@dataclass
class ParsedRule:
    rule_type: str
    normalized_value: str
    raw: str


@dataclass
class ParseResult:
    rules: list[ParsedRule] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    duplicates: int = 0
    lines_total: int = 0
    lines_skipped: int = 0
    detected_format: str = FMT_PLAIN


# ──────────────────────────────────────────────────────────────────────────────
# Format detection
# ──────────────────────────────────────────────────────────────────────────────

def _ext_from_ref(ref: str) -> str:
    """Extract lowercase extension from URL or file path (without dot)."""
    path = urlparse(ref).path if ref.startswith(("http://", "https://")) else ref
    dot = path.rfind(".")
    return path[dot + 1:].lower() if dot != -1 else ""


def detect_format(source_format: str, ref: str, raw_bytes: bytes) -> str:
    """Resolve the effective format for the given source.

    Args:
        source_format: user-selected format ('auto', 'plain_text', …)
        ref:           URL or file path (used to check extension)
        raw_bytes:     first few hundred bytes of content (for sniffing)
    """
    if source_format != FMT_AUTO:
        return source_format

    ext = _ext_from_ref(ref)

    if ext == "srs" or raw_bytes[:4] == _SRS_MAGIC:
        return FMT_SRS

    if ext == "json":
        snippet = raw_bytes[:512].decode("utf-8", errors="replace").lstrip()
        if snippet.startswith("{") and ('"rules"' in snippet or '"version"' in snippet):
            return FMT_SINGBOX_JSON
        # Some JSON files are plain text in disguise — fall through to plain_text
        return FMT_PLAIN

    # .txt / .list / .raw / unknown → plain text
    return FMT_PLAIN


# ──────────────────────────────────────────────────────────────────────────────
# Domain normalisation
# ──────────────────────────────────────────────────────────────────────────────

def _normalize_domain(value: str) -> str:
    v = value.strip().lower().lstrip(".")
    try:
        parts = [p.encode("idna").decode("ascii") for p in v.split(".") if p]
        return ".".join(parts)
    except Exception:
        return v


def _looks_like_ip_or_cidr(value: str) -> bool:
    v = value.strip()
    try:
        ipaddress.ip_address(v)
        return True
    except ValueError:
        pass
    try:
        ipaddress.ip_network(v, strict=False)
        return True
    except ValueError:
        pass
    return False


def _looks_like_domain(value: str) -> bool:
    v = value.strip().lstrip(".")
    return bool(re.match(r"^[a-zA-Z0-9]([a-zA-Z0-9\-_]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-_]*[a-zA-Z0-9])?)+$", v))


# ──────────────────────────────────────────────────────────────────────────────
# Plain text line parsers
# ──────────────────────────────────────────────────────────────────────────────

def parse_domain_line(raw: str) -> ParsedRule:
    """Parse one line from a domain-family plain_text source.

    Prefixes:
      regexp:/regex:  → domain_regex
      full:           → domain_exact
      suffix:         → domain_suffix
      keyword:        → error (not supported)
      bare/.domain    → domain_suffix

    Cross-family: if line looks like IP/CIDR, raises ValueError suggesting subnet list.
    """
    line = raw.strip()

    if line.startswith("keyword:"):
        raise ValueError(
            "keyword-правила не поддерживаются, используйте regexp:"
        )

    if line.startswith("regexp:") or line.startswith("regex:"):
        prefix_len = 7 if line.startswith("regexp:") else 6
        pattern = line[prefix_len:].strip()
        if not pattern:
            raise ValueError("Пустой regexp-паттерн")
        re.compile(pattern)
        return ParsedRule("domain_regex", pattern, raw)

    if line.startswith("full:"):
        domain = _normalize_domain(line[5:])
        if not domain:
            raise ValueError("Пустой домен после full:")
        return ParsedRule("domain_exact", domain, raw)

    if line.startswith("suffix:"):
        domain = _normalize_domain(line[7:])
        if not domain:
            raise ValueError("Пустой домен после suffix:")
        return ParsedRule("domain_suffix", domain, raw)

    # bare / .domain
    bare = line.lstrip(".")
    if _looks_like_ip_or_cidr(bare):
        raise ValueError(
            f"Это похоже на IP или подсеть ({bare}). "
            "Добавьте в источник с семейством «Подсети/IP»."
        )

    domain = _normalize_domain(bare)
    if not domain or "." not in domain:
        raise ValueError(f"Не похоже на домен: {line[:60]}")
    return ParsedRule("domain_suffix", domain, raw)


def parse_subnet_line(raw: str) -> ParsedRule:
    """Parse one line from a subnet-family plain_text source.

    Formats: ip:X, cidr:X, X/mask → cidr, bare IP → ip.
    Cross-family: if line looks like a domain, raises ValueError.
    IPv6 is supported if Python ipaddress accepts it.
    """
    line = raw.strip()

    if line.startswith("ip:"):
        addr = line[3:].strip()
        ipaddress.ip_address(addr)
        return ParsedRule("ip", addr, raw)

    if line.startswith("cidr:"):
        net = line[5:].strip()
        ipaddress.ip_network(net, strict=False)
        return ParsedRule("cidr", net, raw)

    if "/" in line:
        ipaddress.ip_network(line, strict=False)
        return ParsedRule("cidr", line, raw)

    try:
        ipaddress.ip_address(line)
        return ParsedRule("ip", line, raw)
    except ValueError:
        pass

    if _looks_like_domain(line):
        raise ValueError(
            f"Это похоже на домен ({line[:40]}). "
            "Добавьте в источник с семейством «Домены»."
        )

    raise ValueError(f"Не распознан как IP или CIDR: {line[:60]}")


# ──────────────────────────────────────────────────────────────────────────────
# sing-box source JSON parser
# ──────────────────────────────────────────────────────────────────────────────

def _parse_singbox_json(text: str, rule_family: str) -> ParseResult:
    """Parse sing-box ruleset JSON (version 1).

    For domain family: extracts domain_suffix, domain, domain_regex arrays.
    For subnet family: extracts ip_cidr arrays.
    """
    import json

    result = ParseResult(detected_format=FMT_SINGBOX_JSON)
    seen: set[tuple[str, str]] = set()

    try:
        data = json.loads(text)
    except Exception as exc:
        result.errors.append(f"JSON parse error: {exc}")
        return result

    rules = data.get("rules", [])
    if not isinstance(rules, list):
        result.errors.append("'rules' is not an array in sing-box JSON")
        return result

    def _add(rule_type: str, value: str, raw: str) -> None:
        key = (rule_type, value)
        if key in seen:
            result.duplicates += 1
        else:
            seen.add(key)
            result.rules.append(ParsedRule(rule_type, value, raw))

    for idx, rule in enumerate(rules):
        if not isinstance(rule, dict):
            continue
        # Logical rules may have sub-rules
        sub_rules = rule.get("rules", [rule])
        for sub in (sub_rules if isinstance(sub_rules, list) else [rule]):
            if not isinstance(sub, dict):
                continue

            if rule_family == "domain":
                for suffix in sub.get("domain_suffix", []):
                    try:
                        _add("domain_suffix", _normalize_domain(str(suffix)), str(suffix))
                    except Exception as exc:
                        result.errors.append(f"rule[{idx}] domain_suffix: {exc}")

                for dom in sub.get("domain", []):
                    try:
                        _add("domain_exact", _normalize_domain(str(dom)), str(dom))
                    except Exception as exc:
                        result.errors.append(f"rule[{idx}] domain: {exc}")

                for pat in sub.get("domain_regex", []):
                    try:
                        re.compile(str(pat))
                        _add("domain_regex", str(pat), str(pat))
                    except Exception as exc:
                        result.errors.append(f"rule[{idx}] domain_regex: {exc}")

            elif rule_family == "subnet":
                for cidr in sub.get("ip_cidr", []):
                    try:
                        net = str(ipaddress.ip_network(str(cidr), strict=False))
                        rt = "ip" if "/" not in net or net.endswith("/32") or net.endswith("/128") else "cidr"
                        _add(rt, net, str(cidr))
                    except Exception as exc:
                        result.errors.append(f"rule[{idx}] ip_cidr: {exc}")

    return result


# ──────────────────────────────────────────────────────────────────────────────
# Multi-line text parser (plain_text)
# ──────────────────────────────────────────────────────────────────────────────

def parse_text(text: str, rule_family: str) -> ParseResult:
    """Parse multi-line plain_text content into ParseResult."""
    result = ParseResult(detected_format=FMT_PLAIN)
    seen: set[tuple[str, str]] = set()
    parse_fn = parse_domain_line if rule_family == "domain" else parse_subnet_line

    lines = text.splitlines()
    result.lines_total = len(lines)

    if len(lines) > MAX_LINES:
        result.errors.append(
            f"Слишком много строк: {len(lines)} > {MAX_LINES}. "
            f"Обрабатываются только первые {MAX_LINES}."
        )
        lines = lines[:MAX_LINES]

    for i, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            result.lines_skipped += 1
            continue
        try:
            rule = parse_fn(raw)
            key = (rule.rule_type, rule.normalized_value)
            if key in seen:
                result.duplicates += 1
            else:
                seen.add(key)
                result.rules.append(rule)
        except Exception as exc:
            snippet = raw.strip()[:80]
            result.errors.append(f"Строка {i}: {exc} — {snippet}")

    return result


# ──────────────────────────────────────────────────────────────────────────────
# Dispatcher: parse by effective format
# ──────────────────────────────────────────────────────────────────────────────

def parse_content(text: str, rule_family: str, effective_format: str) -> ParseResult:
    """Route to the appropriate parser based on effective_format."""
    if effective_format == FMT_SRS:
        result = ParseResult(detected_format=FMT_SRS)
        result.errors.append(
            "SRS-файл обнаружен, но импорт бинарного формата sing-box (.srs) "
            "не поддержан в Stage 2E. "
            "Используйте plain_text или sing-box source JSON (.json)."
        )
        return result

    if effective_format == FMT_SINGBOX_JSON:
        return _parse_singbox_json(text, rule_family)

    # plain_text or auto-resolved to plain_text
    return parse_text(text, rule_family)


# ──────────────────────────────────────────────────────────────────────────────
# URL / file fetching
# ──────────────────────────────────────────────────────────────────────────────

def _validate_url(url: str) -> None:
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"Разрешены только схемы http/https. Получена схема: '{parsed.scheme}'"
        )
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("URL без хоста")
    if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        raise ValueError(f"Локальные адреса запрещены: {host}")
    try:
        addr = ipaddress.ip_address(host)
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_unspecified:
            raise ValueError(f"Приватные/зарезервированные IP запрещены: {host}")
    except ValueError as exc:
        if any(kw in str(exc) for kw in ("запрещены", "Локальные", "Приватные")):
            raise


def fetch_url(url: str) -> bytes:
    """Fetch URL content as bytes with safety limits."""
    import urllib.request

    _validate_url(url)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "hiddify-routing/1.0"},
    )
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        data = resp.read(MAX_BYTES + 1)

    if len(data) > MAX_BYTES:
        raise ValueError(
            f"Ответ сервера превышает {MAX_BYTES // 1024} KB."
        )
    return data


def read_local_file(path: str) -> bytes:
    """Read a local file restricted to ALLOWED_LOCAL_DIR, return bytes."""
    import os

    real = os.path.realpath(path)
    allowed_real = os.path.realpath(ALLOWED_LOCAL_DIR)
    if not (real == allowed_real or real.startswith(allowed_real + os.sep)):
        raise ValueError(
            f"Путь вне разрешённого каталога {ALLOWED_LOCAL_DIR}: {path}"
        )
    if not os.path.isfile(real):
        raise ValueError(f"Файл не найден: {real}")

    with open(real, "rb") as fh:
        data = fh.read(MAX_BYTES + 1)

    if len(data) > MAX_BYTES:
        raise ValueError(
            f"Файл превышает {MAX_BYTES // 1024} KB."
        )
    return data


def fetch_source_bytes(source: object) -> tuple[bytes, str]:
    """Return (raw_bytes, ref) for a CommercialRoutingRuleSource object.

    ref is the URL or file path, used for format detection.
    """
    st = getattr(source, "source_type", "text") or "text"
    if st == "text":
        text = getattr(source, "content_text", "") or ""
        return text.encode("utf-8"), ""
    if st == "external_url":
        url = getattr(source, "url", "") or ""
        return fetch_url(url), url
    if st == "local_file":
        path = getattr(source, "local_path", "") or ""
        return read_local_file(path), path
    raise ValueError(f"Неизвестный source_type: {st}")


# ──────────────────────────────────────────────────────────────────────────────
# High-level helpers used by the admin view
# ──────────────────────────────────────────────────────────────────────────────

def preview_source(source: object) -> tuple[ParseResult, Optional[str]]:
    """Fetch and parse a source. Returns (ParseResult, fetch_error_or_None)."""
    try:
        raw_bytes, ref = fetch_source_bytes(source)
    except Exception as exc:
        return ParseResult(), str(exc)

    src_fmt = getattr(source, "source_format", FMT_AUTO) or FMT_AUTO
    family = getattr(source, "rule_family", "domain") or "domain"

    eff_fmt = detect_format(src_fmt, ref, raw_bytes[:512])

    text = raw_bytes.decode("utf-8", errors="replace")
    result = parse_content(text, family, eff_fmt)
    result.detected_format = eff_fmt
    return result, None


def import_source(source: object) -> tuple[int, int, Optional[str]]:
    """Fetch, parse, and upsert rules into commercial_routing_custom_rule.

    Workflow:
      1. Delete all existing rules owned by this source (source_id=source.id).
      2. Parse fresh content.
      3. Insert parsed rules with source_id set; skip those already owned by
         another source (same rule_type+normalized_value).

    Returns (inserted_count, skipped_duplicates, error_or_None).
    Does NOT commit — caller must commit.
    """
    from hiddifypanel.database import db
    from hiddifypanel.models.commercial_routing_custom_rule import (
        CommercialRoutingCustomRule,
    )

    result, fetch_err = preview_source(source)
    if fetch_err:
        return 0, 0, fetch_err
    if not result.rules and result.errors:
        return 0, 0, f"Ошибки разбора: {result.errors[0]}"

    source_id = getattr(source, "id", None)
    policy = getattr(source, "outbound_policy", "direct_ru") or "direct_ru"

    # Delete all rules previously imported from this source
    if source_id is not None:
        CommercialRoutingCustomRule.query.filter_by(source_id=source_id).delete()

    inserted = 0
    skipped = 0

    for parsed_rule in result.rules:
        existing = CommercialRoutingCustomRule.query.filter_by(
            rule_type=parsed_rule.rule_type,
            normalized_value=parsed_rule.normalized_value,
        ).first()
        if existing:
            skipped += 1
            continue
        rule = CommercialRoutingCustomRule(
            rule_type=parsed_rule.rule_type,
            value=parsed_rule.raw.strip(),
            normalized_value=parsed_rule.normalized_value,
            outbound_policy=policy,
            enabled=True,
            source_id=source_id,
        )
        db.session.add(rule)
        inserted += 1

    return inserted, skipped, None
