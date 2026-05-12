"""
Tablas del dashboard:
- top_pages (top productos / top categorias): marts.top_pages_diario
- opportunities (queries pos 10-20):           marts.query_daily + canib_query_page_daily
- cannibalization (queries con >1 URL prod):   marts.query_daily + canib_query_page_daily
"""
import datetime as dt

from psycopg.rows import dict_row

from db.conn import pool
from metrics import FILTERS, compute_ranges


# =====================================================================
# 1) TOP PAGES (top-products + top-categories)
# =====================================================================

# Lee de marts.top_pages_diario (filter pre-explotado, grano filter+page+date).
# Filter va como bind param, no como template SQL.

_TOP_PAGES_SQL = """
WITH cur AS (
    -- Una sola pasada: agrupa todo, ordena por clicks DESC, top N.
    -- Antes habia un CTE `top` que sacaba SOLO la lista de pages, y despues
    -- `cur` re-escaneaba para sacar las metricas. Mismo data, dos pasadas.
    SELECT page,
        MAX(cat) AS cat,
        SUM(clicks)::bigint AS clicks,
        SUM(impressions)::bigint AS impressions,
        CASE WHEN SUM(impressions)>0 THEN SUM(clicks)::float/SUM(impressions) ELSE 0 END AS ctr,
        CASE WHEN SUM(impressions)>0 THEN SUM(pos_num)/SUM(impressions) ELSE 0 END AS position
    FROM marts.top_pages_diario
    WHERE event_date BETWEEN %(cur_s)s AND %(cur_e)s
      AND filter = %(filter)s
    GROUP BY page
    ORDER BY SUM(clicks) DESC
    LIMIT %(limit)s
),
top AS (SELECT page FROM cur),
mom AS (
    SELECT page,
        SUM(clicks)::bigint AS clicks,
        SUM(impressions)::bigint AS impressions,
        CASE WHEN SUM(impressions)>0 THEN SUM(clicks)::float/SUM(impressions) ELSE 0 END AS ctr,
        CASE WHEN SUM(impressions)>0 THEN SUM(pos_num)/SUM(impressions) ELSE 0 END AS position
    FROM marts.top_pages_diario
    WHERE event_date BETWEEN %(mom_s)s AND %(mom_e)s
      AND filter = %(filter)s
      AND page IN (SELECT page FROM top)
    GROUP BY page
),
yoy AS (
    SELECT page,
        SUM(clicks)::bigint AS clicks,
        SUM(impressions)::bigint AS impressions,
        CASE WHEN SUM(impressions)>0 THEN SUM(clicks)::float/SUM(impressions) ELSE 0 END AS ctr,
        CASE WHEN SUM(impressions)>0 THEN SUM(pos_num)/SUM(impressions) ELSE 0 END AS position
    FROM marts.top_pages_diario
    WHERE event_date BETWEEN %(yoy_s)s AND %(yoy_e)s
      AND filter = %(filter)s
      AND page IN (SELECT page FROM top)
    GROUP BY page
)
SELECT
    c.page, c.cat,
    c.clicks, c.impressions, c.ctr, c.position,
    COALESCE(m.clicks, 0)      AS mom_clicks,
    COALESCE(m.impressions, 0) AS mom_impressions,
    COALESCE(m.ctr, 0)         AS mom_ctr,
    COALESCE(m.position, 0)    AS mom_position,
    COALESCE(y.clicks, 0)      AS yoy_clicks,
    COALESCE(y.impressions, 0) AS yoy_impressions,
    COALESCE(y.ctr, 0)         AS yoy_ctr,
    COALESCE(y.position, 0)    AS yoy_position
FROM cur c
LEFT JOIN mom m USING (page)
LEFT JOIN yoy y USING (page)
ORDER BY c.clicks DESC
"""


def compute_top_pages(filter_name: str, from_: dt.date, to_: dt.date, limit: int = 20) -> dict:
    if filter_name not in FILTERS:
        raise ValueError(f"filter desconocido: {filter_name}")

    cur_r, mom_r, yoy_r = compute_ranges(from_, to_)

    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            # Sorts/aggregations grandes sobre 250k+ filas chocan con work_mem
            # default (4MB) y caen a disco. Subirlo a 256MB para esta sesion
            # mantiene todo en RAM. Es por-sesion, no afecta otros workers.
            cur.execute("SET LOCAL work_mem = '256MB'")
            cur.execute(_TOP_PAGES_SQL, {
                "filter": filter_name,
                "cur_s": cur_r.start, "cur_e": cur_r.end,
                "mom_s": mom_r.start, "mom_e": mom_r.end,
                "yoy_s": yoy_r.start, "yoy_e": yoy_r.end,
                "limit": limit,
            })
            rows = cur.fetchall()

    return {
        "filter": filter_name,
        "range": {"start": from_.isoformat(), "end": to_.isoformat()},
        "rows": [_format_row(r) for r in rows],
    }


def _pct(curr: float, prev: float) -> float | None:
    if prev == 0:
        return None
    return (curr - prev) / prev


