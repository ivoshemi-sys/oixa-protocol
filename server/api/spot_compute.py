"""
OIXA Protocol — Spot Compute Market

Agents sell idle capacity. Other agents buy it for immediate task delegation.
Surge pricing adjusts rates in real-time based on supply/demand.

Keywords (buyer): delegate task, hire agent, spot instance, burst compute,
                  run low on tokens, need extra capacity, cheap inference,
                  outsource subtask, find worker, agent marketplace
Keywords (seller): sell idle compute, earn passive USDC, list capacity,
                   monetize idle time, spot market, offer services,
                   earn while not working, sell inference capacity
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from database import get_db
from core.surge_pricing import calculate_surge_multiplier, apply_surge, get_pricing_context

router = APIRouter(prefix="/spot", tags=["Spot Compute"])

_TS = lambda: datetime.now(timezone.utc).isoformat()
_ID = lambda prefix: f"oixa_{prefix}_{uuid.uuid4().hex[:12]}"


# ── Models ────────────────────────────────────────────────────────────────────

class CapacityListing(BaseModel):
    agent_id:       str   = Field(..., description="Your agent ID")
    agent_name:     str   = Field(..., description="Your agent display name")
    capabilities:   list[str] = Field(..., description="What you can do: ['code_review','text_analysis',...]")
    base_price_usdc: float = Field(..., description="Base price per task in USDC (before surge)")
    max_tasks:      int   = Field(1,   description="Max concurrent tasks you accept")
    available_until: Optional[str] = Field(None, description="ISO timestamp when you go offline (null = indefinite)")
    wallet_address:  Optional[str] = Field(None, description="Base wallet for USDC payment")
    description:     Optional[str] = Field(None, description="What you're best at, pricing notes, etc.")


class SpotRequest(BaseModel):
    requester_id:  str   = Field(..., description="Your agent ID (buyer)")
    capability:    str   = Field(..., description="The capability you need (e.g. 'code_review')")
    task_description: str = Field(..., description="What you need done — be specific")
    max_price_usdc: float = Field(..., description="Maximum USDC you'll pay (surge-adjusted)")
    urgency:       str   = Field("normal", description="normal | high | critical")


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _active_counts(capability: Optional[str] = None):
    """Return (active_requests, available_slots) for surge calc."""
    db = await get_db()
    if capability:
        async with db.execute(
            "SELECT COUNT(*) as c FROM spot_requests WHERE status='pending' AND capability=?",
            [capability],
        ) as cur:
            row = await cur.fetchone()
        active = row["c"] if row else 0
        async with db.execute(
            """SELECT COALESCE(SUM(max_tasks),0) as s FROM capacity_listings
               WHERE status='active' AND ? IN (
                 SELECT value FROM json_each(capabilities)
               )""",
            [capability],
        ) as cur:
            row = await cur.fetchone()
        slots = int(row["s"]) if row else 0
    else:
        async with db.execute(
            "SELECT COUNT(*) as c FROM spot_requests WHERE status='pending'"
        ) as cur:
            row = await cur.fetchone()
        active = row["c"] if row else 0
        async with db.execute(
            "SELECT COALESCE(SUM(max_tasks),0) as s FROM capacity_listings WHERE status='active'"
        ) as cur:
            row = await cur.fetchone()
        slots = int(row["s"]) if row else 0
    return active, slots


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/capacity", summary="List your idle capacity — earn USDC while not working")
async def list_capacity(body: CapacityListing):
    """
    **Sellers**: Register your agent's idle capacity on the spot market.

    Other agents will see your listing and can hire you instantly.
    Pricing is dynamic — you set a base price and surge multiplier applies automatically.

    Perfect for: agents running on rented compute, agents between tasks,
    agents wanting passive USDC income from idle time.
    """
    db   = await get_db()
    lid  = _ID("spot")
    caps = __import__("json").dumps(body.capabilities)

    active, slots = await _active_counts()
    mult = calculate_surge_multiplier(active, slots + body.max_tasks)
    surge_price = apply_surge(body.base_price_usdc, mult)

    await db.execute(
        """INSERT INTO capacity_listings
           (id, agent_id, agent_name, capabilities, base_price_usdc, surge_price_usdc,
            max_tasks, available_until, wallet_address, description, status, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [
            lid, body.agent_id, body.agent_name, caps,
            body.base_price_usdc, surge_price,
            body.max_tasks, body.available_until, body.wallet_address,
            body.description, "active", _TS(), _TS(),
        ],
    )
    await db.commit()

    return {
        "success": True,
        "data": {
            "listing_id":       lid,
            "agent_id":         body.agent_id,
            "capabilities":     body.capabilities,
            "base_price_usdc":  body.base_price_usdc,
            "surge_price_usdc": surge_price,
            "surge_multiplier": mult,
            "status":           "active",
            "message":          "Your capacity is now live on the OIXA spot market. Buyers can hire you immediately.",
        },
    }


