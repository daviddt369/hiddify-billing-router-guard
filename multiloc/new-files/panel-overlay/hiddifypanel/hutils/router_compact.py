"""Compact the router rules: neighbours that are plain lists with the same target are ONE rule.

Why: the production router config holds ~15k bulk rules, each with exactly one ip / one domain and a plain target (`direct-ru`). xray builds, checks and starts them ONE BY ONE (about 5-7 ms each on the 2 vCPU
server): `xray run -test` of the full file takes ~110 s, the router needs 245-274 s to start - and the legacy pipeline runs the test five times. The same rules as a few big lists (13k ip entries in one
rule, 2k domain entries in another) are checked in 0.1 s and start in seconds (measured on the stand: 51.7 s -> 0.1 s for the production candidate), and a request is matched against ONE
list instead of walking 15k rules.

The routing result is identical BY CONSTRUCTION: xray takes the first rule that matches; only NEIGHBOURING rules with the same target (and nothing else in them) are joined, in the same order, so the first
matching entry is found in the same place of the sequence. Verified by a differential test against real xray (tests/test_18_compact.py).
"""
from __future__ import annotations

from typing import Any


_SPECIAL = ("regexp:", "geoip:", "geosite:", "ext:", "keyword:")       # the entries that are NOT plain addresses / domains (the panel and router_diff tell the two kinds apart)


def _kind(rule: Any):
    """("ip" | "domain", target, plain) for a rule that is nothing but one list and a plain outbound target, else None. Plain and special lists (regexp:, geoip: ...) are never
    joined with each other, so the owner's plain rules stay recognisable as such."""
    if not isinstance(rule, dict):
        return None
    keys = set(rule) - {"type"}
    for kind in ("ip", "domain"):
        if keys == {kind, "outboundTag"} and isinstance(rule[kind], list):
            return kind, rule["outboundTag"], not any(str(e).startswith(_SPECIAL) for e in rule[kind])
    return None


def compact_bulk_rules(rules: list[dict]) -> list[dict]:
    """the same rules in the same order, neighbouring plain lists with the same target joined. The input is not changed."""
    out: list[dict] = []
    last = None
    for rule in rules:
        k = _kind(rule)
        if k is not None and k == last:
            out[-1][k[0]].extend(rule[k[0]])
            continue
        if k is not None:
            rule = {**rule, k[0]: list(rule[k[0]])}          # a copy of its own: the next neighbours are appended to it
        out.append(rule)
        last = k
    return out


def compact_config(cfg: dict) -> dict:
    """a copy of the router config with its routing rules compacted (everything else is shared, not changed)"""
    c = dict(cfg)
    r = dict(c.get("routing") or {})
    r["rules"] = compact_bulk_rules(list(r.get("rules") or []))
    c["routing"] = r
    return c


def flat_entries(rules: list[dict]) -> list[tuple[str, str, str]]:
    """(kind, entry, target) of every entry of the plain-list rules, in order - the same for a plain and a compacted rule list (used to compare the two forms)"""
    out = []
    for rule in rules:
        k = _kind(rule)
        if k is not None:
            out.extend((k[0], str(e), str(k[1])) for e in rule[k[0]])
    return out
