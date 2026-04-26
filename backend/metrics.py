"""
Logica compartida por los 4 endpoints /metrics/*:
- Calcula rangos (current, mom=periodo previo, yoy=mismo periodo un anio atras)
- Define los filtros WHERE por tipo de pagina
- Corre los SQL de KPIs y trend
"""
import datetime as dt
from dataclasses import dataclass

from psycopg.rows import dict_row

from bucket_cache import BucketCache
from db.conn import pool

# --- Filtros por tipo de pagina (hardcoded, no user input = safe) ---
FILTERS: dict[str, str] = {
    "overall":   "TRUE",
    "products":  "prod_bool = true",
    "category":  "prod_bool = false AND blog = false "
                 "AND page NOT IN ('https://www.endado.com/', 'http://www.endado.com/')",
    "recambios": "page ILIKE '%%/recambios/%%'",
}


@dataclass
class Range:
    label: str
    start: dt.date
    end: dt.date


def compute_ranges(from_: dt.date, to_: dt.date) -> tuple[Range, Range, Range]:
    """Dado [from, to], calcula current + periodo-anterior + mismo-periodo-hace-1-anio."""
    duration_days = (to_ - from_).days + 1
    mom_end   = from_ - dt.timedelta(days=1)
    mom_start = mom_end - dt.timedelta(days=duration_days - 1)
    yoy_start = from_ - dt.timedelta(days=365)
    yoy_end   = to_   - dt.timedelta(days=365)
    return (
        Range("current", from_, to_),
        Range("mom",     mom_start, mom_end),
        Range("yoy",     yoy_start, yoy_end),
    )


# --- SQL templates (el {filter} se reemplaza desde FILTERS) ---

# KPIs por dia para el bucket cache (L2). Devuelve componentes crudos:
#   clicks         -> aditivo
#   impressions    -> aditivo (= denominador de pos avg, page-level no dedupa)
#   pos_num        -> SUM(position * impressions), aditivo
# Para componer un rango: ctr = sum(clicks)/sum(imp); pos = sum(pos_num)/sum(imp).
_KPIS_BUCKET_SQL = """
SELECT
    date,
    COALESCE(SUM(clicks), 0)::bigint                            AS clicks,
    COALESCE(SUM(impressions), 0)::bigint                       AS impressions,
    COALESCE(SUM(position * impressions), 0)::float             AS pos_num
FROM extraccion_gsc_page
WHERE date = ANY(%(days)s)
  AND ({filter})
GROUP BY date
"""

_TREND_SQL = """
SELECT
    DATE_TRUNC(%(gran)s, date)::date          AS date,
    COALESCE(SUM(clicks), 0)::bigint          AS clicks,
    COALESCE(SUM(impressions), 0)::bigint     AS impressions
FROM extraccion_gsc_page
WHERE date BETWEEN %(start)s AND %(end)s
  AND ({filter})
GROUP BY DATE_TRUNC(%(gran)s, date)
ORDER BY 1
"""

VALID_GRANULARITIES = {"day", "week", "month"}

TREND_MONTHS_BACK = 24  # "mapa panoramico" fijo


def _fetch_kpi_days(filter_name: str, days: list[dt.date]) -> dict[dt.date, dict]:
    """Trae los componentes crudos para los dias dados, en una sola query."""
    if not days:
        return {}
    sql = _KPIS_BUCKET_SQL.format(filter=FILTERS[filter_name])
    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, {"days": days})
            rows = cur.fetchall()
    return {
        r["date"]: {
            "clicks": r["clicks"],
            "impressions": r["impressions"],
            "pos_num": r["pos_num"],
        }
        for r in rows
    }


def _empty_kpi_day() -> dict:
    return {"clicks": 0, "impressions": 0, "pos_num": 0.0}


def _compose_kpis(comps: list[dict]) -> dict:
    """Reduce per-day components a la metric final (clicks, imp, ctr, position)."""
    clicks = sum(c["clicks"] for c in comps)
    imp = sum(c["impressions"] for c in comps)
    pos_num = sum(c["pos_num"] for c in comps)
    return {
        "clicks": clicks,
        "impressions": imp,
        "ctr": (clicks / imp) if imp else 0.0,
        "position": (pos_num / imp) if imp else 0.0,
    }


