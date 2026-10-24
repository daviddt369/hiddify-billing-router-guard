#!/usr/bin/env bash
# golden/install.sh - brings a FRESH Ubuntu 24.04 server to the state of the golden node.
#
#   sudo bash golden/install.sh --params /path/params.env [--vendor DIR] [--data SNAPSHOT_DIR]
#
# Stages, in this order (the default is all of them; a failed run can be repeated, every stage is idempotent; --stages "a b c" runs only those):
#   base     Hiddify Manager v12.0.0 + the business / routing / antishare addons (disaster-recovery/bootstrap.sh)
#   overlay  the files of the node that the base does not reproduce (golden/MANIFEST.tsv): our panel code, templates, scripts, systemd units, monitoring, auto-director ...
#   vendor   third-party build that must not live in git: the Admin V2 app (DIR/admin-v2.tar.gz, from the private golden bundle)
#   pins     the xray version the node runs (golden/pins.env), downloaded from the official release and checked by sha256
#   post     compile, daemon-reload, the routing tables (scripts/multiloc_ctl.py init), restart of the panel services
#   data     the database, users, keys, certificates, secrets from a disaster-recovery snapshot (restore.sh SNAPSHOT_DIR)  - only with --data
#   enable   enables and starts the node's own units (golden/UNITS.enabled): monitoring, auto-director, router, timers      - only after data
#   verify   golden/verify.py (every file of the manifest identical to the node's) and golden/smoke.py (the panel answers, all menu pages open)
# Do not point DNS / CDN at the server before `data` and `verify` have passed.
# Network note for rehearsals: with real data restored the server talks to Telegram, Let's Encrypt and your exit nodes. Rehearse it only on a machine
# without a default route (the clean-room procedure in golden/README.md).
set -Eeuo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
PARAMS=""; VENDOR=""; DATA=""; STAGES=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --params) PARAMS="$2"; shift ;;
        --vendor) VENDOR="$2"; shift ;;
        --data) DATA="$2"; shift ;;
        --stages) STAGES="$2"; shift ;;
        -h|--help) sed -n 2,22p "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 1 ;;
    esac
    shift
done
if [[ -z "$STAGES" ]]; then
    STAGES="base overlay vendor pins post"
    [[ -n "$DATA" ]] && STAGES="$STAGES data enable"
    STAGES="$STAGES verify"
