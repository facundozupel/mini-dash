# MiniDash Endado — instrucciones para Claude

Dashboard CRO mini que consume la DB existente de **endado.com** (read-only) y renderiza KPIs, tabla y trend de 24 meses. Pensado como proyecto de aprendizaje de la arquitectura "API + cache + frontend estatico".

## Stack

- **Backend:** FastAPI + psycopg3 + pydantic-settings + cachetools (TTL 24h in-process)
- **DB:** Postgres 14 en VPS (5.161.212.136), DB `endado` (no la modificamos, solo lectura)
- **Frontend:** HTML + CSS + JS vanilla + Chart.js CDN (servido por la misma FastAPI desde `/`)
- **Deploy futuro:** Render Static (front) + VPS Docker Swarm/Traefik (API) en `minidash.facundo.click`

## Estructura

```
MICRO-DASH/
├── backend/
│   ├── api.py          ← FastAPI, endpoints, CORS, prewarm, sirve frontend
│   ├── settings.py     ← .env via pydantic-settings
│   ├── cache.py        ← CacheStore (Protocol) + TTL/Dict adapters + @snapshot decorator (L1) + warm() + STATS
│   ├── bucket_cache.py ← BucketCache (L2): cachea componentes per-day y compone rangos
│   ├── metrics.py      ← KPIs (current/MoM/YoY) + trend 24m + FILTERS dict (usa bucket cache)
│   ├── tables.py       ← top_pages / opportunities / cannibalization
│   ├── db/conn.py      ← psycopg3 pool
│   ├── queries/        ← (vacio por ahora; SQL inline en metrics.py/tables.py)
│   ├── pyproject.toml
│   ├── Dockerfile      ← (para deploy)
│   ├── .env.example
│   └── .env            ← NUNCA al repo
└── frontend/
    ├── index.html      ← Date picker + tabs + KPIs + chart toolbar + tablas
    ├── main.js         ← Estado, fetch, render, recargas segmentadas
    └── styles.css      ← Dark theme
```

## Endpoints actuales

| Endpoint | Cambia con | Cache key |
|---|---|---|
| `GET /metrics/{filter}?from=&to=` | date picker | `metrics:{filter}:{from}:{to}` |
| `GET /trend/{filter}?granularity=day\|week\|month` | granularity (rango fijo 24m) | `trend:{filter}:{gran}:{today}` |
| `GET /tables/top-products?from=&to=&limit=20` | date picker | `top-products:{from}:{to}:{limit}` |
| `GET /tables/top-categories?from=&to=&limit=20` | date picker | igual |
| `GET /tables/opportunities?from=&to=&limit=50&min_imp=500` | date picker | `opportunities:{from}:{to}:{limit}:{min_imp}` |
| `GET /tables/cannibalization?from=&to=&limit=50` | date picker | igual |
| `GET /health` | — | — |
| `POST /internal/cache/flush` (header `x-internal-secret`) | — | — |
| `GET /internal/cache/stats?top_keys=N` (header `x-internal-secret`) | — | — |
| `POST /internal/cache/stats/clear` (header `x-internal-secret`) | — | — |

`FILTERS` en `metrics.py`:
- `overall` — `TRUE`
- `products` — `prod_bool = true`
- `category` — `prod_bool = false AND blog = false AND page NOT IN ('https://www.endado.com/', 'http://www.endado.com/')`
- `recambios` — `page ILIKE '%%/recambios/%%'`

## Reglas de SQL (IMPORTANTE)

1. **Impresiones globales (page-level):** `SUM(impressions)` directo sobre `extraccion_gsc_page`.
2. **Impresiones por query:** dedup con `MAX(impressions) GROUP BY query, date` y luego `SUM`. La tabla `extraccion_gsc` duplica impresiones entre URLs de una misma query.
3. **Clicks:** SIEMPRE `SUM` directo (no se duplican).
4. **Posicion promedio:** ponderada por impresiones → `SUM(position*impressions)/SUM(impressions)`. Sin esto queda sesgada.
5. **`%` literal en SQL templates:** escapar como `%%` cuando va por psycopg con params (`%s`/`%(name)s`). Ejemplo: `page ILIKE '%%/recambios/%%'`.

## Comparativas MoM/YoY

Calculadas en `metrics.compute_ranges(from_, to_)`:
- **MoM** = periodo previo de **igual duracion** (no mes calendario). Si rango es 7d, MoM son los 7d previos.
- **YoY** = mismo rango exacto, **365 dias atras**.

## Cache strategy — dos capas

Hay dos capas de cache que comparten el mismo `_store` (un solo `TTLCache`):

