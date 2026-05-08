#!/usr/bin/env bash
set -euo pipefail

NFT_BIN="${NFT_BIN:-/usr/sbin/nft}"
TABLE_NAME="${HIDDIFY_ANTI_SHARE_NFT_TABLE:-hiddify_antishare}"
TABLE_FAMILY="${HIDDIFY_ANTI_SHARE_NFT_FAMILY:-inet}"
CHAIN_NAME="${HIDDIFY_ANTI_SHARE_NFT_CHAIN:-input}"
SET_V4="${HIDDIFY_ANTI_SHARE_NFT_SET_V4:-blocked_ipv4}"
SET_V6="${HIDDIFY_ANTI_SHARE_NFT_SET_V6:-blocked_ipv6}"

need_cmd() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "[anti-share-nft][ERROR] missing command: $1" >&2
        exit 1
    }
}

ensure_ruleset() {
    "$NFT_BIN" list table "$TABLE_FAMILY" "$TABLE_NAME" >/dev/null 2>&1 || "$NFT_BIN" add table "$TABLE_FAMILY" "$TABLE_NAME"
    "$NFT_BIN" list set "$TABLE_FAMILY" "$TABLE_NAME" "$SET_V4" >/dev/null 2>&1 || \
        "$NFT_BIN" add set "$TABLE_FAMILY" "$TABLE_NAME" "$SET_V4" "{ type ipv4_addr; flags timeout; }"
    "$NFT_BIN" list set "$TABLE_FAMILY" "$TABLE_NAME" "$SET_V6" >/dev/null 2>&1 || \
        "$NFT_BIN" add set "$TABLE_FAMILY" "$TABLE_NAME" "$SET_V6" "{ type ipv6_addr; flags timeout; }"
    "$NFT_BIN" list chain "$TABLE_FAMILY" "$TABLE_NAME" "$CHAIN_NAME" >/dev/null 2>&1 || \
        "$NFT_BIN" add chain "$TABLE_FAMILY" "$TABLE_NAME" "$CHAIN_NAME" "{ type filter hook input priority -5; policy accept; }"

    if ! "$NFT_BIN" list chain "$TABLE_FAMILY" "$TABLE_NAME" "$CHAIN_NAME" | grep -Fq "@$SET_V4"; then
        "$NFT_BIN" add rule "$TABLE_FAMILY" "$TABLE_NAME" "$CHAIN_NAME" ip saddr "@$SET_V4" counter drop
    fi
    if ! "$NFT_BIN" list chain "$TABLE_FAMILY" "$TABLE_NAME" "$CHAIN_NAME" | grep -Fq "@$SET_V6"; then
        "$NFT_BIN" add rule "$TABLE_FAMILY" "$TABLE_NAME" "$CHAIN_NAME" ip6 saddr "@$SET_V6" counter drop
    fi
}

ban_ip() {
    local ip="$1"
    local ttl="$2"
    local set_name="$SET_V4"
    if [[ "$ip" == *:* ]]; then
        set_name="$SET_V6"
    fi
    "$NFT_BIN" add element "$TABLE_FAMILY" "$TABLE_NAME" "$set_name" "{ $ip timeout ${ttl}s }"
}

delete_ip() {
    local ip="$1"
    local set_name="$SET_V4"
    if [[ "$ip" == *:* ]]; then
        set_name="$SET_V6"
    fi
    "$NFT_BIN" delete element "$TABLE_FAMILY" "$TABLE_NAME" "$set_name" "{ $ip }" 2>/dev/null || true
}

list_ruleset() {
    "$NFT_BIN" list table "$TABLE_FAMILY" "$TABLE_NAME"
}

main() {
    need_cmd "$NFT_BIN"
    local action="${1:-}"
    case "$action" in
        ensure)
            ensure_ruleset
            ;;
        ban)
            [[ $# -ge 3 ]] || { echo "usage: $0 ban <ip> <ttl_seconds> [label]" >&2; exit 1; }
            ensure_ruleset
            ban_ip "$2" "$3"
            ;;
        unban)
            [[ $# -ge 2 ]] || { echo "usage: $0 unban <ip>" >&2; exit 1; }
            ensure_ruleset
            delete_ip "$2"
            ;;
        list)
            ensure_ruleset
            list_ruleset
            ;;
        *)
            echo "usage: $0 {ensure|ban|unban|list}" >&2
            exit 1
            ;;
    esac
}

main "$@"
