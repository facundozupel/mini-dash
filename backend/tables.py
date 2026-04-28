"""
Tablas del dashboard:
- top_pages (top productos / top categorias): marts.top_pages_diario
- opportunities (queries pos 10-20):           marts.kpis_query_diario + canib_query_page_daily
- cannibalization (queries con >1 URL prod):   marts.canib_query_page_daily + marts.kpis_query_diario
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
WITH top AS (
    SELECT page, SUM(clicks) AS clicks
    FROM marts.top_pages_diario
    WHERE event_date BETWEEN %(cur_s)s AND %(cur_e)s
      AND filter = %(filter)s
    GROUP BY page
    ORDER BY clicks DESC
    LIMIT %(limit)s
),
cur AS (
    SELECT page,
        MAX(cat) AS cat,
        SUM(clicks)::bigint AS clicks,
        SUM(impressions)::bigint AS impressions,
        CASE WHEN SUM(impressions)>0 THEN SUM(clicks)::float/SUM(impressions) ELSE 0 END AS ctr,
        CASE WHEN SUM(impressions)>0 THEN SUM(pos_num)/SUM(impressions) ELSE 0 END AS position
    FROM marts.top_pages_diario
    WHERE event_date BETWEEN %(cur_s)s AND %(cur_e)s
      AND filter = %(filter)s
      AND page IN (SELECT page FROM top)
    GROUP BY page
),
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
#   - marts.kpis_query_diario       : impressions_dedup + pos_num + impressions_sum
#                                     (la dedup MAX por (query, date) ya esta pre-calculada)
#   - marts.canib_query_page_daily  : VIEW query+page+date para top_url

_OPPORTUNITIES_SQL = """
WITH per_q AS (
    SELECT
        query,
        SUM(impressions_dedup)::bigint                         AS impressions,
        SUM(pos_num) / NULLIF(SUM(impressions_sum), 0)         AS position
    FROM marts.kpis_query_diario
    WHERE event_date BETWEEN %(cur_s)s AND %(cur_e)s
    GROUP BY query
),
clicks_q AS (
    SELECT query, SUM(clicks)::bigint AS clicks
    FROM marts.kpis_query_diario
    WHERE event_date BETWEEN %(cur_s)s AND %(cur_e)s
    GROUP BY query
),
top_url AS (
    SELECT DISTINCT ON (query) query, page
    FROM (
        SELECT query, page, SUM(impressions) AS imp
        FROM marts.canib_query_page_daily
        WHERE event_date BETWEEN %(cur_s)s AND %(cur_e)s
        GROUP BY query, page
    ) t
    ORDER BY query, imp DESC
),
per_q_mom AS (
    SELECT
        query,
        COALESCE(SUM(clicks), 0)::bigint               AS clicks,
        COALESCE(SUM(impressions_dedup), 0)::bigint    AS impressions
    FROM marts.kpis_query_diario
    WHERE event_date BETWEEN %(mom_s)s AND %(mom_e)s
    GROUP BY query
),
per_q_yoy AS (
    SELECT
        query,
        COALESCE(SUM(clicks), 0)::bigint               AS clicks,
        COALESCE(SUM(impressions_dedup), 0)::bigint    AS impressions
    FROM marts.kpis_query_diario
    WHERE event_date BETWEEN %(yoy_s)s AND %(yoy_e)s
    GROUP BY query
)
SELECT
    pq.query, tu.page, cq.clicks, pq.impressions, pq.position,
    CASE WHEN pq.impressions > 0 THEN cq.clicks::float / pq.impressions ELSE 0 END AS ctr,
    COALESCE(m.clicks, 0)::bigint      AS mom_clicks,
    COALESCE(m.impressions, 0)::bigint AS mom_impressions,
    COALESCE(y.clicks, 0)::bigint      AS yoy_clicks,
    COALESCE(y.impressions, 0)::bigint AS yoy_impressions
FROM per_q pq
JOIN clicks_q cq  USING (query)
JOIN top_url tu   USING (query)
LEFT JOIN per_q_mom m USING (query)
LEFT JOIN per_q_yoy y USING (query)
WHERE pq.position BETWEEN 10 AND 20
  AND pq.impressions >= %(min_imp)s
ORDER BY pq.impressions DESC
LIMIT %(limit)s
"""


def compute_opportunities(from_: dt.date, to_: dt.date, limit: int = 50, min_imp: int = 100) -> dict:
    cur_r, mom_r, yoy_r = compute_ranges(from_, to_)
    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
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
#   - marts.canib_query_page_daily : VIEW grano (query, page, date) con is_product
#   - marts.kpis_query_diario      : usamos impressions_dedup pre-calculada
#                                    en lugar de re-calcular MAX(impressions) GROUP BY query, date

_CANNIBALIZATION_SQL = """
WITH q_pages AS (
    SELECT
        query,
        page,
        is_product,
        SUM(clicks)::bigint                                         AS clicks,
        SUM(impressions)::bigint                                    AS impressions,
        SUM(position*impressions)/NULLIF(SUM(impressions),0)        AS position
    FROM marts.canib_query_page_daily
    WHERE event_date BETWEEN %(cur_s)s AND %(cur_e)s
    GROUP BY query, page, is_product
),
q_summary AS (
    SELECT
        query,
        COUNT(DISTINCT page)                                         AS total_urls,
        COUNT(DISTINCT page) FILTER (WHERE is_product = true)        AS product_urls,
        SUM(clicks)::bigint                                          AS clicks
    FROM q_pages
    GROUP BY query
),
-- queries canibalizantes: >1 URL y TODAS producto
canib AS (
    SELECT query, total_urls, clicks
    FROM q_summary
    WHERE product_urls = total_urls
      AND total_urls > 1
    ORDER BY clicks DESC
    LIMIT %(limit)s
),
-- impresiones DEDUP por query: usamos el mart que ya tiene impressions_dedup pre-calculada
imp_q AS (
    SELECT query, SUM(impressions_dedup)::bigint AS impressions
    FROM marts.kpis_query_diario
    WHERE event_date BETWEEN %(cur_s)s AND %(cur_e)s
    GROUP BY query
),
-- Detalle de URLs por query canibalizante (JSON)
urls_detail AS (
    SELECT
        qp.query,
        jsonb_agg(
            jsonb_build_object(
                'page', qp.page,
                'clicks', qp.clicks,
                'impressions', qp.impressions,
                'position', qp.position
            ) ORDER BY qp.clicks DESC
        ) AS urls
    FROM q_pages qp
    JOIN canib c ON c.query = qp.query
    GROUP BY qp.query
)
SELECT
    c.query, c.total_urls, c.clicks,
    i.impressions,
    u.urls
FROM canib c
JOIN imp_q i  USING (query)
JOIN urls_detail u USING (query)
ORDER BY c.clicks DESC
"""


def compute_cannibalization(from_: dt.date, to_: dt.date, limit: int = 50) -> dict:
    cur_r, _, _ = compute_ranges(from_, to_)
    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
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
