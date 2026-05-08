from __future__ import annotations


def compute_cycle_usage_delta(current_usage: int | None, previous_cycle_usage: int | None) -> int:
    current = int(current_usage or 0)
    previous = int(previous_cycle_usage or 0)
    return max(0, current - previous)


def compute_traffic_multiplier(current_delta: int, previous_delta: int) -> float:
    if current_delta <= 0 or previous_delta <= 0:
        return 1.0
    ratio = current_delta / max(previous_delta, 1)
    return max(1.0, min(10.0, round(ratio, 2)))
