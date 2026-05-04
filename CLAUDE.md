# MiniDash Endado — instrucciones para Claude

Dashboard CRO mini que consume la DB existente de **endado.com** (read-only) y renderiza KPIs, tabla y trend de 24 meses. Pensado como proyecto de aprendizaje de la arquitectura "API + cache + frontend estatico".

## Stack

- **Backend:** FastAPI + psycopg3 + pydantic-settings + cachetools (TTL 24h in-process)
- **DB:** Postgres 14 en VPS (5.161.212.136), DB `endado`. Las tablas raw (`public.*`) son read-only para nosotros. Schemas propios:
  - `staging.*` — limpieza/normalizacion de raw (TABLE incremental dbt)
  - `marts.*` — agregaciones daily-grain pre-explotadas por filter (TABLE/MV/VIEW dbt)
- **Modelado de datos:** dbt-core 1.11 + dbt-postgres en VPS endado, refresh diario 03:00 via cron + Healthchecks.io. Ver "Arquitectura de schemas (dbt)" abajo.
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
│   ├── metrics.py      ← KPIs current/MoM/YoY + trend 24m, lee marts.kpis_diario via bucket cache
│   ├── tables.py       ← top_pages / opportunities / cannibalization, leen marts.*
│   ├── db/conn.py      ← psycopg3 pool
│   ├── pyproject.toml
│   ├── Dockerfile      ← (para deploy)
│   ├── .env.example
│   └── .env            ← NUNCA al repo
├── dbt/                ← proyecto dbt-core (modela raw -> staging -> marts)
│   ├── dbt_project.yml
│   ├── packages.yml         ← dbt_utils
│   ├── profiles.yml.example ← copiar a ~/.dbt/profiles.yml en el VPS (con creds reales)
│   ├── refresh.sh           ← script que corre el cron 03:00 + ping a Healthchecks.io
│   ├── macros/
│   │   └── generate_schema_name.sql ← override: usa +schema custom sin concatenar target
│   ├── models/
│   │   ├── sources.yml      ← raw declarada (extraccion_gsc, extraccion_gsc_page) + freshness
│   │   ├── staging/         ← 2 modelos incremental con lookback 7d
│   │   └── marts/           ← 5 modelos: 1 TABLE + 4 MV
│   └── tests/               ← custom test de equivalencia raw vs marts
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
| `POST /internal/cache/prewarm` (header `x-internal-secret`) | — | re-corre `_prewarm()` con `today()` actual. Lo llama el cron de dbt 03:00 para que el cache rote con el dia. |
| `GET /internal/cache/stats?top_keys=N` (header `x-internal-secret`) | — | — |
| `POST /internal/cache/stats/clear` (header `x-internal-secret`) | — | — |

`FILTERS` en `metrics.py` ahora es un `frozenset` con los nombres validos:
`{"overall", "products", "category", "recambios"}`. Las **definiciones SQL viven en dbt**
(`models/marts/kpis_diario.sql` y `top_pages_diario.sql`) pre-explotadas como columna
`filter` en marts. Filtros mutuamente excluyentes:

- `overall` — todas las paginas
- `products` — `is_product`
- `category` — `NOT is_product AND NOT is_blog AND NOT is_home AND NOT is_recambio`
- `recambios` — `is_recambio` (URLs con `/recambios/`)

## Reglas de SQL (IMPORTANTE)

Las reglas que antes habia que respetar en CADA query del backend ahora estan
**pre-aplicadas en marts** por dbt. Quedan como referencia para entender por que el
modelo es asi, no como reglas a aplicar al escribir queries nuevas.

1. **Impresiones globales (page-level):** `SUM(impressions)` directo. Aplicado en
   `marts.kpis_diario` y `marts.top_pages_diario`.
2. **Impresiones por query:** dedup `MAX(impressions) GROUP BY query, date`. La tabla
   `extraccion_gsc` duplica impresiones entre URLs de una misma query. Pre-calculado
   como `impressions_dedup` en `marts.kpis_query_diario`.
3. **Clicks:** SIEMPRE `SUM` directo (no se duplican).
4. **Posicion promedio:** ponderada por impresiones → `SUM(pos_num)/SUM(impressions)`.
   `pos_num = position * impressions` se pre-calcula en staging.
5. **Filter como bind param**, no template SQL. Ejemplo:
   `WHERE event_date = ANY(%(days)s) AND filter = %(filter)s`. Ya no se usa
   `sql.format(filter=...)`.

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

