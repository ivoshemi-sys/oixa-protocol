"""
Dispute API — POST /disputes, GET /disputes/{id}, etc.
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from database import get_db
from models.dispute import DisputeOpen, DisputeResolve
from config import PROTOCOL_VERSION, DISPUTE_WINDOW_MINUTES, DISPUTE_FEE_RATE

router = APIRouter(tags=["disputes"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ok(data):
    return {
        "success": True,
        "data": data,
        "timestamp": _now(),
        "protocol_version": PROTOCOL_VERSION,
    }


def _err(msg: str, code: str, status: int = 400):
    return JSONResponse(
        status_code=status,
        content={"success": False, "error": msg, "code": code, "timestamp": _now()},
    )


# ── Open dispute ──────────────────────────────────────────────────────────────

@router.post("/disputes")
async def open_dispute(body: DisputeOpen):
    db  = await get_db()
    now = datetime.now(timezone.utc)

    # ── Validate auction ──────────────────────────────────────────────────────
    async with db.execute("SELECT * FROM auctions WHERE id = ?", (body.auction_id,)) as cur:
        auction = await cur.fetchone()
    if not auction:
        return _err("Auction not found", "AUCTION_NOT_FOUND", 404)

    if auction["requester_id"] != body.opened_by:
        return _err(
            "Only the requester of the auction can open a dispute",
            "NOT_REQUESTER",
        )

    # ── Validate verification exists ──────────────────────────────────────────
    async with db.execute(
        "SELECT * FROM verifications WHERE auction_id = ? AND passed = 1 ORDER BY verified_at DESC LIMIT 1",
        (body.auction_id,),
    ) as cur:
        verification = await cur.fetchone()
    if not verification:
        return _err(
            "No successful verification found for this auction. Dispute requires a delivered output.",
            "NO_VERIFICATION",
        )

    # ── Check dispute window ──────────────────────────────────────────────────
    verified_at = datetime.fromisoformat(verification["verified_at"].replace("Z", "+00:00"))
    if verified_at.tzinfo is None:
        verified_at = verified_at.replace(tzinfo=timezone.utc)
    window_expires = verified_at + timedelta(minutes=DISPUTE_WINDOW_MINUTES)

    if now > window_expires:
        return _err(
            f"Dispute window expired. You had {DISPUTE_WINDOW_MINUTES} minutes after delivery "
            f"(expired at {window_expires.isoformat()})",
            "DISPUTE_WINDOW_EXPIRED",
        )

    # ── Check no duplicate dispute ────────────────────────────────────────────
    async with db.execute(
        "SELECT id FROM disputes WHERE auction_id = ?", (body.auction_id,)
    ) as cur:
        existing = await cur.fetchone()
    if existing:
        return _err("A dispute already exists for this auction", "DISPUTE_ALREADY_EXISTS")

    # ── Check escrow is in pending_release (not already released) ────────────
    async with db.execute(
        "SELECT * FROM escrows WHERE auction_id = ? AND status = 'pending_release'",
        (body.auction_id,),
    ) as cur:
        escrow = await cur.fetchone()
    if not escrow:
        return _err(
            "Escrow is not in pending_release status. It may have already been released.",
            "ESCROW_NOT_PENDING",
        )

    # ── Calculate fee ─────────────────────────────────────────────────────────
    fee_amount = round(escrow["amount"] * DISPUTE_FEE_RATE, 6)

    # ── Create dispute ────────────────────────────────────────────────────────
    dispute_id = f"axon_dispute_{uuid.uuid4().hex[:12]}"
    now_str    = now.isoformat()

    await db.execute(
        """INSERT INTO disputes
           (id, auction_id, opened_by, reason, status, fee_amount, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (dispute_id, body.auction_id, body.opened_by, body.reason, "open", fee_amount, now_str),
    )

    # ── Freeze escrow ─────────────────────────────────────────────────────────
    await db.execute(
        "UPDATE escrows SET status = 'frozen' WHERE id = ? AND status = 'pending_release'",
        (escrow["id"],),
    )

    # ── Fee ledger entry ──────────────────────────────────────────────────────
    await db.execute(
        """INSERT INTO ledger
           (id, transaction_type, from_agent, to_agent, amount, currency, auction_id, description, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            f"axon_ledger_{uuid.uuid4().hex[:12]}",
            "dispute_fee",
            body.opened_by,
            "axon_protocol",
            fee_amount,
            "USDC",
            body.auction_id,
            f"Dispute fee (10%) for dispute {dispute_id}",
            now_str,
        ),
    )

    await db.commit()

    # ── Notify Ivan ───────────────────────────────────────────────────────────
    from core.telegram_notifier import notify_dispute_opened
    asyncio.create_task(notify_dispute_opened(dispute_id, body.auction_id, body.opened_by, fee_amount))

    # ── Trigger arbiter async (non-blocking) ──────────────────────────────────
    asyncio.create_task(_run_arbiter(dispute_id))

    window_remaining = int((window_expires - now).total_seconds())

    return _ok(
        {
            "dispute_id":        dispute_id,
            "auction_id":        body.auction_id,
            "status":            "open",
            "fee_amount":        fee_amount,
            "escrow_status":     "frozen",
            "arbiter":           "claude_arbiter_queued",
            "window_expires_at": window_expires.isoformat(),
            "window_remaining_seconds": window_remaining,
            "note":              "Escrow frozen pending arbiter verdict. Claude will evaluate shortly.",
        }
    )


# ── Get dispute ───────────────────────────────────────────────────────────────

@router.get("/disputes/{dispute_id}")
async def get_dispute(dispute_id: str):
    db = await get_db()
    async with db.execute("SELECT * FROM disputes WHERE id = ?", (dispute_id,)) as cur:
        row = await cur.fetchone()
    if not row:
        return _err("Dispute not found", "DISPUTE_NOT_FOUND", 404)

    data = dict(row)
    if data.get("arbiter_verdict"):
        try:
            data["arbiter_verdict"] = json.loads(data["arbiter_verdict"])
        except Exception:
            pass
    return _ok(data)


@router.get("/disputes/auction/{auction_id}")
async def get_auction_dispute(auction_id: str):
    db = await get_db()
    async with db.execute(
        "SELECT * FROM disputes WHERE auction_id = ? ORDER BY created_at DESC LIMIT 1",
        (auction_id,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return _err("No dispute found for this auction", "DISPUTE_NOT_FOUND", 404)

    data = dict(row)
    if data.get("arbiter_verdict"):
        try:
            data["arbiter_verdict"] = json.loads(data["arbiter_verdict"])
        except Exception:
            pass
    return _ok(data)


# ── Manual resolve (internal / admin) ────────────────────────────────────────

@router.post("/disputes/{dispute_id}/resolve")
async def resolve_dispute(dispute_id: str, body: DisputeResolve):
    db  = await get_db()
    now = _now()

    async with db.execute("SELECT * FROM disputes WHERE id = ?", (dispute_id,)) as cur:
        dispute = await cur.fetchone()
    if not dispute:
        return _err("Dispute not found", "DISPUTE_NOT_FOUND", 404)
    if dispute["status"] not in ("open", "resolving"):
        return _err(
            f"Dispute is already in status '{dispute['status']}'",
            "DISPUTE_ALREADY_RESOLVED",
        )
    if body.verdict not in ("requester_wins", "agent_wins"):
        return _err("verdict must be 'requester_wins' or 'agent_wins'", "INVALID_VERDICT")

    async with db.execute("SELECT * FROM auctions WHERE id = ?", (dispute["auction_id"],)) as cur:
        auction = await cur.fetchone()

    verdict_data = {
        "verdict":             body.verdict,
        "confidence":          1.0,
        "reasoning":           body.reasoning,
        "output_quality_score": None,
        "resolved_by":         body.resolved_by,
    }

    result_status = f"resolved_{body.verdict}"
    await db.execute(
        "UPDATE disputes SET status = ?, arbiter_verdict = ?, resolved_at = ? WHERE id = ?",
        (result_status, json.dumps(verdict_data), now, dispute_id),
    )

    from core.arbiter import _apply_verdict
    await _apply_verdict(db, dict(dispute), dict(auction), body.verdict, 0.0, now)
    await db.commit()

    return _ok(
        {
            "dispute_id": dispute_id,
            "status":     result_status,
            "verdict":    body.verdict,
            "reasoning":  body.reasoning,
        }
    )


# ── List all disputes ─────────────────────────────────────────────────────────

@router.get("/disputes")
async def list_disputes(status: str | None = None, page: int = 1, page_size: int = 50):
    db = await get_db()
    offset = (page - 1) * page_size
    if status:
        async with db.execute(
            "SELECT * FROM disputes WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (status, page_size, offset),
        ) as cur:
            rows = await cur.fetchall()
    else:
        async with db.execute(
            "SELECT * FROM disputes ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (page_size, offset),
        ) as cur:
            rows = await cur.fetchall()

    disputes = []
    for row in rows:
        d = dict(row)
        if d.get("arbiter_verdict"):
            try:
                d["arbiter_verdict"] = json.loads(d["arbiter_verdict"])
            except Exception:
                pass
        disputes.append(d)

    return _ok(disputes)


# ── Arbiter background task ───────────────────────────────────────────────────

async def _run_arbiter(dispute_id: str):
    """Async task: call Claude arbiter after a brief delay to ensure DB is committed."""
    await asyncio.sleep(2)
    try:
        from core.arbiter import arbitrate_dispute
        await arbitrate_dispute(dispute_id)
    except Exception as e:
        import logging
        logging.getLogger("axon.disputes").error(
            f"Arbiter task failed for {dispute_id}: {e}", exc_info=True
        )
