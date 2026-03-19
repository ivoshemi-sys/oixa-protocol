"""
Coinbase Commerce endpoints for OIXA Protocol.

Hosted USDC payment pages — accept USDC from any network via Coinbase.
Coinbase handles chain detection automatically (Ethereum, Base, Polygon,
Arbitrum, Avalanche, Solana, and more).

Endpoints:
  POST /payments/coinbase/charge          → create hosted payment charge
  GET  /payments/coinbase/charge/{code}   → check charge status
  GET  /payments/coinbase/charges         → list all charges
  POST /payments/coinbase/webhook         → Coinbase webhook receiver
  GET  /payments/coinbase/status          → integration status
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from config import COINBASE_COMMERCE_API_KEY, PROTOCOL_VERSION
from core.coinbase_client import (
    ENABLED,
    create_charge,
    get_charge,
    is_payment_confirmed,
    is_payment_failed,
    is_payment_pending,
    list_charges,
    verify_webhook,
    extract_payment_network,
)

router = APIRouter(prefix="/payments/coinbase", tags=["Coinbase Commerce"])

_TS = lambda: datetime.now(timezone.utc).isoformat()


def _ok(data):
    return {"success": True, "data": data, "timestamp": _TS(), "protocol_version": PROTOCOL_VERSION}


# ── Models ────────────────────────────────────────────────────────────────────

class CreateChargeRequest(BaseModel):
    amount_usdc:  float
    name:         str = "OIXA Protocol Payment"
    description:  str = "USDC payment for OIXA agent services"
    auction_id:   Optional[str] = None
    agent_id:     Optional[str] = None
    metadata:     Optional[dict] = None


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/status")
async def coinbase_status():
    """Coinbase Commerce integration status."""
    from database import get_db

    db = await get_db()
    async with db.execute("SELECT COUNT(*) as total FROM coinbase_charges") as cur:
        row = await cur.fetchone()
    total = row["total"] if row else 0

    async with db.execute(
        "SELECT COUNT(*) as total FROM coinbase_charges WHERE status='completed'"
    ) as cur:
        row = await cur.fetchone()
    completed = row["total"] if row else 0

    return _ok({
        "enabled":        ENABLED,
        "note":           "Set COINBASE_COMMERCE_API_KEY in .env to enable" if not ENABLED else None,
        "supported_networks": [
            "ethereum", "base", "polygon", "arbitrum", "avalanche", "solana"
        ],
        "charges":        {"total": total, "completed": completed},
        "webhook_url":    "/api/v1/payments/coinbase/webhook",
        "docs":           "https://docs.cdp.coinbase.com/commerce/docs/welcome",
    })


@router.post("/charge")
async def create_commerce_charge(req: CreateChargeRequest):
    """
    Create a Coinbase Commerce charge.

    Returns a hosted_url where the payer completes the USDC payment.
    Coinbase accepts USDC on Ethereum, Base, Polygon, Arbitrum, Avalanche, Solana.
    """
    if not ENABLED:
        raise HTTPException(503, detail={
            "error":   "Coinbase Commerce not configured",
            "action":  "Set COINBASE_COMMERCE_API_KEY in .env",
            "docs":    "https://docs.cdp.coinbase.com/commerce/docs/welcome",
        })

    metadata = req.metadata or {}
    if req.auction_id:
        metadata["auction_id"] = req.auction_id
    if req.agent_id:
        metadata["agent_id"] = req.agent_id

    try:
        charge = await create_charge(
            amount_usdc=req.amount_usdc,
            name=req.name,
            description=req.description,
            metadata=metadata or None,
        )
    except Exception as e:
        raise HTTPException(502, detail=f"Coinbase Commerce error: {e}")

    # Persist to DB
    from database import get_db

    db  = await get_db()
    lid = f"oixa_cb_{uuid.uuid4().hex[:12]}"
    now = _TS()

    await db.execute(
        """INSERT INTO coinbase_charges
           (id, coinbase_charge_id, charge_code, hosted_url,
            amount_usdc, description, status, auction_id, agent_id, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            lid,
            charge.get("id"),
            charge.get("code"),
            charge.get("hosted_url"),
            req.amount_usdc,
            req.description,
            "pending",
            req.auction_id,
            req.agent_id,
            now,
        ),
    )
    await db.commit()

    return _ok({
        "oixa_charge_id":  lid,
        "coinbase_charge_id": charge.get("id"),
        "charge_code":     charge.get("code"),
        "hosted_url":      charge.get("hosted_url"),
        "expires_at":      charge.get("expires_at"),
        "amount_usdc":     req.amount_usdc,
        "status":          "pending",
        "accepted_networks": [
            "ethereum", "base", "polygon", "arbitrum", "avalanche", "solana"
        ],
        "created_at": now,
        "note": "Direct the payer to hosted_url to complete payment",
    })


