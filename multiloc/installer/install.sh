#!/bin/bash
# thin wrapper: ./install.sh preflight|apply|verify|status|rollback [args]
exec /usr/bin/python3 "$(dirname "$(readlink -f "$0")")/multiloc_installer.py" "$@"
