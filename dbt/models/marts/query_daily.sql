{{
  config(
    materialized='materialized_view',
    on_configuration_change='apply',
    indexes=[
      {'columns': ['query', 'event_date'], 'unique': true},
      {'columns': ['event_date'], 'type': 'brin'},
    ]
  )
}}

-- marts.query_daily
-- Grano: (query, event_date). ~10M filas.
-- Unifica los dos marts anteriores que tenian el mismo grano pero columnas
-- distintas:
--   - kpis_query_diario        → opportunities (clicks, imp_dedup, pos_num, imp_sum)
--   - canib_query_summary_daily → cannibalization (clicks, imp_dedup, all_product, pages)
--
-- Las columnas en comun (clicks, impressions_dedup) estaban guardadas dos
-- veces en disco. La unificada las guarda una sola vez.
--
-- TOAST: la columna `pages` (array de URLs distinct por dia) puede ser larga.
-- Postgres la guarda en una tabla auxiliar (TOAST) y solo la lee si la query
-- la pide explicitamente. Opportunities NO la pide → cero overhead.
--
-- Indices: igual que las viejas pero sin el btree(query) standalone, que en
-- ambas viejas tenia uso despreciable (~1 idx_scan) y pesaba 122 MB cada uno.
-- El unique(query, event_date) cubre lookups por query como leading-column.

SELECT
    query,
    event_date,
    SUM(clicks)::bigint                AS clicks,
    SUM(impressions)::bigint           AS impressions_sum,
    MAX(impressions)::bigint           AS impressions_dedup,
    SUM(pos_num)                       AS pos_num,
    BOOL_AND(is_product)               AS all_product_today,
    array_agg(DISTINCT page)           AS pages
FROM {{ ref('endado_gsc_query_page_daily') }}
GROUP BY query, event_date
ORDER BY event_date
