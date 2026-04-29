{{
  config(
    materialized='incremental',
    unique_key=['query', 'page', 'event_date'],
    on_schema_change='sync_all_columns',
    indexes=[
      {'columns': ['event_date'], 'type': 'brin'},
      {'columns': ['query'], 'type': 'btree'},
      {'columns': ['page'], 'type': 'btree'},
    ]
  )
}}

-- staging.endado_gsc_query_page_daily
-- Limpieza de extraccion_gsc con flags pre-calculados.
-- Grano: una fila por (query, page, event_date). ~18M filas.
-- Refresh incremental con lookback 7 dias.
--
-- BRIN(event_date): los rangos amplios (>1% de la tabla) le ganaban al btree
-- (Postgres elegia seq scan). BRIN ocupa ~100x menos y es ideal para columnas
-- ordenadas fisicamente, que es el caso porque los rows se appendean por fecha.

SELECT
    date                                               AS event_date,
    DATE_TRUNC('week',  date)::date                    AS week_start,
    DATE_TRUNC('month', date)::date                    AS month_start,
    query,
    page,
    cat,
    COALESCE(prod_bool, false)                         AS is_product,
    COALESCE(blog, false)                              AS is_blog,
    COALESCE(kws_brand, false)                         AS is_brand,
    page LIKE '%/recambios/%'                          AS is_recambio,
    page IN (
        'https://www.endado.com/',
        'http://www.endado.com/'
    )                                                  AS is_home,
    clicks::bigint                                     AS clicks,
    impressions::bigint                                AS impressions,
    position                                           AS position,
    position * impressions::float                      AS pos_num
FROM {{ source('raw', 'extraccion_gsc') }}

{% if is_incremental() %}
WHERE date > (SELECT COALESCE(MAX(event_date), '1900-01-01'::date) - INTERVAL '7 days' FROM {{ this }})
{% endif %}
