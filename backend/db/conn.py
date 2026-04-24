from psycopg_pool import ConnectionPool
from settings import settings

_dsn = (
    f"host={settings.db_host} port={settings.db_port} "
    f"dbname={settings.db_name} user={settings.db_user} "
    f"password={settings.db_password}"
)

pool = ConnectionPool(_dsn, min_size=1, max_size=5, open=False)


def ping() -> bool:
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            return cur.fetchone()[0] == 1
