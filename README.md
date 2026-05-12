# MiniDash Endado

Dashboard CRO chiquito para **endado.com**, hecho como proyecto de aprendizaje de la arquitectura "API + cache + frontend estatico".

Lee de la DB de endado existente (Postgres en VPS), agrega KPIs / tablas / trend, y los muestra con un frontend vanilla.

## Que ves

- **KPIs** del rango elegido (clicks, impresiones, CTR, posicion) con MoM y YoY.
- **Trend de 24 meses** panoramico, agrupable por dia / semana / mes.
- **4 vistas:** Overall, Productos, Categorias (hubs), `/recambios`.
- **4 tablas:** Top productos, Top categorias, Oportunidades (queries pos 10-20), Canibalizacion de productos.
- **Modo Bots** (tab aparte, consume [BOT-DASH-API](https://github.com/facundozupel/bot-dash-api)): KPIs de logs Apache, top URLs / top bots / by-section / by-status / by-type, **drill-down inline al click** en filas (status → URLs con ese status para un bot, bot → top 50 productos que hittea ese bot).

## Stack

- Backend: FastAPI + psycopg3 + cachetools
- DB: Postgres (existente, read-only)
- Frontend: HTML + JS vanilla + Chart.js
- Cache: in-process TTL 24h con prewarm al boot, **dos capas**:
  - **L1 (`@snapshot`)** — cachea respuestas finales por `(filtro, from, to)`. Una sola fuente de verdad para keys via `warm()`.
  - **L2 (`BucketCache`)** — cachea componentes per-day y compone rangos al vuelo. Mover el date picker un dia recomputa ~2 dias en vez de 90.
  - Storage detras de un `CacheStore` Protocol → swap a Redis o mock para tests sin tocar callers.
  - Stats hit/miss per-key expuestas en `/internal/cache/stats` para medir antes de optimizar.

## Quick start (local)

```bash
# 1. Cloná y entrá
git clone <repo>
cd MICRO-DASH/backend

# 2. Configurá .env (copialo del ejemplo)
cp .env.example .env
# editá .env con tus credenciales de la DB endado

# 3. Instalá deps en venv
python3 -m venv .venv
.venv/bin/pip install "fastapi>=0.115" "uvicorn[standard]>=0.32" "psycopg[binary,pool]>=3.2" \
    "pydantic-settings>=2.6" "pyjwt>=2.9" "cachetools>=5.5" "google-auth>=2.35" "python-multipart>=0.0.12"

# 4. Levantá
.venv/bin/uvicorn api:app --host 127.0.0.1 --port 8000

# 5. Abrí
open http://localhost:8000
```

El boot tarda ~60s en estar 100% caliente (prewarm en background). El servidor responde antes pero los primeros endpoints pueden ser cache miss los primeros segundos.

## Endpoints

| Endpoint | Que devuelve |
|---|---|
| `GET /` | Frontend (solo en dev local) |
| `GET /metrics/{overall\|products\|category\|recambios}?from=&to=` | KPIs + MoM + YoY |
| `GET /trend/{filter}?granularity=day\|week\|month` | Trend 24m panoramico |
| `GET /tables/top-products?from=&to=&limit=20` | Top N productos con comparativas |
| `GET /tables/top-categories?from=&to=&limit=20` | Top N hubs no-producto no-blog |
| `GET /tables/opportunities?from=&to=&limit=50&min_imp=500` | Queries en pos 10-20 con volumen |
| `GET /tables/cannibalization?from=&to=&limit=50` | Queries con >1 URL de producto compitiendo |
| `GET /health` | Healthcheck (DB connectivity) |
| `POST /internal/cache/flush` | Limpia cache (header `x-internal-secret`) |
| `POST /internal/cache/prewarm` | Recorre prewarm con today() actual (header `x-internal-secret`). Lo llama cron post-dbt. |
| `GET /internal/cache/stats` | Hit/miss per-key (header `x-internal-secret`) |
| `POST /internal/cache/stats/clear` | Reset contadores (header `x-internal-secret`) |

## Estructura

```
MICRO-DASH/
├── backend/      ← FastAPI + SQL + cache + Dockerfile
├── frontend/     ← HTML + JS + CSS (deploy a Render)
├── dbt/          ← raw -> staging -> marts + refresh.sh (cron)
├── deploy/       ← docker-stack.yml + .env productivo + DEPLOY.md (runbook)
├── CLAUDE.md     ← guia tecnica para iterar con Claude
└── README.md     ← este archivo
```

## Deploy (productivo)

- **Frontend** → Render Static, dominio `mini-dash.onrender.com` (custom domain `minidash.facundo.click` opcional via CNAME).
- **Backend** → VPS personal `master.facundo.click` (Docker Swarm + Traefik existentes), dominio `https://api-minidash.facundo.click`. Cert Let's Encrypt automatico.
- **DB endado** → mismo VPS, no se toca. La api se conecta por DNS interno (`postgres_postgres` en la red `network_public`).

Runbook completo en [`deploy/DEPLOY.md`](./deploy/DEPLOY.md). Incluye gotchas (ej: `docker stack deploy` no actualiza imagenes locales — usar `docker service update --force`).

## Status

- [x] Local end-to-end con data real
- [x] Indices creados en DB endado para perf (BRIN + btree donde corresponde)
- [x] Cache "deepada": `@snapshot` decorator (L1) + `CacheStore` Protocol (testeable sin DB)
- [x] Bucket cache (L2) para `/metrics/*` con componentes per-day reusables
- [x] Stats hit/miss per-key (`/internal/cache/stats`) para decidir optimizaciones con datos
- [x] Pipeline raw -> staging -> marts modelado en dbt (2 staging + 4 marts) + cron diario 07:00 + ANALYZE post-refresh + Healthchecks.io
- [x] **53 tests dbt** (not_null + unique + accepted_values + freshness + expression_is_true >= 0). Tests UNIQUE en tablas grandes scoped a `event_date >= CURRENT_DATE - 30 days` para no caer por disco.
- [x] **Refresh estricto**: `refresh.sh` corre `dbt run → dbt test → ANALYZE → flush + prewarm`. Si `dbt test` falla, ABORTA (no ANALYZE, no prewarm). El cache de ayer sigue sirviendo data sana hasta arreglar. Mail con detalle del test que fallo via Healthchecks.io.
- [x] **Consolidacion query_daily**: las dos MVs query-level (kpis_query_diario + canib_query_summary_daily) fusionadas en `marts.query_daily`. Opportunities y cannibalization leen del mismo fisico. -2 GB en disco, perf mejor.
- [x] **Deploy productivo**: front en Render, back en VPS con Swarm/Traefik, TLS automatico
- [x] Endpoint `/internal/cache/prewarm` enganchado al cron de dbt para que el cache rote con el dia
- [x] Custom date ranges optimizados: top-products 8s→0.25s, opportunities 47s→1.2s, canib 23s→4.1s. Warm <0.05s.
- [ ] Tests unitarios
- [ ] Auth (Google Sign-In + JWT)
