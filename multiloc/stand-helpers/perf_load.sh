#!/bin/bash
# time of ONE xray-router config load/validation with the production-sized rule set, on 24 cores and pinned to 2 cores (prod has 2 vCPU)
CFG=${1:-/etc/xray-router/config.json}
X=/usr/bin/xray
echo "config: $CFG $(stat -c %s $CFG) bytes, rules: $(python3 -c "import json;print(len(json.load(open('$CFG'))['routing']['rules']))")"
for cpus in "0-23" "0,1"; do
  s=$(date +%s.%N)
  taskset -c $cpus $X run -test -config $CFG > /tmp/xt.out 2>&1; rc=$?
  e=$(date +%s.%N)
  printf 'cpus=%-5s rc=%s wall=%.1fs  %s\n' "$cpus" "$rc" "$(echo "$e - $s" | bc)" "$(tail -1 /tmp/xt.out | cut -c1-60)"
done
