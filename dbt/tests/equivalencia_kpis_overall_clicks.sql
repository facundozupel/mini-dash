-- Custom test de equivalencia para el switch inicial.
-- Verifica que la suma de clicks en marts.kpis_diario (filter='overall')
-- coincide exactamente con la suma de clicks en raw.extraccion_gsc_page.
--
-- Si esta query devuelve filas, el test FALLA: hay drift entre raw y marts.

WITH from_marts AS (
    SELECT SUM(clicks) AS total
    FROM {{ ref('kpis_diario') }}
    WHERE filter = 'overall'
),
from_raw AS (
    SELECT SUM(clicks) AS total
    FROM {{ source('raw', 'extraccion_gsc_page') }}
)
SELECT 'mismatch' AS issue, m.total AS marts_total, r.total AS raw_total
FROM from_marts m, from_raw r
WHERE m.total <> r.total
