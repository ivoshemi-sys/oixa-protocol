"""
Circle Payments Network endpoints for OIXA Protocol.

Institutional USDC payments via Circle's API:
  - Payment intents (request USDC via blockchain or wire)
  - Payout transfers (send USDC to any chain from Circle account)
  - Account balance / configuration

Endpoints:
  GET  /payments/circle/status              → integration status
  POST /payments/circle/intent              → create payment intent
  GET  /payments/circle/intent/{id}         → check intent status
  GET  /payments/circle/intents             → list all intents
  POST /payments/circle/transfer            → outbound USDC transfer
  GET  /payments/circle/payments            → list incoming payments
  GET  /payments/circle/balance             → Circle account balance
  POST /payments/circle/webhook             → Circle event handler
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from config import CIRCLE_API_KEY, PROTOCOL_VERSION
from core.circle_client import (
    ENABLED,
    create_payment_intent,
    create_transfer,
    get_balance,
    get_payment,
    get_payment_intent,
    is_payment_complete,
    is_payment_failed,
    list_payment_intents,
    list_payments,
)

router = APIRouter(prefix="/payments/circle", tags=["Circle Payments"])

_TS = lambda: datetime.now(timezone.utc).isoformat()


def _ok(data):
    return {"success": True, "data": data, "timestamp": _TS(), "protocol_version": PROTOCOL_VERSION}


# ── Models ────────────────────────────────────────────────────────────────────

class CreateIntentRequest(BaseModel):
    amount_usdc:          float
    description:          str = "OIXA Protocol payment"
    auction_id:           Optional[str] = None
    agent_id:             Optional[str] = None
    settlement_currency:  str = "USD"


class CreateTransferRequest(BaseModel):
    destination_address: str
    destination_chain:   str  # "BASE", "ETH", "ARB", "AVAX", "MATIC"
    amount_usdc:         float
    auction_id:          Optional[str] = None


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/status")
async def circle_status():
    """Circle Payments Network integration status."""
    from database import get_db

    db = await get_db()
    async with db.execute("SELECT COUNT(*) as total FROM circle_payments") as cur:
        row = await cur.fetchone()
    total = row["total"] if row else 0

    balance_data = None
    if ENABLED:
        try:
            balance_data = await get_balance()
        except Exception:
            pass

    return _ok({
        "enabled":  ENABLED,
        "note":     "Set CIRCLE_API_KEY in .env to enable" if not ENABLED else None,
        "balance":  balance_data,
        "payments": {"total": total},
        "supported_chains": ["BASE", "ETH", "ARB", "AVAX", "MATIC"],
        "docs":     "https://developers.circle.com/circle-mint/docs",
    })


@router.post("/intent")
async def create_circle_intent(req: CreateIntentRequest):
    """
    Create a Circle payment intent.

    Returns a payment intent that a payer can fulfill by sending USDC from
    their Circle wallet or directly on-chain (Base, Ethereum, Arbitrum, etc).
    """
    if not ENABLED:
        raise HTTPException(503, detail={
            "error":  "Circle API not configured",
            "action": "Set CIRCLE_API_KEY in .env",
            "docs":   "https://developers.circle.com/circle-mint/docs",
        })

    idempotency_key = f"oixa-{uuid.uuid4()}"

    try:
        intent = await create_payment_intent(
            amount_usdc=req.amount_usdc,
            description=req.description,
            idempotency_key=idempotency_key,
            settlement_currency=req.settlement_currency,
        )
    except Exception as e:
        raise HTTPException(502, detail=f"Circle API error: {e}")

    # Persist
    from database import get_db

    db  = await get_db()
    lid = f"oixa_circle_{uuid.uuid4().hex[:12]}"
    now = _TS()

    await db.execute(
        """INSERT INTO circle_payments
           (id, circle_intent_id, amount_usdc, description, status,
            auction_id, agent_id, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            lid,
            intent.get("id"),
            req.amount_usdc,
            req.description,
            intent.get("status", "pending"),
            req.auction_id,
            req.agent_id,
            now,
        ),
    )
    await db.commit()

    return _ok({
        "oixa_payment_id":  lid,
        "circle_intent_id": intent.get("id"),
        "amount_usdc":      req.amount_usdc,
        "status":           intent.get("status", "pending"),
        "payment_methods":  intent.get("paymentMethods", []),
        "created_at":       now,
        "note": "Share circle_intent_id with the payer to fulfill the payment",
    })


