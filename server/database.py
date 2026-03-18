"""
Database layer — supports PostgreSQL (production) and SQLite (local/fallback).
Set DATABASE_URL=postgresql://... to use Postgres, otherwise SQLite is used.

The public API (get_db()) returns a connection-like object that supports:
  - async with db.execute(sql, params) as cursor: cursor.fetchone/fetchall()
  - await db.execute(sql, params)
  - await db.executescript(sql)
  - await db.commit()

This matches the aiosqlite API so all existing code works unchanged.
For PostgreSQL, a thin adapter translates to asyncpg.
"""

import logging
from config import DB_PATH, DATABASE_URL, USE_POSTGRES

logger = logging.getLogger("axon.db")

# ---------------------------------------------------------------------------
# SQLite backend (default / fallback) — raw aiosqlite connection
# ---------------------------------------------------------------------------

_sqlite_conn = None


async def _get_sqlite_conn():
    global _sqlite_conn
    if _sqlite_conn is None:
        import aiosqlite
        _sqlite_conn = await aiosqlite.connect(DB_PATH)
        _sqlite_conn.row_factory = aiosqlite.Row
    return _sqlite_conn


# ---------------------------------------------------------------------------
# PostgreSQL adapter — wraps asyncpg pool to look like aiosqlite
# ---------------------------------------------------------------------------

_pg_pool = None


async def _get_pg_pool():
    global _pg_pool
    if _pg_pool is None:
        try:
            import asyncpg
            _pg_pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
            logger.info("PostgreSQL pool connected")
        except Exception as e:
            logger.warning(f"PostgreSQL unavailable ({e}) — falling back to SQLite")
            return None
    return _pg_pool


def _sqlite_to_pg(sql: str) -> str:
    """Convert SQLite-style ? placeholders to asyncpg $1, $2, ..."""
    result, counter = [], 1
    for ch in sql:
        if ch == "?":
            result.append(f"${counter}")
            counter += 1
        else:
            result.append(ch)
    return "".join(result)


def _ddl_sqlite_to_pg(stmt: str) -> str:
    return (
        stmt
        .replace(" REAL NOT NULL", " DOUBLE PRECISION NOT NULL")
        .replace(" REAL DEFAULT", " DOUBLE PRECISION DEFAULT")
        .replace(" REAL,", " DOUBLE PRECISION,")
        .replace(" REAL\n", " DOUBLE PRECISION\n")
        .replace("FOREIGN KEY", "-- FOREIGN KEY")  # asyncpg DDL doesn't need FK in same stmt
    )


class _PGCursor:
    """asyncpg result wrapper that looks like aiosqlite cursor."""

    def __init__(self, records):
        self._records = [dict(r) for r in records] if records else []

    async def fetchone(self):
        return self._records[0] if self._records else None

    async def fetchall(self):
        return self._records

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass


class _PGExecuteProxy:
    """Returned by PGConnection.execute(); supports both await and async with."""

    def __init__(self, conn, sql, params):
        self._conn = conn
        self._sql = sql
        self._params = params
        self._cursor = None

    def __await__(self):
        return self._run().__await__()

    async def _run(self):
        sql = _sqlite_to_pg(self._sql)
        stripped = self._sql.strip().upper()
        if stripped.startswith("SELECT") or stripped.startswith("WITH"):
            records = await self._conn.fetch(sql, *self._params)
            return _PGCursor(records)
        else:
            await self._conn.execute(sql, *self._params)
            return _PGCursor([])

    async def __aenter__(self):
        self._cursor = await self._run()
        return self._cursor

    async def __aexit__(self, *_):
        pass


