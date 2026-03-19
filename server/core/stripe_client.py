"""
Stripe client wrapper for OIXA Protocol.
Handles Crypto Onramp sessions and Issuing (virtual cards).
Falls back gracefully when STRIPE_SECRET_KEY is not configured.
"""

import asyncio
import logging
from typing import Optional

from config import (
    STRIPE_SECRET_KEY,
    STRIPE_PUBLISHABLE_KEY,
    STRIPE_WEBHOOK_SECRET,
    STRIPE_ISSUING_WEBHOOK_SECRET,
    STRIPE_ENABLED,
)

logger = logging.getLogger(__name__)

# Lazy-init stripe so missing credentials don't crash startup
_stripe = None


def _get_stripe():
    global _stripe
    if _stripe is None:
        if not STRIPE_ENABLED:
            raise RuntimeError("Stripe not configured — set STRIPE_SECRET_KEY in .env")
        import stripe as _s
        _s.api_key = STRIPE_SECRET_KEY
        _stripe = _s
    return _stripe


async def _run(fn, *args, **kwargs):
    """Run a synchronous Stripe call in a thread."""
    return await asyncio.to_thread(fn, *args, **kwargs)


# ── Crypto Onramp ─────────────────────────────────────────────────────────────

async def create_onramp_session(
    wallet_address: str,
    amount_usd: str,
    network: str = "base",
    customer_ip: Optional[str] = None,
) -> dict:
    """
    Create a Stripe Crypto Onramp session.
    Returns {stripe_session_id, client_secret, url}.
    The client_secret is used with @stripe/crypto-onramp-js to embed the widget.
    """
    s = _get_stripe()
    params = {
        "transaction_details": {
            "destination_currency": "usdc",
            "destination_exchange_amount": amount_usd,
            "destination_network": network,
            "wallet_addresses": {network: wallet_address},
        },
    }
    if customer_ip:
        params["customer_ip_address"] = customer_ip

    session = await _run(s.crypto.OnrampSession.create, **params)
    return {
        "stripe_session_id": session.id,
        "client_secret":     session.client_secret,
        "status":            session.status,
        "publishable_key":   STRIPE_PUBLISHABLE_KEY,
    }


async def retrieve_onramp_session(stripe_session_id: str) -> dict:
    s = _get_stripe()
    session = await _run(s.crypto.OnrampSession.retrieve, stripe_session_id)
    return {
        "stripe_session_id": session.id,
        "status":            session.status,
        "transaction_details": dict(session.transaction_details or {}),
    }


def verify_onramp_webhook(payload: bytes, sig_header: str) -> dict:
    """Construct and verify a Stripe onramp webhook event."""
    s = _get_stripe()
    return s.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)


# ── Issuing ───────────────────────────────────────────────────────────────────

async def create_cardholder(
    name: str,
    email: str,
    phone: Optional[str] = None,
    address: Optional[dict] = None,
) -> dict:
    """
    Create a Stripe Issuing cardholder for an agent.
    address: {line1, city, state, postal_code, country}
    """
    s = _get_stripe()
    params: dict = {
        "name":   name,
        "email":  email,
        "type":   "individual",
        "status": "active",
    }
    if phone:
        params["phone_number"] = phone

    # Stripe Issuing requires a billing address
    billing_addr = address or {
        "line1":       "123 Agent St",
        "city":        "Buenos Aires",
        "state":       "BA",
        "postal_code": "1000",
        "country":     "AR",
    }
    params["billing"] = {"address": billing_addr}

    ch = await _run(s.issuing.Cardholder.create, **params)
    return {
        "stripe_cardholder_id": ch.id,
        "name":   ch.name,
        "email":  ch.email,
        "status": ch.status,
    }


async def create_virtual_card(
    stripe_cardholder_id: str,
    spending_limit_usd: float = 100.0,
    currency: str = "usd",
) -> dict:
    """
    Issue a virtual card to a cardholder.
    spending_limit_usd is the max per-transaction limit.
    """
    s = _get_stripe()
    card = await _run(
        s.issuing.Card.create,
        cardholder=stripe_cardholder_id,
        currency=currency,
        type="virtual",
        status="active",
        spending_controls={
            "spending_limits": [
                {
                    "amount":   int(spending_limit_usd * 100),  # in cents
                    "interval": "per_authorization",
                }
            ]
        },
    )
    return {
        "stripe_card_id": card.id,
        "last4":          card.last4,
        "exp_month":      card.exp_month,
        "exp_year":       card.exp_year,
        "status":         card.status,
        "brand":          card.brand,
    }


async def retrieve_card_details(stripe_card_id: str) -> dict:
    """Retrieve full card details including number and CVC (requires Issuing access)."""
    s = _get_stripe()
    card = await _run(
        s.issuing.Card.retrieve,
        stripe_card_id,
        expand=["number", "cvc"],
    )
    return {
        "stripe_card_id": card.id,
        "number":         getattr(card, "number", None),
        "cvc":            getattr(card, "cvc", None),
        "last4":          card.last4,
        "exp_month":      card.exp_month,
        "exp_year":       card.exp_year,
        "status":         card.status,
        "brand":          card.brand,
    }


async def update_card_status(stripe_card_id: str, status: str) -> dict:
    """Set card status: 'active' or 'inactive'."""
    s = _get_stripe()
    card = await _run(s.issuing.Card.modify, stripe_card_id, status=status)
    return {"stripe_card_id": card.id, "status": card.status}


async def list_cards_for_cardholder(stripe_cardholder_id: str) -> list:
    s = _get_stripe()
    cards = await _run(s.issuing.Card.list, cardholder=stripe_cardholder_id, limit=20)
    return [
        {
            "stripe_card_id": c.id,
            "last4":          c.last4,
            "exp_month":      c.exp_month,
            "exp_year":       c.exp_year,
            "status":         c.status,
            "brand":          c.brand,
        }
        for c in cards.data
    ]


def verify_issuing_webhook(payload: bytes, sig_header: str) -> dict:
    """Construct and verify a Stripe Issuing webhook event."""
    s = _get_stripe()
    return s.Webhook.construct_event(payload, sig_header, STRIPE_ISSUING_WEBHOOK_SECRET)