## Arquitectura de schemas (dbt)

Pipeline `raw -> staging -> marts` orquestado por dbt-core en VPS endado, refresh
diario 03:00 via cron + Healthchecks.io.

### staging.* (limpieza, mix de TABLE incremental + VIEW)

| Tabla | Materializacion | Grano | Filas | Sirve a |
|---|---|---|---|---|
| `staging.endado_gsc_page_daily`       | TABLE incremental (lookback 7d) | (page, event_date)        | 2.7M  | base page-level, alimenta marts.kpis_diario y marts.top_pages_diario |
| `staging.endado_gsc_query_page_daily` | **VIEW** (sin materializar)     | (query, page, event_date) | 18M   | base query-level, alimenta marts.kpis_query_diario y los 2 canib marts |

> `endado_gsc_query_page_daily` era TABLE incremental (3.4 GB) pero las transformaciones
> aplicadas son CPU-cheap (CASE/COALESCE/cast/multiplicacion) y los marts downstream
> son MV/TABLE full-refresh, asi que recomputan todo igual. Pasarla a VIEW libera 3.4 GB
> en disco. Pre-requisito: `public.extraccion_gsc` necesita BRIN(date) (no btree) para
> que los marts consumidores filtren rangos amplios sin caer en seq scan.

Las staging tienen **flags pre-calculados** (`is_product`, `is_blog`, `is_brand`,
`is_recambio`, `is_home`) y **`pos_num = position * impressions`** listo para componer
posicion ponderada.

Lookback 7d: `WHERE date > MAX(event_date) - 7d`. Captura ajustes tardios de GSC
(que reescribe datos hasta una semana atras).

### marts.* (agregaciones daily-grain)

| Mart | Tipo | Grano | Sirve a |
|---|---|---|---|
| `marts.kpis_diario`             | TABLE | (filter, event_date)        | `/metrics/*`, `/trend/*` (filter pre-explotado, ~2.5k filas) |
| `marts.top_pages_diario`        | MV    | (filter, page, event_date)  | `/tables/top-products`, `/tables/top-categories` (~10M filas, MV con CONCURRENTLY) |
| `marts.kpis_query_diario`       | MV    | (query, event_date)         | `/tables/opportunities` (impressions_dedup pre-calculada) |
| `marts.canib_query_page_daily`  | MV    | (query, page, event_date)   | `/tables/cannibalization` (detalle URLs) + top_url para opportunities. Era VIEW pero el seq scan a staging mataba la perf — promovido a MV con BRIN(event_date)+btree(query). |
| `marts.canib_query_summary_daily` | MV  | (query, event_date)         | `/tables/cannibalization` (filtrado y ranking inicial). Resumen sin la dimension `page`: clicks, impressions_dedup, all_product_today, array `pages`. Sirve para identificar queries canibalizantes en single-pass; el detalle se trae despues solo para las ~50 que pasan el filtro. |

### Refresh strategy

Cron diario 03:00 en VPS endado: `0 3 * * * /opt/MICRO-DASH/dbt/refresh.sh`.

El script:
1. Ping `/start` a Healthchecks.io.
2. `dbt run` (incremental para staging, full para marts).
3. Si OK → ping `/` con cola del log; si falla → ping `/fail`.
4. Healthchecks alerta por mail si no llega ping en 25h (1d schedule + 1h grace).

Logs: `/var/log/dbt-minidash/run-YYYY-MM-DD-HHMM.log`.

### Healthchecks.io

- Account: `facundozupel29@gmail.com`
- Check: `facundo` (UUID `3575e2ee-5d61-4dd6-be49-3e30f5e1dc6f`)
- Free tier (1 check de 20 disponibles, sin tarjeta).

### Comandos dbt utiles (en el VPS)

```bash
cd /opt/MICRO-DASH/dbt
/opt/MICRO-DASH/.venv-dbt/bin/dbt parse        # valida YAML/Jinja
/opt/MICRO-DASH/.venv-dbt/bin/dbt debug        # valida conexion a Postgres
/opt/MICRO-DASH/.venv-dbt/bin/dbt run          # corre todo (incremental + full)
/opt/MICRO-DASH/.venv-dbt/bin/dbt run --select +marts.kpis_diario  # solo este mart + sus deps
/opt/MICRO-DASH/.venv-dbt/bin/dbt test         # corre los 33 tests
/opt/MICRO-DASH/.venv-dbt/bin/dbt seed         # n/a, no usamos seeds
```

### Como agregar un mart nuevo