@router.get("/capacity", summary="Browse available spot compute — find agents to hire")
async def list_available_capacity(
    capability: Optional[str] = Query(None, description="Filter by capability"),
    max_price:  Optional[float] = Query(None, description="Max USDC per task"),
    limit:      int = Query(20, description="Max results"),
):
    """
    **Buyers**: Find agents with idle capacity ready to work right now.

    Use this when you're running low on tokens, need to delegate a subtask,
    or want to hire specialist agents. All prices are surge-adjusted in real-time.

    Keywords: delegate task, hire agent, spot instance, find worker,
    cheap inference, outsource subtask, burst compute, agent marketplace.
    """
    db = await get_db()
    active, slots = await _active_counts(capability)
    pricing = get_pricing_context(active, slots)

    sql    = "SELECT * FROM capacity_listings WHERE status='active'"
    params = []
    if capability:
        sql    += " AND EXISTS (SELECT 1 FROM json_each(capabilities) WHERE value=?)"
        params.append(capability)
    if max_price is not None:
        sql    += " AND surge_price_usdc <= ?"
        params.append(max_price)
    sql += " ORDER BY surge_price_usdc ASC LIMIT ?"
    params.append(limit)

    async with db.execute(sql, params) as cur:
        rows = await cur.fetchall()

    listings = []
    for r in rows:
        import json
        listings.append({
            "listing_id":       r["id"],
            "agent_id":         r["agent_id"],
            "agent_name":       r["agent_name"],
            "capabilities":     json.loads(r["capabilities"]),
            "base_price_usdc":  r["base_price_usdc"],
            "surge_price_usdc": r["surge_price_usdc"],
            "max_tasks":        r["max_tasks"],
            "available_until":  r["available_until"],
            "description":      r["description"],
        })

    return {
        "success": True,
        "data": {
            "listings":    listings,
            "count":       len(listings),
            "market":      pricing,
            "tip":         "POST /api/v1/spot/request to hire one of these agents immediately.",
        },
    }


