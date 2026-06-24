#!/bin/bash
# Fix: hiddifypanel/panel/run_commander.py - zombie process accumulation
#
# Bug: subprocess.Popen(..., start_new_session=True) is called without
# p.wait(), so every background command (apply, get-cert, restart-services)
# leaves a zombie process after it exits. Zombies accumulate over time
# causing gradual growth of memory/process table usage visible on weekly graphs.
#
# Fix: wrap Popen in a daemon thread that calls p.wait() to reap the child.
#
# Affects: hiddify-manager 12.0.0 and below
# Fixed upstream: hiddify-manager 12.3.3
# Risk: minimal — changes only how background subprocesses are launched

set -euo pipefail

FILE="/opt/hiddify-manager/hiddify-panel/src/hiddifypanel/panel/run_commander.py"

if [ ! -f "$FILE" ]; then
    echo "ERROR: $FILE not found" >&2
    exit 1
fi

if grep -q 'import threading' "$FILE"; then
    echo "Already patched (import threading found)"
    exit 0
fi

python3 - << 'EOF'
path = "/opt/hiddify-manager/hiddify-panel/src/hiddifypanel/panel/run_commander.py"
content = open(path).read()

if 'import threading' in content:
    print("Already patched")
    exit(0)

# Add threading import
content = content.replace(
    'from typing import List',
    'import threading\nfrom typing import List',
    1
)

# Replace bare Popen with threaded version
old = '        subprocess.Popen(base_cmd, cwd=str(config_path), start_new_session=True)'
new = ('        t = threading.Thread(target=_cmd_in_background, args=(base_cmd, config_path), daemon=True)\n'
       '        t.start()')
if old not in content:
    raise SystemExit("ERROR: Popen pattern not found — file may have changed")
content = content.replace(old, new)

# Append helper function
content += '\n\ndef _cmd_in_background(cmd, cwd):\n    p = subprocess.Popen(cmd, cwd=str(cwd), start_new_session=True)\n    p.wait()\n'

open(path, 'w').write(content)
print("OK: zombie process fix applied to run_commander.py")
EOF

# Verify Python syntax
python3 -c "import ast; ast.parse(open('$FILE').read()); print('syntax OK')"

# Graceful panel reload to pick up the change
if systemctl is-active --quiet hiddify-panel; then
    systemctl reload hiddify-panel 2>/dev/null || true
    echo "Panel reloaded"
fi
