"""
Circle CCTP (Cross-Chain Transfer Protocol) endpoints for VELUN Protocol.

Receive USDC from any chain → Base mainnet automatically.

Endpoints:
  GET  /payments/cctp/chains          → supported source chains
  GET  /payments/cctp/instructions/{chain} → deposit instructions for senders
  POST /payments/cctp/submit          → register a CCTP burn tx to monitor
  GET  /payments/cctp/status/{id}     → check transfer status
  GET  /payments/cctp/transfers       → list all CCTP transfers
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import PROTOCOL_VERSION, PROTOCOL_WALLET, BLOCKCHAIN_ENABLED
from core.cctp_client import (
    CHAINS,
    BASE_DOMAIN,
    CCTP_TOKEN_MESSENGER,
    CCTP_MESSAGE_TRANSMITTER,
    extract_message_from_tx,
    fetch_attestation,
    get_deposit_instructions,
)

router = APIRouter(prefix="/payments/cctp", tags=["CCTP"])

_TS = lambda: datetime.now(timezone.utc).isoformat()


def _ok(data):
    return {"success": True, "data": data, "timestamp": _TS(), "protocol_version": PROTOCOL_VERSION}


# ── Models ────────────────────────────────────────────────────────────────────

class SubmitTransferRequest(BaseModel):
    source_chain:  str               # "ethereum", "arbitrum", "avalanche", "polygon", "solana"
    source_tx_hash: str              # tx hash on source chain
    amount_usdc:   float
    message_bytes: Optional[str] = None   # hex, optional if source RPC is configured
    message_hash:  Optional[str] = None   # hex, optional
    auction_id:    Optional[str] = None
    agent_id:      Optional[str] = None


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/chains")
async def supported_chains():
    """List all supported CCTP source chains."""
    return _ok({
        "destination":      "base",
        "destination_domain": BASE_DOMAIN,
        "destination_address": PROTOCOL_WALLET or "(PROTOCOL_WALLET not set)",
        "token_messenger":  CCTP_TOKEN_MESSENGER,
        "message_transmitter": CCTP_MESSAGE_TRANSMITTER,
        "supported_sources": [
            {
                "chain":       k,
                "label":       v["label"],
                "domain":      v["domain"],
                "usdc":        v["usdc"],
                "evm":         v.get("evm", True),
            }
            for k, v in CHAINS.items()
            if k != "base"
        ],
    })


@router.get("/instructions/{source_chain}")
async def deposit_instructions(source_chain: str, amount_usdc: float = 1.0):
    """
    Get step-by-step instructions for a sender to bridge USDC from {source_chain} → Base.

    The sender must:
    1. Approve USDC on source chain
    2. Call TokenMessenger.depositForBurn with the returned parameters
    3. Submit the tx hash to POST /payments/cctp/submit
    """
    if not PROTOCOL_WALLET:
        raise HTTPException(503, detail="PROTOCOL_WALLET not configured")

    if source_chain not in CHAINS:
        supported = [k for k in CHAINS if k != "base"]
        raise HTTPException(400, detail=f"Unsupported chain. Supported: {supported}")

    if amount_usdc <= 0:
        raise HTTPException(400, detail="amount_usdc must be > 0")

    try:
        instructions = get_deposit_instructions(source_chain, amount_usdc)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))

    return _ok({
        "instructions": instructions,
        "step_by_step": [
            f"1. On {CHAINS[source_chain]['label']}, approve USDC ({CHAINS[source_chain]['usdc']}) "
            f"to TokenMessenger ({CCTP_TOKEN_MESSENGER})",
            f"2. Call TokenMessenger.depositForBurn with the params shown in instructions.function_call",
            f"3. Copy the tx hash from step 2",
            f"4. POST /api/v1/payments/cctp/submit with source_chain, source_tx_hash, amount_usdc",
            f"5. VELUN will auto-complete the bridge (attestation + receiveMessage on Base)",
            f"6. Poll GET /api/v1/payments/cctp/status/{{id}} until status=completed",
        ],
    })


@router.post("/submit")
async def submit_cctp_transfer(req: SubmitTransferRequest):
    """
    Register a CCTP burn transaction for automated bridging to Base.

    After the sender calls depositForBurn on source chain, submit the tx hash here.
    VELUN will fetch the MessageSent event, get Circle attestation, and call
    receiveMessage on Base — minting USDC directly to the protocol wallet.
    """
    if req.source_chain not in CHAINS:
        supported = [k for k in CHAINS if k != "base"]
        raise HTTPException(400, detail=f"Unsupported chain. Supported: {supported}")

    if req.source_chain == "solana":
        if not req.message_bytes or not req.message_hash:
            raise HTTPException(
                400,
                detail="For Solana, message_bytes and message_hash are required "
                       "(Solana tx extraction is not supported via web3.py).",
            )

    # Try to auto-extract message from source tx if not provided
    message_hex = req.message_bytes
    message_hash = req.message_hash

    if not message_hex and req.source_chain != "solana":
        extracted = await extract_message_from_tx(req.source_tx_hash, req.source_chain)
        if extracted:
            message_hex, message_hash = extracted

    from database import get_db

    db  = await get_db()
    tid = f"velun_cctp_{uuid.uuid4().hex[:12]}"
    now = _TS()

    await db.execute(
        """INSERT INTO cctp_transfers
           (id, source_chain, source_tx_hash, message_hash, message_bytes,
            amount_usdc, recipient, status, auction_id, agent_id, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            tid,
            req.source_chain,
            req.source_tx_hash,
            message_hash,
            message_hex,
            req.amount_usdc,
            PROTOCOL_WALLET,
            "pending" if (message_hex and message_hash) else "awaiting_message",
            req.auction_id,
            req.agent_id,
            now,
        ),
    )
    await db.commit()

    return _ok({
        "transfer_id":    tid,
        "source_chain":   req.source_chain,
        "source_tx_hash": req.source_tx_hash,
        "amount_usdc":    req.amount_usdc,
        "status":         "pending" if (message_hex and message_hash) else "awaiting_message",
        "message_extracted": bool(message_hex),
        "message_hash":   message_hash,
        "next": (
            f"GET /api/v1/payments/cctp/status/{tid} — poll for completion (usually 2-20 min)"
            if (message_hex and message_hash)
            else "Message extraction failed. Check source chain RPC env var or provide message_bytes manually."
        ),
        "created_at": now,
    })


