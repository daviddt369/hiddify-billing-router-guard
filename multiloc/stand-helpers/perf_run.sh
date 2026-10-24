#!/bin/bash
set -uo pipefail
cd /work/src
echo "== 1) pristine (no feature) + 15076 filler RU rules"
python3 multiloc_installer.py rollback >/dev/null 2>&1
bash /work/tools/stand_pyrun.sh /work/tools/seed_dataplane.py --filler=15076 2>&1 | tail -1
python3 /work/tools/perf_apply.py legacy-15k-rules 2
echo "== 2) feature installed, locations enabled"
python3 multiloc_installer.py apply >/dev/null 2>&1
cd /work/tests && /opt/hiddify-manager/.venv313/bin/python -c "
import sys; sys.path.insert(0,'/work/tests')
from stand_lib import *
reset_locations_to_stand_default()
" 
python3 /work/tools/perf_apply.py with-feature-15k-rules 2
