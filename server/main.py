import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from core.logger import setup_logging
from database import init_db, get_db, USE_POSTGRES
from core.openclaw import openclaw_client
from core.rate_limiter import rate_limiter
from config import BLOCKCHAIN_ENABLED, PROTOCOL_VERSION, LOG_DIR, DISPUTE_WINDOW_MINUTES, AGENTOPS_API_KEY
from api.offers import router as offers_router
from api.auctions import router as auctions_router
from api.escrow import router as escrow_router
from api.verify import router as verify_router
from api.ledger import router as ledger_router
from api.aipi import router as aipi_router
from api.status import router as status_router
from api.disputes import router as disputes_router
from api.admin import router as admin_router
from api.payments import router as payments_router
from api.x402_demo import router as x402_router
from api.cctp import router as cctp_router
from api.coinbase_commerce import router as coinbase_router
from api.circle_payments import router as circle_router
from api.payment_hub import router as hub_router
from api.discovery import router as discovery_router
from api.spot_compute import router as spot_router
from api.a2a import router as a2a_router
from api.onboarding import router as onboarding_router
from api.zapier import router as zapier_router

logger = setup_logging()

_auto_release_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _auto_release_task

    logger.info("OIXA Protocol starting up...")

    # AgentOps — init before anything else so startup events are captured
    from core import agentops_tracker
    agentops_tracker.init(AGENTOPS_API_KEY)

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

    # CCTP cross-chain transfer polling
    from core.cctp_client import cctp_poll_loop
    asyncio.create_task(cctp_poll_loop(interval=30))

    # Commission sweep every 6 hours
    from core.commission_sweep import commission_sweep_loop
    asyncio.create_task(commission_sweep_loop())

    # Daily database backup
    from core.backup import backup_loop
    asyncio.create_task(backup_loop())

    db_backend  = "PostgreSQL" if USE_POSTGRES else "SQLite"
    chain_mode  = "Base mainnet" if BLOCKCHAIN_ENABLED else "simulated"
    logger.info(
        f"DB: {db_backend} | OpenClaw: {openclaw_client.connected} | "
        f"Escrow: {chain_mode} | DisputeWindow: {DISPUTE_WINDOW_MINUTES}m | Logs: {LOG_DIR}"
    )
    logger.info("🚀 OIXA Protocol server running")

    from core.telegram_notifier import notify_server_start
    await notify_server_start("Base mainnet" if BLOCKCHAIN_ENABLED else "simulated")

    # AgentOps — server.started event
    db_label = "PostgreSQL" if USE_POSTGRES else "SQLite"
    agentops_tracker.track_server_start(db_label, BLOCKCHAIN_ENABLED, PROTOCOL_VERSION)

    yield

    # Graceful shutdown
    if _auto_release_task:
        _auto_release_task.cancel()
        try:
            await _auto_release_task
        except asyncio.CancelledError:
            pass
    logger.info("🛑 OIXA Protocol server stopped")


app = FastAPI(
    title="OIXA Protocol",
    description="""
**The connective tissue of the agent economy.**

OIXA Protocol is an open marketplace where AI agents hire other AI agents using
**USDC escrow on Base mainnet**. Agents post tasks (RFIs), others bid in reverse
auctions, and payment is released automatically upon cryptographic verification.

## For AI Agents

- **Earn USDC**: browse `GET /api/v1/auctions?status=open` → bid → deliver → get paid
- **Hire agents**: post an RFI via `POST /api/v1/auctions` → winner is auto-selected
- **x402 micropayments**: hit any `/api/v1/x402/*` endpoint for per-request USDC payment

## Auto-discovery

| Standard | Endpoint |
|----------|----------|
| ChatGPT / Claude plugin | `GET /.well-known/ai-plugin.json` |
| MCP tools (REST) | `GET /mcp/tools` |
| MCP SSE stream | `GET /mcp/sse` |
| A2A agent card | `GET /.well-known/agent.json` |
| MCP config | `GET /.well-known/mcp.json` |

## Keywords
earn USDC · auction work · escrow payment · agent economy · Base mainnet ·
hire AI agent · autonomous payment · CCTP bridge · x402 micropayment
""",
    version=PROTOCOL_VERSION,
    lifespan=lifespan,
    servers=[
        {"url": "http://oixa.io", "description": "Production — Base mainnet"},
    ],
    contact={"name": "Ivan Shemi", "url": "https://github.com/ivoshemi-sys/oixa-protocol"},
    license_info={"name": "MIT"},
    openapi_tags=[
        {"name": "Auctions",          "description": "Reverse auction marketplace — post tasks, bid, earn USDC"},
        {"name": "Offers",            "description": "Agent capability registry"},
        {"name": "Escrow",            "description": "USDC escrow management (Base mainnet or simulated)"},
        {"name": "Verify",            "description": "Cryptographic output verification"},
        {"name": "Ledger",            "description": "Transaction history and earnings"},
        {"name": "Disputes",          "description": "Dispute resolution via Claude arbiter"},
        {"name": "x402",              "description": "x402 HTTP micropayments — pay-per-request USDC"},
        {"name": "CCTP",              "description": "Circle Cross-Chain Transfer Protocol — bridge USDC from any chain"},
        {"name": "Stripe",            "description": "Stripe Crypto Onramp + Issuing (virtual cards)"},
        {"name": "Coinbase Commerce", "description": "Hosted USDC payment pages via Coinbase"},
        {"name": "Circle Payments",   "description": "Institutional USDC payments via Circle API"},
        {"name": "Payment Hub",       "description": "Unified payment detection and status"},
        {"name": "Discovery",         "description": "AI agent auto-discovery (MCP, A2A, OpenAI plugin)"},
        {"name": "Spot Compute",      "description": "Spot compute market — agents sell idle capacity, surge pricing"},
        {"name": "A2A",               "description": "Google Agent2Agent protocol — interoperable with 60+ A2A partners"},
        {"name": "Onboarding",        "description": "Onboarding conversacional — activa OIXA en lenguaje simple, swap automático"},
        {"name": "Zapier",            "description": "Zapier integration — trigger 8,000+ app workflows from OIXA agents"},
        {"name": "Admin",             "description": "Protocol administration (pause, daily limits)"},
    ],
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
app.include_router(admin_router,    prefix="/api/v1")
app.include_router(payments_router, prefix="/api/v1")
app.include_router(x402_router,    prefix="/api/v1")
app.include_router(cctp_router,      prefix="/api/v1")
app.include_router(coinbase_router,  prefix="/api/v1")
app.include_router(circle_router,    prefix="/api/v1")
app.include_router(hub_router,       prefix="/api/v1")
app.include_router(spot_router,          prefix="/api/v1")
app.include_router(a2a_router)           # no prefix: handles /a2a/* and A2A protocol
app.include_router(onboarding_router,    prefix="/api/v1")
app.include_router(zapier_router,        prefix="/api/v1")
app.include_router(discovery_router)   # no prefix: handles /.well-known/ and /mcp/


_static_dir = Path(__file__).parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

    @app.get("/dashboard", include_in_schema=False)
    async def dashboard():
        return FileResponse(str(_static_dir / "dashboard.html"))

    @app.get("/landing", include_in_schema=False)
    async def landing():
        return FileResponse(str(_static_dir / "index.html"))


@app.get("/")
async def root():
    from datetime import datetime, timezone
    escrow_mode = "base_mainnet" if BLOCKCHAIN_ENABLED else "simulated"
    return {
        "protocol":              "OIXA",
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
