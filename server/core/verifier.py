"""
Output verification.

After a successful verification, the escrow is set to 'pending_release'
instead of being released immediately. The auto-release job releases it
after DISPUTE_WINDOW_MINUTES if no dispute is opened.

If a dispute IS opened, the escrow stays 'frozen' until the arbiter resolves it.
"""

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone, timedelta

from database import get_db
from config import DISPUTE_WINDOW_MINUTES

logger = logging.getLogger("velun.verifier")


async def verify_output(auction_id: str, output: str, agent_id: str) -> dict:
    db  = await get_db()
    now = datetime.now(timezone.utc)
    now_str = now.isoformat()

    output_hash = hashlib.sha256(output.encode()).hexdigest()
    details     = {}
    passed      = True
    fail_reason = None
    auction     = None

    # ── Validation ────────────────────────────────────────────────────────────
    if not output or not output.strip():
        passed      = False
        fail_reason = "Output is empty"

    if passed:
        async with db.execute("SELECT * FROM auctions WHERE id = ?", (auction_id,)) as cur:
            auction = await cur.fetchone()

        if not auction:
            passed      = False
            fail_reason = "Auction not found"
        elif auction["winner_id"] != agent_id:
            passed      = False
            fail_reason = f"Agent {agent_id} is not the winner (winner: {auction['winner_id']})"
        elif auction["status"] not in ("closed", "completed"):
            passed      = False
            fail_reason = f"Auction status is '{auction['status']}', expected 'closed'"

    # ── On success: start dispute window ──────────────────────────────────────
    if passed and auction:
        window_expires = now + timedelta(minutes=DISPUTE_WINDOW_MINUTES)
        details = {
            "output_length":       len(output),
            "output_text":         output,          # stored for arbiter context
            "auction_id":          auction_id,
            "winning_agent":       agent_id,
            "verified_at":         now_str,
            "dispute_window_expires_at": window_expires.isoformat(),
            "dispute_window_minutes":    DISPUTE_WINDOW_MINUTES,
        }

        async with db.execute(
            "SELECT * FROM escrows WHERE auction_id = ? AND status = 'held'", (auction_id,)
        ) as cur:
            escrow = await cur.fetchone()

        if escrow:
            # ── Set escrow to pending_release (NOT released yet) ──────────────
            await db.execute(
                "UPDATE escrows SET status = 'pending_release' WHERE id = ? AND status = 'held'",
                (escrow["id"],),
            )
            details["escrow_status"]      = "pending_release"
            details["pending_release"]    = True
            details["dispute_window_open"] = True

        # ── Update auction status to 'delivered' (not completed yet) ─────────
        await db.execute(
            "UPDATE auctions SET status = 'delivered' WHERE id = ? AND status = 'closed'",
            (auction_id,),
        )

        logger.info(
            f"Verification passed | auction={auction_id} | agent={agent_id} | "
            f"dispute_window_expires={window_expires.isoformat()}"
        )

    else:
        details["fail_reason"] = fail_reason
        logger.warning(f"Verification failed | auction={auction_id} | reason={fail_reason}")

    # ── Save verification record (include full output text for arbiter) ───────
    verify_id = f"velun_verify_{uuid.uuid4().hex[:12]}"
    await db.execute(
        """INSERT INTO verifications (id, auction_id, output_hash, verified_at, passed, details)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (verify_id, auction_id, output_hash, now_str, passed, json.dumps(details)),
    )
    await db.commit()

    return {"passed": passed, "output_hash": output_hash, "details": details}
