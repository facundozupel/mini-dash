"""
Tablas del dashboard:
- top_pages (top productos / top categorias): usa extraccion_gsc_page
- opportunities (queries pos 10-20):           usa extraccion_gsc
- cannibalization (queries con >1 URL prod):   usa extraccion_gsc
"""
import datetime as dt

from psycopg.rows import dict_row

from db.conn import pool
from metrics import FILTERS, compute_ranges


# =====================================================================
# 1) TOP PAGES (top-products + top-categories)
# =====================================================================

_TOP_PAGES_SQL = """
WITH top AS (
    SELECT page, SUM(clicks) AS clicks
    FROM extraccion_gsc_page
    WHERE date BETWEEN %(cur_s)s AND %(cur_e)s AND ({filter})
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
        CASE WHEN SUM(impressions)>0 THEN SUM(position*impressions)/SUM(impressions) ELSE 0 END AS position
    FROM extraccion_gsc_page
    WHERE date BETWEEN %(cur_s)s AND %(cur_e)s AND ({filter})
      AND page IN (SELECT page FROM top)
    GROUP BY page
),
mom AS (
    SELECT page,
        SUM(clicks)::bigint AS clicks,
        SUM(impressions)::bigint AS impressions,
        CASE WHEN SUM(impressions)>0 THEN SUM(clicks)::float/SUM(impressions) ELSE 0 END AS ctr,
        CASE WHEN SUM(impressions)>0 THEN SUM(position*impressions)/SUM(impressions) ELSE 0 END AS position
    FROM extraccion_gsc_page
    WHERE date BETWEEN %(mom_s)s AND %(mom_e)s AND ({filter})
      AND page IN (SELECT page FROM top)
    GROUP BY page
),
yoy AS (
    SELECT page,
        SUM(clicks)::bigint AS clicks,
        SUM(impressions)::bigint AS impressions,
        CASE WHEN SUM(impressions)>0 THEN SUM(clicks)::float/SUM(impressions) ELSE 0 END AS ctr,
        CASE WHEN SUM(impressions)>0 THEN SUM(position*impressions)/SUM(impressions) ELSE 0 END AS position
    FROM extraccion_gsc_page
    WHERE date BETWEEN %(yoy_s)s AND %(yoy_e)s AND ({filter})
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
    sql = _TOP_PAGES_SQL.format(filter=FILTERS[filter_name])

    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, {
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

_OPPORTUNITIES_SQL = """
WITH per_q_d AS (
    -- impresiones DEDUPLICADAS por query+date (MAX), y posicion ponderada por imp dentro del dia
    SELECT
        query, date,
        MAX(impressions)::bigint                                        AS impressions,
        SUM(position * impressions) / NULLIF(SUM(impressions), 0)       AS position
    FROM extraccion_gsc
    WHERE date BETWEEN %(cur_s)s AND %(cur_e)s
    GROUP BY query, date
),
per_q AS (
    SELECT
        query,
        SUM(impressions)::bigint                                         AS impressions,
        SUM(position * impressions) / NULLIF(SUM(impressions), 0)        AS position
    FROM per_q_d
    GROUP BY query
),
clicks_q AS (
    SELECT query, SUM(clicks)::bigint AS clicks
    FROM extraccion_gsc
    WHERE date BETWEEN %(cur_s)s AND %(cur_e)s
    GROUP BY query
),
top_url AS (
    SELECT DISTINCT ON (query) query, page
    FROM (
        SELECT query, page, SUM(impressions) AS imp
        FROM extraccion_gsc
        WHERE date BETWEEN %(cur_s)s AND %(cur_e)s
        GROUP BY query, page
    ) t
    ORDER BY query, imp DESC
),
-- periodos anteriores (clicks + dedup impresiones, sin filtro posicion)
per_q_mom AS (
    SELECT q.query,
        COALESCE(SUM(c.clicks), 0)::bigint AS clicks,
        COALESCE(SUM(i.impressions), 0)::bigint AS impressions
    FROM (SELECT DISTINCT query FROM per_q) q
    LEFT JOIN (
        SELECT query, SUM(clicks) AS clicks
        FROM extraccion_gsc
        WHERE date BETWEEN %(mom_s)s AND %(mom_e)s
        GROUP BY query
    ) c ON c.query = q.query
    LEFT JOIN (
        SELECT query, SUM(max_imp) AS impressions FROM (
            SELECT query, date, MAX(impressions) AS max_imp
            FROM extraccion_gsc
            WHERE date BETWEEN %(mom_s)s AND %(mom_e)s
            GROUP BY query, date
        ) t GROUP BY query
    ) i ON i.query = q.query
    GROUP BY q.query
),
per_q_yoy AS (
    SELECT q.query,
        COALESCE(SUM(c.clicks), 0)::bigint AS clicks,
        COALESCE(SUM(i.impressions), 0)::bigint AS impressions
    FROM (SELECT DISTINCT query FROM per_q) q
    LEFT JOIN (
        SELECT query, SUM(clicks) AS clicks
        FROM extraccion_gsc
        WHERE date BETWEEN %(yoy_s)s AND %(yoy_e)s
        GROUP BY query
    ) c ON c.query = q.query
    LEFT JOIN (
        SELECT query, SUM(max_imp) AS impressions FROM (
            SELECT query, date, MAX(impressions) AS max_imp
            FROM extraccion_gsc
            WHERE date BETWEEN %(yoy_s)s AND %(yoy_e)s
            GROUP BY query, date
        ) t GROUP BY query
    ) i ON i.query = q.query
    GROUP BY q.query
)
SELECT
    pq.query, tu.page, cq.clicks, pq.impressions, pq.position,
    CASE WHEN pq.impressions > 0 THEN cq.clicks::float / pq.impressions ELSE 0 END AS ctr,
    m.clicks AS mom_clicks, m.impressions AS mom_impressions,
    y.clicks AS yoy_clicks, y.impressions AS yoy_impressions
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

_CANNIBALIZATION_SQL = """
WITH q_pages AS (
    SELECT
        query,
        page,
        prod_bool,
        SUM(clicks)::bigint                                         AS clicks,
        SUM(impressions)::bigint                                    AS impressions,
        SUM(position*impressions)/NULLIF(SUM(impressions),0)        AS position
    FROM extraccion_gsc
    WHERE date BETWEEN %(cur_s)s AND %(cur_e)s
    GROUP BY query, page, prod_bool
),
q_summary AS (
    SELECT
        query,
        COUNT(DISTINCT page)                                         AS total_urls,
        COUNT(DISTINCT page) FILTER (WHERE prod_bool = true)         AS product_urls,
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
-- impresiones DEDUP por query
imp_q AS (
    SELECT query, SUM(max_imp)::bigint AS impressions
    FROM (
        SELECT query, date, MAX(impressions) AS max_imp
        FROM extraccion_gsc
        WHERE date BETWEEN %(cur_s)s AND %(cur_e)s
        GROUP BY query, date
    ) t
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
