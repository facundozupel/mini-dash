{{
  config(
    materialized='table',
    indexes=[
      {'columns': ['filter', 'event_date'], 'unique': true},
      {'columns': ['event_date'], 'type': 'btree'},
    ]
  )
}}

-- marts.kpis_diario
-- KPIs page-level pre-agregados por filter+dia.
-- Grano: una fila por (filter, event_date). ~2400 filas (4 filtros x ~600d).
-- Sirve a /metrics/* y /trend/*.
-- Pre-explota los 4 filtros: una sola query del backend pega 1 fila por dia.

WITH base AS (
    SELECT
        event_date,
        page,
        clicks,
        impressions,
        pos_num,
        is_product,
        is_blog,
        is_home,
        is_recambio
    FROM {{ ref('endado_gsc_page_daily') }}
),

overall AS (
    SELECT 'overall'::text AS filter, event_date,
           SUM(clicks) AS clicks, SUM(impressions) AS impressions, SUM(pos_num) AS pos_num
    FROM base
    GROUP BY event_date
),

products AS (
    SELECT 'products'::text AS filter, event_date,
           SUM(clicks) AS clicks, SUM(impressions) AS impressions, SUM(pos_num) AS pos_num
    FROM base
    WHERE is_product
    GROUP BY event_date
),

category AS (
    SELECT 'category'::text AS filter, event_date,
           SUM(clicks) AS clicks, SUM(impressions) AS impressions, SUM(pos_num) AS pos_num
    FROM base
    WHERE NOT is_product AND NOT is_blog AND NOT is_home AND NOT is_recambio
    GROUP BY event_date
),

recambios AS (
    SELECT 'recambios'::text AS filter, event_date,
           SUM(clicks) AS clicks, SUM(impressions) AS impressions, SUM(pos_num) AS pos_num
    FROM base
    WHERE is_recambio
    GROUP BY event_date
)

SELECT * FROM overall
UNION ALL SELECT * FROM products
UNION ALL SELECT * FROM category
UNION ALL SELECT * FROM recambios
