#!/bin/bash
ip link del vkt-h 2>/dev/null || true
ip netns del vkturn 2>/dev/null || true
exit 0
