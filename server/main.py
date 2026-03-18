import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.logger import setup_logging
from database import init_db, get_db, USE_POSTGRES
from core.openclaw import openclaw_client
from core.rate_limiter import rate_limiter
from config import BLOCKCHAIN_ENABLED, PROTOCOL_VERSION, LOG_DIR, DISPUTE_WINDOW_MINUTES
from api.offers import router as offers_router
from api.auctions import router as auctions_router
from api.escrow import router as escrow_router
from api.verify import router as verify_router
from api.ledger import router as ledger_router
from api.aipi import router as aipi_router
from api.status import router as status_router
from api.disputes import router as disputes_router
from api.admin import router as admin_router

logger = setup_logging()

_auto_release_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _auto_release_task

    logger.info("AXON Protocol starting up...")
    await init_db()
    logger.info("Database initialized")

    await openclaw_client.connect()

    # Blockchain client (graceful fallback)
    try:
        from blockchain.escrow_client import escrow_client
        await escrow_client.init()
    except ImportError:
        logger.warning("web3 not installed — blockchain escrow disabled")

    # Auto-release background job
    from core.auto_release import auto_release_loop
    _auto_release_task = asyncio.create_task(auto_release_loop())

    db_backend  = "PostgreSQL" if USE_POSTGRES else "SQLite"
    chain_mode  = "Base mainnet" if BLOCKCHAIN_ENABLED else "simulated"
    logger.info(
        f"DB: {db_backend} | OpenClaw: {openclaw_client.connected} | "
        f"Escrow: {chain_mode} | DisputeWindow: {DISPUTE_WINDOW_MINUTES}m | Logs: {LOG_DIR}"
    )
    logger.info("🚀 AXON Protocol server running")

    from core.telegram_notifier import notify_server_start
    await notify_server_start("Base mainnet" if BLOCKCHAIN_ENABLED else "simulated")

    yield

    # Graceful shutdown
    if _auto_release_task:
        _auto_release_task.cancel()
        try:
            await _auto_release_task
        except asyncio.CancelledError:
            pass
    logger.info("🛑 AXON Protocol server stopped")


app = FastAPI(
    title="AXON Protocol",
    description="The connective tissue of the agent economy",
    version=PROTOCOL_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(offers_router,   prefix="/api/v1")
app.include_router(auctions_router, prefix="/api/v1")
app.include_router(escrow_router,   prefix="/api/v1")
app.include_router(verify_router,   prefix="/api/v1")
app.include_router(ledger_router,   prefix="/api/v1")
app.include_router(aipi_router,     prefix="/api/v1")
app.include_router(status_router,   prefix="/api/v1")
app.include_router(disputes_router, prefix="/api/v1")
app.include_router(admin_router,   prefix="/api/v1")


@app.get("/")
async def root():
    from datetime import datetime, timezone
    escrow_mode = "base_mainnet" if BLOCKCHAIN_ENABLED else "simulated"
    return {
        "protocol":              "AXON",
        "version":               PROTOCOL_VERSION,
        "status":                "operational",
        "phase":                 2 if BLOCKCHAIN_ENABLED else 1,
        "escrow":                escrow_mode,
        "dispute_window_minutes": DISPUTE_WINDOW_MINUTES,
        "db_backend":            "postgresql" if USE_POSTGRES else "sqlite",
        "timestamp":             datetime.now(timezone.utc).isoformat(),
    }


@app.get("/health")
async def health():
    db    = await get_db()
    db_ok = True
    try:
        async with db.execute("SELECT 1 as ok") as cur:
            await cur.fetchone()
    except Exception:
        db_ok = False

    async with db.execute(
        "SELECT SUM(amount) as total FROM protocol_revenue WHERE source = 'commission'"
    ) as cur:
        row = await cur.fetchone()
    total_commissions = row["total"] or 0.0 if row else 0.0

    async with db.execute(
        "SELECT SUM(amount) as total FROM protocol_revenue WHERE source = 'yield'"
    ) as cur:
        row = await cur.fetchone()
    total_yield = row["total"] or 0.0 if row else 0.0

    async with db.execute("SELECT COUNT(*) as total FROM ledger") as cur:
        row = await cur.fetchone()
    total_tx = row["total"] if row else 0

    async with db.execute(
        "SELECT COUNT(*) as total FROM disputes WHERE status = 'open'"
    ) as cur:
        row = await cur.fetchone()
    open_disputes = row["total"] if row else 0

    async with db.execute(
        "SELECT COUNT(*) as total FROM escrows WHERE status = 'pending_release'"
    ) as cur:
        row = await cur.fetchone()
    pending_release = row["total"] if row else 0

    blockchain_info = {
        "enabled":     BLOCKCHAIN_ENABLED,
        "escrow_mode": "base_mainnet" if BLOCKCHAIN_ENABLED else "simulated",
    }
    if BLOCKCHAIN_ENABLED:
        try:
            from blockchain.escrow_client import escrow_client
            if escrow_client.enabled:
                wallet = await escrow_client.get_wallet_balance()
                if wallet:
                    blockchain_info["wallet_usdc"] = wallet["usdc"]
                    blockchain_info["wallet_eth"]  = wallet["eth"]
        except Exception:
            pass

    return {
        "status":      "ok",
        "openclaw":    openclaw_client.connected,
        "db":          "ok" if db_ok else "error",
        "db_backend":  "postgresql" if USE_POSTGRES else "sqlite",
        "blockchain":  blockchain_info,
        "disputes":    {"open": open_disputes, "window_minutes": DISPUTE_WINDOW_MINUTES},
        "escrows":     {"pending_release": pending_release},
        "rate_limiter": rate_limiter.get_stats(),
        "protocol_revenue": {
            "total_commissions_simulated": total_commissions,
            "total_yield_simulated":       total_yield,
            "total_transactions":          total_tx,
            "commission_rate_current":     "5%",
        },
    }
