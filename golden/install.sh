#!/usr/bin/env bash
# golden/install.sh - brings a FRESH Ubuntu 24.04 server to the state of the golden node.
#
#   sudo bash golden/install.sh --params /path/params.env [--vendor DIR] [--data SNAPSHOT_DIR | --fresh]
#   --fresh = a NEW node with an empty database (no --data): the same stages without `data`, the optional units follow FEATURES= of the params file
#
# Stages, in this order (the default is all of them; a failed run can be repeated, every stage is idempotent; --stages "a b c" runs only those):
#   base     Hiddify Manager v12.0.0 + the business / routing / antishare addons (disaster-recovery/bootstrap.sh)
#   overlay  the files of the node that the base does not reproduce (golden/MANIFEST.tsv): our panel code, templates, scripts, systemd units, monitoring, auto-director ...
#   vendor   third-party build that must not live in git: the Admin V2 app (DIR/admin-v2.tar.gz, from the private golden bundle)
#   pins     the xray version the node runs (golden/pins.env), downloaded from the official release and checked by sha256
#   post     compile, daemon-reload, the routing tables (scripts/multiloc_ctl.py init), restart of the panel services
#   data     the database, users, keys, certificates, secrets from a disaster-recovery snapshot (restore.sh SNAPSHOT_DIR)  - only with --data
#   router   builds /etc/xray-router/config.json from the restored database (scripts/multiloc_ctl.py apply --full)                - only after data
#   enable   enables and starts the node's own units (golden/UNITS.enabled): monitoring, auto-director, router, timers      - only after data
#   verify   golden/verify.py (every file of the manifest identical to the node's) and golden/smoke.py (the panel answers, all menu pages open)
# Do not point DNS / CDN at the server before `data` and `verify` have passed.
# Network note for rehearsals: with real data restored the server talks to Telegram, Let's Encrypt and your exit nodes. Rehearse it only on a machine
# without a default route (the clean-room procedure in golden/README.md).
set -Eeuo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
PARAMS=""; VENDOR=""; DATA=""; STAGES=""; FRESH=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --params) PARAMS="$2"; shift ;;
        --vendor) VENDOR="$2"; shift ;;
        --data) DATA="$2"; shift ;;
        --stages) STAGES="$2"; shift ;;
        --fresh) FRESH=1 ;;
        -h|--help) sed -n 2,22p "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 1 ;;
    esac
    shift
done
if [[ -z "$STAGES" ]]; then
    STAGES="base overlay vendor pins post"
    [[ -n "$DATA" ]] && STAGES="$STAGES data router enable"
    [[ -z "$DATA" && "$FRESH" == 1 ]] && STAGES="$STAGES router enable"
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
    # the public build carries the product name as @@PRODUCT_NAME@@ (the node's own build, taken with capture, already has its name in it: nothing to do then)
    python3 - "$PARAMS" "$(SP)/static/admin-v2" <<'PY'
import os, re, sys
params = dict(l.rstrip("\n").split("=", 1) for l in open(sys.argv[1], encoding="utf-8") if "=" in l and not l.startswith("#"))
name = params.get("PRODUCT_NAME", "")
n = 0
for root, _, files in os.walk(sys.argv[2]):
    for f in files:
        if not f.endswith((".js", ".html")):
            continue
        p = os.path.join(root, f)
        s = open(p, encoding="utf-8").read()
        if "@@PRODUCT_NAME@@" in s:
            open(p, "w", encoding="utf-8", newline="").write(s.replace("@@PRODUCT_NAME@@", name))
            n += 1
print(f"[golden] Admin V2: product name set in {n} file(s)")
PY
    chown -R root:root "$(SP)/static/admin-v2"; find "$(SP)/static/admin-v2" -type d -exec chmod 755 {} +; find "$(SP)/static/admin-v2" -type f -exec chmod 644 {} +
fi

