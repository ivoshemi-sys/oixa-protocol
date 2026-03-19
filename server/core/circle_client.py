"""
Circle Payments API client for VELUN Protocol.

Handles institutional USDC payments via Circle's API:
  - Payment intents (request USDC from a payer's Circle wallet)
  - Payout tracking
  - Webhook verification

Docs: https://developers.circle.com/circle-mint/docs/payments-quickstart
API:  https://api.circle.com/v1/
"""

import logging
import uuid
from typing import Optional

import httpx

from config import CIRCLE_API_KEY, CIRCLE_API_URL

logger = logging.getLogger("velun.circle")

ENABLED = bool(CIRCLE_API_KEY)


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {CIRCLE_API_KEY}",
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }


async def _request(method: str, path: str, data: Optional[dict] = None) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.request(
            method,
            f"{CIRCLE_API_URL}{path}",
            json=data,
            headers=_headers(),
        )
        resp.raise_for_status()
        return resp.json()


# ── Account info ──────────────────────────────────────────────────────────────

async def get_account_config() -> dict:
    """Get Circle account configuration and entity details."""
    if not ENABLED:
        raise RuntimeError("Circle API not configured — set CIRCLE_API_KEY")
    return await _request("GET", "/v1/configuration")


async def get_balance() -> dict:
    """Get Circle account balances."""
    if not ENABLED:
        raise RuntimeError("Circle API not configured")
    return await _request("GET", "/v1/businessAccount/balances")


# ── Payment intents ───────────────────────────────────────────────────────────

async def create_payment_intent(
    amount_usdc: float,
    description: str = "",
    idempotency_key: Optional[str] = None,
    settlement_currency: str = "USD",
) -> dict:
    """
    Create a Circle payment intent.
    The payer can pay from any Circle wallet or supported blockchain.
    Returns payment intent with hosted_url for the payer.
    """
    if not ENABLED:
        raise RuntimeError("Circle API not configured")

    body = {
        "idempotencyKey": idempotency_key or str(uuid.uuid4()),
        "amount": {
            "amount":   f"{amount_usdc:.2f}",
            "currency": "USD",
        },
        "settlementCurrency": settlement_currency,
        "paymentMethods": [
            {"type": "blockchain", "chain": "BASE"},
            {"type": "blockchain", "chain": "ETH"},
            {"type": "blockchain", "chain": "ARB"},
            {"type": "blockchain", "chain": "AVAX"},
            {"type": "blockchain", "chain": "MATIC"},
        ],
    }
    if description:
        body["description"] = description

    result = await _request("POST", "/v1/paymentIntents", body)
    return result.get("data", result)


async def get_payment_intent(intent_id: str) -> dict:
    """Retrieve a payment intent by ID."""
    if not ENABLED:
        raise RuntimeError("Circle API not configured")
    result = await _request("GET", f"/v1/paymentIntents/{intent_id}")
    return result.get("data", result)


async def list_payment_intents(status: Optional[str] = None) -> list:
    """List payment intents, optionally filtered by status."""
    if not ENABLED:
        raise RuntimeError("Circle API not configured")
    path = "/v1/paymentIntents"
    if status:
        path += f"?status={status}"
    result = await _request("GET", path)
    return result.get("data", [])


# ── Payments (incoming) ───────────────────────────────────────────────────────

async def get_payment(payment_id: str) -> dict:
    """Get a specific incoming payment."""
    if not ENABLED:
        raise RuntimeError("Circle API not configured")
    result = await _request("GET", f"/v1/payments/{payment_id}")
    return result.get("data", result)


async def list_payments(status: Optional[str] = None, limit: int = 25) -> list:
    """List incoming payments."""
    if not ENABLED:
        raise RuntimeError("Circle API not configured")
    path = f"/v1/payments?pageSize={limit}"
    if status:
        path += f"&status={status}"
    result = await _request("GET", path)
    return result.get("data", [])


# ── Transfers (outgoing payouts) ──────────────────────────────────────────────

async def create_transfer(
    destination_address: str,
    destination_chain: str,
    amount_usdc: float,
    idempotency_key: Optional[str] = None,
) -> dict:
    """
    Send USDC from Circle account to a blockchain address.
    destination_chain: "BASE", "ETH", "ARB", "AVAX", "MATIC"
    """
    if not ENABLED:
        raise RuntimeError("Circle API not configured")

    body = {
        "idempotencyKey": idempotency_key or str(uuid.uuid4()),
        "source":  {"type": "wallet", "id": "primary"},
        "destination": {
            "type":    "blockchain",
            "address": destination_address,
            "chain":   destination_chain.upper(),
        },
        "amount": {
            "amount":   f"{amount_usdc:.6f}",
            "currency": "USD",
        },
    }
    result = await _request("POST", "/v1/transfers", body)
    return result.get("data", result)


# ── Status helpers ────────────────────────────────────────────────────────────

def is_payment_complete(status: str) -> bool:
    return status.lower() in ("paid", "complete", "confirmed")


def is_payment_failed(status: str) -> bool:
    return status.lower() in ("failed", "canceled", "expired")
