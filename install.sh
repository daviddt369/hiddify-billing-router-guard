#!/usr/bin/env bash
# install.sh - one command that turns a FRESH Ubuntu 24.04 (or 22.04) server into a working panel (Cheburnator: the Hiddify-based stack of this repository).
#
#   sudo bash install.sh                 asks a few questions (name, domain, support contact), installs everything, prints the admin link
#   sudo bash install.sh --yes           no questions: defaults, or the options below
#   sudo bash install.sh --params FILE   use a parameters file made earlier (golden/make_params.py writes one)
#   sudo bash install.sh --restore SNAPSHOT_DIR --params FILE [--vendor DIR]
#                                        move an EXISTING node to this server: same stack, then its data from a disaster-recovery snapshot
#   sudo bash install.sh --node          an EXIT node (the server whose IP the users get): the plain Hiddify panel, no billing layer; then node/wg-exit.sh
#                                        (WireGuard) and, on the entrance node, node/ru-attach-wg.sh connect it (node/README.ru.md)
#
# Options for the questions: --name TEXT  --domain HOST  --support-handle NAME  --admin-ip IP  --features "routing_links dr_backup"
# The server must be reachable from the internet; point the DNS record of --domain at it BEFORE the install if you give one.
# Everything is installed from this repository and the pinned upstream releases (checked by sha256); see golden/README.ru.md and NOTICE.md.
set -Eeuo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARAMS=""; RESTORE=""; VENDOR="$HERE/vendor"; YES=0; NODE=0; EXTRA=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --params) PARAMS="$2"; shift ;;
        --restore) RESTORE="$2"; shift ;;
        --vendor) VENDOR="$2"; shift ;;
        --yes) YES=1 ;;
        --node) NODE=1 ;;
        --name|--domain|--support-handle|--admin-ip|--features) EXTRA+=("$1" "$2"); shift ;;
        -h|--help) sed -n 2,14p "$0"; exit 0 ;;
        *) echo "unknown argument: $1 (see --help)" >&2; exit 1 ;;
    esac
    shift
done
die() { echo "[install][ERROR] $*" >&2; exit 1; }
say() { echo "[install] $*"; }

[[ "$(id -u)" -eq 0 ]] || die "run as root: sudo bash install.sh"
. /etc/os-release 2>/dev/null || true
[[ "${ID:-}" == ubuntu && "${VERSION_ID:-}" =~ ^(22\.04|24\.04)$ ]] || die "Ubuntu 22.04 or 24.04 is required (this is ${PRETTY_NAME:-unknown})"
for t in python3 curl; do command -v "$t" >/dev/null || { apt-get update -qq && apt-get install -y -qq python3 curl ca-certificates; break; }; done
[[ -s /root/.ssh/authorized_keys ]] || say "NOTE: /root/.ssh/authorized_keys is empty; the key-only SSH policy of the stack will wait until a key is there (re-run: bash install.sh --params FILE)"

if [[ "$NODE" == 1 ]]; then
    say "exit node: the plain Hiddify base (no billing / routing layer)"
    DR_STAGES="preflight base" bash "$HERE/disaster-recovery/bootstrap.sh"
    echo
    say "READY. The admin links of this node:"
    ( cd /opt/hiddify-manager/hiddify-panel && /opt/hiddify-manager/.venv313/bin/python -m hiddifypanel admin-links 2>/dev/null ) || say "(print them with: cd /opt/hiddify-manager/hiddify-panel && ../.venv313/bin/python -m hiddifypanel admin-links)"
    say "Next: on the entrance node  bash node/ru-attach-wg.sh keygen NAME ; here  bash node/wg-exit.sh --apply --peer-pub <that key> ; then  bash node/ru-attach-wg.sh attach NAME ...  (node/README.ru.md)"
    exit 0
fi

if [[ -z "$PARAMS" ]]; then
    PARAMS=/root/cheburnator.env
    if [[ -f "$PARAMS" ]]; then
        say "using the parameters of the earlier run: $PARAMS (delete it to answer the questions again)"
    else
        ARGS=(--out "$PARAMS" "${EXTRA[@]}")
        [[ "$YES" == 1 ]] && ARGS+=(--yes)
        python3 "$HERE/golden/make_params.py" "${ARGS[@]}"
    fi
fi
[[ -f "$PARAMS" ]] || die "no parameters file: $PARAMS"
[[ -f "$VENDOR/admin-v2.tar.gz" ]] || die "no Admin V2 build in $VENDOR (vendor/admin-v2.tar.gz of this repository)"

if [[ -n "$RESTORE" ]]; then
    exec bash "$HERE/golden/install.sh" --params "$PARAMS" --vendor "$VENDOR" --data "$RESTORE"
fi
bash "$HERE/golden/install.sh" --params "$PARAMS" --vendor "$VENDOR" --fresh

DOMAIN="$(grep -E '^BRAND_DOMAIN=' "$PARAMS" | head -1 | cut -d= -f2-)"
if [[ -n "$DOMAIN" && "$DOMAIN" != *.invalid ]]; then
    if ( cd /opt/hiddify-manager/hiddify-panel && /opt/hiddify-manager/.venv313/bin/python -m hiddifypanel add-domain -d "$DOMAIN" -m direct >/dev/null 2>&1 ); then
        say "domain $DOMAIN added to the panel. When its DNS record points at this server, press Apply in the panel (or run: bash /opt/hiddify-manager/apply_configs.sh) to get its certificate."
    else
        say "NOTE: could not add the domain $DOMAIN automatically; add it in the panel (Domains)."
    fi
fi

echo
say "READY. The admin links of the panel:"
( cd /opt/hiddify-manager/hiddify-panel && /opt/hiddify-manager/.venv313/bin/python -m hiddifypanel admin-links 2>/dev/null ) || say "(print them with: cd /opt/hiddify-manager/hiddify-panel && ../.venv313/bin/python -m hiddifypanel admin-links)"
say "Parameters of this node: $PARAMS (private, keep a copy). Exit nodes are added in the panel: Routing -> Upstreams / Countries."