- **L1 — `@snapshot` (cache.py):** cachea la **respuesta final** por `(name, args)`. Key tipica: `metrics:overall:2026-03-25:2026-04-23`. Se llena via prewarm o el primer hit. Si el rango se repite identico → hit instantaneo.
- **L2 — `BucketCache` (bucket_cache.py):** cachea **componentes per-day** que componen una metric. Key tipica: `kpi_bucket:overall:2026-04-15`. Sirve cuando L1 missea pero el rango solapa con dias ya vistos. Solo `compute_metrics` usa L2 (ver seccion "Bucket cache (L2)" abajo).

Modulo `cache.py` esta "deepado" (modulo profundo en el sentido de Ousterhout): interfaz chica, implementacion grande detras.

**Componentes:**

- `CacheStore` (Protocol): contrato `get/set/clear`. Sirve para swap a Redis o mockear en tests sin tocar callers.
- `TTLCacheStore`: adaptador real, wrapea `cachetools.TTLCache(maxsize=4096, ttl=86400)` (1 worker, 1 cache, in-process). El maxsize esta dimensionado para los buckets: 24m × 4 filtros = ~2900 keys posibles solo de `kpi_bucket`.
- `DictCacheStore`: adaptador para tests, dict pelado sin TTL.
- `_store: CacheStore`: singleton del proceso. Reasignable via `set_store()` para tests.
- `STATS: dict[str, {hits, misses}]`: contadores per-key, alimentados desde L1 y L2 user-driven (NO desde `warm()`). Sirven para medir hit rate real y decidir si vale la pena agregar bucket cache a un endpoint nuevo.

**`@snapshot(name, key_extras=None)` — decorator:**

Convierte una funcion `compute(*args) -> dict` en una funcion cacheada que devuelve `{"cache": "hit"|"miss", "data": ...}`. La cache key se deriva auto de los args posicionales (dates → ISO; resto → `str()`). Registra `(name, compute_fn, key_extras)` en `SNAPSHOTS` para que `warm()` replay el mismo path.

```python
# Uso normal
cached_metrics = snapshot("metrics")(compute_metrics)
# Llamado: cached_metrics("overall", from_, to_) → key "metrics:overall:2026-04-22:2026-05-22"

# Con key_extras (para rotacion diaria del trend)
cached_trend = snapshot("trend", key_extras=lambda: [dt.date.today().isoformat()])(compute_trend)
# Llamado: cached_trend("overall", "month") → key "trend:overall:month:2026-04-25"

# Adaptando args (top-products comparte compute_top_pages con top-categories)
cached_top_products = snapshot("top-products")(lambda f, t, lim: compute_top_pages("products", f, t, lim))
```

**`warm(name, args_list)`:** replay del path cacheado para una lista de tuplas. Una sola fuente de verdad para keys → prewarm y endpoints **no pueden driftear**.

**Prewarm en background al boot** (`api.lifespan`):
- 4 metrics (default range = ultimos 30d ending today-3)
- 12 trends (4 filtros × 3 granularidades, rango fijo 24m)
- 4 tablas (default range)
- Tarda ~60s. No bloquea el startup (corre en task asyncio).

**Invalidacion manual:** `POST /internal/cache/flush` con header `x-internal-secret: $INTERNAL_SECRET`.

**Escape hatch:** `get_or_compute(key, compute_fn)` sigue exportado para casos donde el shape `name+args -> key` no encaja (response no-JSON, key irregular, etc).

## Bucket cache (L2)

### El problema que resuelve

L1 (`@snapshot`) cachea por `(filtro, from, to)` exacto. Si el usuario pide `2026-03-25 → 2026-04-23` y despues `2026-03-26 → 2026-04-24`, son keys **distintas** y la segunda llamada **recomputa todo** el rango aunque solapa 29 dias con la primera.

Para `compute_metrics` esto es peor de lo que parece: cada llamada calcula 3 ventanas (current + MoM + YoY). Mover el date picker un dia = recomputar 90 dias de datos cuando 88 ya estaban hechos.

Lo medimos antes de decidir (endpoint `/internal/cache/stats`). En sesion real:
- L1 hit rate de `metrics`: 81% — pero solo porque el usuario repitio 2 rangos. Cualquier rango nuevo = miss completo.
- Distintos rangos vistos: 2. Si ese numero crece (uso real de date picker), L1 solo deja de servir.

### La idea

Si la metric se descompone en **componentes aditivos**, podemos:
1. Cachear los componentes crudos por dia.
2. Para cualquier rango, juntar los buckets ya cacheados, computar **solo los dias faltantes en una sola query batch**, y componer.

Asi un rango que solapa 90% con otro previo solo gatilla 10% del trabajo.

### Cuando NO usar bucket cache

