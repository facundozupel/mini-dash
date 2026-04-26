"""
Bucket cache: una capa L2 para metricas time-range.

## El problema

`@snapshot` cachea por (filtro, from, to). Si el usuario pide
2026-03-25→2026-04-23 y despues 2026-03-26→2026-04-24, son keys distintas
y la segunda recomputa TODO el rango aunque solapa 29 dias.

## La idea

Si la metric se descompone en componentes aditivos (o ratios de sumas),
podemos:
  1. Cachear componentes crudos por dia.
  2. Para cualquier rango, juntar los buckets ya cacheados, computar solo
     los dias faltantes en UNA sola query batch, y componer.

Asi un rango que solapa 90% con otro previo solo gatilla 10% del trabajo.

## Cuando NO usar

- Top-N (top-20 del mes != union de tops diarios → no compone).
- Percentiles, medianas, distinct counts (no son aditivos).
- Metricas que requieren ver toda la ventana en una pasada.

## Cuando SI usar

- SUM, COUNT, MIN, MAX → triviales.
- AVG ponderada → cachea numerador y denominador, divide al componer.
- CTR = clicks/impressions → cachea ambos, divide al componer.

## Diseno

`BucketCache(name, fetch_days, compose, empty)`:

- `fetch_days(dim_key, days) -> dict[date, components]`:
    Recibe los dias FALTANTES y devuelve un dict. Una sola query batch.
    El dim_key es lo que distingue cuentas/filtros (no-fecha). Por ejemplo,
    el nombre del filtro de paginas.

- `compose(components_list) -> dict`:
    Recibe la lista ordenada por fecha de los componentes (cacheados o
    recien fetcheados) y devuelve la metric final.

- `empty()`:
    Componente "cero" para dias sin datos. Necesario porque fetch_days
    solo devuelve dias con filas; los dias vacios igual se cachean para
    no re-preguntar.

Las keys son `{name}:{dim_key}:{YYYY-MM-DD}` y se guardan en el `_store`
compartido con `@snapshot`. Eso significa que `flush()` los limpia juntos
y las stats los registran junto a los snapshots de L1.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Callable

from cache import _record, _store


class BucketCache:
    def __init__(
        self,
        name: str,
        fetch_days: Callable[[str, list[dt.date]], dict[dt.date, Any]],
        compose: Callable[[list[Any]], dict],
        empty: Callable[[], Any],
    ) -> None:
        self.name = name
        self.fetch_days = fetch_days
        self.compose = compose
        self.empty = empty

    def _key(self, dim_key: str, day: dt.date) -> str:
        return f"{self.name}:{dim_key}:{day.isoformat()}"

    def get_range(self, dim_key: str, from_: dt.date, to_: dt.date) -> dict:
        days = [from_ + dt.timedelta(days=i) for i in range((to_ - from_).days + 1)]
        cached: dict[dt.date, Any] = {}
        missing: list[dt.date] = []

        # 1) Lookup por dia. Cada acceso suma a STATS (igual que @snapshot).
        for day in days:
            key = self._key(dim_key, day)
            value, hit = _store.get(key)
            _record(key, hit)
            if hit:
                cached[day] = value
            else:
                missing.append(day)

        # 2) Si hay dias faltantes, una sola query batch los trae todos.
        if missing:
            fetched = self.fetch_days(dim_key, missing)
            for day in missing:
                comp = fetched.get(day, self.empty())
                _store.set(self._key(dim_key, day), comp)
                cached[day] = comp

        # 3) Compose en orden cronologico.
        return self.compose([cached[d] for d in days])
