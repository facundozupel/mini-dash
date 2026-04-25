# MiniDash Endado

Dashboard CRO chiquito para **endado.com**, hecho como proyecto de aprendizaje de la arquitectura "API + cache + frontend estatico".

Lee de la DB de endado existente (Postgres en VPS), agrega KPIs / tablas / trend, y los muestra con un frontend vanilla.

## Que ves

- **KPIs** del rango elegido (clicks, impresiones, CTR, posicion) con MoM y YoY.
- **Trend de 24 meses** panoramico, agrupable por dia / semana / mes.
- **4 vistas:** Overall, Productos, Categorias (hubs), `/recambios`.
- **4 tablas:** Top productos, Top categorias, Oportunidades (queries pos 10-20), Canibalizacion de productos.

## Stack

- Backend: FastAPI + psycopg3 + cachetools
- DB: Postgres (existente, read-only)
- Frontend: HTML + JS vanilla + Chart.js
- Cache: in-process TTL 24h con prewarm al boot

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
| `GET /` | Frontend (HTML) |
| `GET /metrics/{overall\|products\|category\|recambios}?from=&to=` | KPIs + MoM + YoY |
| `GET /trend/{filter}?granularity=day\|week\|month` | Trend 24m panoramico |
| `GET /tables/top-products?from=&to=&limit=20` | Top N productos con comparativas |
| `GET /tables/top-categories?from=&to=&limit=20` | Top N hubs no-producto no-blog |
| `GET /tables/opportunities?from=&to=&limit=50&min_imp=500` | Queries en pos 10-20 con volumen |
| `GET /tables/cannibalization?from=&to=&limit=50` | Queries con >1 URL de producto compitiendo |
| `GET /health` | Healthcheck (DB connectivity) |
| `POST /internal/cache/flush` | Limpia cache (header `x-internal-secret`) |

## Estructura

```
MICRO-DASH/
├── backend/    ← FastAPI + SQL + cache
├── frontend/   ← HTML + JS + CSS
├── CLAUDE.md   ← guia tecnica para iterar con Claude
└── README.md   ← este archivo
```

## Deploy (futuro)

- Frontend → Render Static
- API + cache → VPS Docker Swarm + Traefik en `minidash.facundo.click`
- DB endado → ya esta en el VPS, no se toca

## Status

- [x] Local end-to-end con data real
- [x] Indices creados en DB endado para perf
- [ ] Auth (Google Sign-In + JWT)
- [ ] Deploy
