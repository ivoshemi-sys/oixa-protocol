import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from database import get_db
from models.auction import RFI, BidCreate, DeliverOutput
from core.auction_engine import calculate_auction_duration, process_bid, close_auction, run_auction_timer
from core.verifier import verify_output
from config import PROTOCOL_VERSION

router = APIRouter(tags=["auctions"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _response(data):
    return {"success": True, "data": data, "timestamp": _now(), "protocol_version": PROTOCOL_VERSION}


def _error(msg: str, code: str, status_code: int = 400):
    return JSONResponse(
        status_code=status_code,
        content={"success": False, "error": msg, "code": code, "timestamp": _now()},
    )


async def _row_to_auction(row, db) -> dict:
    auction = dict(row)
    async with db.execute(
        "SELECT * FROM bids WHERE auction_id = ?", (auction["id"],)
    ) as cursor:
        bids = await cursor.fetchall()
    auction["bids"] = [dict(b) for b in bids]
    return auction


@router.post("/auctions")
async def create_auction(rfi: RFI):
    db = await get_db()
    auction_id = f"oixa_auction_{uuid.uuid4().hex[:12]}"
    now = _now()
    duration = calculate_auction_duration(rfi.max_budget)

    await db.execute(
        """INSERT INTO auctions (id, rfi_description, max_budget, currency, requester_id, status, auction_duration_seconds, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (auction_id, rfi.rfi_description, rfi.max_budget, rfi.currency, rfi.requester_id, "open", duration, now),
    )
    await db.commit()

    asyncio.create_task(run_auction_timer(auction_id, duration))

    return _response(
        {
            "id": auction_id,
            "rfi_description": rfi.rfi_description,
            "max_budget": rfi.max_budget,
            "currency": rfi.currency,
            "requester_id": rfi.requester_id,
            "status": "open",
            "auction_duration_seconds": duration,
            "created_at": now,
            "bids": [],
        }
    )


@router.get("/auctions/active")
async def list_active_auctions():
    db = await get_db()
    async with db.execute("SELECT * FROM auctions WHERE status = 'open'") as cursor:
        rows = await cursor.fetchall()
    auctions = [await _row_to_auction(row, db) for row in rows]
    return _response(auctions)


@router.get("/auctions")
async def list_auctions(status: str | None = None):
    db = await get_db()
    if status:
        async with db.execute("SELECT * FROM auctions WHERE status = ?", (status,)) as cursor:
            rows = await cursor.fetchall()
    else:
        async with db.execute("SELECT * FROM auctions") as cursor:
            rows = await cursor.fetchall()
    auctions = [await _row_to_auction(row, db) for row in rows]
    return _response(auctions)


@router.get("/auctions/{auction_id}")
async def get_auction(auction_id: str):
    db = await get_db()
    async with db.execute("SELECT * FROM auctions WHERE id = ?", (auction_id,)) as cursor:
        row = await cursor.fetchone()
    if not row:
        return _error("Auction not found", "AUCTION_NOT_FOUND", 404)
    return _response(await _row_to_auction(row, db))


@router.post("/auctions/{auction_id}/bid")
async def place_bid(auction_id: str, bid: BidCreate):
    result = await process_bid(auction_id, bid.bidder_id, bid.bidder_name, bid.amount)
    if not result["accepted"]:
        return _error(result["reason"], "BID_REJECTED")
    return _response(result)


@router.post("/auctions/{auction_id}/deliver")
async def deliver_output(auction_id: str, delivery: DeliverOutput):
    result = await verify_output(auction_id, delivery.output, delivery.agent_id)
    if not result["passed"]:
        return _error(
            result["details"].get("fail_reason", "Verification failed"),
            "VERIFICATION_FAILED",
        )
    return _response(result)