@router.get("/intent/{intent_id}")
async def check_circle_intent(intent_id: str):
    """Check the status of a Circle payment intent."""
    if not ENABLED:
        # Return from DB only
        from database import get_db
        db = await get_db()
        async with db.execute(
            "SELECT * FROM circle_payments WHERE id=? OR circle_intent_id=?",
            (intent_id, intent_id),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            raise HTTPException(404, detail="Payment intent not found")
        return _ok(dict(row))

    try:
        live_intent = await get_payment_intent(intent_id)
    except Exception as e:
        raise HTTPException(502, detail=f"Circle API error: {e}")

    # Update DB if status changed
    from database import get_db

    db = await get_db()
    live_status = live_intent.get("status", "")
    now = _TS()
    completed_at = now if is_payment_complete(live_status) else None
    await db.execute(
        "UPDATE circle_payments SET status=?, completed_at=? WHERE circle_intent_id=?",
        (live_status, completed_at, intent_id),
    )
    await db.commit()

    return _ok({"intent": live_intent})


@router.get("/intents")
async def list_circle_intents(status: Optional[str] = None):
    """List Circle payment intents from local DB."""
    from database import get_db

    db = await get_db()
    if status:
        async with db.execute(
            "SELECT * FROM circle_payments WHERE status=? ORDER BY created_at DESC LIMIT 25",
            (status,),
        ) as cur:
            rows = await cur.fetchall() or []
    else:
        async with db.execute(
            "SELECT * FROM circle_payments ORDER BY created_at DESC LIMIT 25"
        ) as cur:
            rows = await cur.fetchall() or []

    return _ok({"payments": [dict(r) for r in rows], "total": len(rows)})


@router.post("/transfer")
async def outbound_transfer(req: CreateTransferRequest):
    """
    Send USDC from Circle account to a blockchain address.
    Use this to pay agents or external addresses from the protocol's Circle balance.
    """
    if not ENABLED:
        raise HTTPException(503, detail="Circle API not configured")

    valid_chains = {"BASE", "ETH", "ARB", "AVAX", "MATIC"}
    chain = req.destination_chain.upper()
    if chain not in valid_chains:
        raise HTTPException(400, detail=f"Invalid chain. Valid: {sorted(valid_chains)}")

    idem_key = f"oixa-transfer-{uuid.uuid4()}"
    try:
        transfer = await create_transfer(
            destination_address=req.destination_address,
            destination_chain=chain,
            amount_usdc=req.amount_usdc,
            idempotency_key=idem_key,
        )
    except Exception as e:
        raise HTTPException(502, detail=f"Circle API error: {e}")

    return _ok({
        "circle_transfer_id":  transfer.get("id"),
        "destination_address": req.destination_address,
        "destination_chain":   chain,
        "amount_usdc":         req.amount_usdc,
        "status":              transfer.get("status"),
    })


@router.get("/payments")
async def list_circle_incoming(status: Optional[str] = None):
    """List incoming payments to Circle account."""
    if not ENABLED:
        raise HTTPException(503, detail="Circle API not configured")
    try:
        payments = await list_payments(status=status)
    except Exception as e:
        raise HTTPException(502, detail=str(e))
    return _ok({"payments": payments, "total": len(payments)})


@router.get("/balance")
async def circle_balance():
    """Get Circle account balances."""
    if not ENABLED:
        raise HTTPException(503, detail="Circle API not configured")
    try:
        balance = await get_balance()
    except Exception as e:
        raise HTTPException(502, detail=str(e))
    return _ok(balance)


@router.post("/webhook")
async def circle_webhook(request: Request):
    """
    Receive Circle payment webhooks.

    Configure at: https://app.circle.com/developers/webhooks
    Webhook URL: https://<your-domain>/api/v1/payments/circle/webhook

    Events handled:
      payments.confirmed → mark payment complete
    """
    import json

    payload = await request.body()
    try:
        body = json.loads(payload)
    except Exception:
        raise HTTPException(400, detail="Invalid JSON")

    event_type = body.get("type", "")
    data       = body.get("data", {})
    payment_id = data.get("id") or data.get("paymentIntentId")

    from database import get_db

    db = await get_db()
    now = _TS()

    if "confirmed" in event_type.lower() or "paid" in event_type.lower():
        await db.execute(
            "UPDATE circle_payments SET status='completed', completed_at=? "
            "WHERE circle_intent_id=? OR circle_payment_id=?",
            (now, payment_id, payment_id),
        )
        await db.commit()

        from core.telegram_notifier import _send
        await _send(
            f"💰 *Circle Payment Confirmed*\n"
            f"ID: `{payment_id}`\n"
            f"Event: `{event_type}`"
        )

    return {"success": True, "processed": event_type}
