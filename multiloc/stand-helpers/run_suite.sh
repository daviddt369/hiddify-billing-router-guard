#!/bin/bash
# usage: run_suite.sh <module> [<module> ...]   -> /work/out/results/<module>.log + one-line summary each
cd /work/tests
for m in "$@"; do
  /opt/hiddify-manager/.venv313/bin/python -W ignore -m unittest $m > /work/out/results/$m.log 2>&1
  rc=$?
  summary=$(grep -E "^Ran|^OK|^FAILED" /work/out/results/$m.log | tr '\n' ' ')
  echo "$m rc=$rc :: $summary"
  grep -E "^(ERROR|FAIL):" /work/out/results/$m.log | cut -c1-200
done