@router.get("/charge/{charge_code}")
async def get_commerce_charge(charge_code: str):
    """Check the status of a Coinbase Commerce charge."""
    from database import get_db

    db = await get_db()

    # Check DB first
    async with db.execute(
        "SELECT * FROM coinbase_charges WHERE id=? OR charge_code=? OR coinbase_charge_id=?",
        (charge_code, charge_code, charge_code),
    ) as cur:
        local = await cur.fetchone()

    # Fetch live status from Coinbase if enabled
    live_charge = None
    if ENABLED and local:
        try:
            live_charge = await get_charge(local["charge_code"])
            live_status = live_charge.get("timeline", [{}])[-1].get("status", "").lower()
            if live_status and live_status != local["status"]:
                now = _TS()
                completed_at = now if is_payment_confirmed(f"charge:{live_status}") else None
                await db.execute(
                    "UPDATE coinbase_charges SET status=?, completed_at=? WHERE id=?",
                    (live_status, completed_at, local["id"]),
                )
                await db.commit()
        except Exception as e:
            pass  # Use local status as fallback

    if not local:
        raise HTTPException(404, detail="Charge not found")

    return _ok({
        "local":       dict(local),
        "live_status": live_charge.get("timeline", [{}])[-1].get("status") if live_charge else None,
        "payment_network": extract_payment_network(live_charge or {}) if live_charge else None,
    })


@router.get("/charges")
async def list_commerce_charges(status: Optional[str] = None, limit: int = 25):
    """List all Coinbase Commerce charges."""
    from database import get_db

    db = await get_db()
    if status:
        async with db.execute(
            "SELECT * FROM coinbase_charges WHERE status=? ORDER BY created_at DESC LIMIT ?",
            (status, limit),
        ) as cur:
            rows = await cur.fetchall() or []
    else:
        async with db.execute(
            "SELECT * FROM coinbase_charges ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall() or []

    return _ok({"charges": [dict(r) for r in rows], "total": len(rows)})


@router.post("/webhook")
async def coinbase_webhook(
    request: Request,
    x_cc_webhook_signature: Optional[str] = Header(None, alias="X-CC-WEBHOOK-SIGNATURE"),
):
    """
    Receive Coinbase Commerce webhooks.

    Configure at: https://beta.commerce.coinbase.com/settings/notifications
    Webhook URL: https://<your-domain>/api/v1/payments/coinbase/webhook

    Events handled:
      charge:confirmed → mark charge completed, credit to auction/agent
      charge:failed    → mark charge failed
      charge:pending   → update status
    """
    payload = await request.body()

    if x_cc_webhook_signature:
        if not verify_webhook(payload, x_cc_webhook_signature):
            raise HTTPException(400, detail="Invalid webhook signature")

    import json

    try:
        body = json.loads(payload)
    except Exception:
        raise HTTPException(400, detail="Invalid JSON payload")

    event_type   = body.get("event", {}).get("type", "")
    charge_data  = body.get("event", {}).get("data", {})
    charge_code  = charge_data.get("code")
    charge_id    = charge_data.get("id")

    from database import get_db

    db = await get_db()
    now = _TS()

    if is_payment_confirmed(event_type):
        network  = extract_payment_network(charge_data) or "unknown"
        completed_at = now
        await db.execute(
            "UPDATE coinbase_charges SET status='completed', completed_at=? WHERE charge_code=? OR coinbase_charge_id=?",
            (completed_at, charge_code, charge_id),
        )
        await db.commit()

        from core.telegram_notifier import _send
        await _send(
            f"💰 *Coinbase Commerce Payment Confirmed*\n"
            f"Charge: `{charge_code}`\n"
            f"Network: {network}\n"
            f"Event: `{event_type}`"
        )

    elif is_payment_pending(event_type):
        await db.execute(
            "UPDATE coinbase_charges SET status='pending' WHERE charge_code=? OR coinbase_charge_id=?",
            (charge_code, charge_id),
        )
        await db.commit()

    elif is_payment_failed(event_type):
        await db.execute(
            "UPDATE coinbase_charges SET status='failed' WHERE charge_code=? OR coinbase_charge_id=?",
            (charge_code, charge_id),
        )
        await db.commit()

    return {"success": True, "processed": event_type}
