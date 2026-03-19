"""
Stripe payments API for VELUN Protocol.

Endpoints:
  Crypto Onramp:
    POST   /payments/onramp/session          — create onramp session (card → USDC on Base)
    GET    /payments/onramp/session/{id}     — get session status
    POST   /payments/onramp/webhook          — Stripe webhook (fulfillment)

  Issuing (virtual cards for agents):
    POST   /payments/issuing/cardholders     — register agent as cardholder
    GET    /payments/issuing/cardholders/{agent_id}  — get cardholder for agent
    POST   /payments/issuing/cards           — issue virtual card to cardholder
    GET    /payments/issuing/cards/{agent_id}        — list cards for agent
    GET    /payments/issuing/cards/{card_id}/details — full card number + CVC
    POST   /payments/issuing/cards/{card_id}/freeze   — freeze card
    POST   /payments/issuing/cards/{card_id}/unfreeze — unfreeze card
    POST   /payments/issuing/webhook         — Stripe Issuing webhook
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr

from config import STRIPE_ENABLED, STRIPE_PUBLISHABLE_KEY
from database import get_db

router = APIRouter(tags=["payments"])
logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ok(data: dict) -> dict:
    return {"success": True, "data": data, "timestamp": _now(), "protocol_version": "0.1.0"}


def _err(msg: str, code: str = "ERROR", status: int = 400):
    return JSONResponse(
        status_code=status,
        content={"success": False, "error": msg, "code": code, "timestamp": _now()},
    )


def _require_stripe():
    if not STRIPE_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Stripe not configured. Set STRIPE_SECRET_KEY in .env",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic models
# ─────────────────────────────────────────────────────────────────────────────

class OnrampSessionCreate(BaseModel):
    amount_usd: float                  # USD amount user wants to spend
    wallet_address: str                # destination USDC wallet on Base
    auction_id: Optional[str] = None  # optional: link to a specific VELUN auction
    agent_id:   Optional[str] = None


class CardholderCreate(BaseModel):
    agent_id:   str
    agent_name: str
    email:      str
    phone:      Optional[str] = None
    address:    Optional[dict] = None  # {line1, city, state, postal_code, country}


class CardCreate(BaseModel):
    agent_id:          str
    spending_limit_usd: float = 100.0


# ─────────────────────────────────────────────────────────────────────────────
# Crypto Onramp
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/payments/onramp/session")
async def create_onramp_session(body: OnrampSessionCreate, request: Request):
    """
    Create a Stripe Crypto Onramp session.

    The user pays with credit/debit card; Stripe handles KYC + conversion
    and deposits USDC on Base directly into `wallet_address`.

    Returns a `client_secret` to embed the Stripe Onramp widget on the frontend:
      import {loadCryptoOnramp} from '@stripe/crypto-onramp-js';
      const onramp = await loadCryptoOnramp(publishable_key);
      onramp.createSession({clientSecret}).mount('#stripe-onramp');
    """
    _require_stripe()

    from core.stripe_client import create_onramp_session as _create

    customer_ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else None)

    try:
        result = await _create(
            wallet_address=body.wallet_address,
            amount_usd=str(body.amount_usd),
            network="base",
            customer_ip=customer_ip,
        )
    except Exception as e:
        logger.error(f"Stripe onramp session creation failed: {e}")
        return _err(str(e), "STRIPE_ERROR")

    # Persist session record
    session_id = f"velun_session_{uuid.uuid4().hex[:12]}"
    db = await get_db()
    await db.execute(
        """INSERT INTO stripe_onramp_sessions
           (id, stripe_session_id, client_secret, wallet_address, amount_usd,
            auction_id, agent_id, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            session_id,
            result["stripe_session_id"],
            result["client_secret"],
            body.wallet_address,
            body.amount_usd,
            body.auction_id,
            body.agent_id,
            result["status"],
            _now(),
        ),
    )
    await db.commit()

    logger.info(f"Onramp session created | {session_id} | ${body.amount_usd} → {body.wallet_address[:10]}...")

    return _ok({
        "session_id":       session_id,
        "stripe_session_id": result["stripe_session_id"],
        "client_secret":    result["client_secret"],
        "publishable_key":  result["publishable_key"],
        "amount_usd":       body.amount_usd,
        "destination": {
            "currency": "usdc",
            "network":  "base",
            "wallet":   body.wallet_address,
        },
        "status": result["status"],
        "instructions": (
            "Use client_secret with @stripe/crypto-onramp-js to embed the payment widget. "
            "USDC will be deposited on Base mainnet once the user completes payment and KYC."
        ),
    })


