"""
Admin API for OIXA Protocol.
Endpoints for emergency controls, Safe setup, and system management.
Protected by PROTOCOL_PRIVATE_KEY presence (server-side only — not a public API).
"""

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from config import PROTOCOL_PRIVATE_KEY, BLOCKCHAIN_ENABLED, PROTOCOL_WALLET, DAILY_LIMIT_USD
from database import get_db

router = APIRouter(tags=["admin"])
logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_admin(x_admin_key: str | None):
    """Minimal auth: caller must send the last 8 chars of PROTOCOL_PRIVATE_KEY as X-Admin-Key."""
    if not PROTOCOL_PRIVATE_KEY:
        raise HTTPException(status_code=503, detail="Admin controls not configured")
    expected = PROTOCOL_PRIVATE_KEY[-8:]
    if x_admin_key != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


# ── Emergency pause ───────────────────────────────────────────────────────────

@router.post("/admin/pause")
async def emergency_pause(x_admin_key: str | None = Header(default=None)):
    """Pause the OIXAEscrow contract on-chain."""
    _require_admin(x_admin_key)

    result = {"action": "pause", "blockchain": None, "timestamp": _now()}

    if BLOCKCHAIN_ENABLED:
        from blockchain.escrow_client import escrow_client
        chain_result = await escrow_client.pause_contract()
        result["blockchain"] = chain_result

    from core.telegram_notifier import notify_emergency_pause
    await notify_emergency_pause(paused=True)

    logger.warning("[ADMIN] Emergency pause triggered")
    return {"success": True, "data": result, "timestamp": _now()}


@router.post("/admin/unpause")
async def emergency_unpause(x_admin_key: str | None = Header(default=None)):
    """Unpause the OIXAEscrow contract on-chain."""
    _require_admin(x_admin_key)

    result = {"action": "unpause", "blockchain": None, "timestamp": _now()}

    if BLOCKCHAIN_ENABLED:
        from blockchain.escrow_client import escrow_client
        chain_result = await escrow_client.unpause_contract()
        result["blockchain"] = chain_result

    from core.telegram_notifier import notify_emergency_pause
    await notify_emergency_pause(paused=False)

    logger.info("[ADMIN] Contract unpaused")
    return {"success": True, "data": result, "timestamp": _now()}


@router.get("/admin/contract-status")
async def contract_status(x_admin_key: str | None = Header(default=None)):
    """Check on-chain contract state."""
    _require_admin(x_admin_key)

    data: dict = {
        "blockchain_enabled": BLOCKCHAIN_ENABLED,
        "protocol_wallet":    PROTOCOL_WALLET,
        "timestamp":          _now(),
    }

    if BLOCKCHAIN_ENABLED:
        from blockchain.escrow_client import escrow_client
        data["paused"]  = await escrow_client.is_paused()
        data["stats"]   = await escrow_client.get_contract_stats()
        data["balance"] = await escrow_client.get_wallet_balance()

    return {"success": True, "data": data, "timestamp": _now()}


# ── Safe multisig ─────────────────────────────────────────────────────────────

@router.get("/admin/safe-address")
async def get_safe_address(x_admin_key: str | None = Header(default=None)):
    """Compute or return the deterministic Safe address for the protocol wallet."""
    _require_admin(x_admin_key)

    from config import SAFE_ADDRESS
    if SAFE_ADDRESS:
        return {"success": True, "data": {"safe_address": SAFE_ADDRESS, "source": "env"}, "timestamp": _now()}

    if not PROTOCOL_WALLET:
        raise HTTPException(status_code=503, detail="PROTOCOL_WALLET not set")

    from blockchain.safe_setup import predict_safe_address
    safe_addr = predict_safe_address(PROTOCOL_WALLET)
    return {
        "success": True,
        "data": {
            "safe_address": safe_addr,
            "source":       "computed",
            "owner":        PROTOCOL_WALLET,
            "note":         "Set SAFE_ADDRESS in .env and deploy with: python -m blockchain.safe_setup deploy",
        },
        "timestamp": _now(),
    }


@router.post("/admin/setup-safe")
async def setup_safe(x_admin_key: str | None = Header(default=None)):
    """Deploy a Safe{Core} multisig on Base mainnet for the protocol wallet."""
    _require_admin(x_admin_key)

    from config import BASE_RPC_URL, PROTOCOL_PRIVATE_KEY as PK
    if not PK or not BASE_RPC_URL:
        raise HTTPException(status_code=503, detail="BASE_RPC_URL and PROTOCOL_PRIVATE_KEY required")

    from blockchain.safe_setup import deploy_safe
    try:
        safe_address = await deploy_safe(BASE_RPC_URL, PK)
        return {"success": True, "data": {"safe_address": safe_address}, "timestamp": _now()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Daily limit ───────────────────────────────────────────────────────────────

@router.get("/admin/daily-spending")
async def daily_spending_status(x_admin_key: str | None = Header(default=None)):
    """Current day's spending vs limit."""
    _require_admin(x_admin_key)

    db    = await get_db()
    from core.daily_limit import get_daily_spent
    spent = await get_daily_spent(db)

    return {
        "success": True,
        "data": {
            "spent_today_usd": round(spent, 4),
            "limit_usd":       DAILY_LIMIT_USD,
            "remaining_usd":   round(max(0.0, DAILY_LIMIT_USD - spent), 4),
            "pct_used":        round(spent / DAILY_LIMIT_USD, 4) if DAILY_LIMIT_USD > 0 else 0,
        },
        "timestamp": _now(),
    }
