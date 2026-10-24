# CP-10 · AUTO director · общие функции (подключается из других скриптов)
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PKG=$(cd "$HERE/.." && pwd)
PYV=/opt/hiddify-manager/.venv313/bin/python
CTL="$PYV /opt/hiddify-manager/scripts/multiloc_ctl.py"
LAYER=/opt/hiddify-manager/.venv313/lib/python3.13/site-packages/hiddifypanel/hutils/multiloc_router.py
MLOC=/opt/hiddify-manager/.venv313/lib/python3.13/site-packages/hiddifypanel/hutils/multiloc.py
REL_LAYER=opt/hiddify-manager/.venv313/lib/python3.13/site-packages/hiddifypanel/hutils/multiloc_router.py
REL_MLOC=opt/hiddify-manager/.venv313/lib/python3.13/site-packages/hiddifypanel/hutils/multiloc.py
BASE_LAYER=a0d08a6f2e64826b      # CP-05 (на проде с 2026-10-16)
NEW_LAYER=9ee29ef38b1387a3
BASE_MLOC=ecfe5e7804972758
NEW_MLOC=4231b5b5d944aeef
SOCK=/run/xray-router-api.sock
CONF_DIR=/etc/auto-director
fail=0
ok()   { echo "  OK    $*"; }
bad()  { echo "  FAIL  $*"; fail=1; }
warn() { echo "  WARN  $*"; }
sha16() { sha256sum "$1" 2>/dev/null | cut -c1-16; }

# panel setting through the panel's own setter (cache invalidation included): set_hconfig <name> <value|-> ; prints "<old>"
hconfig_get() {
  (cd /opt/hiddify-manager/hiddify-panel && HCFG_NAME="$1" $PYV - <<'PY'
import os
os.environ.setdefault("HIDDIFY_CFG_PATH", "/opt/hiddify-manager/hiddify-panel/app.cfg")
os.environ.setdefault("HIDDIFY_CONFIG_PATH", "/opt/hiddify-manager/")
from hiddifypanel import create_app
app = create_app(app_mode="cli")
with app.app_context():
    from hiddifypanel.models import ConfigEnum, hconfig
    print(hconfig(getattr(ConfigEnum, os.environ["HCFG_NAME"])))
PY
  ) 2>/dev/null | tail -1
}
hconfig_set() {
  (cd /opt/hiddify-manager/hiddify-panel && HCFG_NAME="$1" HCFG_VALUE="$2" $PYV - <<'PY'
import os
os.environ.setdefault("HIDDIFY_CFG_PATH", "/opt/hiddify-manager/hiddify-panel/app.cfg")
os.environ.setdefault("HIDDIFY_CONFIG_PATH", "/opt/hiddify-manager/")
from hiddifypanel import create_app
app = create_app(app_mode="cli")
with app.app_context():
    from hiddifypanel.models import ConfigEnum, hconfig, set_hconfig
    set_hconfig(getattr(ConfigEnum, os.environ["HCFG_NAME"]), os.environ["HCFG_VALUE"])
    print(hconfig(getattr(ConfigEnum, os.environ["HCFG_NAME"])))
PY
  ) 2>/dev/null | tail -1
}

wait_entrances() { for i in $(seq 1 60); do (exec 3<>/dev/tcp/127.0.0.1/20808) 2>/dev/null && (exec 3<>/dev/tcp/127.0.0.1/20811) 2>/dev/null && return 0; sleep 1; done; return 1; }
