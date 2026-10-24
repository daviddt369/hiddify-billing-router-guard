#!/bin/bash
# DEV loop on the stand: rollback the installed feature (if any) -> rebuild payload/manifest -> apply -> canonical test state -> router apply
set -uo pipefail
cd /work/src
echo "== rollback (if installed)"
python3 multiloc_installer.py rollback 2>&1 | tail -3
echo "== rebuild payload + manifest"
python3 build_payload.py >/dev/null && python3 build_manifest.py
echo "== apply"
python3 multiloc_installer.py apply 2>&1 | tail -14
echo "== canonical test state"
cd /work/tests && /opt/hiddify-manager/.venv313/bin/python - <<'PY'
import sys; sys.path.insert(0,'/work/tests')
from stand_lib import *
reset_locations_to_stand_default()
apply_router()
print("router ready")
PY
