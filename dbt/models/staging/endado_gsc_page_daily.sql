{{
  config(
    materialized='incremental',
    unique_key=['page', 'event_date'],
    on_schema_change='sync_all_columns',
    indexes=[
      {'columns': ['event_date'], 'type': 'btree'},
      {'columns': ['page'], 'type': 'btree'},
    ]
  )
}}

-- staging.endado_gsc_page_daily
-- Limpieza de extraccion_gsc_page con flags pre-calculados.
-- Grano: una fila por (page, event_date).
-- Refresh incremental con lookback 7 dias para capturar ajustes tardios de GSC.

SELECT
    date                                               AS event_date,
    DATE_TRUNC('week',  date)::date                    AS week_start,
    DATE_TRUNC('month', date)::date                    AS month_start,
    page,
    cat,
    COALESCE(prod_bool, false)                         AS is_product,
    COALESCE(blog, false)                              AS is_blog,
    page LIKE '%/recambios/%'                          AS is_recambio,
    page IN (
        'https://www.endado.com/',
        'http://www.endado.com/'
    )                                                  AS is_home,
    clicks::bigint                                     AS clicks,
    impressions::bigint                                AS impressions,
    position                                           AS position,
    position * impressions::float                      AS pos_num
FROM {{ source('raw', 'extraccion_gsc_page') }}

{% if is_incremental() %}
WHERE date > (SELECT COALESCE(MAX(event_date), '1900-01-01'::date) - INTERVAL '7 days' FROM {{ this }})
{% endif %}