fi
has() { [[ " $STAGES " == *" $1 "* ]]; }
say() { echo "[golden] $*"; }
die() { echo "[golden][ERROR] $*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "run as root"
[[ -n "$PARAMS" && -f "$PARAMS" ]] || die "--params FILE is required (see golden/params.example.env)"
command -v python3 >/dev/null || die "python3 is missing"
python3 "$HERE/overlay_apply.py" --params "$PARAMS" --backup /nonexistent --dry-run >/dev/null || python3 "$HERE/overlay_apply.py" --params "$PARAMS" --backup /nonexistent --dry-run
has vendor && [[ -z "$VENDOR" || ! -f "$VENDOR/admin-v2.tar.gz" ]] && die "stage vendor needs --vendor DIR with admin-v2.tar.gz (the private golden bundle)"
has data && [[ -z "$DATA" || ! -f "$DATA/mariadb-full-dump.sql" ]] && die "stage data needs --data SNAPSHOT_DIR (a disaster-recovery snapshot with mariadb-full-dump.sql)"

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="/root/golden-backup-$STAMP"
PYV=/opt/hiddify-manager/.venv313/bin/python
SP() { ls -d /opt/hiddify-manager/.venv313/lib/python3.*/site-packages/hiddifypanel | head -1; }

if has base; then
    say "stage base: Hiddify v12.0.0 + addons (disaster-recovery/bootstrap.sh)"
    DR_STAGES="${DR_STAGES:-preflight offsite base addons headers selectel}" bash "$REPO_ROOT/disaster-recovery/bootstrap.sh"
fi

if has overlay; then
    say "stage overlay (backup of replaced files: $BACKUP)"
    [[ -d /opt/hiddify-manager/hiddify-panel ]] || die "no Hiddify base found: run the base stage first"
    python3 "$HERE/overlay_apply.py" --params "$PARAMS" --backup "$BACKUP"
fi

if has vendor; then
    say "stage vendor: Admin V2 app"
    tar -xzf "$VENDOR/admin-v2.tar.gz" -C "$(SP)/static"
    chown -R root:root "$(SP)/static/admin-v2"; find "$(SP)/static/admin-v2" -type d -exec chmod 755 {} +; find "$(SP)/static/admin-v2" -type f -exec chmod 644 {} +
fi

if has pins; then
    say "stage pins: xray"
    # shellcheck disable=SC1091
    source "$HERE/pins.env"
    if [[ "$(sha256sum /usr/bin/xray 2>/dev/null | cut -d' ' -f1)" == "$XRAY_BIN_SHA256" ]]; then
        say "xray $XRAY_VERSION already in place"
    else
        T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
        curl -fsSL -m 300 -o "$T/xray.zip" "$XRAY_ZIP_URL" || die "cannot download $XRAY_ZIP_URL"
        [[ "$(sha256sum "$T/xray.zip" | cut -d' ' -f1)" == "$XRAY_ZIP_SHA256" ]] || die "xray zip checksum differs from the pinned one"
        python3 -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extract('xray', sys.argv[2])" "$T/xray.zip" "$T"
        [[ "$(sha256sum "$T/xray" | cut -d' ' -f1)" == "$XRAY_BIN_SHA256" ]] || die "xray binary checksum differs from the pinned one"
        mkdir -p "$BACKUP/pins"
        for t in $XRAY_TARGETS; do
            [[ -f "$t" ]] && cp -a "$t" "$BACKUP/pins/$(echo "$t" | tr / _)"
            mkdir -p "$(dirname "$t")"; install -m 755 "$T/xray" "$t"
        done
        systemctl restart hiddify-xray 2>/dev/null || true
        say "xray $XRAY_VERSION installed (old binaries kept in $BACKUP/pins)"
    fi
fi

if has post; then
    say "stage post"
    "$PYV" -m compileall -q "$(SP)" >/dev/null || die "a Python file of the overlay does not compile"
    systemctl daemon-reload
    if [[ -f /opt/hiddify-manager/scripts/multiloc_ctl.py ]]; then
        (cd /opt/hiddify-manager/hiddify-panel && timeout 300 "$PYV" /opt/hiddify-manager/scripts/multiloc_ctl.py init) || say "WARNING: multiloc_ctl init failed (tables of the routing layer)"
    fi
    systemctl daemon-reload
    systemctl restart hiddify-panel hiddify-panel-background-tasks || say "WARNING: panel restart failed"
    for _ in $(seq 1 60); do curl -s -o /dev/null -m 2 http://127.0.0.1:9000/ && break; sleep 1; done
    say "panel: $(systemctl is-active hiddify-panel)"
fi

if has data; then
    say "stage data: restore from $DATA"
    bash "$REPO_ROOT/disaster-recovery/restore.sh" "$DATA"
    # restore.sh swaps the database and appends the CDN snippets: the overlay must still be the node's
    python3 "$HERE/verify.py" --params "$PARAMS" || say "WARNING: the overlay differs after the restore (listed above)"
fi

if has enable; then
    say "stage enable: the node's own units"
    systemctl daemon-reload
    while IFS= read -r u; do
        [[ -z "$u" || "$u" == \#* ]] && continue
        if systemctl cat "$u" >/dev/null 2>&1; then systemctl enable --now "$u" >/dev/null 2>&1 && say "enabled $u" || say "WARNING: $u did not start"; else say "WARNING: unit $u does not exist here"; fi
    done < "$HERE/UNITS.enabled"
fi

if has verify; then
    say "stage verify"
    python3 "$HERE/verify.py" --params "$PARAMS" || say "WARNING: differences listed above"
    if has data || [[ -f /opt/hiddify-manager/current.json ]]; then "$PYV" "$HERE/smoke.py" || say "WARNING: the smoke test found problems (the 'proxy stats' page is served by an external service and is expected to fail on a new server)"; fi
fi
say "DONE (stages: $STAGES). Rollback of the overlay: golden/rollback.sh $BACKUP"