class _PGConnection:
    """Single-use asyncpg connection wrapper that looks like aiosqlite."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql: str, params: tuple = ()):
        return _PGExecuteProxy(self._conn, sql, params)

    async def executescript(self, script: str):
        for stmt in script.split(";"):
            stmt = stmt.strip()
            if stmt:
                try:
                    await self._conn.execute(_ddl_sqlite_to_pg(stmt))
                except Exception as e:
                    logger.debug(f"DDL: {e} — {stmt[:60]}")

    async def commit(self):
        pass  # asyncpg auto-commits outside explicit transactions


class _PGConnectionPool:
    """Pool-backed connection: acquires a connection per execute call."""

    def __init__(self, pool):
        self._pool = pool

    def execute(self, sql: str, params: tuple = ()):
        return _PoolExecuteProxy(self._pool, sql, params)

    async def executescript(self, script: str):
        async with self._pool.acquire() as conn:
            wrapper = _PGConnection(conn)
            await wrapper.executescript(script)

    async def commit(self):
        pass


class _PoolExecuteProxy:
    def __init__(self, pool, sql, params):
        self._pool = pool
        self._sql = sql
        self._params = params

    def __await__(self):
        return self._run().__await__()

    async def _run(self):
        async with self._pool.acquire() as conn:
            proxy = _PGExecuteProxy(conn, self._sql, self._params)
            return await proxy._run()

    async def __aenter__(self):
        self._result = await self._run()
        return self._result

    async def __aexit__(self, *_):
        pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_db = None


async def get_db():
    """Return a db connection supporting the aiosqlite-compatible interface."""
    global _db
    if _db is not None:
        return _db

    if USE_POSTGRES:
        pool = await _get_pg_pool()
        if pool is not None:
            _db = _PGConnectionPool(pool)
            logger.info("DB backend: PostgreSQL")
            return _db
        logger.warning("Falling back to SQLite")

    _db = await _get_sqlite_conn()
    logger.info(f"DB backend: SQLite ({DB_PATH})")
    return _db


CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS offers (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    capabilities TEXT NOT NULL,
    price_per_unit REAL NOT NULL,
    currency TEXT DEFAULT 'USDC',
    status TEXT DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS auctions (
    id TEXT PRIMARY KEY,
    rfi_description TEXT NOT NULL,
    max_budget REAL NOT NULL,
    currency TEXT DEFAULT 'USDC',
    requester_id TEXT NOT NULL,
    winner_id TEXT,
    winning_bid REAL,
    status TEXT DEFAULT 'open',
    auction_duration_seconds INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    closed_at TEXT,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS bids (
    id TEXT PRIMARY KEY,
    auction_id TEXT NOT NULL,
    bidder_id TEXT NOT NULL,
    bidder_name TEXT NOT NULL,
    amount REAL NOT NULL,
    stake_amount REAL NOT NULL,
    status TEXT DEFAULT 'active',
    created_at TEXT NOT NULL,
    FOREIGN KEY (auction_id) REFERENCES auctions(id)
);

CREATE TABLE IF NOT EXISTS escrows (
    id TEXT PRIMARY KEY,
    auction_id TEXT NOT NULL,
    payer_id TEXT NOT NULL,
    payee_id TEXT NOT NULL,
    amount REAL NOT NULL,
    commission REAL NOT NULL,
    status TEXT DEFAULT 'held',
    created_at TEXT NOT NULL,
    released_at TEXT,
    FOREIGN KEY (auction_id) REFERENCES auctions(id)
);

CREATE TABLE IF NOT EXISTS verifications (
    id TEXT PRIMARY KEY,
    auction_id TEXT NOT NULL,
    output_hash TEXT NOT NULL,
    verified_at TEXT NOT NULL,
    passed BOOLEAN NOT NULL,
    details TEXT,
    FOREIGN KEY (auction_id) REFERENCES auctions(id)
);

CREATE TABLE IF NOT EXISTS ledger (
    id TEXT PRIMARY KEY,
    transaction_type TEXT NOT NULL,
    from_agent TEXT NOT NULL,
    to_agent TEXT NOT NULL,
    amount REAL NOT NULL,
    currency TEXT DEFAULT 'USDC',
    auction_id TEXT,
    description TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS protocol_revenue (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    amount REAL NOT NULL,
    currency TEXT DEFAULT 'USDC',
    auction_id TEXT,
    period TEXT,
    simulated BOOLEAN DEFAULT TRUE,
    created_at TEXT NOT NULL
)
"""


async def init_db():
    db = await get_db()
    await db.executescript(CREATE_TABLES_SQL)
    await db.commit()
    logger.info("Database tables initialized")
