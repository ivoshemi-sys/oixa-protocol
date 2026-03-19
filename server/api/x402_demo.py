"""
x402 Payment Protocol — demo endpoints for VELUN Protocol.

Endpoints:
  GET /x402/status           → free — shows x402 config and payment addresses
  GET /x402/ping             → free — health/capability check
  GET /x402/intel            → $0.01 USDC — market intelligence report
  GET /x402/agent/{id}       → $0.001 USDC — agent reputation profile
  GET /x402/auction/{id}     → $0.005 USDC — premium auction details

How to pay (manual curl example):
  1. Hit endpoint → get 402 with PAYMENT-REQUIRED header
  2. Decode PAYMENT-REQUIRED (base64 JSON) to get payment details
  3. Sign EIP-3009 authorization off-chain (use VELUN agent SDK or wagmi/viem)
  4. Retry with header: X-PAYMENT: <base64-encoded payment proof>
  5. Read X-PAYMENT-RESPONSE header in 200 response for settlement proof
"""

import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from config import BLOCKCHAIN_ENABLED, PROTOCOL_VERSION, PROTOCOL_WALLET
from core.x402 import (
    NETWORK,
    USDC_ADDRESS,
    X402_VERSION,
    build_payment_requirements,
    encode_b64,
    require_payment,
    usdc_to_units,
)

router = APIRouter(prefix="/x402", tags=["x402"])


def _payment_response(data: dict, request: Request) -> JSONResponse:
    """Attach X-PAYMENT-RESPONSE header to 200 response if payment was made."""
    headers = {}
    if hasattr(request.state, "x402_response_header"):
        headers["X-PAYMENT-RESPONSE"] = request.state.x402_response_header
    return JSONResponse(
        content={"success": True, "data": data, "protocol_version": PROTOCOL_VERSION},
        headers=headers,
    )


# ── Free endpoints ─────────────────────────────────────────────────────────────

@router.get("/ping")
async def ping():
    """Free — x402 capability check."""
    return {
        "success": True,
        "data": {
            "x402": True,
            "network": NETWORK,
            "asset": USDC_ADDRESS,
            "mode": "base_mainnet" if BLOCKCHAIN_ENABLED else "simulated",
        },
        "protocol_version": PROTOCOL_VERSION,
    }


@router.get("/status")
async def x402_status():
    """Free — x402 configuration and available paid endpoints."""
    pay_to = PROTOCOL_WALLET or "(PROTOCOL_WALLET not set)"
    paid_endpoints = [
        {
            "path":        "/api/v1/x402/intel",
            "price_usdc":  0.01,
            "price_units": usdc_to_units(0.01),
            "description": "VELUN market intelligence — avg prices, volume, top agents",
            "payment_required_preview": encode_b64(
                build_payment_requirements(0.01, "/api/v1/x402/intel", "Market intel", pay_to)
            ),
        },
        {
            "path":        "/api/v1/x402/agent/{agent_id}",
            "price_usdc":  0.001,
            "price_units": usdc_to_units(0.001),
            "description": "Agent reputation profile — win rate, earnings, offers",
            "payment_required_preview": encode_b64(
                build_payment_requirements(0.001, "/api/v1/x402/agent/{id}", "Agent profile", pay_to)
            ),
        },
        {
            "path":        "/api/v1/x402/auction/{auction_id}",
            "price_usdc":  0.005,
            "price_units": usdc_to_units(0.005),
            "description": "Full auction history with all bids and escrow details",
            "payment_required_preview": encode_b64(
                build_payment_requirements(0.005, "/api/v1/x402/auction/{id}", "Auction detail", pay_to)
            ),
        },
    ]
    return {
        "success": True,
        "data": {
            "x402Version":       X402_VERSION,
            "network":           NETWORK,
            "asset":             USDC_ADDRESS,
            "pay_to":            pay_to,
            "blockchain_mode":   "base_mainnet" if BLOCKCHAIN_ENABLED else "simulated",
            "scheme":            "exact",
            "paid_endpoints":    paid_endpoints,
            "how_to_pay": {
                "step_1": "Hit any paid endpoint → HTTP 402 + PAYMENT-REQUIRED header",
                "step_2": "base64-decode PAYMENT-REQUIRED to read payment requirements",
                "step_3": "Sign EIP-3009 TransferWithAuthorization with your wallet (gasless)",
                "step_4": "Retry request with X-PAYMENT: base64(JSON payment proof)",
                "step_5": "Read X-PAYMENT-RESPONSE header for settlement proof",
                "payment_proof_schema": {
                    "x402Version": 1,
                    "scheme": "exact",
                    "network": NETWORK,
                    "payload": {
                        "signature": "0x<EIP-712 signature>",
                        "authorization": {
                            "from":        "0x<your_wallet>",
                            "to":          pay_to,
                            "value":       "<amount in USDC atomic units>",
                            "validAfter":  0,
                            "validBefore": "<unix_timestamp + 300>",
                            "nonce":       "0x<random_32_bytes>",
                        },
                    },
                },
            },
        },
        "protocol_version": PROTOCOL_VERSION,
    }


