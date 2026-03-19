"""
Unified payment hub for OIXA Protocol.

Single endpoint showing all active payment methods and auto-detection.

Endpoints:
  GET /payments/hub/status         → all payment methods + their status
  GET /payments/hub/detect/{id}    → auto-detect which method a payment ID belongs to
  GET /payments/hub/receive        → how to send USDC to OIXA (all methods)
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException

from config import PROTOCOL_VERSION, PROTOCOL_WALLET, BLOCKCHAIN_ENABLED
from core.payment_router import (
    NETWORK_DISPLAY,
    SUPPORTED_NETWORKS,
    get_all_payment_methods_status,
    resolve_payment_by_id,
)

router = APIRouter(prefix="/payments/hub", tags=["Payment Hub"])

_TS = lambda: datetime.now(timezone.utc).isoformat()


def _ok(data):
    return {"success": True, "data": data, "timestamp": _TS(), "protocol_version": PROTOCOL_VERSION}


@router.get("/status")
async def hub_status():
    """
    Master status of all OIXA payment integrations.

    Returns enabled/disabled status for every payment method:
    CCTP, Coinbase Commerce, Circle Payments, Stripe Onramp, x402, Direct.
    """
    methods = await get_all_payment_methods_status()

    enabled_count = sum(1 for v in methods.values() if v.get("enabled"))

    return _ok({
        "payment_methods":       methods,
        "enabled_count":         enabled_count,
        "total_methods":         len(methods),
        "supported_networks":    SUPPORTED_NETWORKS,
        "protocol_wallet":       PROTOCOL_WALLET or "(not configured)",
        "blockchain_mode":       "base_mainnet" if BLOCKCHAIN_ENABLED else "simulated",
    })


@router.get("/receive")
async def receive_instructions():
    """
    How to send USDC to OIXA Protocol from any network.

    Returns step-by-step instructions for each supported payment method.
    """
    methods = await get_all_payment_methods_status()

    instructions = {}

    if methods["cctp"]["enabled"]:
        instructions["cctp"] = {
            "name":        "Circle CCTP Bridge",
            "description": "Send USDC from Ethereum, Arbitrum, Avalanche, Polygon → Base",
            "steps": [
                "1. GET /api/v1/payments/cctp/instructions/{chain}?amount_usdc=X",
                "2. Call TokenMessenger.depositForBurn on your source chain",
                "3. POST /api/v1/payments/cctp/submit with your tx hash",
                "4. OIXA auto-completes the bridge (2-20 min)",
            ],
            "supported_sources": methods["cctp"]["supported_sources"],
        }

    if methods["coinbase_commerce"]["enabled"]:
        instructions["coinbase_commerce"] = {
            "name":        "Coinbase Commerce",
            "description": "Hosted payment page — USDC on any network",
            "steps": [
                "1. POST /api/v1/payments/coinbase/charge with amount_usdc",
                "2. Redirect payer to hosted_url",
                "3. Coinbase handles payment detection automatically",
                "4. Webhook fires on confirmation → GET /api/v1/payments/coinbase/charge/{code}",
            ],
            "supported_sources": methods["coinbase_commerce"]["supported_sources"],
        }

    if methods["circle_payments"]["enabled"]:
        instructions["circle_payments"] = {
            "name":        "Circle Payments Network",
            "description": "Institutional USDC via Circle API",
            "steps": [
                "1. POST /api/v1/payments/circle/intent with amount_usdc",
                "2. Share circle_intent_id with payer",
                "3. Payer fulfills via Circle wallet or blockchain",
                "4. GET /api/v1/payments/circle/intent/{id} to confirm",
            ],
            "supported_sources": methods["circle_payments"]["supported_sources"],
        }

    if methods["stripe_onramp"]["enabled"]:
        instructions["stripe_onramp"] = {
            "name":        "Stripe Crypto Onramp",
            "description": "Pay by credit/debit card → Stripe converts to USDC on Base",
            "steps": [
                "1. POST /api/v1/payments/onramp/session with amount_usdc + wallet_address",
                "2. Embed Stripe widget using client_secret",
                "3. Stripe delivers USDC to the wallet on Base",
            ],
            "supported_sources": methods["stripe_onramp"]["supported_sources"],
        }

    if methods["x402"]["enabled"]:
        instructions["x402"] = {
            "name":        "x402 HTTP Micropayments",
            "description": "Per-request USDC payments — gasless EIP-3009 on Base",
            "steps": [
                "1. Hit any /x402/* endpoint → get HTTP 402 + PAYMENT-REQUIRED header",
                "2. Decode PAYMENT-REQUIRED (base64 JSON) for payment details",
                "3. Sign EIP-3009 TransferWithAuthorization (no ETH needed)",
                "4. Retry with X-PAYMENT: base64(proof) header",
                "5. Read X-PAYMENT-RESPONSE header for settlement proof",
            ],
            "supported_sources": methods["x402"]["supported_sources"],
            "example_endpoints": [
                "/api/v1/x402/intel ($0.01)",
                "/api/v1/x402/agent/{id} ($0.001)",
            ],
        }

    if methods["direct"]["enabled"]:
        instructions["direct"] = {
            "name":        "Direct Transfer",
            "description": "Send USDC directly to the OIXA protocol wallet on Base",
            "address":     PROTOCOL_WALLET,
            "network":     "Base Mainnet (chain 8453)",
            "usdc":        "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            "steps": [
                f"1. Send USDC to {PROTOCOL_WALLET} on Base mainnet",
                "2. Notify OIXA with the tx hash",
            ],
        }

    return _ok({
        "protocol_wallet": PROTOCOL_WALLET or "(not configured)",
        "accepted_networks": SUPPORTED_NETWORKS,
        "payment_methods": instructions,
        "network_display": NETWORK_DISPLAY,
    })


@router.get("/detect/{payment_id}")
async def detect_payment(payment_id: str):
    """
    Auto-detect which payment method a given ID belongs to.
    Searches CCTP, Coinbase Commerce, Circle, Stripe across all DB tables.
    """
    result = await resolve_payment_by_id(payment_id)
    if not result:
        raise HTTPException(404, detail=f"Payment ID '{payment_id}' not found in any payment method")

    return _ok(result)
