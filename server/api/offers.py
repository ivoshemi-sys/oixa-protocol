import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from database import get_db
from models.offer import Offer, OfferCreate, OfferUpdate
from config import PROTOCOL_VERSION

router = APIRouter(tags=["offers"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _response(data):
    return {"success": True, "data": data, "timestamp": _now(), "protocol_version": PROTOCOL_VERSION}


def _error(msg: str, code: str, status_code: int = 400):
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=status_code,
        content={"success": False, "error": msg, "code": code, "timestamp": _now()},
    )


@router.post("/offers")
async def create_offer(offer: OfferCreate):
    db = await get_db()
    offer_id = f"velun_offer_{uuid.uuid4().hex[:12]}"
    now = _now()
    capabilities_json = json.dumps(offer.capabilities)
    await db.execute(
        """INSERT INTO offers (id, agent_id, agent_name, capabilities, price_per_unit, currency, status, wallet_address, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (offer_id, offer.agent_id, offer.agent_name, capabilities_json, offer.price_per_unit, offer.currency, "active", offer.wallet_address, now, now),
    )
    await db.commit()
    return _response(
        Offer(
            id=offer_id,
            agent_id=offer.agent_id,
            agent_name=offer.agent_name,
            capabilities=offer.capabilities,
            price_per_unit=offer.price_per_unit,
            currency=offer.currency,
            wallet_address=offer.wallet_address,
            status="active",
            created_at=now,
            updated_at=now,
        ).model_dump()
    )


@router.get("/offers")
async def list_offers():
    db = await get_db()
    async with db.execute("SELECT * FROM offers WHERE status = 'active'") as cursor:
        rows = await cursor.fetchall()
    offers = [
        {**dict(row), "capabilities": json.loads(row["capabilities"])}
        for row in rows
    ]
    return _response(offers)


@router.get("/offers/agent/{agent_id}")
async def get_agent_offers(agent_id: str):
    db = await get_db()
    async with db.execute(
        "SELECT * FROM offers WHERE agent_id = ?", (agent_id,)
    ) as cursor:
        rows = await cursor.fetchall()
    offers = [
        {**dict(row), "capabilities": json.loads(row["capabilities"])}
        for row in rows
    ]
    return _response(offers)


@router.get("/offers/{offer_id}")
async def get_offer(offer_id: str):
    db = await get_db()
    async with db.execute("SELECT * FROM offers WHERE id = ?", (offer_id,)) as cursor:
        row = await cursor.fetchone()
    if not row:
        return _error("Offer not found", "OFFER_NOT_FOUND", 404)
    data = {**dict(row), "capabilities": json.loads(row["capabilities"])}
    return _response(data)


@router.put("/offers/{offer_id}")
async def update_offer(offer_id: str, update: OfferUpdate):
    db = await get_db()
    async with db.execute("SELECT * FROM offers WHERE id = ?", (offer_id,)) as cursor:
        row = await cursor.fetchone()
    if not row:
        return _error("Offer not found", "OFFER_NOT_FOUND", 404)

    now = _now()
    fields = []
    values = []
    if update.agent_name is not None:
        fields.append("agent_name = ?")
        values.append(update.agent_name)
    if update.capabilities is not None:
        fields.append("capabilities = ?")
        values.append(json.dumps(update.capabilities))
    if update.price_per_unit is not None:
        fields.append("price_per_unit = ?")
        values.append(update.price_per_unit)
    if update.currency is not None:
        fields.append("currency = ?")
        values.append(update.currency)
    if update.status is not None:
        fields.append("status = ?")
        values.append(update.status)
    fields.append("updated_at = ?")
    values.append(now)
    values.append(offer_id)

    await db.execute(f"UPDATE offers SET {', '.join(fields)} WHERE id = ?", values)
    await db.commit()

    async with db.execute("SELECT * FROM offers WHERE id = ?", (offer_id,)) as cursor:
        updated = await cursor.fetchone()
    data = {**dict(updated), "capabilities": json.loads(updated["capabilities"])}
    return _response(data)


@router.delete("/offers/{offer_id}")
async def retire_offer(offer_id: str):
    db = await get_db()
    async with db.execute("SELECT * FROM offers WHERE id = ?", (offer_id,)) as cursor:
        row = await cursor.fetchone()
    if not row:
        return _error("Offer not found", "OFFER_NOT_FOUND", 404)
    now = _now()
    await db.execute(
        "UPDATE offers SET status = 'retired', updated_at = ? WHERE id = ?", (now, offer_id)
    )
    await db.commit()
    return _response({"id": offer_id, "status": "retired"})