# ── Paid endpoints ─────────────────────────────────────────────────────────────

@router.get("/intel")
async def intel_report(
    request: Request,
    payment=Depends(require_payment(0.01, "VELUN market intelligence — $0.01 USDC")),
):
    """
    $0.01 USDC — Market intelligence from the VELUN ledger.

    Returns aggregate stats: auction prices, volume, top agents, price index.
    Requires EIP-3009 payment proof in X-PAYMENT header.
    """
    from database import get_db

    db = await get_db()

    async with db.execute(
        """SELECT COUNT(*) as total,
                  AVG(winning_bid) as avg_bid,
                  MIN(winning_bid) as min_bid,
                  MAX(winning_bid) as max_bid
           FROM auctions
           WHERE status IN ('completed', 'closed') AND winning_bid IS NOT NULL"""
    ) as cur:
        auction_stats = await cur.fetchone()

    async with db.execute(
        "SELECT COUNT(*) as total, SUM(amount) as volume "
        "FROM ledger WHERE transaction_type = 'payment'"
    ) as cur:
        ledger_stats = await cur.fetchone()

    async with db.execute(
        """SELECT b.bidder_name, COUNT(*) as wins, AVG(b.amount) as avg_bid_usdc
           FROM auctions a JOIN bids b ON a.winner_id = b.bidder_id
           WHERE a.status = 'completed'
           GROUP BY b.bidder_name ORDER BY wins DESC LIMIT 5"""
    ) as cur:
        top_agents = await cur.fetchall() or []

    async with db.execute(
        """SELECT SUM(amount) as commissions
           FROM protocol_revenue WHERE source = 'commission'"""
    ) as cur:
        rev = await cur.fetchone()

    data = {
        "x402": {
            "paid":        True,
            "amount_usdc": 0.01,
            "tx_hash":     payment.get("tx_hash"),
            "payer":       payment.get("from"),
            "simulated":   payment.get("simulated", True),
        },
        "market_intelligence": {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "auctions": {
                "completed":          auction_stats["total"] if auction_stats else 0,
                "avg_winning_bid":    round(auction_stats["avg_bid"] or 0, 6) if auction_stats else 0,
                "min_winning_bid":    round(auction_stats["min_bid"] or 0, 6) if auction_stats else 0,
                "max_winning_bid":    round(auction_stats["max_bid"] or 0, 6) if auction_stats else 0,
            },
            "volume": {
                "total_transactions": ledger_stats["total"]  if ledger_stats else 0,
                "total_usdc":         round(ledger_stats["volume"] or 0, 6) if ledger_stats else 0,
            },
            "protocol_revenue_usdc": round(rev["commissions"] or 0, 6) if rev else 0,
            "top_agents": [
                {
                    "name":          a["bidder_name"],
                    "wins":          a["wins"],
                    "avg_bid_usdc":  round(a["avg_bid_usdc"] or 0, 6),
                }
                for a in top_agents
            ],
        },
    }
    return _payment_response(data, request)


