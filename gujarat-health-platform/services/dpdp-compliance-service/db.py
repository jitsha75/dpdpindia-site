import os
import time
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://dpdp:dpdp_demo_pw@localhost:5432/dpdp")
pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=8, open=False, kwargs={"row_factory": dict_row, "prepare_threshold": None},
                      timeout=30)


def open_pool(retries=60):
    last = None
    for _ in range(retries):
        try:
            pool.open(wait=True, timeout=5)
            with pool.connection() as c:
                c.execute("select 1")
            return
        except Exception as e:  # database still starting
            last = e
            time.sleep(1)
    raise RuntimeError(f"database not reachable: {last}")


class Tx:
    """Thin wrapper around a connection so call sites stay short."""
    def __init__(self, conn):
        self.c = conn

    def all(self, sql, params=None):
        with self.c.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else []

    def one(self, sql, params=None):
        with self.c.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone() if cur.description else None

    def run(self, sql, params=None):
        with self.c.cursor() as cur:
            cur.execute(sql, params)
            return cur.rowcount

    def many(self, sql, rows):
        if rows:
            with self.c.cursor() as cur:
                cur.executemany(sql, rows)

    def val(self, sql, params=None):
        r = self.one(sql, params)
        return next(iter(r.values())) if r else None


@contextmanager
def tx():
    with pool.connection() as conn:
        yield Tx(conn)       # commits on clean exit, rolls back on exception


def J(obj):
    return Jsonb(obj)


def init_schema():
    sql = (Path(__file__).parent / "schema.sql").read_text()
    with pool.connection() as conn:
        conn.execute(sql)


def get_setting(t: Tx, key, default=None):
    r = t.one("select value from ops.settings where key=%s", (key,))
    return r["value"] if r else default


def set_setting(t: Tx, key, value):
    t.run("insert into ops.settings(key,value) values(%s,%s) on conflict(key) do update set value=excluded.value, updated_at=now()",
          (key, J(value)))
