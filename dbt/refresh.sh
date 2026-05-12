#!/usr/bin/env bash
# Cron diario que corre dbt run + pinga healthchecks.io.
# Pings:
#   /start  -> "arrancando"
#   (vacio) -> "termine OK"
#   /fail   -> "fallo, mira el log"
#
# Si dbt run no termina en GRACE_TIMEOUT, healthchecks tambien alerta porque
# el ping de exito nunca llega.

set -uo pipefail

DBT_DIR="/opt/MICRO-DASH/dbt"
VENV="/opt/MICRO-DASH/.venv-dbt"
LOG_DIR="/var/log/dbt-minidash"
HC_UUID="3575e2ee-5d61-4dd6-be49-3e30f5e1dc6f"
HC_URL="https://hc-ping.com/${HC_UUID}"

mkdir -p "$LOG_DIR"
LOG_FILE="${LOG_DIR}/run-$(date +%F-%H%M).log"

# Ping start
curl -fsS --retry 3 -m 10 "${HC_URL}/start" > /dev/null 2>&1 || true

# Run dbt
cd "$DBT_DIR" || { curl -fsS --retry 3 -m 10 --data-raw "cd $DBT_DIR failed" "${HC_URL}/fail"; exit 1; }

"${VENV}/bin/dbt" run >> "$LOG_FILE" 2>&1
RC=$?

if [ $RC -ne 0 ]; then
    # dbt run fallo: alerta con cola del log y exit
    curl -fsS --retry 3 -m 10 --data-raw "RUN FAIL rc=$RC. Tail log: $(tail -20 ${LOG_FILE})" "${HC_URL}/fail" > /dev/null 2>&1 || true
    exit $RC
fi

# dbt run OK → corremos tests en modo ESTRICTO.
# Si algun test falla: alerta con qué test fallo + ABORT (no ANALYZE, no
# flush, no prewarm). El cache del API sigue con respuestas de ayer (sanas)
# por su TTL de 24h. Vos arreglas, corres refresh.sh a mano, y listo.
"${VENV}/bin/dbt" test >> "$LOG_FILE" 2>&1
RC_TEST=$?

if [ $RC_TEST -ne 0 ]; then
    # Extrae solo las lineas relevantes del log (FAIL / Failure / ERROR)
    # para que el mail llegue autoexplicativo.
    FAIL_DETAIL=$(grep -E "FAIL|Failure in test|ERROR" "$LOG_FILE" | tail -20)
    curl -fsS --retry 3 -m 10 --data-raw "TEST FAIL rc=$RC_TEST. ABORT: cache de ayer sigue vivo. Detalle: ${FAIL_DETAIL}" "${HC_URL}/fail" > /dev/null 2>&1 || true
    exit $RC_TEST
fi

# Run + test OK → ANALYZE + prewarm.
# ANALYZE inmediatamente despues de dbt: stats viejas hacen que el planner
# elija seq scan sobre BRIN. Sin esto, queries que deberian tardar 200ms
# tardan 10s+. dbt no corre ANALYZE automatico despues de run.
PG_CONTAINER=$(docker ps -q -f name=postgres_postgres)
if [ -n "$PG_CONTAINER" ]; then
    docker exec -i "$PG_CONTAINER" psql -U postgres -d endado <<SQL >> "$LOG_FILE" 2>&1
ANALYZE public.extraccion_gsc;
ANALYZE public.extraccion_gsc_page;
ANALYZE staging.endado_gsc_page_daily;
ANALYZE marts.kpis_diario;
ANALYZE marts.top_pages_diario;
ANALYZE marts.canib_query_page_daily;
ANALYZE marts.query_daily;
SQL
fi

# Ping success
curl -fsS --retry 3 -m 10 --data-raw "OK $(tail -3 ${LOG_FILE})" "${HC_URL}" > /dev/null 2>&1 || true

# Refresh del cache del API. Dos pasos:
#   1. flush   - tira todas las keys vivas. Garantiza que rangos custom
#                cacheados con marts viejos no sobrevivan al refresh.
#   2. prewarm - re-llena las keys default (4 metrics + 12 trends + 4
#                tablas) leyendo de marts frescos. Sin esto, el primer
#                usuario del dia espera 60s mientras se computa.
ENV_FILE="/opt/mini-dash/deploy/.env"
if [ -f "$ENV_FILE" ]; then
    SECRET=$(grep '^INTERNAL_SECRET=' "$ENV_FILE" | cut -d= -f2-)
    curl -fsS --retry 2 -m 30 -X POST \
        -H "x-internal-secret: ${SECRET}" \
        https://api-minidash.facundo.click/internal/cache/flush \
        >> "$LOG_FILE" 2>&1 || true
    curl -fsS --retry 2 -m 30 -X POST \
        -H "x-internal-secret: ${SECRET}" \
        https://api-minidash.facundo.click/internal/cache/prewarm \
        >> "$LOG_FILE" 2>&1 || true
fi

exit 0
