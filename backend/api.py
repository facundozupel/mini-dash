import asyncio
import datetime as dt
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from cache import clear_stats, flush as cache_flush, snapshot, stats_summary, warm
from db.conn import pool, ping
from metrics import FILTERS, compute_metrics, compute_trend
from settings import settings
from tables import compute_cannibalization, compute_opportunities, compute_top_pages

log = logging.getLogger("minidash")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# ----------- Default range -----------
# GSC tiene ~3 dias de lag, asi que por default mostramos hasta today-3.
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


# ----------- Endpoints cacheados -----------
# Cada `@snapshot(name)` registra (name, compute_fn) en el registry de cache.py.
# La key se deriva auto de los args posicionales (dates -> ISO). El response
# queda envuelto en `{"cache": "hit"|"miss", "data": ...}`.
# Prewarm replay las mismas funciones desde una sola fuente de verdad.

cached_metrics = snapshot("metrics")(compute_metrics)

# trend rota su key por dia (key_extras agrega today.isoformat()).
cached_trend = snapshot(
    "trend",
    key_extras=lambda: [dt.date.today().isoformat()],
)(compute_trend)

# top-products / top-categories comparten compute_top_pages pero son entries
# distintas en el cache (filter_name distinto -> rows distintas).
cached_top_products = snapshot("top-products")(
    lambda f, t, limit: compute_top_pages("products", f, t, limit)
)
cached_top_categories = snapshot("top-categories")(
    lambda f, t, limit: compute_top_pages("category", f, t, limit)
)
cached_opportunities = snapshot("opportunities")(compute_opportunities)
cached_cannibalization = snapshot("cannibalization")(compute_cannibalization)


# ----------- Prewarm -----------

def _prewarm():
    """Carga el cache con el rango por defecto + trends 24m. Corre en background al boot."""
    f, t = _default_range()
    log.info(f"prewarm: rango default {f} -> {t} + trends 24m")
    try:
        warm("metrics", [(name, f, t) for name in FILTERS])
        warm("trend", [(name, g) for name in FILTERS for g in ("day", "week", "month")])
        warm("top-products", [(f, t, 20)])
        warm("top-categories", [(f, t, 20)])
        warm("opportunities", [(f, t, 50, 500)])
        warm("cannibalization", [(f, t, 50)])
        log.info("prewarm: OK, cache listo")
    except Exception as e:
        log.warning(f"prewarm fallo: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool.open()
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


# ----------- HTTP routes -----------

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
    return cached_metrics(filter_name, f, t)


@app.get("/trend/{filter_name}")
def trend(
    filter_name: str,
    granularity: str = Query(default="day", pattern="^(day|week|month)$"),
):
    if filter_name not in FILTERS:
        raise HTTPException(status_code=404, detail=f"filter must be one of {list(FILTERS)}")
    return cached_trend(filter_name, granularity)


@app.get("/tables/top-products")
def top_products(
    from_: dt.date | None = Query(default=None, alias="from"),
    to_: dt.date | None = Query(default=None, alias="to"),
    limit: int = Query(default=20, ge=1, le=200),
):
    f, t = _parse_range(from_, to_)
    return cached_top_products(f, t, limit)


@app.get("/tables/top-categories")
def top_categories(
    from_: dt.date | None = Query(default=None, alias="from"),
    to_: dt.date | None = Query(default=None, alias="to"),
    limit: int = Query(default=20, ge=1, le=200),
):
    f, t = _parse_range(from_, to_)
    return cached_top_categories(f, t, limit)


@app.get("/tables/opportunities")
def opportunities(
    from_: dt.date | None = Query(default=None, alias="from"),
    to_: dt.date | None = Query(default=None, alias="to"),
    limit: int = Query(default=50, ge=1, le=500),
    min_imp: int = Query(default=100, ge=1),
):
    f, t = _parse_range(from_, to_)
    return cached_opportunities(f, t, limit, min_imp)


@app.get("/tables/cannibalization")
def cannibalization(
    from_: dt.date | None = Query(default=None, alias="from"),
    to_: dt.date | None = Query(default=None, alias="to"),
    limit: int = Query(default=50, ge=1, le=500),
):
    f, t = _parse_range(from_, to_)
    return cached_cannibalization(f, t, limit)


@app.post("/internal/cache/flush")
def internal_flush(x_internal_secret: str = Header(default="")):
    if not settings.internal_secret or x_internal_secret != settings.internal_secret:
        raise HTTPException(status_code=401, detail="bad secret")
    cleared = cache_flush()
    return {"flushed_entries": cleared}


@app.get("/internal/cache/stats")
def internal_stats(
    x_internal_secret: str = Header(default=""),
    top_keys: int = Query(default=20, ge=1, le=200),
):
    if not settings.internal_secret or x_internal_secret != settings.internal_secret:
        raise HTTPException(status_code=401, detail="bad secret")
    return stats_summary(top_keys=top_keys)


@app.post("/internal/cache/stats/clear")
def internal_stats_clear(x_internal_secret: str = Header(default="")):
    if not settings.internal_secret or x_internal_secret != settings.internal_secret:
        raise HTTPException(status_code=401, detail="bad secret")
    return {"cleared_keys": clear_stats()}


# Frontend estatico (tiene que ir ULTIMO para no pisar /metrics, /health, etc.)
app.mount("/", StaticFiles(directory="../frontend", html=True), name="frontend")