# Bucket cache (L2): comparte el _store con @snapshot.
# Keys: "kpi_bucket:{filter_name}:{YYYY-MM-DD}".
_kpi_bucket = BucketCache(
    name="kpi_bucket",
    fetch_days=_fetch_kpi_days,
    compose=_compose_kpis,
    empty=_empty_kpi_day,
)


def _run_kpis(filter_name: str, rng: Range) -> dict:
    """Devuelve KPIs componiendo desde bucket cache (L2)."""
    return _kpi_bucket.get_range(filter_name, rng.start, rng.end)


def _run_trend(filter_name: str, rng: Range, gran: str) -> list[dict]:
    sql = _TREND_SQL.format(filter=FILTERS[filter_name])
    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, {"start": rng.start, "end": rng.end, "gran": gran})
            return cur.fetchall()


def _pct_change(curr: float, prev: float) -> float | None:
    if prev == 0:
        return None
    return (curr - prev) / prev


def _diff(curr: float, prev: float) -> float:
    return curr - prev


def compute_metrics(filter_name: str, from_: dt.date, to_: dt.date) -> dict:
    """KPIs current + MoM + YoY (SIN trend — trend va por endpoint aparte)."""
    if filter_name not in FILTERS:
        raise ValueError(f"filter desconocido: {filter_name}")

    cur_r, mom_r, yoy_r = compute_ranges(from_, to_)

    cur_k = _run_kpis(filter_name, cur_r)
    mom_k = _run_kpis(filter_name, mom_r)
    yoy_k = _run_kpis(filter_name, yoy_r)

    def _comparatives(curr: dict, prev: dict, label: str) -> dict:
        return {
            "range": {"start": _r_for(label, cur_r, mom_r, yoy_r).start.isoformat(),
                      "end":   _r_for(label, cur_r, mom_r, yoy_r).end.isoformat()},
            "clicks":      prev["clicks"],
            "impressions": prev["impressions"],
            "ctr":         prev["ctr"],
            "position":    prev["position"],
            "delta_pct": {
                "clicks":      _pct_change(curr["clicks"],      prev["clicks"]),
                "impressions": _pct_change(curr["impressions"], prev["impressions"]),
                "ctr":         _pct_change(curr["ctr"],         prev["ctr"]),
                "position":    _pct_change(curr["position"],    prev["position"]),
            },
            "delta_abs": {
                "position": _diff(curr["position"], prev["position"]),
            },
        }

    return {
        "filter": filter_name,
        "range": {"start": from_.isoformat(), "end": to_.isoformat(), "days": (to_ - from_).days + 1},
        "kpis": {
            "clicks":      cur_k["clicks"],
            "impressions": cur_k["impressions"],
            "ctr":         cur_k["ctr"],
            "position":    cur_k["position"],
        },
        "mom": _comparatives(cur_k, mom_k, "mom"),
        "yoy": _comparatives(cur_k, yoy_k, "yoy"),
    }


def compute_trend(filter_name: str, gran: str) -> dict:
    """Trend panoramico: ultimos 24 meses, independiente del date picker."""
    if filter_name not in FILTERS:
        raise ValueError(f"filter desconocido: {filter_name}")
    if gran not in VALID_GRANULARITIES:
        raise ValueError(f"granularity debe ser uno de {VALID_GRANULARITIES}")

    today = dt.date.today()
    # ~24 meses = 730 dias. Uso relativedelta-esque: subtract 24 months from today.
    start = (today.replace(day=1) - dt.timedelta(days=1)).replace(day=1)
    # Simple: 730 dias atras (aprox 24 meses)
    start = today - dt.timedelta(days=730)
    rng = Range("trend", start, today)
    rows = _run_trend(filter_name, rng, gran)
    return {
        "filter": filter_name,
        "granularity": gran,
        "range": {"start": start.isoformat(), "end": today.isoformat()},
        "points": [
            {"date": r["date"].isoformat(), "clicks": r["clicks"], "impressions": r["impressions"]}
            for r in rows
        ],
    }


def _r_for(label: str, cur_r: Range, mom_r: Range, yoy_r: Range) -> Range:
    return {"current": cur_r, "mom": mom_r, "yoy": yoy_r}[label]
