import uuid
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from database import get_db
from models.escrow import SimulatePayment
from core.auction_engine import calculate_commission
from config import PROTOCOL_VERSION, BLOCKCHAIN_ENABLED

router = APIRouter(tags=["escrow"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _response(data):
    return {"success": True, "data": data, "timestamp": _now(), "protocol_version": PROTOCOL_VERSION}


def _error(msg: str, code: str, status_code: int = 400):
    return JSONResponse(
        status_code=status_code,
        content={"success": False, "error": msg, "code": code, "timestamp": _now()},
    )


@router.get("/escrow/{auction_id}")
async def get_escrow(auction_id: str):
    db = await get_db()
    async with db.execute(
        "SELECT * FROM escrows WHERE auction_id = ?", (auction_id,)
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return _error("Escrow not found for this auction", "ESCROW_NOT_FOUND", 404)

    data = dict(row)
    data["simulated"] = bool(data.get("simulated", True))

    # Enrich with on-chain state if enabled and not simulated
    if BLOCKCHAIN_ENABLED and not data["simulated"]:
        try:
            from blockchain.escrow_client import escrow_client
            onchain = await escrow_client.get_escrow_onchain(data["id"])
            if onchain:
                data["onchain"] = onchain
        except Exception:
            pass

    return _response(data)


@router.post("/escrow/simulate")
async def simulate_payment(payment: SimulatePayment):
    db  = await get_db()
    now = _now()
    commission = calculate_commission(payment.amount)
    escrow_id  = f"velun_escrow_{uuid.uuid4().hex[:12]}"

    await db.execute(
        """INSERT INTO escrows
           (id, auction_id, payer_id, payee_id, amount, commission, status, simulated, tx_hash, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            escrow_id, payment.auction_id,
            payment.payer_id, payment.payee_id,
            payment.amount, commission,
            "held", True, None, now,
        ),
    )

    ledger_id = f"velun_ledger_{uuid.uuid4().hex[:12]}"
    await db.execute(
        """INSERT INTO ledger
           (id, transaction_type, from_agent, to_agent, amount, currency, auction_id, description, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            ledger_id, "payment",
            payment.payer_id, payment.payee_id,
            payment.amount, "USDC", payment.auction_id,
            f"Simulated escrow payment for auction {payment.auction_id}",
            now,
        ),
    )
    await db.commit()

    return _response(
        {
            "id":         escrow_id,
            "auction_id": payment.auction_id,
            "payer_id":   payment.payer_id,
            "payee_id":   payment.payee_id,
            "amount":     payment.amount,
            "commission": commission,
            "status":     "held",
            "simulated":  True,
            "created_at": now,
        }
    )


@router.get("/escrow/wallet/status")
async def wallet_status():
    """Protocol wallet USDC + ETH balance on Base. Requires blockchain configured."""
    if not BLOCKCHAIN_ENABLED:
        return _response({
            "enabled":  False,
            "message":  "Set BASE_RPC_URL, PROTOCOL_PRIVATE_KEY, ESCROW_CONTRACT_ADDRESS to enable",
        })
    try:
        from blockchain.escrow_client import escrow_client
        balances = await escrow_client.get_wallet_balance()
        stats    = await escrow_client.get_contract_stats()
        return _response({"wallet": balances, "contract": stats})
    except Exception as e:
        return _error(str(e), "WALLET_ERROR")


@router.get("/escrow/contract/stats")
async def contract_stats():
    """On-chain contract cumulative stats (locked, released, commissions)."""
    if not BLOCKCHAIN_ENABLED:
        return _response({"enabled": False, "simulated": True})
    try:
        from blockchain.escrow_client import escrow_client
        return _response(await escrow_client.get_contract_stats() or {})
    except Exception as e:
        return _error(str(e), "CONTRACT_STATS_ERROR")
