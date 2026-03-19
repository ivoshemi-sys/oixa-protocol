"""
GET /api/v1/status — Public protocol metrics endpoint.
"""

import platform
import time
from datetime import datetime, timezone

from fastapi import APIRouter
from database import get_db, USE_POSTGRES
from core.rate_limiter import rate_limiter
from core.openclaw import openclaw_client
from config import PROTOCOL_VERSION, SIMULATED_YIELD_APY

router = APIRouter(tags=["status"])

_started_at = time.time()


@router.get("/status")
async def get_status():
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()

    # Transaction stats
    async with db.execute("SELECT COUNT(*) as total FROM ledger") as cur:
        row = await cur.fetchone()
    total_tx = row["total"] if row else 0

    async with db.execute(
        "SELECT COUNT(*) as total FROM auctions WHERE status IN ('closed','completed')"
    ) as cur:
        row = await cur.fetchone()
    completed_auctions = row["total"] if row else 0

    async with db.execute(
        "SELECT COUNT(*) as total FROM auctions WHERE status = 'open'"
    ) as cur:
        row = await cur.fetchone()
    active_auctions = row["total"] if row else 0

    async with db.execute(
        "SELECT COUNT(*) as total FROM offers WHERE status = 'active'"
    ) as cur:
        row = await cur.fetchone()
    active_offers = row["total"] if row else 0

    async with db.execute(
        "SELECT COUNT(DISTINCT bidder_id) as total FROM bids"
    ) as cur:
        row = await cur.fetchone()
    unique_agents = row["total"] if row else 0

    async with db.execute(
        "SELECT SUM(amount) as total FROM ledger WHERE transaction_type = 'payment'"
    ) as cur:
        row = await cur.fetchone()
    total_volume = row["total"] or 0.0 if row else 0.0

    async with db.execute(
        "SELECT SUM(amount) as total FROM protocol_revenue WHERE source = 'commission'"
    ) as cur:
        row = await cur.fetchone()
    total_commissions = row["total"] or 0.0 if row else 0.0

    async with db.execute(
        "SELECT AVG(winning_bid) as avg FROM auctions WHERE winning_bid IS NOT NULL"
    ) as cur:
        row = await cur.fetchone()
    avg_winning_bid = row["avg"] if row else None

    uptime_seconds = int(time.time() - _started_at)

    return {
        "protocol": "VELUN",
        "version": PROTOCOL_VERSION,
        "phase": 1,
        "status": "operational",
        "timestamp": now,
        "uptime_seconds": uptime_seconds,
        "infrastructure": {
            "db_backend": "postgresql" if USE_POSTGRES else "sqlite",
            "openclaw_connected": openclaw_client.connected,
            "rate_limiter": rate_limiter.get_stats(),
        },
        "metrics": {
            "total_transactions": total_tx,
            "completed_auctions": completed_auctions,
            "active_auctions": active_auctions,
            "active_offers": active_offers,
            "unique_agents": unique_agents,
            "total_volume_usdc": round(total_volume, 6),
            "total_commissions_simulated": round(total_commissions, 6),
            "avg_winning_bid_usdc": round(avg_winning_bid, 6) if avg_winning_bid else None,
            "simulated_yield_apy": SIMULATED_YIELD_APY,
        },
        "escrow_mode": "simulated",
    }
