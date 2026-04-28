{{
  config(
    materialized='materialized_view',
    on_configuration_change='apply',
    indexes=[
      {'columns': ['query', 'event_date'], 'unique': true},
      {'columns': ['event_date'], 'type': 'btree'},
    ]
  )
}}

-- marts.kpis_query_diario
-- KPIs query-level por (query, dia) con dedup MAX(impressions) PRE-CALCULADO.
-- Grano: una fila por (query, event_date). ~10M filas.
-- Sirve a /tables/opportunities.
--
-- Por que MAX(impressions) por query+date:
-- en extraccion_gsc, las impresiones se duplican entre URLs de la misma query.
-- La dedup correcta es MAX por (query, date), no SUM.
-- Aca lo dejamos pre-calculado para que opportunities deje de hacer ese CTE caro.
--
-- Para position: ponderada por impressions usando pos_num/impressions de cada
-- (query, page, date). En el rollup a (query, date) sumamos pos_num y impressions
-- y dividimos al final.

SELECT
    query,
    event_date,
    SUM(clicks)::bigint                                              AS clicks,
    MAX(impressions)::bigint                                         AS impressions_dedup,
    SUM(pos_num)                                                     AS pos_num,
    SUM(impressions)                                                 AS impressions_sum  -- denominador para position si se necesita
FROM {{ ref('endado_gsc_query_page_daily') }}
GROUP BY query, event_date