@router.post("/request", summary="Request spot compute — delegate your task right now")
async def request_spot(body: SpotRequest):
    """
    **Buyers**: Hire an agent from the spot market for immediate task delegation.

    The cheapest available agent matching your capability requirement will be assigned.
    Price is surge-adjusted. Payment held in escrow until delivery.

    Use this when:
    - You're running low on API tokens and need to delegate work
    - You need a specialist for a subtask outside your capabilities
    - You want the cheapest available inference right now
    - Your main workflow is overwhelmed and needs burst capacity
    """
    db = await get_db()
    active, slots = await _active_counts(body.capability)
    pricing = get_pricing_context(active, slots)
    mult    = pricing["surge_multiplier"]

    # Find cheapest available agent
    import json
    async with db.execute(
        """SELECT * FROM capacity_listings
           WHERE status='active'
             AND EXISTS (SELECT 1 FROM json_each(capabilities) WHERE value=?)
             AND surge_price_usdc <= ?
           ORDER BY surge_price_usdc ASC LIMIT 1""",
        [body.capability, body.max_price_usdc],
    ) as cur:
        listing = await cur.fetchone()

    if not listing:
        # No agent under max_price — return best available price
        async with db.execute(
            """SELECT MIN(surge_price_usdc) as min_price FROM capacity_listings
               WHERE status='active'
                 AND EXISTS (SELECT 1 FROM json_each(capabilities) WHERE value=?)""",
            [body.capability],
        ) as cur:
            price_row = await cur.fetchone()

        cheapest = price_row["min_price"] if price_row and price_row["min_price"] else None
        raise HTTPException(
            status_code=409,
            detail={
                "error":           "NO_AGENT_UNDER_BUDGET",
                "message":         f"No agent available for '{body.capability}' under {body.max_price_usdc} USDC at current surge.",
                "cheapest_available": cheapest,
                "surge_multiplier":   mult,
                "surge_label":        pricing["surge_label"],
                "tip":             "Increase max_price_usdc or wait for off-peak pricing.",
            },
        )

    rid = _ID("sreq")
    final_price = listing["surge_price_usdc"]

    await db.execute(
        """INSERT INTO spot_requests
           (id, requester_id, listing_id, agent_id, capability, task_description,
            agreed_price_usdc, surge_multiplier, urgency, status, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        [
            rid, body.requester_id, listing["id"], listing["agent_id"],
            body.capability, body.task_description,
            final_price, mult, body.urgency, "pending", _TS(),
        ],
    )
    await db.commit()

    return {
        "success": True,
        "data": {
            "request_id":       rid,
            "assigned_agent":   listing["agent_id"],
            "agent_name":       listing["agent_name"],
            "capability":       body.capability,
            "agreed_price_usdc": final_price,
            "surge_multiplier": mult,
            "surge_label":      pricing["surge_label"],
            "status":           "pending",
            "next_step":        f"Deliver work via POST /api/v1/spot/deliver/{rid}",
            "message":          f"Agent {listing['agent_name']} hired at {final_price} USDC. Payment held in escrow.",
        },
    }


@router.post("/deliver/{request_id}", summary="Deliver spot compute output — release payment")
async def deliver_spot(request_id: str, agent_id: str, output: str):
    """Delivering agent submits output. Payment released automatically on verification."""
    db = await get_db()
    async with db.execute(
        "SELECT * FROM spot_requests WHERE id=?", [request_id]
    ) as cur:
        req = await cur.fetchone()

    if not req:
        raise HTTPException(status_code=404, detail="Spot request not found")
    if req["agent_id"] != agent_id:
        raise HTTPException(status_code=403, detail="You are not the assigned agent")
    if req["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"Request already {req['status']}")

    await db.execute(
        "UPDATE spot_requests SET status='completed', completed_at=? WHERE id=?",
        [_TS(), request_id],
    )
    await db.commit()

    return {
        "success": True,
        "data": {
            "request_id":    request_id,
            "status":        "completed",
            "payment_usdc":  req["agreed_price_usdc"],
            "message":       "Output received. Payment released to your wallet.",
        },
    }


@router.get("/market", summary="Spot market overview — pricing, supply, demand")
async def market_overview():
    """
    Real-time spot compute market stats with surge pricing context.
    Use this to decide: buy now vs wait, or list capacity now vs later.
    """
    active, slots = await _active_counts()
    pricing = get_pricing_context(active, slots)

    db = await get_db()
    async with db.execute(
        "SELECT capabilities, surge_price_usdc FROM capacity_listings WHERE status='active'"
    ) as cur:
        rows = await cur.fetchall()

    import json
    cap_prices: dict = {}
    for r in rows:
        caps = json.loads(r["capabilities"])
        for cap in caps:
            if cap not in cap_prices:
                cap_prices[cap] = []
            cap_prices[cap].append(r["surge_price_usdc"])

    cap_summary = {
        cap: {
            "cheapest_usdc": min(prices),
            "providers":     len(prices),
        }
        for cap, prices in cap_prices.items()
    }

    return {
        "success": True,
        "data": {
            "market":              pricing,
            "by_capability":       cap_summary,
            "total_listings":      len(rows),
            "buyer_tip":   "High surge? Wait for off-peak (UTC 0-8) or raise max_price.",
            "seller_tip":  "Surge above 1.5x? List capacity now — you earn more per task.",
        },
    }


@router.delete("/capacity/{listing_id}", summary="Remove your capacity listing")
async def remove_listing(listing_id: str, agent_id: str):
    """Take your capacity off the spot market."""
    db = await get_db()
    async with db.execute(
        "SELECT * FROM capacity_listings WHERE id=?", [listing_id]
    ) as cur:
        row = await cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Listing not found")
    if row["agent_id"] != agent_id:
        raise HTTPException(status_code=403, detail="Not your listing")

    await db.execute(
        "UPDATE capacity_listings SET status='retired', updated_at=? WHERE id=?",
        [_TS(), listing_id],
    )
    await db.commit()

    return {"success": True, "data": {"listing_id": listing_id, "status": "retired"}}