@router.get("/status/{transfer_id}")
async def transfer_status(transfer_id: str):
    """Check the status of a CCTP cross-chain transfer."""
    from database import get_db

    db = await get_db()
    async with db.execute(
        "SELECT * FROM cctp_transfers WHERE id=?", (transfer_id,)
    ) as cur:
        row = await cur.fetchone()

    if not row:
        raise HTTPException(404, detail="Transfer not found")

    transfer = dict(row)

    # Live attestation check if still attesting
    attestation_status = None
    if transfer.get("message_hash") and transfer["status"] in ("pending", "attesting"):
        att = await fetch_attestation(transfer["message_hash"])
        if att:
            attestation_status = "signed"
        else:
            attestation_status = "pending_confirmations"

    return _ok({
        **transfer,
        "attestation_live_status": attestation_status,
    })


@router.get("/transfers")
async def list_transfers(status: Optional[str] = None, limit: int = 25):
    """List all CCTP cross-chain transfers."""
    from database import get_db

    db = await get_db()

    if status:
        async with db.execute(
            "SELECT * FROM cctp_transfers WHERE status=? ORDER BY created_at DESC LIMIT ?",
            (status, limit),
        ) as cur:
            rows = await cur.fetchall() or []
    else:
        async with db.execute(
            "SELECT * FROM cctp_transfers ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall() or []

    return _ok({
        "transfers": [dict(r) for r in rows],
        "total":     len(rows),
    })
