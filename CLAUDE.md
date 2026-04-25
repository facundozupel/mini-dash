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
│   ├── cache.py        ← TTLCache wrapper + flush
│   ├── metrics.py      ← KPIs (current/MoM/YoY) + trend 24m + FILTERS dict
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

## Cache strategy

- `cachetools.TTLCache(maxsize=32, ttl=86400)` in-process (1 worker, 1 cache).
- **Prewarm en background al boot** (`api.lifespan`):
  - 4 metrics (default range = ultimos 30d ending today-3)
  - 12 trends (4 filtros × 3 granularidades, rango fijo 24m)
  - 4 tablas (default range)
  - Tarda ~60s. No bloquea el startup (corre en task asyncio).
- Trend tiene `today.isoformat()` en la key → rotates 1x/dia.
- Para invalidar manualmente: `POST /internal/cache/flush` con header `x-internal-secret: $INTERNAL_SECRET`.

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

## Estado actual del proyecto

✅ Hecho:
- Plumbing local end-to-end (DB ↔ API ↔ frontend)
- 4 endpoints `/metrics/*` con MoM/YoY
- 4 endpoints `/tables/*` con comparativas
- `/trend/*` panoramico 24 meses, 3 granularidades
- Cache TTL + prewarm + invalidacion manual
- Frontend con date picker, tabs, granularity, loading UX
- Indices en DB endado

⏳ Pendiente:
- Auth (Google Sign-In + JWT + whitelist) — `GOOGLE_CLIENT_ID` y `JWT_SECRET` ya estan en `.env.example`
- Deploy: Dockerizar y subir al VPS con Traefik en `minidash.facundo.click`
- Frontend separado en Render (configurar CORS bien)

## Gotchas conocidos

- `auto-warm` puede tardar ~60s la primera vez. No bloquea pero la pagina inicial puede mostrar loading mientras corre.
- Granularity `day` sobre 24m = ~700 puntos. Chart.js lo renderiza ok pero puede laguear en mobile. Default `month` por eso.
- `extraccion_gsc.cat` puede ser `null` → frontend muestra "—".
- El Mac de Facu se desconecta de Postgres si una query tarda >18min. Usar SSH+docker exec para operaciones largas.