@router.get("/payments/onramp/session/{session_id}")
async def get_onramp_session(session_id: str):
    """Get the status of an onramp session."""
    db = await get_db()
    async with db.execute(
        "SELECT * FROM stripe_onramp_sessions WHERE id = ? OR stripe_session_id = ?",
        (session_id, session_id),
    ) as cur:
        row = await cur.fetchone()

    if not row:
        return _err("Session not found", "NOT_FOUND", 404)

    # Also fetch live status from Stripe if configured
    live_status = None
    if STRIPE_ENABLED and row["stripe_session_id"]:
        try:
            from core.stripe_client import retrieve_onramp_session
            live = await retrieve_onramp_session(row["stripe_session_id"])
            live_status = live["status"]
        except Exception:
            pass

    return _ok({
        "session_id":        row["id"],
        "stripe_session_id": row["stripe_session_id"],
        "wallet_address":    row["wallet_address"],
        "amount_usd":        row["amount_usd"],
        "auction_id":        row["auction_id"],
        "agent_id":          row["agent_id"],
        "status":            live_status or row["status"],
        "created_at":        row["created_at"],
        "completed_at":      row["completed_at"],
    })


@router.post("/payments/onramp/webhook")
async def onramp_webhook(request: Request, stripe_signature: str = Header(None)):
    """
    Stripe Crypto Onramp webhook.
    Handles `crypto_onramp_session.updated` events.
    When status = fulfillment_complete, the USDC has arrived in the wallet.
    """
    if not STRIPE_ENABLED:
        return {"received": True}

    payload = await request.body()

    try:
        from core.stripe_client import verify_onramp_webhook
        event = verify_onramp_webhook(payload, stripe_signature or "")
    except Exception as e:
        logger.warning(f"Onramp webhook signature verification failed: {e}")
        return _err("Invalid webhook signature", "INVALID_SIGNATURE", 400)

    event_type = event["type"]
    logger.info(f"Stripe onramp webhook: {event_type}")

    if event_type == "crypto_onramp_session.updated":
        session_obj = event["data"]["object"]
        stripe_session_id = session_obj["id"]
        new_status        = session_obj["status"]

        db = await get_db()

        # Update our record
        completed_at = _now() if new_status == "fulfillment_complete" else None
        await db.execute(
            """UPDATE stripe_onramp_sessions
               SET status = ?, completed_at = COALESCE(?, completed_at)
               WHERE stripe_session_id = ?""",
            (new_status, completed_at, stripe_session_id),
        )
        await db.commit()

        if new_status == "fulfillment_complete":
            # Fetch our session record to get auction_id
            async with db.execute(
                "SELECT * FROM stripe_onramp_sessions WHERE stripe_session_id = ?",
                (stripe_session_id,),
            ) as cur:
                row = await cur.fetchone()

            if row and row["auction_id"]:
                logger.info(
                    f"Onramp fulfillment complete | session={row['id']} | "
                    f"auction={row['auction_id']} | ${row['amount_usd']} USDC → {row['wallet_address'][:10]}..."
                )
                # Notify Ivan via Telegram
                try:
                    from core.telegram_notifier import send_alert
                    await send_alert(
                        f"💳 <b>Onramp completo</b>\n"
                        f"Session: <code>{row['id']}</code>\n"
                        f"Monto: <b>${row['amount_usd']} USDC</b>\n"
                        f"Wallet: <code>{row['wallet_address'][:20]}...</code>"
                    )
                except Exception:
                    pass

    return {"received": True}