def _format_row(r: dict) -> dict:
    return {
        "page": r["page"],
        "cat":  r.get("cat"),
        "clicks":      r["clicks"],
        "impressions": r["impressions"],
        "ctr":         r["ctr"],
        "position":    r["position"],
        "mom": {
            "clicks":      r["mom_clicks"],
            "impressions": r["mom_impressions"],
            "delta_pct": {
                "clicks":      _pct(r["clicks"],      r["mom_clicks"]),
                "impressions": _pct(r["impressions"], r["mom_impressions"]),
                "ctr":         _pct(r["ctr"],         r["mom_ctr"]),
                "position":    _pct(r["position"],    r["mom_position"]),
            },
        },
        "yoy": {
            "clicks":      r["yoy_clicks"],
            "impressions": r["yoy_impressions"],
            "delta_pct": {
                "clicks":      _pct(r["clicks"],      r["yoy_clicks"]),
                "impressions": _pct(r["impressions"], r["yoy_impressions"]),
                "ctr":         _pct(r["ctr"],         r["yoy_ctr"]),
                "position":    _pct(r["position"],    r["yoy_position"]),
            },
        },
    }


# =====================================================================
# 2) OPPORTUNITIES: queries en posicion 10-20 con volumen
# =====================================================================
#
# Marts usados:
#   - marts.query_daily             : impressions_dedup + pos_num + impressions_sum
#                                     (la dedup MAX por (query, date) ya esta pre-calculada).
#                                     Comparte fisico con cannibalization (mismo grano).
#   - marts.canib_query_page_daily  : VIEW query+page+date para top_url

_OPPORTUNITIES_SQL = """
-- Patron filter-first, lookup-later:
-- 1) Identificar las queries que pasan el filtro pos 10-20 + min_imp con UN
--    solo scan al cur range. LIMIT 50.
-- 2) Para esas ~50 queries, lookups por unique(query, event_date) en los marts:
--    top_url (canib_query_page_daily), MoM y YoY (query_daily).

WITH per_q AS (
    SELECT
        query,
        SUM(clicks)::bigint                              AS clicks,
        SUM(impressions_dedup)::bigint                   AS impressions,
        SUM(pos_num) / NULLIF(SUM(impressions_sum), 0)   AS position
    FROM marts.query_daily
    WHERE event_date BETWEEN %(cur_s)s AND %(cur_e)s
    GROUP BY query
),
qualified AS (
    -- Filtro y LIMIT acá: el universo se achica de ~150k a ~50 queries.
    SELECT query, clicks, impressions, position
    FROM per_q
    WHERE position BETWEEN 10 AND 20
      AND impressions >= %(min_imp)s
    ORDER BY impressions DESC
    LIMIT %(limit)s
),
-- Lookups: query IN (lista de ~50) → index hit por leading column del unique.
top_url AS (
    SELECT DISTINCT ON (query) query, page
    FROM (
        SELECT query, page, SUM(impressions) AS imp
        FROM marts.canib_query_page_daily
        WHERE event_date BETWEEN %(cur_s)s AND %(cur_e)s
          AND query IN (SELECT query FROM qualified)
        GROUP BY query, page
    ) t
    ORDER BY query, imp DESC
),
per_q_mom AS (
    SELECT
        query,
        SUM(clicks)::bigint              AS clicks,
        SUM(impressions_dedup)::bigint   AS impressions
    FROM marts.query_daily
    WHERE event_date BETWEEN %(mom_s)s AND %(mom_e)s
      AND query IN (SELECT query FROM qualified)
    GROUP BY query
),
per_q_yoy AS (
    SELECT
        query,
        SUM(clicks)::bigint              AS clicks,
        SUM(impressions_dedup)::bigint   AS impressions
    FROM marts.query_daily
    WHERE event_date BETWEEN %(yoy_s)s AND %(yoy_e)s
      AND query IN (SELECT query FROM qualified)
    GROUP BY query
)
SELECT
    q.query, tu.page, q.clicks, q.impressions, q.position,
    CASE WHEN q.impressions > 0 THEN q.clicks::float / q.impressions ELSE 0 END AS ctr,
    COALESCE(m.clicks, 0)::bigint      AS mom_clicks,
    COALESCE(m.impressions, 0)::bigint AS mom_impressions,
    COALESCE(y.clicks, 0)::bigint      AS yoy_clicks,
    COALESCE(y.impressions, 0)::bigint AS yoy_impressions
FROM qualified q
JOIN top_url tu   USING (query)
LEFT JOIN per_q_mom m USING (query)
LEFT JOIN per_q_yoy y USING (query)
ORDER BY q.impressions DESC
"""


