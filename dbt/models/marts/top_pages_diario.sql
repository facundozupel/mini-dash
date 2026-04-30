{{
  config(
    materialized='materialized_view',
    on_configuration_change='apply',
    indexes=[
      {'columns': ['filter', 'page', 'event_date'], 'unique': true},
      {'columns': ['event_date'], 'type': 'brin'},
      {'columns': ['filter'],     'type': 'btree'},
    ]
  )
}}

-- marts.top_pages_diario
-- Top-pages pre-agregadas por (filter, page, dia).
-- Grano: una fila por (filter, page, event_date). ~10-15M filas.
-- Sirve a /tables/top-products y /tables/top-categories.
-- MV con CONCURRENTLY para no bloquear lectores durante refresh.
--
-- BRIN(event_date) + btree(filter): rangos de fecha amplios sobre tabla grande
-- chocaban con btree(filter, event_date) (heap fetches dispersos = 6-8s en
-- custom range). ORDER BY event_date al final clustera la MV fisicamente para
-- que BRIN devuelva block ranges ajustados.

WITH base AS (
    SELECT
        event_date,
        page,
        cat,
        clicks,
        impressions,
        pos_num,
        is_product,
        is_blog,
        is_home,
        is_recambio
    FROM {{ ref('endado_gsc_page_daily') }}
),

products AS (
    SELECT 'products'::text AS filter, event_date, page, MAX(cat) AS cat,
           SUM(clicks) AS clicks, SUM(impressions) AS impressions, SUM(pos_num) AS pos_num
    FROM base WHERE is_product
    GROUP BY event_date, page
),

category AS (
    SELECT 'category'::text AS filter, event_date, page, MAX(cat) AS cat,
           SUM(clicks) AS clicks, SUM(impressions) AS impressions, SUM(pos_num) AS pos_num
    FROM base WHERE NOT is_product AND NOT is_blog AND NOT is_home AND NOT is_recambio
    GROUP BY event_date, page
),

recambios AS (
    SELECT 'recambios'::text AS filter, event_date, page, MAX(cat) AS cat,
           SUM(clicks) AS clicks, SUM(impressions) AS impressions, SUM(pos_num) AS pos_num
    FROM base WHERE is_recambio
    GROUP BY event_date, page
),

overall AS (
    SELECT 'overall'::text AS filter, event_date, page, MAX(cat) AS cat,
           SUM(clicks) AS clicks, SUM(impressions) AS impressions, SUM(pos_num) AS pos_num
    FROM base
    GROUP BY event_date, page
)

SELECT * FROM (
    SELECT * FROM overall
    UNION ALL SELECT * FROM products
    UNION ALL SELECT * FROM category
    UNION ALL SELECT * FROM recambios
) u
ORDER BY event_date
