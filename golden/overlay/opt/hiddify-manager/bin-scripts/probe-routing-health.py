#!/usr/bin/env python3
"""
probe-routing-health.py — check reachability of xray-router upstream nodes.

Reads CommercialRoutingUpstream from DB, probes each enabled upstream,
writes results back to DB (last_status, last_error, last_checked_at) and
to /opt/hiddify-manager/var/commercial-routing-status.json.

Probe strategy:
  vless / trojan : TCP connect to URI host:port (5 s timeout)
  wireguard      : ICMP ping to wg_endpoint IP (1 packet, 3 s timeout)
  test_blackhole : always "unknown" (not a real outbound)

Statuses:
  online   — reachable, latency measured
  offline  — connection refused or timeout
  degraded — high latency (> DEGRADED_MS)
  unknown  — not probed (disabled, test_blackhole, bad config)
"""

import json
import os
import re
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlparse, unquote

DB_NAME        = os.environ.get("DB_NAME", "hiddifypanel")
STATUS_FILE    = "/opt/hiddify-manager/var/commercial-routing-status.json"
TIMEOUT_S      = 5.0
DEGRADED_MS    = 500
XRAY_ROUTER_TAG_PREFIX = "upstream-"


# ─── DB helpers ──────────────────────────────────────────────────────────────

def db_query(sql, db=DB_NAME):
    import subprocess
    result = subprocess.run(
        ["mysql", db, "-N", "-B", "-e", sql],
        capture_output=True, text=True, timeout=10
    )
    return result.stdout.strip()


def get_upstreams():
    rows = db_query(
        "SELECT id, name, enabled, tunnel_type, "
        "vless_uri, trojan_uri, wg_endpoint "
        "FROM commercial_routing_upstream ORDER BY id;"
    )
    upstreams = []
    for line in rows.splitlines():
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        upstreams.append({
            "id":          int(parts[0]),
            "name":        parts[1],
            "enabled":     parts[2] == "1",
            "tunnel_type": parts[3],
            "vless_uri":   parts[4],
            "trojan_uri":  parts[5],
            "wg_endpoint": parts[6],
        })
    return upstreams


def save_upstream_status(upstream_id, status, latency_ms, error):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    error_sql = "NULL" if not error else "'" + error.replace("'", "''")[:500] + "'"
    latency_sql = "NULL" if latency_ms is None else str(int(latency_ms))
    db_query(
        f"UPDATE commercial_routing_upstream SET "
        f"last_status='{status}', "
        f"last_error={error_sql}, "
        f"last_checked_at='{now}' "
        f"WHERE id={upstream_id};"
    )


# ─── Endpoint parsers ─────────────────────────────────────────────────────────

def vless_endpoint(uri: str):
    """Extract (host, port) from vless:// URI."""
    try:
        parsed = urlparse(uri)
        host = parsed.hostname or ""
        port = parsed.port or 443
        return host, int(port)
    except Exception:
        return None, None


def trojan_endpoint(uri: str):
    """Extract (host, port) from trojan:// URI."""
    try:
        parsed = urlparse(uri)
        host = parsed.hostname or ""
        port = parsed.port or 443
        return host, int(port)
    except Exception:
        return None, None


def wg_endpoint_ip(endpoint: str):
    """Extract IP from 'host:port' WireGuard endpoint string."""
    try:
        host, _port = endpoint.rsplit(":", 1)
        return host.strip()
    except Exception:
        return None


# ─── Probes ──────────────────────────────────────────────────────────────────

def probe_tcp(host: str, port: int, timeout: float = TIMEOUT_S):
    """TCP connect probe. Returns (latency_ms, error_str)."""
    try:
        t0 = time.monotonic()
        with socket.create_connection((host, port), timeout=timeout):
            pass
        ms = (time.monotonic() - t0) * 1000
        return ms, None
    except socket.timeout:
        return None, f"timeout after {timeout}s"
    except ConnectionRefusedError:
        return None, "connection refused"
    except OSError as e:
        return None, str(e)


