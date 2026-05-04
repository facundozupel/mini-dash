{{
  config(
    materialized='view'
  )
}}

-- staging.endado_gsc_query_page_daily
-- Limpieza de extraccion_gsc con flags pre-calculados.
-- Grano: una fila por (query, page, event_date). ~18M filas.
--
-- Era TABLE incremental (3.4 GB en disco). Convertida a VIEW para liberar
-- espacio: las transformaciones aplicadas son CPU-cheap (CASE/COALESCE/cast/
-- multiplicacion) y los marts downstream son MV/TABLE full-refresh, asi que
-- recomputan todo igual. El "ahorro" del lookback 7d incremental ya no aplicaba.
--
-- Pre-requisito: raw.extraccion_gsc debe tener BRIN(date) para que los marts
-- consumidores puedan filtrar rangos amplios sin caer en seq scan.

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