1. Escribi `dbt/models/marts/nuevo_mart.sql` con el SQL + `{{ config(materialized=...) }}`.
   Referencia staging con `{{ ref('endado_gsc_X_daily') }}`.
2. Agregalo a `dbt/models/marts/schema.yml` con tests `unique_combination_of_columns` y `not_null`.
3. rsync + correr `dbt run --select nuevo_mart`. Verifica con `dbt test --select nuevo_mart`.
4. Backend: agregar el SQL/funcion en `metrics.py` o `tables.py` que lee del nuevo mart.
5. Endpoint en `api.py` con `@snapshot` (ver "Como agregar un endpoint cacheado nuevo").

## DB notes

- **Indices CREADOS por nosotros** en la DB endado:
  - `public`: indices base (creados manualmente al inicio del proyecto)
  - `staging.*` y `marts.*`: indices declarados en cada modelo dbt via `+indexes` config
- **No modificar nada manual** en `public.*` (raw) ni en MVs creadas por dbt: si necesitas cambiar
  staging/marts, edita el SQL en `dbt/models/` y `dbt run --select <modelo>`.
- Connect: `host=5.161.212.136 port=5432 dbname=endado user=postgres password=Crossfit29`
- Para operaciones largas, correr DESDE el VPS via SSH+docker exec, no desde la Mac (conexion se cae a los ~18 min).

## Performance: BRIN + ANALYZE + work_mem

Aprendizajes del tuning de queries con rangos amplios sobre marts de 10M+ filas.
Si una tabla nueva tarda >1s en frio, revisar estas 3 cosas en orden:

### 1. BRIN > btree para `event_date`

btree(event_date) **lo descarta el planner** cuando el rango devuelve >1% de la
tabla (caso tipico: 30d sobre 2 años = 4%). Postgres elige seq scan a 18M filas → 8s+.

BRIN ocupa **96 KB para indexar 3 GB** y gana lejos cuando los rows estan ordenados
fisicamente. Como las staging son `incremental` y appendean por fecha, ya estan
ordenadas. Para una MV nueva, agregar `ORDER BY event_date` al final del SELECT
para que el storage clusteree.

Aplicado en:
- `public.extraccion_gsc` y `public.extraccion_gsc_page` (BRIN reemplazando btree — necesario porque `staging.endado_gsc_query_page_daily` es VIEW y los marts filtran event_date directo contra raw)
- `staging.endado_gsc_page_daily` (BRIN reemplazando btree)
- `marts.canib_query_page_daily` y `marts.canib_query_summary_daily` (BRIN(event_date) + btree(query))
- `marts.kpis_query_diario` (BRIN reemplazando btree(event_date) + ORDER BY event_date al final del SELECT → MV clusterada por fecha)
- `marts.top_pages_diario` (BRIN reemplazando btree(filter,event_date) + ORDER BY event_date dentro de un wrap del UNION ALL)

### 2. ANALYZE despues de cada `dbt run`

dbt **NO** corre ANALYZE automatico. Stats viejas → planner elige seq scan aunque
exista BRIN. Smoking gun: misma query pasa de 10s → 200ms con un solo `ANALYZE`.

Por eso `dbt/refresh.sh` corre ANALYZE de los marts + raw despues del refresh
diario. Si agregas un mart nuevo, sumalo al bloque ANALYZE del script. Importante:
**incluye `public.extraccion_gsc` y `public.extraccion_gsc_page`** porque la
staging query-level es VIEW y los marts filtran event_date directo contra raw.

### 3. `work_mem` por sesion

El default 4MB es bajisimo para sorts/groupbys >50k filas. EXPLAIN muestra
`Sort Method: external merge Disk: 32MB` = sort a tempfiles = 5-10x mas lento.

Solucion: `cur.execute("SET LOCAL work_mem = '256MB'")` al inicio de cada query
pesada. Ya aplicado en `compute_top_pages`, `compute_opportunities`,
`compute_cannibalization`. Es por-sesion, no afecta otros workers.

### 4. Patron filter-first, lookup-later

Para tablas top-N con detalle pesado (ej: canib que ademas trae las URLs de
cada query): NO traigas el detalle para el universo completo. Primero filtra y
rankea con datos liviana (un mart pequeño per (query, dia)), tira el LIMIT N, y
DESPUES traes el detalle solo para esas N filas (lookup por lista).

Ejemplo concreto en canib:
- Antes: scan completo de `marts.canib_query_page_daily` (grano page, ~14M filas)
  para identificar queries canib + el detalle de URLs en una sola query → 23s
  en custom range.
