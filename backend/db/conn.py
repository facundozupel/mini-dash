from psycopg_pool import ConnectionPool
from settings import settings

_dsn = (
    f"host={settings.db_host} port={settings.db_port} "
    f"dbname={settings.db_name} user={settings.db_user} "
    f"password={settings.db_password}"
)


def _configure(conn) -> None:
    # El contenedor postgres del VPS tiene /dev/shm = 64 MB (default Docker, sin
    # --shm-size). Si una query usa parallel workers + agregaciones grandes,
    # falla con: psycopg.errors.DiskFull "could not resize shared memory segment".
    # Apagamos el paralelismo a nivel sesion. Trade-off: queries un poco mas
    # lentas, pero no fallan por shm. Como leemos casi todo de marts ya
    # pre-agregadas, la perdida es despreciable.
    with conn.cursor() as cur:
        cur.execute("SET max_parallel_workers_per_gather = 0")
    conn.commit()


pool = ConnectionPool(_dsn, min_size=1, max_size=5, open=False, configure=_configure)


def ping() -> bool:
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            return cur.fetchone()[0] == 1
