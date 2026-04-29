{{
  config(
    materialized='materialized_view',
    on_configuration_change='apply',
    indexes=[
      {'columns': ['event_date'], 'type': 'brin'},
      {'columns': ['query'], 'type': 'btree'},
      {'columns': ['query', 'event_date'], 'type': 'btree'},
    ]
  )
}}

-- marts.canib_query_page_daily
-- Proyeccion liviana de staging.endado_gsc_query_page_daily con solo las columnas
-- que necesita /tables/cannibalization (y top_url para opportunities).
--
-- Antes era VIEW. Pero cada query que pegaba aca expandia a un seq scan a la
-- staging de 18M filas (Postgres elegia seq scan sobre btree(event_date) por
-- selectividad ~4%). Promovido a MV con BRIN(event_date) + btree(query).
--
-- ORDER BY event_date al final para que las paginas fisicas queden clusteradas
-- por fecha; eso hace que BRIN devuelva block ranges muy ajustados.

SELECT
    event_date,
    query,
    page,
    is_product,
    clicks,
    impressions,
    position
FROM {{ ref('endado_gsc_query_page_daily') }}
ORDER BY event_date