def compute_opportunities(from_: dt.date, to_: dt.date, limit: int = 50, min_imp: int = 100) -> dict:
    cur_r, mom_r, yoy_r = compute_ranges(from_, to_)
    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SET LOCAL work_mem = '256MB'")
            cur.execute(_OPPORTUNITIES_SQL, {
                "cur_s": cur_r.start, "cur_e": cur_r.end,
                "mom_s": mom_r.start, "mom_e": mom_r.end,
                "yoy_s": yoy_r.start, "yoy_e": yoy_r.end,
                "limit": limit, "min_imp": min_imp,
            })
            rows = cur.fetchall()

    out = []
    for r in rows:
        out.append({
            "query": r["query"],
            "page":  r["page"],
            "clicks":      r["clicks"],
            "impressions": r["impressions"],
            "ctr":         r["ctr"],
            "position":    r["position"],
            "mom": {
                "clicks":      r["mom_clicks"],
                "impressions": r["mom_impressions"],
                "delta_pct": {
                    "clicks":      _pct(r["clicks"],      r["mom_clicks"]),
                    "impressions": _pct(r["impressions"], r["mom_impressions"]),
                },
            },
            "yoy": {
                "clicks":      r["yoy_clicks"],
                "impressions": r["yoy_impressions"],
                "delta_pct": {
                    "clicks":      _pct(r["clicks"],      r["yoy_clicks"]),
                    "impressions": _pct(r["impressions"], r["yoy_impressions"]),
                },
            },
        })
    return {
        "range": {"start": from_.isoformat(), "end": to_.isoformat()},
        "rows": out,
    }


# =====================================================================
# 3) CANNIBALIZATION: queries con >1 URL donde TODAS son producto
# =====================================================================
#
# Lee de:
#   - marts.query_daily             : grano (query, dia) con clicks,
#     impressions_dedup, all_product_today y array `pages`. Sirve al filtro
#     y ranking inicial sin tener que tocar el grano (query, page, dia).
#     Comparte fisico con opportunities (mismo grano).
#   - marts.canib_query_page_daily  : grano (query, page, dia). Solo para el
#     detalle de URLs de las ~50 queries que ya pasaron el filtro.

_CANNIBALIZATION_SQL = """
-- Single-pass sobre query_daily: agrega clicks, impressions_dedup, all_product
-- y los arrays de pages per-day en una sola pasada (HashAggregate).
-- Despues calcula total_urls como subselect sobre las queries que ya pasan
-- el filtro all_product (subplan corre solo para las ~5k candidates, no las 150k).
WITH per_q AS (
    SELECT
        query,
        SUM(clicks)::bigint                AS clicks,
        SUM(impressions_dedup)::bigint     AS impressions,
        BOOL_AND(all_product_today)        AS all_product,
        jsonb_agg(to_jsonb(pages))         AS pages_per_day
    FROM marts.query_daily
    WHERE event_date BETWEEN %(cur_s)s AND %(cur_e)s
    GROUP BY query
),
canib AS (
    SELECT
        query,
        clicks,
        impressions,
        total_urls
    FROM (
        SELECT
            query,
            clicks,
            impressions,
            (
                SELECT COUNT(DISTINCT page_text)
                FROM jsonb_array_elements(pages_per_day) AS day_arr,
                     jsonb_array_elements_text(day_arr) AS page_text
            ) AS total_urls
        FROM per_q
        WHERE all_product = true
    ) q
    WHERE total_urls > 1
    ORDER BY clicks DESC
    LIMIT %(limit)s
),
-- Detalle de URLs solo para las queries canib (lookup por lista, no scan
-- completo). Re-agrega clicks/impressions/position desde el grano page-day.
urls_detail AS (
    SELECT
        qp.query,
        jsonb_agg(
            jsonb_build_object(
                'page', qp.page,
                'clicks', qp.clicks_sum,
                'impressions', qp.impressions_sum,
                'position', qp.position_avg
            ) ORDER BY qp.clicks_sum DESC
        ) AS urls
    FROM (
        SELECT
            query,
            page,
            SUM(clicks)::bigint                                   AS clicks_sum,
            SUM(impressions)::bigint                              AS impressions_sum,
            SUM(position*impressions)/NULLIF(SUM(impressions),0)  AS position_avg
        FROM marts.canib_query_page_daily
        WHERE event_date BETWEEN %(cur_s)s AND %(cur_e)s
          AND query IN (SELECT query FROM canib)
        GROUP BY query, page
    ) qp
    GROUP BY qp.query
)
SELECT
    c.query, c.total_urls, c.clicks, c.impressions,
    u.urls
FROM canib c
JOIN urls_detail u USING (query)
ORDER BY c.clicks DESC
"""


def compute_cannibalization(from_: dt.date, to_: dt.date, limit: int = 50) -> dict:
    cur_r, _, _ = compute_ranges(from_, to_)
    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            # 512MB acotado a esta sesion: el HashAggregate de la summary mart
            # arma 110k buckets con jsonb arrays adentro y se queda corto con
            # 256MB (caia a Sort+GroupAggregate, ~3x mas lento).
            cur.execute("SET LOCAL work_mem = '512MB'")
            cur.execute(_CANNIBALIZATION_SQL, {
                "cur_s": cur_r.start, "cur_e": cur_r.end,
                "limit": limit,
            })
            rows = cur.fetchall()

    out = [
        {
            "query": r["query"],
            "total_urls": r["total_urls"],
            "clicks": r["clicks"],
            "impressions": r["impressions"],
            "urls": r["urls"],
        }
        for r in rows
    ]
    return {
        "range": {"start": from_.isoformat(), "end": to_.isoformat()},
        "rows": out,
    }
