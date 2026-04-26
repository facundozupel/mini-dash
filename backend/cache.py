"""
Capa de cache "deepada".

- `CacheStore` es un Protocol (puerto): get/set/clear. Permite mockear en tests
  o swap a Redis sin tocar callers.
- `TTLCacheStore` es el adaptador real (cachetools.TTLCache, in-process, TTL 24h).
- `DictCacheStore` es el adaptador para tests (dict simple, sin TTL).
- `@snapshot(name)` es un decorator que convierte una funcion de computo
  (`compute(*args) -> dict`) en una funcion cacheada que devuelve
  `{"cache": "hit"|"miss", "data": ...}`. La key se deriva automaticamente
  de los args posicionales. Registra la entry en SNAPSHOTS para que prewarm
  pueda replayar el mismo path.
- `warm(name, args_list)` corre las mismas funciones registradas con sets
  de args, sin duplicar la logica de key/store. Una sola fuente de verdad.

`get_or_compute` y `flush` se mantienen como escape hatch / endpoint de flush.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Callable, Iterable, Protocol

from cachetools import TTLCache
from settings import settings

log = logging.getLogger("minidash.cache")


# ----------- Puerto -----------

class CacheStore(Protocol):
    def get(self, key: str) -> tuple[Any, bool]: ...  # (value, found)
    def set(self, key: str, value: Any) -> None: ...
    def clear(self) -> int: ...


# ----------- Adaptadores -----------

class TTLCacheStore:
    def __init__(self, maxsize: int, ttl: int) -> None:
        self._c: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl)

    def get(self, key: str) -> tuple[Any, bool]:
        if key in self._c:
            return self._c[key], True
        return None, False

    def set(self, key: str, value: Any) -> None:
        self._c[key] = value

    def clear(self) -> int:
        n = len(self._c)
        self._c.clear()
        return n


class DictCacheStore:
    """Para tests: sin TTL, sin maxsize."""

    def __init__(self) -> None:
        self._d: dict[str, Any] = {}

    def get(self, key: str) -> tuple[Any, bool]:
        if key in self._d:
            return self._d[key], True
        return None, False

    def set(self, key: str, value: Any) -> None:
        self._d[key] = value

    def clear(self) -> int:
        n = len(self._d)
        self._d.clear()
        return n


# ----------- Singleton del proceso -----------

# maxsize subio a 4096 cuando agregamos bucket_cache (L2): un range de 24m * 4 filtros
# = ~2900 buckets potenciales. 32 alcanzaba para los snapshots L1 pero no para esto.
_store: CacheStore = TTLCacheStore(maxsize=4096, ttl=settings.cache_ttl_seconds)


def set_store(store: CacheStore) -> None:
    """Para tests: reemplazar el store del proceso."""
    global _store
    _store = store


# ----------- Registry + decorator -----------

SNAPSHOTS: dict[str, tuple[Callable, Callable[[], list[str]] | None]] = {}

# Contadores de hit/miss por key, solo de llamadas user-driven (no `warm()`).
# Sirven para responder "vale la pena bucket-cachear?" midiendo cuanto pega
# el usuario en keys distintas al default prewarmeado.
STATS: dict[str, dict[str, int]] = {}


def _record(key: str, hit: bool) -> None:
    s = STATS.setdefault(key, {"hits": 0, "misses": 0})
    s["hits" if hit else "misses"] += 1


def _fmt(v: Any) -> str:
    if isinstance(v, dt.date):
        return v.isoformat()
    return str(v)


def _build_key(name: str, args: tuple, key_extras: Callable[[], list[str]] | None) -> str:
    parts = [_fmt(a) for a in args]
    if key_extras is not None:
        parts.extend(key_extras())
    return f"{name}:" + ":".join(parts)


def snapshot(name: str, key_extras: Callable[[], list[str]] | None = None):
    """
    Convierte `compute(*args) -> dict` en `cached(*args) -> {"cache": ..., "data": ...}`.

    - Cache key = f"{name}:{arg1}:{arg2}:..." con dates -> isoformat.
    - `key_extras()` agrega segmentos calculados (ej: today.isoformat() para rotacion diaria).
    - Registra `name` en SNAPSHOTS para que `warm()` replay el mismo path.
    """
    if name in SNAPSHOTS:
        raise ValueError(f"snapshot '{name}' ya registrado")

    def decorator(compute_fn: Callable):
        SNAPSHOTS[name] = (compute_fn, key_extras)

        def wrapped(*args):
            key = _build_key(name, args, key_extras)
            value, hit = _store.get(key)
            _record(key, hit)
            if hit:
                return {"cache": "hit", "data": value}
            value = compute_fn(*args)
            _store.set(key, value)
            return {"cache": "miss", "data": value}

        wrapped.__name__ = f"cached_{name.replace('-', '_')}"
        wrapped.__wrapped__ = compute_fn  # type: ignore[attr-defined]
        return wrapped

    return decorator


def warm(name: str, args_list: Iterable[tuple]) -> int:
    """
    Replay del path cacheado para una lista de tuplas de args.
    Devuelve cuantas entries se computaron (las hits no cuentan).
    """
    if name not in SNAPSHOTS:
        log.warning(f"warm: snapshot '{name}' no registrado")
        return 0
    compute_fn, key_extras = SNAPSHOTS[name]
    misses = 0
    for args in args_list:
        key = _build_key(name, args, key_extras)
        _, hit = _store.get(key)
        if hit:
            continue
        value = compute_fn(*args)
        _store.set(key, value)
        misses += 1
    return misses


# ----------- Escape hatch / API publica historica -----------

def get_or_compute(key: str, compute_fn: Callable[[], Any]) -> tuple[Any, bool]:
    """
    Acceso de bajo nivel al store. Para casos donde el shape (name+args -> key)
    no encaja con `@snapshot` (response no-JSON, key irregular, etc.).
    """
    value, hit = _store.get(key)
    if hit:
        return value, True
    value = compute_fn()
    _store.set(key, value)
    return value, False


def flush() -> int:
    return _store.clear()


def stats_summary(top_keys: int = 20) -> dict:
    """
    Vista agregada de STATS: totales por snapshot + top-K keys por uso.
    `name` se extrae del prefijo de la key (`"<name>:..."`).
    """
    by_name: dict[str, dict[str, int]] = {}
    rows: list[tuple[str, int, int]] = []
    for key, s in STATS.items():
        name = key.split(":", 1)[0]
        agg = by_name.setdefault(name, {"hits": 0, "misses": 0, "keys": 0})
        agg["hits"] += s["hits"]
        agg["misses"] += s["misses"]
        agg["keys"] += 1
        rows.append((key, s["hits"], s["misses"]))
    rows.sort(key=lambda r: r[1] + r[2], reverse=True)
    return {
        "by_name": by_name,
        "top_keys": [
            {"key": k, "hits": h, "misses": m, "hit_rate": h / (h + m) if (h + m) else 0.0}
            for k, h, m in rows[:top_keys]
        ],
        "total_keys": len(STATS),
    }


def clear_stats() -> int:
    n = len(STATS)
    STATS.clear()
    return n
