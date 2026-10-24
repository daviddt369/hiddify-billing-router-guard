import os

import redis


def is_fake_redis_enabled() -> bool:
    return os.environ.get("HIDDIFY_FAKE_REDIS", "").lower() in {"1", "true", "yes", "on"}


def is_local_sqlite_enabled() -> bool:
    return os.environ.get("HIDDIFY_LOCAL_SQLITE", "").lower() in {"1", "true", "yes", "on"}


def get_redis_client():
    if is_fake_redis_enabled():
        import fakeredis

        return fakeredis.FakeStrictRedis()

    redis_url = os.environ.get("REDIS_URI_MAIN")
    if not redis_url:
        raise KeyError("REDIS_URI_MAIN")
    return redis.from_url(redis_url)
