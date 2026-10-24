#!/bin/bash
# runs inside the namespace (NetworkNamespacePath): address, up, default route into the xray TUN
for i in $(seq 1 30); do ip link show vkt0 >/dev/null 2>&1 && break; sleep 0.3; done
ip addr replace 10.99.0.5/30 dev vkt0
ip link set vkt0 up
ip route replace default dev vkt0
