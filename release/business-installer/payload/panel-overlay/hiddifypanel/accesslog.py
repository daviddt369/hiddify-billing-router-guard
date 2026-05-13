import datetime
import json
import os
import re
from typing import Dict

from loguru import logger


XRAY_ACCESS_LOG = os.environ.get(
    "HIDDIFY_XRAY_ACCESS_LOG",
    "/opt/hiddify-manager/log/system/xray.access.log",
)
XRAY_ACCESS_STATE = os.environ.get(
    "HIDDIFY_XRAY_ACCESS_STATE",
    "/opt/hiddify-manager/log/system/xray.access.state.json",
)
ACCESS_TTL_SECONDS = int(os.environ.get("HIDDIFY_CONNECTED_IP_TTL", "120"))

_XRAY_ACCESS_RE = re.compile(
    r"^(?P<ts>\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?) "
    r"from (?:(?:tcp|udp):)?(?P<ip>\[[^\]]+\]|[^: ]+):\d+ "
    r"accepted .* email: (?P<uuid>[0-9a-fA-F-]+)@hiddify\.com$"
)


def _parse_timestamp(value: str) -> float:
    for fmt in ("%Y/%m/%d %H:%M:%S.%f", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.datetime.strptime(value, fmt).timestamp()
        except ValueError:
            continue
    raise ValueError(f"Unsupported xray access timestamp: {value}")


def _load_state(path: str) -> dict:
    if not os.path.exists(path):
        return {"offset": 0, "seen": {}}
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
        if not isinstance(state, dict):
            return {"offset": 0, "seen": {}}
        state.setdefault("offset", 0)
        state.setdefault("seen", {})
        return state
    except Exception:
        return {"offset": 0, "seen": {}}


def _save_state(path: str, state: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=True, sort_keys=True)
    except PermissionError:
        logger.warning("No permission to write xray access state at {}", path)


def _normalize_ip(ip: str) -> str:
    if ip.startswith("[") and ip.endswith("]"):
        return ip[1:-1]
    return ip


def collect_recent_ips(
    *,
    log_path: str = XRAY_ACCESS_LOG,
    state_path: str = XRAY_ACCESS_STATE,
    ttl_seconds: int = ACCESS_TTL_SECONDS,
    now: datetime.datetime | None = None,
) -> Dict[str, list[str]]:
    now_ts = (now or datetime.datetime.now()).timestamp()
    state = _load_state(state_path)
    seen = state.get("seen", {})

    if not os.path.exists(log_path):
        pruned = _prune_seen(seen, now_ts, ttl_seconds)
        state["seen"] = pruned
        state["offset"] = 0
        _save_state(state_path, state)
        return _seen_to_ip_map(pruned)

    try:
        file_size = os.path.getsize(log_path)
        offset = int(state.get("offset", 0) or 0)
        if offset < 0 or offset > file_size:
            offset = 0

        with open(log_path, encoding="utf-8", errors="ignore") as f:
            f.seek(offset)
            for line in f:
                match = _XRAY_ACCESS_RE.match(line.strip())
                if not match:
                    continue
                uuid = match.group("uuid").lower()
                ip = _normalize_ip(match.group("ip"))
                ts = _parse_timestamp(match.group("ts"))
                user_seen = seen.setdefault(uuid, {})
                user_seen[ip] = ts
            state["offset"] = f.tell()
    except PermissionError:
        logger.warning("No permission to read xray access log at {}", log_path)
        pruned = _prune_seen(seen, now_ts, ttl_seconds)
        state["seen"] = pruned
        _save_state(state_path, state)
        return _seen_to_ip_map(pruned)

    pruned = _prune_seen(seen, now_ts, ttl_seconds)
    state["seen"] = pruned
    _save_state(state_path, state)
    return _seen_to_ip_map(pruned)


def _prune_seen(seen: dict, now_ts: float, ttl_seconds: int) -> dict:
    threshold = now_ts - ttl_seconds
    pruned: dict = {}
    for uuid, ips in seen.items():
        recent = {
            ip: ts
            for ip, ts in ips.items()
            if isinstance(ts, (int, float)) and ts >= threshold
        }
        if recent:
            pruned[uuid] = recent
    return pruned


def _seen_to_ip_map(seen: dict) -> Dict[str, list[str]]:
    return {
        uuid: sorted(ips.keys())
        for uuid, ips in seen.items()
        if ips
    }
