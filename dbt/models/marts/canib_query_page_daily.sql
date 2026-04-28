{{
  config(materialized='view')
}}

-- marts.canib_query_page_daily
-- Proyeccion liviana de staging.endado_gsc_query_page_daily con solo las columnas
-- que necesita /tables/cannibalization. No materializa: sin sentido duplicar 3GB.
-- Si en el futuro la query es lenta, promover a MV con agregacion mas chica.

SELECT
    event_date,
    query,
    page,
    is_product,
    clicks,
    impressions,
    position
FROM {{ ref('endado_gsc_query_page_daily') }}
