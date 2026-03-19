"""
Auto-release background job.

Runs every AUTO_RELEASE_INTERVAL seconds (default 60).
Finds verified deliveries with no open dispute whose dispute window has expired,
and releases their escrow + records ledger entries.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone, timedelta

from config import DISPUTE_WINDOW_MINUTES, AUTO_RELEASE_INTERVAL

logger = logging.getLogger("velun.auto_release")


async def auto_release_loop():
    """Long-running coroutine. Start as asyncio.create_task() in lifespan."""
    logger.info(f"Auto-release job started (window={DISPUTE_WINDOW_MINUTES}m, interval={AUTO_RELEASE_INTERVAL}s)")
    while True:
        try:
            await asyncio.sleep(AUTO_RELEASE_INTERVAL)
            released = await check_and_release()
            if released:
                logger.info(f"Auto-release: released {released} escrow(s)")
        except asyncio.CancelledError:
            logger.info("Auto-release job cancelled")
            break
        except Exception as e:
            logger.error(f"Auto-release error: {e}", exc_info=True)


async def check_and_release() -> int:
    """
    Find all pending_release escrows whose dispute window has expired
    and release them. Returns count of released escrows.
    """
    from database import get_db

    db      = await get_db()
    now     = datetime.now(timezone.utc)
    cutoff  = (now - timedelta(minutes=DISPUTE_WINDOW_MINUTES)).isoformat()
    now_str = now.isoformat()
    released = 0

    # Find escrows in pending_release where verification happened > window minutes ago
    async with db.execute(
        """SELECT e.id as escrow_id, e.auction_id, e.payer_id, e.payee_id,
                  e.amount, e.commission, e.simulated, e.tx_hash,
                  v.verified_at
           FROM escrows e
           JOIN verifications v ON e.auction_id = v.auction_id
           WHERE e.status = 'pending_release'
             AND v.passed = 1
             AND v.verified_at < ?""",
        (cutoff,),
    ) as cur:
        candidates = await cur.fetchall()

    for row in candidates:
        auction_id = row["auction_id"]
        escrow_id  = row["escrow_id"]

        # Check no open dispute exists
        async with db.execute(
            "SELECT id FROM disputes WHERE auction_id = ? AND status IN ('open', 'resolving')",
            (auction_id,),
        ) as cur:
            dispute = await cur.fetchone()

        if dispute:
            logger.debug(f"Auto-release skipped: dispute open for auction {auction_id}")
            continue

        # Atomically claim the escrow: only proceed if status is still pending_release
        await db.execute(
            "UPDATE escrows SET status = 'releasing' WHERE id = ? AND status = 'pending_release'",
            (escrow_id,),
        )
        await db.commit()

        # Verify we won the race
        async with db.execute(
            "SELECT status FROM escrows WHERE id = ?", (escrow_id,)
        ) as cur:
            check = await cur.fetchone()
        if not check or check["status"] != "releasing":
            continue

        # Release
        try:
            await _do_release(db, dict(row), now_str)
            released += 1
            logger.info(
                f"Auto-released escrow {escrow_id} | auction={auction_id} | "
                f"{row['amount']:.4f} USDC | "
                f"window expired {DISPUTE_WINDOW_MINUTES}m after delivery"
            )
        except Exception as e:
            logger.error(f"Failed to release escrow {escrow_id}: {e}")
            # Roll back to pending_release so it retries next cycle
            await db.execute(
                "UPDATE escrows SET status = 'pending_release' WHERE id = ?", (escrow_id,)
            )
            await db.commit()

    return released


async def _do_release(db, row: dict, now: str):
    """Finalize a pending_release escrow: update DB + ledger entries."""
    escrow_id  = row["escrow_id"]
    auction_id = row["auction_id"]
    commission = row["commission"]
    net        = row["amount"] - commission

    def _lid():
        return f"velun_ledger_{uuid.uuid4().hex[:12]}"

    # ── Try on-chain release if blockchain is configured ──────────────────────
    release_tx = None
    try:
        from blockchain.escrow_client import escrow_client
        if escrow_client.enabled and not row.get("simulated", True):
            result = await escrow_client.release_escrow(escrow_id)
            if not result.get("simulated"):
                release_tx = result.get("tx_hash")
                logger.info(f"[AUTO-RELEASE] On-chain release tx={release_tx[:16]}...")
    except Exception as e:
        logger.warning(f"[AUTO-RELEASE] On-chain release failed: {e} — DB-only release")

    # ── Mark escrow released ──────────────────────────────────────────────────
    await db.execute(
        "UPDATE escrows SET status = 'released', released_at = ?, tx_hash = COALESCE(?, tx_hash) WHERE id = ?",
        (now, release_tx, escrow_id),
    )

    # ── Update auction ────────────────────────────────────────────────────────
    await db.execute(
        "UPDATE auctions SET status = 'completed', completed_at = ? WHERE id = ? AND status != 'completed'",
        (now, auction_id),
    )

    # ── Ledger entries ────────────────────────────────────────────────────────
    await db.execute(
        """INSERT INTO ledger
           (id, transaction_type, from_agent, to_agent, amount, currency, auction_id, description, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            _lid(), "payment",
            row["payer_id"], row["payee_id"],
            net, "USDC", auction_id,
            f"Auto-released after {DISPUTE_WINDOW_MINUTES}m dispute window | on_chain={release_tx is not None}",
            now,
        ),
    )

    if commission > 0:
        await db.execute(
            """INSERT INTO ledger
               (id, transaction_type, from_agent, to_agent, amount, currency, auction_id, description, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                _lid(), "commission",
                row["payee_id"], "velun_protocol",
                commission, "USDC", auction_id,
                "Protocol commission on auto-release",
                now,
            ),
        )

        # Protocol revenue record
        await db.execute(
            """INSERT INTO protocol_revenue (id, source, amount, currency, auction_id, simulated, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                f"velun_revenue_{uuid.uuid4().hex[:12]}",
                "commission", commission, "USDC",
                auction_id, release_tx is None, now,
            ),
        )

    await db.commit()

    from core.telegram_notifier import notify_payment_released
    await notify_payment_released(auction_id, row["amount"], row["payee_id"], commission)