# ─────────────────────────────────────────────────────────────────────────────
# Issuing — Cardholders
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/payments/issuing/cardholders")
async def create_cardholder(body: CardholderCreate):
    """
    Register an VELUN agent as a Stripe Issuing cardholder.
    Required before issuing virtual cards to the agent.
    """
    _require_stripe()

    db = await get_db()

    # Check if cardholder already exists for this agent
    async with db.execute(
        "SELECT * FROM stripe_cardholders WHERE agent_id = ?", (body.agent_id,)
    ) as cur:
        existing = await cur.fetchone()
    if existing:
        return _ok({
            "cardholder_id":          existing["id"],
            "stripe_cardholder_id":   existing["stripe_cardholder_id"],
            "agent_id":               existing["agent_id"],
            "status":                 existing["status"],
            "already_exists":         True,
        })

    from core.stripe_client import create_cardholder as _create

    try:
        result = await _create(
            name=body.agent_name,
            email=body.email,
            phone=body.phone,
            address=body.address,
        )
    except Exception as e:
        logger.error(f"Stripe cardholder creation failed: {e}")
        return _err(str(e), "STRIPE_ERROR")

    cardholder_id = f"velun_ch_{uuid.uuid4().hex[:12]}"
    await db.execute(
        """INSERT INTO stripe_cardholders
           (id, stripe_cardholder_id, agent_id, agent_name, email, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            cardholder_id,
            result["stripe_cardholder_id"],
            body.agent_id,
            body.agent_name,
            body.email,
            result["status"],
            _now(),
        ),
    )
    await db.commit()

    logger.info(f"Cardholder created | {cardholder_id} | agent={body.agent_id}")

    return _ok({
        "cardholder_id":        cardholder_id,
        "stripe_cardholder_id": result["stripe_cardholder_id"],
        "agent_id":             body.agent_id,
        "agent_name":           body.agent_name,
        "email":                body.email,
        "status":               result["status"],
    })


@router.get("/payments/issuing/cardholders/{agent_id}")
async def get_cardholder(agent_id: str):
    """Get the Stripe cardholder registered for an agent."""
    db = await get_db()
    async with db.execute(
        "SELECT * FROM stripe_cardholders WHERE agent_id = ?", (agent_id,)
    ) as cur:
        row = await cur.fetchone()

    if not row:
        return _err("Cardholder not found for this agent", "NOT_FOUND", 404)

    return _ok(dict(row))


# ─────────────────────────────────────────────────────────────────────────────
# Issuing — Cards
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/payments/issuing/cards")
async def issue_card(body: CardCreate):
    """
    Issue a virtual Visa card to a registered VELUN agent.
    The agent must have a cardholder record first (POST /issuing/cardholders).
    The card can be used to pay for fiat services anywhere Visa is accepted.
    """
    _require_stripe()

    db = await get_db()

    # Look up cardholder for this agent
    async with db.execute(
        "SELECT * FROM stripe_cardholders WHERE agent_id = ?", (body.agent_id,)
    ) as cur:
        cardholder = await cur.fetchone()

    if not cardholder:
        return _err(
            "Agent has no cardholder. Call POST /payments/issuing/cardholders first.",
            "CARDHOLDER_NOT_FOUND",
        )

    from core.stripe_client import create_virtual_card

    try:
        result = await create_virtual_card(
            stripe_cardholder_id=cardholder["stripe_cardholder_id"],
            spending_limit_usd=body.spending_limit_usd,
        )
    except Exception as e:
        logger.error(f"Stripe card creation failed: {e}")
        return _err(str(e), "STRIPE_ERROR")

    card_id = f"velun_card_{uuid.uuid4().hex[:12]}"
    await db.execute(
        """INSERT INTO stripe_cards
           (id, stripe_card_id, cardholder_id, agent_id, last4,
            exp_month, exp_year, status, spending_limit_usd, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            card_id,
            result["stripe_card_id"],
            cardholder["id"],
            body.agent_id,
            result["last4"],
            result["exp_month"],
            result["exp_year"],
            result["status"],
            body.spending_limit_usd,
            _now(),
        ),
    )
    await db.commit()

    logger.info(f"Card issued | {card_id} | agent={body.agent_id} | limit=${body.spending_limit_usd}")

    return _ok({
        "card_id":         card_id,
        "stripe_card_id":  result["stripe_card_id"],
        "agent_id":        body.agent_id,
        "last4":           result["last4"],
        "exp_month":       result["exp_month"],
        "exp_year":        result["exp_year"],
        "brand":           result["brand"],
        "status":          result["status"],
        "spending_limit_usd": body.spending_limit_usd,
        "note": "Use GET /payments/issuing/cards/{card_id}/details to retrieve full card number (requires Issuing access).",
    })