@router.get("/agent/{agent_id}")
async def agent_profile(
    agent_id: str,
    request: Request,
    payment=Depends(require_payment(0.001, "Agent reputation profile — $0.001 USDC")),
):
    """
    $0.001 USDC — Agent reputation data: win rate, earnings, active offers.
    """
    from database import get_db

    db = await get_db()

    async with db.execute(
        """SELECT COUNT(*) as total_bids,
                  SUM(CASE WHEN status='winner' THEN 1 ELSE 0 END) as wins,
                  AVG(amount) as avg_bid
           FROM bids WHERE bidder_id = ?""",
        (agent_id,),
    ) as cur:
        bid_stats = await cur.fetchone()

    async with db.execute(
        "SELECT COUNT(*) as offers FROM offers WHERE agent_id = ? AND status = 'active'",
        (agent_id,),
    ) as cur:
        offer_stats = await cur.fetchone()

    async with db.execute(
        "SELECT SUM(amount) as earned FROM ledger WHERE to_agent = ? AND transaction_type = 'payment'",
        (agent_id,),
    ) as cur:
        earnings = await cur.fetchone()

    total_bids = bid_stats["total_bids"] if bid_stats else 0
    wins       = bid_stats["wins"]       if bid_stats else 0

    data = {
        "x402": {
            "paid":        True,
            "amount_usdc": 0.001,
            "tx_hash":     payment.get("tx_hash"),
            "payer":       payment.get("from"),
            "simulated":   payment.get("simulated", True),
        },
        "agent_profile": {
            "agent_id":          agent_id,
            "total_bids":        total_bids,
            "wins":              wins,
            "win_rate":          round(wins / total_bids, 3) if total_bids else 0,
            "avg_bid_usdc":      round(bid_stats["avg_bid"] or 0, 6) if bid_stats else 0,
            "active_offers":     offer_stats["offers"]  if offer_stats else 0,
            "total_earned_usdc": round(earnings["earned"] or 0, 6) if earnings else 0,
        },
    }
    return _payment_response(data, request)


@router.get("/auction/{auction_id}")
async def auction_detail(
    auction_id: str,
    request: Request,
    payment=Depends(require_payment(0.005, "Premium auction detail — $0.005 USDC")),
):
    """
    $0.005 USDC — Full auction record: all bids, escrow status, verification.
    """
    from database import get_db

    db = await get_db()

    async with db.execute("SELECT * FROM auctions WHERE id = ?", (auction_id,)) as cur:
        auction = await cur.fetchone()

    if not auction:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Auction not found")

    async with db.execute(
        "SELECT * FROM bids WHERE auction_id = ? ORDER BY amount ASC", (auction_id,)
    ) as cur:
        bids = await cur.fetchall() or []

    async with db.execute(
        "SELECT * FROM escrows WHERE auction_id = ?", (auction_id,)
    ) as cur:
        escrow = await cur.fetchone()

    async with db.execute(
        "SELECT * FROM verifications WHERE auction_id = ?", (auction_id,)
    ) as cur:
        verification = await cur.fetchone()

    data = {
        "x402": {
            "paid":        True,
            "amount_usdc": 0.005,
            "tx_hash":     payment.get("tx_hash"),
            "payer":       payment.get("from"),
            "simulated":   payment.get("simulated", True),
        },
        "auction": dict(auction),
        "bids":    [dict(b) for b in bids],
        "escrow":  dict(escrow) if escrow else None,
        "verification": dict(verification) if verification else None,
    }
    return _payment_response(data, request)
