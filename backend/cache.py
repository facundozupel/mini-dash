from cachetools import TTLCache
from settings import settings

# Snapshot cache in-process: 1 entrada por endpoint, TTL 24h.
_cache: TTLCache = TTLCache(maxsize=32, ttl=settings.cache_ttl_seconds)


def get_or_compute(key: str, compute_fn):
    if key in _cache:
        return _cache[key], True  # hit
    value = compute_fn()
    _cache[key] = value
    return value, False  # miss


def flush():
    n = len(_cache)
    _cache.clear()
    return n
