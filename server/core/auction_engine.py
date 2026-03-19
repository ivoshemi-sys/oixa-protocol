import asyncio
import logging
import uuid
from datetime import datetime, timezone

from database import get_db
from config import STAKE_PERCENTAGE

logger = logging.getLogger("oixa.auction")


def calculate_auction_duration(max_budget: float) -> int:
    if max_budget < 0.10:
        return 2
    elif max_budget < 10.0:
        return 5
    elif max_budget < 1000.0:
        return 15
    else:
        return 60


def calculate_commission(amount: float) -> float:
    if amount < 1.0:
        return amount * 0.03
    elif amount <= 100.0:
        return amount * 0.05
    else:
        return amount * 0.02


async def process_bid(auction_id: str, bidder_id: str, bidder_name: str, amount: float) -> dict:
    db  = await get_db()
    now = datetime.now(timezone.utc).isoformat()

    async with db.execute("SELECT * FROM auctions WHERE id = ?", (auction_id,)) as cur:
        auction = await cur.fetchone()

    if not auction:
        return {"accepted": False, "reason": "Auction not found"}
    if auction["status"] != "open":
        return {"accepted": False, "reason": f"Auction is not open (status: {auction['status']})"}
    if amount >= auction["max_budget"]:
        return {"accepted": False, "reason": f"Bid {amount} must be less than max_budget {auction['max_budget']}"}
    if auction["winning_bid"] is not None and amount >= auction["winning_bid"]:
        return {"accepted": False, "reason": f"Bid {amount} must be lower than current best {auction['winning_bid']} (inverse auction)"}

    stake_amount = amount * STAKE_PERCENTAGE
    bid_id = f"oixa_bid_{uuid.uuid4().hex[:12]}"

    await db.execute(
        """INSERT INTO bids (id, auction_id, bidder_id, bidder_name, amount, stake_amount, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (bid_id, auction_id, bidder_id, bidder_name, amount, stake_amount, "active", now),
    )

    if auction["winner_id"]:
        await db.execute(
            "UPDATE bids SET status = 'refunded' WHERE auction_id = ? AND bidder_id = ? AND status = 'active' AND id != ?",
            (auction_id, auction["winner_id"], bid_id),
        )

    await db.execute(
        "UPDATE auctions SET winner_id = ?, winning_bid = ? WHERE id = ?",
        (bidder_id, amount, auction_id),
    )
    await db.commit()

    logger.info(f"Bid accepted | auction={auction_id} | bidder={bidder_id} | amount={amount} | stake={stake_amount:.4f}")
    return {
        "accepted":       True,
        "bid_id":         bid_id,
        "current_winner": bidder_id,
        "current_best":   amount,
        "stake_amount":   stake_amount,
    }


async def close_auction(auction_id: str) -> dict:
    db  = await get_db()
    now = datetime.now(timezone.utc).isoformat()

    async with db.execute("SELECT * FROM auctions WHERE id = ?", (auction_id,)) as cur:
        auction = await cur.fetchone()

    if not auction or auction["status"] != "open":
        return {"success": False, "reason": "Auction not open or not found"}

    await db.execute(
        "UPDATE auctions SET status = 'closed', closed_at = ? WHERE id = ?",
        (now, auction_id),
    )

    winner_id   = auction["winner_id"]
    winning_bid = auction["winning_bid"]

    if winner_id and winning_bid is not None:
        await db.execute(
            "UPDATE bids SET status = 'winner' WHERE auction_id = ? AND bidder_id = ? AND status = 'active'",
            (auction_id, winner_id),
        )

        commission = calculate_commission(winning_bid)
        escrow_id  = f"oixa_escrow_{uuid.uuid4().hex[:12]}"

        # ── Daily spending limit check ────────────────────────────────────────
        from core.daily_limit import check_limit, record_spending
        try:
            await check_limit(winning_bid, db)
        except ValueError as e:
            logger.warning(f"Daily limit hit — cancelling auction {auction_id}: {e}")
            await db.execute("UPDATE auctions SET status = 'cancelled' WHERE id = ?", (auction_id,))
            await db.commit()
            return {"success": False, "error": str(e), "auction_id": auction_id}
        await record_spending(winning_bid, auction_id, f"Escrow for auction {auction_id}", db)

        # ── DB escrow record (always) ─────────────────────────────────────────
        await db.execute(
            """INSERT INTO escrows
               (id, auction_id, payer_id, payee_id, amount, commission, status,
                simulated, tx_hash, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                escrow_id, auction_id,
                auction["requester_id"], winner_id,
                winning_bid, commission,
                "held", True, None, now,
            ),
        )

        # Stake ledger entry
        ledger_id = f"oixa_ledger_{uuid.uuid4().hex[:12]}"
        await db.execute(
            """INSERT INTO ledger
               (id, transaction_type, from_agent, to_agent, amount, currency, auction_id, description, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ledger_id, "stake", winner_id, "oixa_protocol",
                winning_bid * STAKE_PERCENTAGE, "USDC",
                auction_id, f"Stake held for auction {auction_id}", now,
            ),
        )
        await db.commit()

        # ── Blockchain escrow (if configured) ────────────────────────────────
        chain_result = {"simulated": True}
        try:
            from blockchain.escrow_client import escrow_client
            if escrow_client.enabled:
                # Look up winner's on-chain wallet (if registered in offers)
                payee_wallet = await _get_agent_wallet(winner_id)
                chain_result = await escrow_client.create_escrow(
                    escrow_id     = escrow_id,
                    auction_id    = auction_id,
                    payee_address = payee_wallet,
                    amount_usdc   = winning_bid,
                    commission_usdc = commission,
                )
                if not chain_result.get("simulated"):
                    # Update DB with on-chain tx info
                    await db.execute(
                        "UPDATE escrows SET simulated = ?, tx_hash = ? WHERE id = ?",
                        (False, chain_result.get("tx_hash"), escrow_id),
                    )
                    await db.commit()
                    logger.info(f"On-chain escrow created | {escrow_id} | tx={chain_result['tx_hash'][:16]}...")
        except ImportError:
            pass
        except Exception as e:
            logger.error(f"Blockchain escrow creation error: {e}")

        from core.telegram_notifier import notify_escrow_created
        await notify_escrow_created(auction_id, winning_bid, winner_id)

        from core.openclaw import openclaw_client
        await openclaw_client.broadcast(
            "auction_closed",
            {
                "auction_id": auction_id,
                "winner_id":  winner_id,
                "winning_bid": winning_bid,
                "escrow_id":  escrow_id,
                "on_chain":   not chain_result.get("simulated", True),
            },
        )

        logger.info(
            f"Auction closed | {auction_id} | winner={winner_id} | "
            f"bid={winning_bid} | escrow={escrow_id} | "
            f"on_chain={not chain_result.get('simulated', True)}"
        )
        return {
            "success":    True,
            "auction_id": auction_id,
            "winner_id":  winner_id,
            "winning_bid": winning_bid,
            "escrow_id":  escrow_id,
            "on_chain":   not chain_result.get("simulated", True),
        }
    else:
        await db.commit()
        await _cancel_auction(auction_id)
        logger.info(f"Auction cancelled (no bids) | {auction_id}")
        return {"success": True, "auction_id": auction_id, "winner_id": None, "reason": "No bids received"}


async def _cancel_auction(auction_id: str):
    db  = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "UPDATE auctions SET status = 'cancelled', closed_at = ? WHERE id = ?",
        (now, auction_id),
    )
    await db.commit()


async def _get_agent_wallet(agent_id: str) -> str | None:
    """Return on-chain wallet address for an agent (stored in offers.wallet_address)."""
    try:
        db = await get_db()
        async with db.execute(
            "SELECT wallet_address FROM offers WHERE agent_id = ? AND wallet_address IS NOT NULL LIMIT 1",
            (agent_id,),
        ) as cur:
            row = await cur.fetchone()
        return row["wallet_address"] if row else None
    except Exception:
        return None


async def run_auction_timer(auction_id: str, duration_seconds: int):
    await asyncio.sleep(duration_seconds)
    db = await get_db()
    async with db.execute("SELECT status FROM auctions WHERE id = ?", (auction_id,)) as cur:
        row = await cur.fetchone()
    if row and row["status"] == "open":
        await close_auction(auction_id)