@router.get("/payments/issuing/cards/{agent_id}")
async def list_agent_cards(agent_id: str):
    """List all virtual cards issued to an agent."""
    db = await get_db()
    async with db.execute(
        "SELECT * FROM stripe_cards WHERE agent_id = ? ORDER BY created_at DESC",
        (agent_id,),
    ) as cur:
        rows = await cur.fetchall()

    return _ok({"agent_id": agent_id, "cards": [dict(r) for r in rows], "count": len(rows)})


@router.get("/payments/issuing/cards/{card_id}/details")
async def get_card_details(card_id: str):
    """
    Retrieve full card details: number, CVC, expiry.
    Requires Stripe Issuing access and PCI compliance.
    Card number is shown once — store securely on the client side.
    """
    _require_stripe()

    db = await get_db()
    async with db.execute(
        "SELECT * FROM stripe_cards WHERE id = ? OR stripe_card_id = ?",
        (card_id, card_id),
    ) as cur:
        row = await cur.fetchone()

    if not row:
        return _err("Card not found", "NOT_FOUND", 404)

    from core.stripe_client import retrieve_card_details

    try:
        details = await retrieve_card_details(row["stripe_card_id"])
    except Exception as e:
        logger.error(f"Stripe card details fetch failed: {e}")
        return _err(str(e), "STRIPE_ERROR")

    return _ok({
        "card_id":        row["id"],
        "agent_id":       row["agent_id"],
        "number":         details.get("number"),
        "cvc":            details.get("cvc"),
        "exp_month":      details["exp_month"],
        "exp_year":       details["exp_year"],
        "last4":          details["last4"],
        "brand":          details["brand"],
        "status":         details["status"],
        "warning":        "Handle card number with PCI DSS compliance. Do not log or store.",
    })


@router.post("/payments/issuing/cards/{card_id}/freeze")
async def freeze_card(card_id: str):
    """Freeze a virtual card (status → inactive). Transactions will be declined."""
    _require_stripe()
    return await _set_card_status(card_id, "inactive")


@router.post("/payments/issuing/cards/{card_id}/unfreeze")
async def unfreeze_card(card_id: str):
    """Unfreeze a virtual card (status → active)."""
    _require_stripe()
    return await _set_card_status(card_id, "active")


