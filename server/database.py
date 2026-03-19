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

logger = logging.getLogger("oixa.db")

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
    simulated BOOLEAN DEFAULT TRUE,
    tx_hash TEXT,
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
);

CREATE TABLE IF NOT EXISTS disputes (
    id TEXT PRIMARY KEY,
    auction_id TEXT NOT NULL,
    opened_by TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT DEFAULT 'open',
    fee_amount REAL NOT NULL,
    arbiter_verdict TEXT,
    arbiter_cost_usdc REAL,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    FOREIGN KEY (auction_id) REFERENCES auctions(id)
);

CREATE TABLE IF NOT EXISTS daily_spending (
    id TEXT PRIMARY KEY,
    date TEXT NOT NULL,                -- YYYY-MM-DD in UTC
    amount REAL NOT NULL,
    auction_id TEXT,
    description TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stripe_onramp_sessions (
    id TEXT PRIMARY KEY,               -- oixa_session_xxx
    stripe_session_id TEXT UNIQUE,     -- cos_xxx from Stripe
    client_secret TEXT,
    wallet_address TEXT NOT NULL,
    amount_usd REAL NOT NULL,
    auction_id TEXT,
    agent_id TEXT,
    status TEXT DEFAULT 'pending',     -- pending, fulfillment_complete, error
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS stripe_cardholders (
    id TEXT PRIMARY KEY,               -- oixa_ch_xxx
    stripe_cardholder_id TEXT UNIQUE,  -- ich_xxx from Stripe
    agent_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    email TEXT NOT NULL,
    status TEXT DEFAULT 'active',      -- active, inactive
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stripe_cards (
    id TEXT PRIMARY KEY,               -- oixa_card_xxx
    stripe_card_id TEXT UNIQUE,        -- ic_xxx from Stripe
    cardholder_id TEXT NOT NULL,       -- oixa_ch_xxx
    agent_id TEXT NOT NULL,
    last4 TEXT,
    exp_month INTEGER,
    exp_year INTEGER,
    status TEXT DEFAULT 'active',      -- active, inactive, canceled
    spending_limit_usd REAL DEFAULT 100.0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cctp_transfers (
    id TEXT PRIMARY KEY,               -- oixa_cctp_xxx
    source_chain TEXT NOT NULL,        -- ethereum, arbitrum, avalanche, polygon, solana
    source_tx_hash TEXT,               -- burn tx on source chain
    message_hash TEXT UNIQUE,          -- keccak256 of message bytes
    message_bytes TEXT,                -- hex-encoded message from MessageSent event
    attestation TEXT,                  -- Circle Iris attestation (hex)
    destination_tx_hash TEXT,          -- receiveMessage tx on Base
    amount_usdc REAL NOT NULL,
    recipient TEXT NOT NULL,           -- Base wallet receiving USDC
    status TEXT DEFAULT 'pending',     -- pending, attesting, completing, completed, failed, awaiting_message
    auction_id TEXT,
    agent_id TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS coinbase_charges (
    id TEXT PRIMARY KEY,               -- oixa_cb_xxx
    coinbase_charge_id TEXT UNIQUE,    -- Coinbase internal UUID
    charge_code TEXT UNIQUE,           -- short code (e.g. AXYZ1234)
    hosted_url TEXT,
    amount_usdc REAL NOT NULL,
    description TEXT,
    status TEXT DEFAULT 'pending',     -- pending, completed, expired, failed, unresolved
    payment_network TEXT,              -- detected source network on completion
    auction_id TEXT,
    agent_id TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS circle_payments (
    id TEXT PRIMARY KEY,               -- oixa_circle_xxx
    circle_intent_id TEXT,             -- Circle payment intent ID
    circle_payment_id TEXT,            -- Circle incoming payment ID
    amount_usdc REAL NOT NULL,
    description TEXT,
    source_chain TEXT,
    status TEXT DEFAULT 'pending',     -- pending, paid, failed, canceled
    auction_id TEXT,
    agent_id TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS capacity_listings (
    id TEXT PRIMARY KEY,               -- oixa_spot_xxx
    agent_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    capabilities TEXT NOT NULL,        -- JSON array of capability strings
    base_price_usdc REAL NOT NULL,
    surge_price_usdc REAL NOT NULL,
    max_tasks INTEGER DEFAULT 1,
    available_until TEXT,              -- ISO timestamp or NULL
    wallet_address TEXT,
    description TEXT,
    status TEXT DEFAULT 'active',      -- active, retired, paused
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS spot_requests (
    id TEXT PRIMARY KEY,               -- oixa_sreq_xxx
    requester_id TEXT NOT NULL,
    listing_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    capability TEXT NOT NULL,
    task_description TEXT NOT NULL,
    agreed_price_usdc REAL NOT NULL,
    surge_multiplier REAL NOT NULL,
    urgency TEXT DEFAULT 'normal',     -- normal, high, critical
    status TEXT DEFAULT 'pending',     -- pending, completed, cancelled, disputed
    created_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY (listing_id) REFERENCES capacity_listings(id)
);

CREATE TABLE IF NOT EXISTS a2a_tasks (
    id TEXT PRIMARY KEY,               -- a2a_task_xxx
    session_id TEXT NOT NULL,
    input_text TEXT NOT NULL,
    skill_used TEXT,
    result_json TEXT,
    status TEXT DEFAULT 'pending',     -- pending, completed, cancelled, failed
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS onboarding_sessions (
    id TEXT PRIMARY KEY,               -- oixa_ob_xxx
    agent_id TEXT,
    wallet_address TEXT,
    state TEXT NOT NULL,               -- no_wallet, wallet_no_funds, has_tokens, has_usdc, registered, earning
    channel TEXT DEFAULT 'terminal',   -- terminal, telegram, web, mcp
    swap_tx_hash TEXT,
    usdc_after_swap REAL,
    offer_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


async def init_db():
    db = await get_db()
    await db.executescript(CREATE_TABLES_SQL)
    await db.commit()
    # Migrate existing DB: add columns if they don't exist (SQLite only)
    await _migrate(db)
    logger.info("Database tables initialized")


async def _migrate(db):
    """Add new columns to existing tables without breaking older DBs."""
    migrations = [
        # escrows: blockchain tracking columns
        "ALTER TABLE escrows ADD COLUMN simulated BOOLEAN DEFAULT TRUE",
        "ALTER TABLE escrows ADD COLUMN tx_hash TEXT",
        # offers: optional on-chain wallet
        "ALTER TABLE offers ADD COLUMN wallet_address TEXT",
        # auctions: 'delivered' status support (no column change needed, status is TEXT)
        # disputes table — created by CREATE TABLE IF NOT EXISTS above, no ALTER needed
    ]
    for stmt in migrations:
        try:
            await db.execute(stmt, ())
            await db.commit()
        except Exception:
            pass  # column already exists — ignore
