def compute_user_inactive_reason(
    *,
    enabled: bool,
    usage_limit: int,
    current_usage: int,
    remaining_days: int,
    max_ips: int | None = None,
    device_count: int = 0,
) -> str | None:
    if not enabled:
        return "disabled"
    if usage_limit < current_usage:
        return "usage_limit"
    if remaining_days < 0:
        return "expired"
    if max_ips and device_count > max_ips:
        return "max_ips"
    return None


def compute_user_active(
    *,
    enabled: bool,
    usage_limit: int,
    current_usage: int,
    remaining_days: int,
    max_ips: int | None = None,
    device_count: int = 0,
) -> bool:
    return compute_user_inactive_reason(
        enabled=enabled,
        usage_limit=usage_limit,
        current_usage=current_usage,
        remaining_days=remaining_days,
        max_ips=max_ips,
        device_count=device_count,
    ) is None
