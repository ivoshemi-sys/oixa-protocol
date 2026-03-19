from datetime import datetime, timezone

from fastapi import APIRouter
from database import get_db
from config import PROTOCOL_VERSION

router = APIRouter(tags=["aipi"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _response(data):
    return {"success": True, "data": data, "timestamp": _now(), "protocol_version": PROTOCOL_VERSION}


@router.get("/aipi")
async def get_aipi():
    db = await get_db()

    async with db.execute("SELECT COUNT(*) as total FROM ledger") as cursor:
        total_tx = (await cursor.fetchone())["total"]

    async with db.execute(
        "SELECT AVG(winning_bid) as avg_price, MIN(winning_bid) as min_price, MAX(winning_bid) as max_price FROM auctions WHERE status IN ('closed', 'completed') AND winning_bid IS NOT NULL"
    ) as cursor:
        price_row = await cursor.fetchone()

    async with db.execute(
        "SELECT COUNT(*) as total FROM auctions WHERE status IN ('closed', 'completed', 'cancelled')"
    ) as cursor:
        completed_auctions = (await cursor.fetchone())["total"]

    return _response(
        {
            "index_name": "OIXA Intelligence Price Index",
            "version": "0.1.0",
            "total_transactions": total_tx,
            "completed_auctions": completed_auctions,
            "price_stats": {
                "avg_winning_bid": price_row["avg_price"],
                "min_winning_bid": price_row["min_price"],
                "max_winning_bid": price_row["max_price"],
            },
            "note": "Phase 1 — real data, full access. Subscription restrictions activate in Phase 3.",
        }
    )


@router.get("/aipi/full")
async def get_aipi_full():
    db = await get_db()

    async with db.execute(
        "SELECT * FROM auctions WHERE status IN ('closed', 'completed') ORDER BY created_at DESC LIMIT 100"
    ) as cursor:
        rows = await cursor.fetchall()

    auctions = [dict(r) for r in rows]

    async with db.execute(
        "SELECT transaction_type, SUM(amount) as total, COUNT(*) as count FROM ledger GROUP BY transaction_type"
    ) as cursor:
        tx_breakdown = await cursor.fetchall()

    return _response(
        {
            "recent_auctions": auctions,
            "transaction_breakdown": [dict(r) for r in tx_breakdown],
        }
    )


@router.get("/aipi/history")
async def get_aipi_history():
    db = await get_db()
    async with db.execute(
        """SELECT DATE(created_at) as day, AVG(winning_bid) as avg_bid, COUNT(*) as count
           FROM auctions WHERE winning_bid IS NOT NULL
           GROUP BY DATE(created_at) ORDER BY day DESC LIMIT 30"""
    ) as cursor:
        rows = await cursor.fetchall()
    return _response({"price_history_30d": [dict(r) for r in rows]})