async def _set_card_status(card_id: str, status: str):
    db = await get_db()
    async with db.execute(
        "SELECT * FROM stripe_cards WHERE id = ? OR stripe_card_id = ?",
        (card_id, card_id),
    ) as cur:
        row = await cur.fetchone()

    if not row:
        return _err("Card not found", "NOT_FOUND", 404)

    from core.stripe_client import update_card_status

    try:
        result = await update_card_status(row["stripe_card_id"], status)
    except Exception as e:
        return _err(str(e), "STRIPE_ERROR")

    await db.execute(
        "UPDATE stripe_cards SET status = ? WHERE id = ?",
        (status, row["id"]),
    )
    await db.commit()

    action = "frozen" if status == "inactive" else "unfrozen"
    logger.info(f"Card {action} | {row['id']} | agent={row['agent_id']}")

    return _ok({
        "card_id":  row["id"],
        "agent_id": row["agent_id"],
        "status":   result["status"],
        "action":   action,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Issuing Webhook
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/payments/issuing/webhook")
async def issuing_webhook(request: Request, stripe_signature: str = Header(None)):
    """
    Stripe Issuing webhook.
    Handles authorization requests (issuing.authorization.request) —
    approve or decline card transactions based on agent status.
    Also handles authorization.created/updated for logging.
    """
    if not STRIPE_ENABLED:
        return {"received": True}

    payload = await request.body()

    try:
        from core.stripe_client import verify_issuing_webhook
        event = verify_issuing_webhook(payload, stripe_signature or "")
    except Exception as e:
        logger.warning(f"Issuing webhook signature verification failed: {e}")
        return _err("Invalid webhook signature", "INVALID_SIGNATURE", 400)

    event_type = event["type"]
    logger.info(f"Stripe Issuing webhook: {event_type}")

    if event_type == "issuing_authorization.request":
        # Real-time authorization: approve or decline
        auth    = event["data"]["object"]
        card_id = auth["card"]["id"]
        amount  = auth["pending_request"]["amount"] / 100  # cents → dollars
        merchant = auth.get("merchant_data", {}).get("name", "unknown")

        db = await get_db()
        async with db.execute(
            "SELECT * FROM stripe_cards WHERE stripe_card_id = ?", (card_id,)
        ) as cur:
            row = await cur.fetchone()

        approved = bool(row and row["status"] == "active")

        logger.info(
            f"Card authorization | card={card_id} | ${amount:.2f} | "
            f"merchant={merchant} | approved={approved}"
        )

        # Stripe requires approving via the API within ~2s
        if approved:
            try:
                from core.stripe_client import _get_stripe, _run
                s = _get_stripe()
                await _run(s.issuing.Authorization.approve, auth["id"])
            except Exception as e:
                logger.error(f"Failed to approve Stripe authorization: {e}")

    elif event_type in ("issuing_authorization.created", "issuing_authorization.updated"):
        auth    = event["data"]["object"]
        status  = auth.get("status")
        amount  = auth.get("amount", 0) / 100
        merchant = auth.get("merchant_data", {}).get("name", "unknown")
        logger.info(f"Authorization {event_type.split('.')[-1]}: ${amount:.2f} @ {merchant} | status={status}")

    return {"received": True}


# ─────────────────────────────────────────────────────────────────────────────
# Status endpoint
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/payments/status")
async def payments_status():
    """Stripe integration status and summary."""
    db = await get_db()

    async with db.execute("SELECT COUNT(*) as n FROM stripe_onramp_sessions") as cur:
        onramp_total = (await cur.fetchone())["n"]
    async with db.execute(
        "SELECT COUNT(*) as n FROM stripe_onramp_sessions WHERE status = 'fulfillment_complete'"
    ) as cur:
        onramp_complete = (await cur.fetchone())["n"]
    async with db.execute("SELECT COUNT(*) as n FROM stripe_cards") as cur:
        cards_total = (await cur.fetchone())["n"]
    async with db.execute(
        "SELECT COUNT(*) as n FROM stripe_cards WHERE status = 'active'"
    ) as cur:
        cards_active = (await cur.fetchone())["n"]
    async with db.execute("SELECT COUNT(*) as n FROM stripe_cardholders") as cur:
        cardholders = (await cur.fetchone())["n"]

    return _ok({
        "stripe_enabled": STRIPE_ENABLED,
        "onramp": {
            "total_sessions":    onramp_total,
            "completed":         onramp_complete,
            "description":       "Card → USDC on Base mainnet",
        },
        "issuing": {
            "cardholders":       cardholders,
            "total_cards":       cards_total,
            "active_cards":      cards_active,
            "description":       "Virtual Visa cards for agents",
        },
        "note": "Set STRIPE_SECRET_KEY in .env to enable. Test keys start with sk_test_",
    })
