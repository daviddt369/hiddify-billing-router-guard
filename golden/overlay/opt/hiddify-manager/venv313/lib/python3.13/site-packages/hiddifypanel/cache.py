import os
from redis_cache import RedisCache, chunks, compact_dump
from pickle import dumps, loads
from loguru import logger
from hiddifypanel.dev_runtime import get_redis_client, is_fake_redis_enabled


class _DummyCachedFunction:
    def __init__(self, fn):
        self._fn = fn

    def __call__(self, *args, **kwargs):
        return self._fn(*args, **kwargs)

    def invalidate(self, *args, **kwargs):
        return None

    def invalidate_all(self):
        return None


class DummyCache:
    def __init__(self):
        self.cached_functions = set()

    def cache(self, ttl=0, limit=0, namespace=None, exception_handler=None):
        def decorator(fn):
            wrapped = _DummyCachedFunction(fn)
            self.cached_functions.add(wrapped)
            return wrapped
        return decorator

    def invalidate_all_cached_functions(self):
        return True


class DummyRedisClient:
    def set(self, *args, **kwargs):
        return True

    def get(self, *args, **kwargs):
        return None

    def delete(self, *args, **kwargs):
        return 0

class CustomRedisCache(RedisCache):
    def __init__(self, redis_client, prefix="rc", serializer=compact_dump, deserializer=loads, key_serializer=None, support_cluster=True, exception_handler=None):
        super().__init__(redis_client, prefix, serializer, deserializer, key_serializer, support_cluster, exception_handler)
        self.cached_functions = set()

    def cache(self, ttl=0, limit=0, namespace=None, exception_handler=None):
        res = super().cache(ttl, limit, namespace, exception_handler)
        self.cached_functions.add(res)
        return res

    def invalidate_all_cached_functions(self):
        try:
            for f in self.cached_functions:
                f.invalidate_all()
            logger.trace("Invalidating all cached functions")
            chunks_gen = chunks(f'{self.prefix}*', 5000)
            for keys in chunks_gen:
                self.client.delete(*keys)
            logger.trace("Successfully invalidated all cached functions")
            return True
        except Exception as err:
            with logger.contextualize(error=err):
                logger.error("Failed to invalidate all cached functions")
            return False


if is_fake_redis_enabled():
    redis_client = DummyRedisClient()
    cache = DummyCache()
else:
    redis_client = get_redis_client()
    cache = CustomRedisCache(redis_client=redis_client, prefix="h", serializer=dumps, deserializer=loads)