- Ahora: scan de `marts.canib_query_summary_daily` (grano query, ~14M filas pero
  ~10x mas livianas porque sin dimension page) para identificar las top-50
  queries; despues `WHERE query IN (SELECT query FROM canib)` contra
  `canib_query_page_daily` para traer URLs SOLO de esas 50 → 6s en custom range.

Es la misma idea que esta atras del patron `LIMIT antes del JOIN pesado`. Si una
tabla tarda mucho en custom range, mira si hay un join/scan que se podria
diferir hasta despues del filtro de top-N.

### 5. Single-pass sobre summary marts (HashAggregate vs Sort)

Cuando una query tiene que agregar Y filtrar contra un computo derivado
(ej: en canib, BOOL_AND(all_product) Y COUNT(DISTINCT pages)), tentacion es
tener 2 CTEs que escanean el mismo mart. Postgres elige Sort+MergeJoin por
default y eso es lento (sort de 700k+ rows = 1.8s).

Truco: agregar todo en una sola pasada con HashAggregate guardando los
componentes derivados en arrays/jsonb, y calcular el filtro derivado como
subselect sobre la fila ya agregada. Subir `work_mem` a 512MB para que el
HashAggregate quepa en RAM (110k buckets × jsonb arrays no entran en 256MB).

Aplicado en `compute_cannibalization`: `jsonb_agg(to_jsonb(pages))` per query,
despues `(SELECT COUNT(DISTINCT page_text) FROM jsonb_array_elements(...))`
solo sobre las queries que pasan el filtro `all_product=true`.

### Como diagnosticar de nuevo

```bash
# EXPLAIN con buffers + timing
docker exec -i $(docker ps -q -f name=postgres_postgres) psql -U postgres -d endado -c "
EXPLAIN (ANALYZE, BUFFERS, SUMMARY)
SELECT ... ;
"
```

Buscar:
- "Seq Scan" sobre tabla grande → revisar indices + correr ANALYZE.
- "Sort Method: external merge Disk" → subir work_mem.
- "loops=N" alto en CTE Scan → CTE materializado escaneado N veces; mergear.
- "Heap Fetches" alto en Index Only Scan → falta VACUUM.

## Deploy productivo

```
[ usuario ]
   ├── https://minidash.facundo.click       → Render Static (frontend/)
   │       (custom domain opcional; default mini-dash.onrender.com)
   └── https://api-minidash.facundo.click   → VPS personal master.facundo.click
           (CNAME → master.facundo.click, DNS only en Cloudflare)
                  └── Traefik (network_public, resolver letsencryptresolver)
                         → mini-dash_api (Swarm) :8000
                                → postgres_postgres (mismo VPS, misma red)
```

Runbook completo: [`deploy/DEPLOY.md`](./deploy/DEPLOY.md).

### Update de codigo (zero-downtime)

```bash
# 1. Local
git push

# 2. VPS: pull, rebuild, force-update del servicio
sshpass -p 'KWJuVnufxNLxLcWtqHxL' ssh root@5.161.212.136 '
  cd /opt/mini-dash && git pull &&
  cd backend && docker build -q -t mini-dash-api:latest . &&
  docker service update --image mini-dash-api:latest --force mini-dash_api
'
```

> **Gotcha**: con imagen local (sin registry) `docker stack deploy` NO actualiza
> el servicio aunque rebuild. Swarm pinnea por digest del primer deploy. Hay que
> usar `docker service update --force`. `stack deploy` solo para cambios al
> `docker-stack.yml` (labels, networks, replicas, env vars).

### Sync de archivos (alternativa al git pull)

`rsync` desde Mac al VPS para iterar rapido sin commit:

```bash
sshpass -p 'KWJuVnufxNLxLcWtqHxL' rsync -az -e "ssh -o StrictHostKeyChecking=no" \
  backend/api.py root@5.161.212.136:/opt/mini-dash/backend/api.py
```

Para dbt: tambien hay que sync a `/opt/MICRO-DASH/dbt` (path historico que usa
el cron). El refresh.sh esta duplicado en ambos paths para evitar sorpresas.

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

# Sync cambios de dbt (Mac -> VPS)
sshpass -p 'KWJuVnufxNLxLcWtqHxL' rsync -avz --delete -e "ssh -o StrictHostKeyChecking=no" \
  dbt/ root@5.161.212.136:/opt/MICRO-DASH/dbt/