- **Top-N:** top-20 del mes ≠ union de tops diarios. No compone.
- **Percentiles, medianas, distinct counts:** no son aditivos.
- **Metricas que requieren ver toda la ventana en una pasada** (ej: ranking, dedup global).

Por eso `/tables/*` se quedan solo con L1.

### Cuando SI usar

- **SUM, COUNT, MIN, MAX:** triviales, asociativos.
- **AVG ponderada:** cachea numerador y denominador como componentes; divide al componer.
- **CTR = clicks/impressions:** misma idea — cachea ambos, divide al final.

### Como funciona en metrics.py

KPIs son SUM, SUM, ratio (CTR), AVG ponderada (position). Todo compone:

```python
# Componentes per-day (lo que va al cache):
{
  "clicks":      SUM(clicks)              -- aditivo
  "impressions": SUM(impressions)         -- aditivo (= denom de pos avg)
  "pos_num":     SUM(position*impressions)-- aditivo (numerador de pos avg)
}

# Composicion al servir un rango:
clicks_total   = sum(clicks per day)
imp_total      = sum(impressions per day)
ctr            = clicks_total / imp_total
position_avg   = sum(pos_num per day) / imp_total
```

Equivalencia matematica con la SQL anterior:

```
SUM(clicks)        over rango = SUM_d ( SUM(clicks)              per dia )   ✓
SUM(impressions)   over rango = SUM_d ( SUM(impressions)         per dia )   ✓
SUM(pos*imp)/SUM(imp)         = SUM_d(pos_num) / SUM_d(imp)                   ✓
```

### Diseño del framework — `bucket_cache.py`

```python
class BucketCache:
    def __init__(
        self,
        name: str,
        fetch_days: Callable[[str, list[date]], dict[date, Any]],  # batch query
        compose: Callable[[list[Any]], dict],                       # reduce
        empty: Callable[[], Any],                                   # zero para dias sin datos
    ): ...

    def get_range(self, dim_key: str, from_, to_) -> dict:
        # 1. Lookup per dia en _store. Hits van a una lista, misses a otra.
        # 2. Si hay misses, una sola query batch los trae.
        # 3. Cachea los nuevos. Compose con la lista ordenada por fecha.
```

- `dim_key`: lo no-fecha que distingue cuentas (ej: nombre del filtro). Las keys quedan `{name}:{dim_key}:{YYYY-MM-DD}`.
- `fetch_days` recibe **solo los dias faltantes** y devuelve `dict[date, components]`. Usa `WHERE date = ANY(%(days)s)` — una sola roundtrip a Postgres.
- `empty()` se usa para dias sin filas (no las queremos repreguntar). Con TTL de 24h se auto-cura si despues llega data.

### Resultado medido

Reset stats, pedir el rango default (ya prewarmeado) y despues uno corrido 1 dia:

| Test | L1 | L2 (kpi_bucket) |
|---|---|---|
| Default range repeat | hit | no se accede |
| Range corrido 1 dia | miss | 88 hits + 2 misses (sobre 90 accesos) |

Los 90 accesos = 30 dias × 3 ventanas (current+MoM+YoY). Solo 2 dias nuevos: el `to_+1` del current y el `to_+1` del yoy. MoM no agrega nada porque `Mar 25` (su nuevo `to_`) ya estaba cacheado como parte del current original.

Costo: una query batch que pide 2 fechas en vez de 90 dias × 3 queries.

### Como agregar bucket cache a un endpoint nuevo

1. Verificar que la metric **compone** (regla: si no es SUM/COUNT/MIN/MAX/AVG-ponderada, probablemente no).
2. Definir los componentes crudos. Para AVG ponderada acordate de cachear numerador y denominador por separado.
3. Escribir `_fetch_X_days(dim_key, days) -> dict[date, components]` con una sola query `WHERE date = ANY(%(days)s)`.
4. Escribir `_compose_X(comps) -> dict` (la metric final).
5. Escribir `_empty_X_day() -> dict` (todos los componentes en cero).
6. Instanciar `BucketCache(name, fetch, compose, empty)` y llamarla desde la funcion de computo. La capa L1 (`@snapshot`) sigue arriba sin cambios.

## Como agregar un endpoint cacheado nuevo

1. Escribi la funcion de computo en `metrics.py` o `tables.py` (tiene que devolver un dict serializable).
2. En `api.py`, registrala con `@snapshot`:
   ```python
   cached_foo = snapshot("foo")(compute_foo)
   ```
3. Agrega el route handler que parsea params y delega:
   ```python
   @app.get("/foo")
   def foo(...):
       return cached_foo(*args)
   ```
4. Agrega prewarm en `_prewarm()`:
   ```python
   warm("foo", [(args1,), (args2,)])
   ```

Eso es todo. La key se deriva sola, el response queda envuelto, prewarm replay el mismo path.

