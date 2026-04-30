{{
  config(
    materialized='materialized_view',
    on_configuration_change='apply',
    indexes=[
      {'columns': ['event_date'], 'type': 'brin'},
      {'columns': ['query'], 'type': 'btree'},
    ]
  )
}}

-- marts.canib_query_summary_daily
-- Resumen per (query, event_date) para acelerar /tables/cannibalization en
-- custom date ranges.
--
-- El cuello en la query original era escanear marts.canib_query_page_daily
-- (grano query+page+dia) sobre 30+ dias para identificar queries canibalizantes.
-- Aca pre-resolvemos el resumen por dia, asi:
--
--   - clicks/impressions/impressions_dedup → SUM/MAX directo (aditivo)
--   - all_product over range → BOOL_AND(all_product_today)
--   - total_urls over range  → COUNT(DISTINCT) sobre el unnest del array `pages`
--
-- El detalle de URLs (JSON con clicks per page) sigue saliendo de
-- canib_query_page_daily, pero solo para las ~50 queries que pasan el filtro
-- (no para el universo completo).

SELECT
    query,
    event_date,
    SUM(clicks)::bigint                AS clicks,
    SUM(impressions)::bigint           AS impressions,
    -- impressions_dedup: la tabla raw de GSC duplica impresiones entre URLs de
    -- una misma query. MAX(impressions) por (query, dia) recupera el numero real
    -- (mismo truco que kpis_query_diario).
    MAX(impressions)::bigint           AS impressions_dedup,
    BOOL_AND(is_product)               AS all_product_today,
    array_agg(DISTINCT page)           AS pages
FROM {{ ref('endado_gsc_query_page_daily') }}
GROUP BY query, event_date
ORDER BY event_date
