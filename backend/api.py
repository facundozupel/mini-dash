import asyncio
import datetime as dt
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from cache import flush as cache_flush, get_or_compute
from db.conn import pool, ping
from metrics import FILTERS, compute_metrics, compute_trend
from settings import settings
from tables import compute_cannibalization, compute_opportunities, compute_top_pages

log = logging.getLogger("minidash")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _prewarm():
    """Carga el cache con el rango por defecto (ultimos 30d) para que la primera vista no espere."""
    today = dt.date.today()
    to_ = today - dt.timedelta(days=3)
    from_ = to_ - dt.timedelta(days=29)
    today_iso = dt.date.today().isoformat()
    log.info(f"prewarm: cargando rango default {from_} -> {to_} + trends 24m")
    try:
        # KPIs para el rango default (4 filtros)
        for name in FILTERS:
            get_or_compute(
                f"metrics:{name}:{from_}:{to_}",
                lambda n=name: compute_metrics(n, from_, to_),
            )
        # Trends panoramicos (24 meses) para 4 filtros x 3 granularidades = 12
        for name in FILTERS:
            for gran in ("day", "week", "month"):
                get_or_compute(
                    f"trend:{name}:{gran}:{today_iso}",
                    lambda n=name, g=gran: compute_trend(n, g),
                )
        # Tablas del rango default
        get_or_compute(f"top-products:{from_}:{to_}:20", lambda: compute_top_pages("products", from_, to_, 20))
        get_or_compute(f"top-categories:{from_}:{to_}:20", lambda: compute_top_pages("category", from_, to_, 20))
        get_or_compute(f"opportunities:{from_}:{to_}:50:500", lambda: compute_opportunities(from_, to_, 50, 500))
        get_or_compute(f"cannibalization:{from_}:{to_}:50", lambda: compute_cannibalization(from_, to_, 50))
        log.info("prewarm: OK, cache listo")
    except Exception as e:
        log.warning(f"prewarm fallo: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool.open()
    # Arranca el prewarm en background — no bloquea el startup
    asyncio.create_task(asyncio.to_thread(_prewarm))
    yield
    pool.close()


app = FastAPI(title="MiniDash Endado", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin] if settings.frontend_origin != "*" else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----------- Helpers -----------

# GSC tiene ~3 dias de lag, asi que por default mostramos hasta today-3
DATA_LAG_DAYS = 3
DEFAULT_RANGE_DAYS = 30


def _default_range() -> tuple[dt.date, dt.date]:
    today = dt.date.today()
    to_ = today - dt.timedelta(days=DATA_LAG_DAYS)
    from_ = to_ - dt.timedelta(days=DEFAULT_RANGE_DAYS - 1)
    return from_, to_


def _parse_range(
    from_: dt.date | None,
    to_: dt.date | None,
) -> tuple[dt.date, dt.date]:
    if from_ is None or to_ is None:
        return _default_range()
    if from_ > to_:
        raise HTTPException(status_code=422, detail="from > to")
    return from_, to_


# ----------- Endpoints -----------

@app.get("/health")
def health():
    try:
        ok = ping()
        return {"status": "ok" if ok else "degraded", "db": ok}
    except Exception as e:
        return {"status": "error", "db": False, "detail": str(e)}


@app.get("/metrics/{filter_name}")
def metrics(
    filter_name: str,
    from_: dt.date | None = Query(default=None, alias="from"),
    to_: dt.date | None = Query(default=None, alias="to"),
):
    if filter_name not in FILTERS:
        raise HTTPException(status_code=404, detail=f"filter must be one of {list(FILTERS)}")

    f, t = _parse_range(from_, to_)
    key = f"metrics:{filter_name}:{f.isoformat()}:{t.isoformat()}"
    data, hit = get_or_compute(key, lambda: compute_metrics(filter_name, f, t))
    return {"cache": "hit" if hit else "miss", "data": data}


@app.get("/trend/{filter_name}")
def trend(
    filter_name: str,
    granularity: str = Query(default="day", pattern="^(day|week|month)$"),
):
    if filter_name not in FILTERS:
        raise HTTPException(status_code=404, detail=f"filter must be one of {list(FILTERS)}")

    # Trend es "panoramico" — fijo 24 meses, no depende del date picker.
    # El cache key solo incluye filter + granularity. Cambia 1 vez por dia (por el rolling window),
    # pero el TTL de 24h ya cubre eso.
    key = f"trend:{filter_name}:{granularity}:{dt.date.today().isoformat()}"
    data, hit = get_or_compute(key, lambda: compute_trend(filter_name, granularity))
    return {"cache": "hit" if hit else "miss", "data": data}


@app.get("/tables/top-products")
def top_products(
    from_: dt.date | None = Query(default=None, alias="from"),
    to_: dt.date | None = Query(default=None, alias="to"),
    limit: int = Query(default=20, ge=1, le=200),
):
    f, t = _parse_range(from_, to_)
    key = f"top-products:{f.isoformat()}:{t.isoformat()}:{limit}"
    data, hit = get_or_compute(key, lambda: compute_top_pages("products", f, t, limit))
    return {"cache": "hit" if hit else "miss", "data": data}


@app.get("/tables/top-categories")
def top_categories(
    from_: dt.date | None = Query(default=None, alias="from"),
    to_: dt.date | None = Query(default=None, alias="to"),
    limit: int = Query(default=20, ge=1, le=200),
):
    f, t = _parse_range(from_, to_)
    key = f"top-categories:{f.isoformat()}:{t.isoformat()}:{limit}"
    data, hit = get_or_compute(key, lambda: compute_top_pages("category", f, t, limit))
    return {"cache": "hit" if hit else "miss", "data": data}


@app.get("/tables/opportunities")
def opportunities(
    from_: dt.date | None = Query(default=None, alias="from"),
    to_: dt.date | None = Query(default=None, alias="to"),
    limit: int = Query(default=50, ge=1, le=500),
    min_imp: int = Query(default=100, ge=1),
):
    f, t = _parse_range(from_, to_)
    key = f"opportunities:{f.isoformat()}:{t.isoformat()}:{limit}:{min_imp}"
    data, hit = get_or_compute(key, lambda: compute_opportunities(f, t, limit, min_imp))
    return {"cache": "hit" if hit else "miss", "data": data}


@app.get("/tables/cannibalization")
def cannibalization(
    from_: dt.date | None = Query(default=None, alias="from"),
    to_: dt.date | None = Query(default=None, alias="to"),
    limit: int = Query(default=50, ge=1, le=500),
):
    f, t = _parse_range(from_, to_)
    key = f"cannibalization:{f.isoformat()}:{t.isoformat()}:{limit}"
    data, hit = get_or_compute(key, lambda: compute_cannibalization(f, t, limit))
    return {"cache": "hit" if hit else "miss", "data": data}


@app.post("/internal/cache/flush")
def internal_flush(x_internal_secret: str = Header(default="")):
    if not settings.internal_secret or x_internal_secret != settings.internal_secret:
        raise HTTPException(status_code=401, detail="bad secret")
    cleared = cache_flush()
    return {"flushed_entries": cleared}


# Frontend estatico (tiene que ir ULTIMO para no pisar /metrics, /health, etc.)
app.mount("/", StaticFiles(directory="../frontend", html=True), name="frontend")