## Como testear (futuro)

```python
from cache import set_store, snapshot, DictCacheStore

def test_cache_hits():
    set_store(DictCacheStore())
    calls = {"n": 0}

    @snapshot("test")
    def compute(x):
        calls["n"] += 1
        return {"v": x * 2}

    assert compute(5) == {"cache": "miss", "data": {"v": 10}}
    assert compute(5) == {"cache": "hit",  "data": {"v": 10}}
    assert calls["n"] == 1
```

Sin Postgres, sin FastAPI, milisegundos.

## DB notes

- **Indices que CREAMOS** en la DB endado (CONCURRENTLY desde dentro del VPS):
  - `idx_gscpage_date ON extraccion_gsc_page(date)` (~18 MB)
  - `idx_gsc_date ON extraccion_gsc(date)` (~121 MB)
  - Sin estos, opportunities/cannibalization timeout (>60s).
- **No modificar nada mas** en la DB endado sin confirmacion explicita del usuario.
- Connect: `host=5.161.212.136 port=5432 dbname=endado user=postgres password=Crossfit29`
  - Para operaciones largas (CREATE INDEX, ANALYZE), correr DESDE el VPS via SSH+docker exec, no desde la Mac (la conexion se cae a los ~18 min).

## Comandos comunes

```bash
# Instalar deps
cd backend && python3 -m venv .venv
.venv/bin/pip install "fastapi>=0.115" "uvicorn[standard]>=0.32" "psycopg[binary,pool]>=3.2" \
    "pydantic-settings>=2.6" "pyjwt>=2.9" "cachetools>=5.5" "google-auth>=2.35" "python-multipart>=0.0.12"

# Levantar local (sirve frontend tambien)
.venv/bin/uvicorn api:app --host 127.0.0.1 --port 8000 --log-level info

# Acceder
open http://localhost:8000

# SSH al VPS personal
sshpass -p 'KWJuVnufxNLxLcWtqHxL' ssh -o StrictHostKeyChecking=no root@5.161.212.136 '<comando>'

# psql contra endado en VPS
docker exec -i $(docker ps -q -f name=postgres_postgres) psql -U postgres -d endado
```

## Convenciones

- Respuestas al usuario: **paso a paso, Feynman, poco verboso**. Una accion por turno cuando es grande, confirmar antes de pasos siguientes.
- SQL templates con `{filter}` placeholder usan `.format()`. Filtros vienen del dict hardcoded `FILTERS` (no user input → safe).
- Dates en respuestas API: ISO `YYYY-MM-DD` strings. Backend acepta `from`/`to` como `date` y los pasa a psycopg como objetos.
- Cache miss vs hit: cada response incluye `{"cache": "hit"|"miss", "data": {...}}` para debug.
- **No escribir cache keys a mano.** Usar `@snapshot(name)` y `warm(name, args_list)`. Si haces `get_or_compute(f"foo:{x}")` en un caller nuevo, estas reintroduciendo el bug que el refactor mato (drift entre lugares que construyen la misma key).

## Estado actual del proyecto

✅ Hecho:
- Plumbing local end-to-end (DB ↔ API ↔ frontend)
- 4 endpoints `/metrics/*` con MoM/YoY
- 4 endpoints `/tables/*` con comparativas
- `/trend/*` panoramico 24 meses, 3 granularidades
- Cache TTL + prewarm + invalidacion manual
- Cache "deepada": `@snapshot` decorator (L1) + `CacheStore` Protocol (testeable sin DB)
- **Bucket cache (L2)** para `compute_metrics`: componentes per-day reusables entre rangos solapados (88/90 hits movienso el picker 1 dia)
- Stats hit/miss per-key (`/internal/cache/stats`) para medir antes de optimizar
- Frontend con date picker, tabs, granularity, loading UX
- Indices en DB endado

⏳ Pendiente:
- Auth (Google Sign-In + JWT + whitelist) — `GOOGLE_CLIENT_ID` y `JWT_SECRET` ya estan en `.env.example`
- Deploy: Dockerizar y subir al VPS con Traefik en `minidash.facundo.click`
- Frontend separado en Render (configurar CORS bien)
- Tests unitarios de `cache.py` (ya es posible con `DictCacheStore`, falta escribirlos)

## Gotchas conocidos

- `auto-warm` puede tardar ~60s la primera vez. No bloquea pero la pagina inicial puede mostrar loading mientras corre.
- Granularity `day` sobre 24m = ~700 puntos. Chart.js lo renderiza ok pero puede laguear en mobile. Default `month` por eso.
- `extraccion_gsc.cat` puede ser `null` → frontend muestra "—".
- El Mac de Facu se desconecta de Postgres si una query tarda >18min. Usar SSH+docker exec para operaciones largas.
