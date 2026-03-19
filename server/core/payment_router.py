"""
Unified payment router for OIXA Protocol.

Auto-detects USDC source network and routes to the correct handler.
Provides a single normalized view across CCTP, Coinbase Commerce,
Circle Payments, Stripe Onramp, and x402.

Network detection priority:
  1. Explicit X-Payment-Network header
  2. CCTP bridge data (source_chain field)
  3. Coinbase Commerce payment network field
  4. Circle payment chain field
  5. Default: Base mainnet
"""

import logging
from typing import Optional

logger = logging.getLogger("oixa.payment_router")

# ── Network normalization map ─────────────────────────────────────────────────

_NETWORK_ALIASES: dict[str, str] = {
    # Canonical name: several spellings/IDs
    "ethereum":  "ethereum",
    "eth":       "ethereum",
    "eip155:1":  "ethereum",
    "1":         "ethereum",

    "arbitrum":  "arbitrum",
    "arb":       "arbitrum",
    "eip155:42161": "arbitrum",
    "42161":     "arbitrum",

    "avalanche": "avalanche",
    "avax":      "avalanche",
    "eip155:43114": "avalanche",
    "43114":     "avalanche",

    "polygon":   "polygon",
    "matic":     "polygon",
    "eip155:137": "polygon",
    "137":       "polygon",

    "base":      "base",
    "eip155:8453": "base",
    "8453":      "base",

    "solana":    "solana",
    "sol":       "solana",
    "svm":       "solana",

    "optimism":  "optimism",
    "op":        "optimism",
    "eip155:10": "optimism",
    "10":        "optimism",
}

NETWORK_DISPLAY: dict[str, str] = {
    "ethereum":  "Ethereum Mainnet",
    "arbitrum":  "Arbitrum One",
    "avalanche": "Avalanche C-Chain",
    "polygon":   "Polygon PoS",
    "base":      "Base Mainnet",
    "solana":    "Solana",
    "optimism":  "OP Mainnet",
    "unknown":   "Unknown",
}

SUPPORTED_NETWORKS = list(NETWORK_DISPLAY.keys())


def normalize_network(raw: Optional[str]) -> str:
    """Normalize any network name/ID to a canonical string."""
    if not raw:
        return "base"
    return _NETWORK_ALIASES.get(raw.lower().strip(), raw.lower().strip())


def detect_network(payment_data: dict) -> str:
    """
    Auto-detect source network from payment metadata.
    Checks multiple fields in priority order.
    """
    # Direct field
    for field in ("source_chain", "network", "chain", "sourceChain", "paymentNetwork"):
        val = payment_data.get(field)
        if val:
            return normalize_network(str(val))

    # Coinbase Commerce format
    payments = payment_data.get("payments", [])
    if payments and isinstance(payments, list):
        last = payments[-1]
        net  = last.get("network") or last.get("chain")
        if net:
            return normalize_network(net)

    # Circle format
    source = payment_data.get("source", {})
    if isinstance(source, dict):
        chain = source.get("chain")
        if chain:
            return normalize_network(chain)

    return "base"


# ── Unified payment status ────────────────────────────────────────────────────

PAYMENT_SOURCES = ("cctp", "coinbase_commerce", "circle", "stripe_onramp", "x402", "direct")


async def get_all_payment_methods_status() -> dict:
    """Return availability and status of every payment integration."""
    from config import (
        BLOCKCHAIN_ENABLED,
        CIRCLE_API_KEY,
        COINBASE_COMMERCE_API_KEY,
        STRIPE_ENABLED,
        CCTP_ATTESTATION_URL,
        PROTOCOL_WALLET,
    )

    return {
        "cctp": {
            "enabled":     BLOCKCHAIN_ENABLED and bool(PROTOCOL_WALLET),
            "description": "Cross-chain USDC bridge: Ethereum, Arbitrum, Avalanche, Polygon → Base",
            "supported_sources": ["ethereum", "arbitrum", "avalanche", "polygon", "solana"],
            "destination": "base",
            "attestation_url": CCTP_ATTESTATION_URL,
        },
        "coinbase_commerce": {
            "enabled":     bool(COINBASE_COMMERCE_API_KEY),
            "description": "Hosted payment page — USDC on any network via Coinbase",
            "supported_sources": ["ethereum", "base", "polygon", "arbitrum", "avalanche", "solana"],
        },
        "circle_payments": {
            "enabled":     bool(CIRCLE_API_KEY),
            "description": "Institutional Circle wallet payments + blockchain payouts",
            "supported_sources": ["ethereum", "base", "arbitrum", "avalanche", "polygon"],
        },
        "stripe_onramp": {
            "enabled":     STRIPE_ENABLED,
            "description": "Card → USDC on Base via Stripe Crypto Onramp",
            "supported_sources": ["fiat_card"],
        },
        "x402": {
            "enabled":     bool(PROTOCOL_WALLET),
            "description": "HTTP 402 micropayments — EIP-3009 gasless USDC on Base",
            "supported_sources": ["base"],
        },
        "direct": {
            "enabled":     bool(PROTOCOL_WALLET),
            "description": "Direct USDC transfer to protocol wallet on Base",
            "supported_sources": ["base"],
            "address": PROTOCOL_WALLET,
        },
    }


async def resolve_payment_by_id(payment_id: str) -> Optional[dict]:
    """
    Look up a payment across all methods by ID.
    Tries each DB table and returns the first match with normalized status.
    """
    from database import get_db

    db = await get_db()
    result = None

    # CCTP
    async with db.execute(
        "SELECT *, 'cctp' as method FROM cctp_transfers WHERE id=? OR message_hash=?",
        (payment_id, payment_id),
    ) as cur:
        row = await cur.fetchone()
    if row:
        result = dict(row)

    # Coinbase Commerce
    if not result:
        async with db.execute(
            "SELECT *, 'coinbase_commerce' as method FROM coinbase_charges "
            "WHERE id=? OR charge_code=? OR coinbase_charge_id=?",
            (payment_id, payment_id, payment_id),
        ) as cur:
            row = await cur.fetchone()
        if row:
            result = dict(row)

    # Circle
    if not result:
        async with db.execute(
            "SELECT *, 'circle' as method FROM circle_payments "
            "WHERE id=? OR circle_payment_id=? OR circle_intent_id=?",
            (payment_id, payment_id, payment_id),
        ) as cur:
            row = await cur.fetchone()
        if row:
            result = dict(row)

    # Stripe Onramp
    if not result:
        async with db.execute(
            "SELECT *, 'stripe_onramp' as method FROM stripe_onramp_sessions "
            "WHERE id=? OR stripe_session_id=?",
            (payment_id, payment_id),
        ) as cur:
            row = await cur.fetchone()
        if row:
            result = dict(row)

    if not result:
        return None

    # Normalize
    return {
        "id":             result.get("id"),
        "method":         result.get("method"),
        "status":         result.get("status", "unknown"),
        "amount_usdc":    result.get("amount_usdc"),
        "source_chain":   result.get("source_chain", "base"),
        "created_at":     result.get("created_at"),
        "completed_at":   result.get("completed_at"),
        "_raw":           result,
    }