if has pins; then
    say "stage pins: xray and geo data"
    # shellcheck disable=SC1091
    source "$HERE/pins.env"
    T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
    fetch() {   # fetch <local file name in --vendor> <url> <sha256> <dest>
        if [[ -n "$VENDOR" && -f "$VENDOR/$1" ]]; then cp "$VENDOR/$1" "$4"; say "using the local copy $VENDOR/$1 (checksum is verified anyway)"
        else curl -fsSL -m 600 -o "$4" "$2" || die "cannot download $2"; fi
        [[ "$(sha256sum "$4" | cut -d' ' -f1)" == "$3" ]] || die "$1: checksum differs from the pinned one"
    }
    mkdir -p "$BACKUP/pins"
    REAL="$(readlink -f /opt/hiddify-manager/xray/bin/xray 2>/dev/null || echo /opt/hiddify-manager/xray/bin/xray)"
    if [[ -f "$REAL" && "$(sha256sum "$REAL" | cut -d' ' -f1)" == "$XRAY_BIN_SHA256" ]]; then
        say "xray $XRAY_VERSION already in place"
    else
        fetch Xray-linux-64.zip "$XRAY_ZIP_URL" "$XRAY_ZIP_SHA256" "$T/xray.zip"
        python3 -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extract('xray', sys.argv[2])" "$T/xray.zip" "$T"
        [[ "$(sha256sum "$T/xray" | cut -d' ' -f1)" == "$XRAY_BIN_SHA256" ]] || die "xray binary checksum differs from the pinned one"
        for t in $XRAY_TARGETS; do
            real="$(readlink -f "$t" 2>/dev/null || echo "$t")"
            [[ -f "$real" ]] && cp -a "$real" "$BACKUP/pins/$(echo "$real" | tr / _)"
            mkdir -p "$(dirname "$real")"; install -m 755 "$T/xray" "$real"
        done
        [[ -e /usr/bin/xray ]] || ln -s /opt/hiddify-manager/xray/bin/xray /usr/bin/xray
        say "xray $XRAY_VERSION installed (old binary kept in $BACKUP/pins)"
    fi
    for g in geoip geosite; do
        up="${g^^}_URL"; sh="${g^^}_SHA256"
        if [[ -f "$GEO_DIR/$g.dat" && "$(sha256sum "$GEO_DIR/$g.dat" | cut -d' ' -f1)" == "${!sh}" ]]; then say "$g.dat release $GEO_RELEASE already in place"; continue; fi
        fetch "$g.dat" "${!up}" "${!sh}" "$T/$g.dat"
        [[ -f "$GEO_DIR/$g.dat" ]] && cp -a "$GEO_DIR/$g.dat" "$BACKUP/pins/$g.dat.old"
        install -m 644 "$T/$g.dat" "$GEO_DIR/$g.dat"; say "$g.dat release $GEO_RELEASE installed"
    done
    systemctl restart hiddify-xray 2>/dev/null || true
fi

if has post; then
    say "stage post"
    "$PYV" -m compileall -q "$(SP)" >/dev/null || die "a Python file of the overlay does not compile"
    "$PYV" "$HERE/translations.py" || say "WARNING: the status texts could not be added to the translation catalogs"
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

if has router; then
    say "stage router: the local router-core config (/etc/xray-router) from the database"
    N_UP="$(mysql hiddifypanel -N -B -e 'SELECT COUNT(*) FROM commercial_routing_upstream' 2>/dev/null || echo 0)"
    if [[ "${N_UP:-0}" == 0 ]]; then
        say "router: there is no exit node in the panel yet, so there is nothing to route to: skipped. The first exit connected with node/ru-attach-wg.sh (or added in the panel) builds the router config."
    else
        (cd /opt/hiddify-manager/hiddify-panel && timeout 1500 "$PYV" /opt/hiddify-manager/scripts/multiloc_ctl.py apply --full --accept-diff) || say "WARNING: the router apply did not finish cleanly (see the output above)"
        (cd /opt/hiddify-manager/hiddify-panel && timeout 300 "$PYV" /opt/hiddify-manager/scripts/multiloc_ctl.py baseline) || true
    fi
fi

if has enable; then
    say "stage enable: the node's own units"
    systemctl daemon-reload
    # a unit line may end with @feature: it is enabled only when the params file lists that feature in FEATURES= (a node restored from a snapshot gets all of them)
    FEATURES="$(grep -E '^FEATURES=' "$PARAMS" | head -1 | cut -d= -f2-)"; [[ -n "$DATA" ]] && FEATURES="all"
    while read -r u feat; do
        [[ -z "$u" || "$u" == \#* ]] && continue
        if [[ "$u" == xray-router.service && ! -s /etc/xray-router/config.json ]]; then
            # without a config the unit would restart in a loop: let systemd skip it until the first exit node creates the config
            install -d /etc/systemd/system/xray-router.service.d
            printf '[Unit]\nConditionPathExists=/etc/xray-router/config.json\n' > /etc/systemd/system/xray-router.service.d/10-needs-config.conf
            systemctl daemon-reload; systemctl stop xray-router 2>/dev/null || true; systemctl reset-failed xray-router 2>/dev/null || true
            say "skipped $u (no router config yet: it starts with the first exit node)"; continue
        fi
        if [[ -n "${feat:-}" && "$FEATURES" != "all" && " $FEATURES " != *" ${feat#@} "* ]]; then say "skipped $u (feature ${feat#@} is off)"; continue; fi
        if systemctl cat "$u" >/dev/null 2>&1; then systemctl enable --now "$u" >/dev/null 2>&1 && say "enabled $u" || say "WARNING: $u did not start"; else say "WARNING: unit $u does not exist here"; fi
    done < "$HERE/UNITS.enabled"
fi

if has verify; then
    say "stage verify"
    python3 "$HERE/verify.py" --params "$PARAMS" || say "WARNING: differences listed above"
    if has data || [[ -f /opt/hiddify-manager/current.json ]]; then "$PYV" "$HERE/smoke.py" || say "WARNING: the smoke test found problems (the 'proxy stats' page is served by an external service and is expected to fail on a new server)"; fi
fi
say "DONE (stages: $STAGES). Rollback of the overlay: golden/rollback.sh $BACKUP"
