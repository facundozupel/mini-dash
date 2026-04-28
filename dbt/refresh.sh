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

if [ $RC -eq 0 ]; then
    # Ping success
    curl -fsS --retry 3 -m 10 --data-raw "OK $(tail -3 ${LOG_FILE})" "${HC_URL}" > /dev/null 2>&1 || true
else
    # Ping fail con cola del log
    curl -fsS --retry 3 -m 10 --data-raw "FAIL rc=$RC. Tail log: $(tail -20 ${LOG_FILE})" "${HC_URL}/fail" > /dev/null 2>&1 || true
fi

exit $RC
