"""
Coinbase Commerce client for VELUN Protocol.

Creates hosted payment pages that accept USDC across multiple networks.
Coinbase Commerce handles chain detection automatically.

Docs: https://docs.cdp.coinbase.com/commerce/docs/welcome
"""

import hashlib
import hmac
import logging
from typing import Optional

import httpx

from config import COINBASE_COMMERCE_API_KEY, COINBASE_COMMERCE_WEBHOOK_SECRET

logger = logging.getLogger("velun.coinbase")

COMMERCE_BASE_URL = "https://api.commerce.coinbase.com"
COMMERCE_API_VERSION = "2018-03-22"

ENABLED = bool(COINBASE_COMMERCE_API_KEY)


def _headers() -> dict:
    return {
        "X-CC-Api-Key":  COINBASE_COMMERCE_API_KEY,
        "X-CC-Version":  COMMERCE_API_VERSION,
        "Content-Type":  "application/json",
    }


async def _request(method: str, path: str, data: Optional[dict] = None) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.request(
            method,
            f"{COMMERCE_BASE_URL}{path}",
            json=data,
            headers=_headers(),
        )
        resp.raise_for_status()
        return resp.json()


# ── Charges ───────────────────────────────────────────────────────────────────

async def create_charge(
    amount_usdc: float,
    name: str,
    description: str,
    metadata: Optional[dict] = None,
) -> dict:
    """
    Create a Coinbase Commerce charge.
    Returns Coinbase charge object including hosted_url and code.
    USDC is accepted on Ethereum, Base, Polygon, Arbitrum, Avalanche, Solana.
    """
    if not ENABLED:
        raise RuntimeError("Coinbase Commerce not configured — set COINBASE_COMMERCE_API_KEY")

    body: dict = {
        "name":         name,
        "description":  description,
        "pricing_type": "fixed_price",
        "local_price": {
            "amount":   f"{amount_usdc:.6f}",
            "currency": "USDC",
        },
    }
    if metadata:
        body["metadata"] = metadata

    result = await _request("POST", "/charges", body)
    return result.get("data", result)


async def get_charge(charge_code: str) -> dict:
    """Retrieve a charge by its short code."""
    if not ENABLED:
        raise RuntimeError("Coinbase Commerce not configured")
    result = await _request("GET", f"/charges/{charge_code}")
    return result.get("data", result)


async def list_charges(limit: int = 25) -> list:
    """List recent charges."""
    if not ENABLED:
        raise RuntimeError("Coinbase Commerce not configured")
    result = await _request("GET", f"/charges?limit={limit}")
    return result.get("data", [])


# ── Webhook verification ──────────────────────────────────────────────────────

def verify_webhook(payload: bytes, signature_header: str) -> bool:
    """
    Verify Coinbase Commerce webhook via HMAC-SHA256.
    Header: X-CC-WEBHOOK-SIGNATURE
    """
    if not COINBASE_COMMERCE_WEBHOOK_SECRET:
        logger.warning("[Coinbase] Webhook secret not set — skipping verification")
        return True
    expected = hmac.new(
        COINBASE_COMMERCE_WEBHOOK_SECRET.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


# ── Charge status mapping ─────────────────────────────────────────────────────

def is_payment_confirmed(event_type: str) -> bool:
    """Return True if the event represents a confirmed USDC payment."""
    return event_type in ("charge:confirmed",)


def is_payment_pending(event_type: str) -> bool:
    return event_type in ("charge:pending", "charge:created")


def is_payment_failed(event_type: str) -> bool:
    return event_type in ("charge:failed", "charge:expired")


# ── Network detection ─────────────────────────────────────────────────────────

def extract_payment_network(charge_data: dict) -> Optional[str]:
    """Extract which network USDC was received on from a confirmed charge."""
    try:
        timeline = charge_data.get("timeline", [])
        for event in reversed(timeline):
            context = event.get("context", "")
            if "CONFIRMED" in context.upper():
                # Coinbase includes chain info in payment.network
                pass
        payments = charge_data.get("payments", [])
        if payments:
            last = payments[-1]
            return last.get("network", "unknown")
    except Exception:
        pass
    return None