def probe_icmp(ip: str, timeout: float = 3.0):
    """ICMP ping via system ping. Returns (latency_ms, error_str)."""
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", str(int(timeout)), ip],
            capture_output=True, text=True, timeout=timeout + 2
        )
        if result.returncode == 0:
            # parse "time=X.X ms" from output
            m = re.search(r"time[=<]([\d.]+)\s*ms", result.stdout)
            ms = float(m.group(1)) if m else 0.0
            return ms, None
        return None, result.stderr.strip() or "ping failed"
    except subprocess.TimeoutExpired:
        return None, f"ping timeout after {timeout}s"
    except Exception as e:
        return None, str(e)


def classify_status(latency_ms, error):
    if error:
        return "offline"
    if latency_ms is not None and latency_ms > DEGRADED_MS:
        return "degraded"
    if latency_ms is not None:
        return "online"
    return "unknown"


# ─── Main ─────────────────────────────────────────────────────────────────────

def probe_upstream(up):
    tunnel = up["tunnel_type"]
    now_str = datetime.now(timezone.utc).isoformat()

    if not up["enabled"]:
        return {"status": "unknown", "latency_ms": None,
                "last_check_at": now_str, "last_error": "disabled"}

    if tunnel == "test_blackhole":
        return {"status": "unknown", "latency_ms": None,
                "last_check_at": now_str, "last_error": "test_blackhole"}

    if tunnel in ("vless",):
        host, port = vless_endpoint(up["vless_uri"])
        if not host:
            return {"status": "unknown", "latency_ms": None,
                    "last_check_at": now_str, "last_error": "bad vless_uri"}
        latency_ms, error = probe_tcp(host, port)

    elif tunnel == "trojan":
        host, port = trojan_endpoint(up["trojan_uri"])
        if not host:
            return {"status": "unknown", "latency_ms": None,
                    "last_check_at": now_str, "last_error": "bad trojan_uri"}
        latency_ms, error = probe_tcp(host, port)

    elif tunnel == "wireguard":
        ip = wg_endpoint_ip(up["wg_endpoint"])
        if not ip:
            return {"status": "unknown", "latency_ms": None,
                    "last_check_at": now_str, "last_error": "bad wg_endpoint"}
        latency_ms, error = probe_icmp(ip)

    else:
        return {"status": "unknown", "latency_ms": None,
                "last_check_at": now_str,
                "last_error": f"unknown tunnel_type: {tunnel}"}

    status = classify_status(latency_ms, error)
    return {
        "status":       status,
        "latency_ms":   round(latency_ms) if latency_ms is not None else None,
        "last_check_at": now_str,
        "last_error":   error,
    }


def main():
    upstreams = get_upstreams()
    if not upstreams:
        print("[probe] No upstreams found in DB", file=sys.stderr)
        sys.exit(0)

    results = {}
    for up in upstreams:
        uid = up["id"]
        tag = f"{XRAY_ROUTER_TAG_PREFIX}{uid}"
        print(f"[probe] Checking {tag} ({up['tunnel_type']}) — {up['name']}")
        r = probe_upstream(up)
        print(f"[probe]   → {r['status']}"
              + (f"  {r['latency_ms']} ms" if r["latency_ms"] is not None else "")
              + (f"  [{r['last_error']}]" if r["last_error"] else ""))

        save_upstream_status(uid, r["status"],
                             r.get("latency_ms"), r.get("last_error"))
        results[str(uid)] = {
            "tag":          tag,
            "name":         up["name"],
            "tunnel_type":  up["tunnel_type"],
            **r,
        }

    os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "upstreams":  results,
    }
    with open(STATUS_FILE, "w") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"[probe] Status written to {STATUS_FILE}")


if __name__ == "__main__":
    main()