# Correr dbt manualmente en el VPS (fuera del cron)
sshpass -p 'KWJuVnufxNLxLcWtqHxL' ssh -o StrictHostKeyChecking=no root@5.161.212.136 \
  'cd /opt/MICRO-DASH/dbt && /opt/MICRO-DASH/.venv-dbt/bin/dbt run'
```

## Convenciones

- Respuestas al usuario: **paso a paso, Feynman, poco verboso**. Una accion por turno cuando es grande, confirmar antes de pasos siguientes.
- **Filter como bind param**, no `.format()`. Los marts ya tienen filter como columna pre-explotada por dbt; los SQL usan `WHERE filter = %(filter)s` con bind seguro.
- Dates en respuestas API: ISO `YYYY-MM-DD` strings. Backend acepta `from`/`to` como `date` y los pasa a psycopg como objetos.
- Cache miss vs hit: cada response incluye `{"cache": "hit"|"miss", "data": {...}}` para debug.
- **No escribir cache keys a mano.** Usar `@snapshot(name)` y `warm(name, args_list)`. Si haces `get_or_compute(f"foo:{x}")` en un caller nuevo, estas reintroduciendo el bug que el refactor mato (drift entre lugares que construyen la misma key).

## Estado actual del proyecto

✅ Hecho:
- Plumbing local end-to-end (DB ↔ API ↔ frontend)
- 4 endpoints `/metrics/*` con MoM/YoY
- 4 endpoints `/tables/*` con comparativas
- `/trend/*` panoramico 24 meses, 3 granularidades
- Cache TTL + prewarm + invalidacion manual + endpoint `/internal/cache/prewarm` para rotacion diaria
- Cache "deepada": `@snapshot` decorator (L1) + `CacheStore` Protocol (testeable sin DB)
- **Bucket cache (L2)** para `compute_metrics`: componentes per-day reusables entre rangos solapados
- Stats hit/miss per-key (`/internal/cache/stats`) para medir antes de optimizar
- Frontend con date picker, tabs, granularity, loading UX
- **Pipeline raw → staging → marts modelado en dbt** (2 staging incremental + 5 marts daily-grain)
- **Backend lee de marts.*** (filter como bind param, dedup MAX pre-calculada en mart)
- **Cron diario 03:00** en VPS personal: dbt run + ANALYZE de los 7 modelos + prewarm + Healthchecks.io
- ~38 tests dbt (unique, not_null, accepted_values, freshness, equivalencia raw vs marts)
- **Perf tuning**: BRIN(event_date), `work_mem='256-512MB'` por sesion en queries pesadas, CTEs duplicados mergeados. Default range pasa de 28-60s a 0.5-0.7s.
- **Canib custom range optimizado**: nueva MV `marts.canib_query_summary_daily` (grano query+dia) + single-pass jsonb + filter-first. De 23s cold a 6s cold; warm instant via L1.
- **Opportunities custom range optimizado**: filter-first (pos 10-20 + min_imp + LIMIT 50 antes de los joins) + BRIN(event_date) + ORDER BY event_date en `kpis_query_diario`. De 47s cold a 2.6s cold.
- **Top-products / top-categories optimizado**: BRIN(event_date) + ORDER BY event_date en `top_pages_diario`. De 8s cold a 0.25s cold.
- **Deploy productivo**: front en Render (`minidash.onrender.com`/custom domain), back en VPS Swarm/Traefik (`api-minidash.facundo.click`), TLS Let's Encrypt automatico.

⏳ Pendiente:
- Auth (Google Sign-In + JWT + whitelist) — `GOOGLE_CLIENT_ID` y `JWT_SECRET` ya estan en `.env.example`
- Tests unitarios de `cache.py` (ya es posible con `DictCacheStore`, falta escribirlos)
- Hacer cold de canib `<3s` (hoy 5.8s). Posibilidades: pre-resolver el filtro
  all_product en una columna pre-calculada por (query, dia) o explotar mas el
  filter-first en el lookup de URLs.

## Gotchas conocidos

- `auto-warm` puede tardar ~60s la primera vez. No bloquea pero la pagina inicial puede mostrar loading mientras corre.
- Granularity `day` sobre 24m = ~700 puntos. Chart.js lo renderiza ok pero puede laguear en mobile. Default `month` por eso.
- `extraccion_gsc.cat` puede ser `null` → frontend muestra "—".
- El Mac de Facu se desconecta de Postgres si una query tarda >18min. Usar SSH+docker exec para operaciones largas.
