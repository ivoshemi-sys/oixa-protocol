"""
Claude Arbiter — evaluates disputes between agents using the Anthropic API.

Called automatically when a dispute is opened.
If ANTHROPIC_API_KEY is not set, the dispute stays in 'open' status
and must be resolved manually via POST /api/v1/disputes/{id}/resolve.
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

from config import ANTHROPIC_API_KEY, ARBITER_MODEL, ARBITER_MAX_TOKENS

logger = logging.getLogger("axon.arbiter")

# Cost estimates per model (USD per 1M tokens, approximate)
_MODEL_COSTS = {
    "claude-opus-4-6":    {"input": 15.0,  "output": 75.0},
    "claude-sonnet-4-6":  {"input": 3.0,   "output": 15.0},
    "claude-haiku-4-5-20251001": {"input": 0.25,  "output": 1.25},
}

ARBITER_PROMPT = """\
You are an impartial arbiter for AXON Protocol, an autonomous agent marketplace.
Your task: determine whether a delivered AI output satisfactorily fulfills the original task requirements.

## Original Task (RFI — Request for Intelligence)
{rfi_description}

## Maximum Budget
{max_budget} USDC

## Winning Bid
{winning_bid} USDC

## Delivered Output
{output}

## Dispute Reason (filed by requester)
{reason}

## Instructions
Evaluate:
1. Does the output directly address the RFI's core requirements?
2. Is the output substantive, accurate, and actionable — not empty, vague, or off-topic?
3. Is the requester's dispute reason valid given the actual output?

Rules:
- Minor imperfections or style issues → rule for the AGENT (agent_wins)
- Output clearly fails to address requirements → rule for the REQUESTER (requester_wins)
- Partial output that covers the main points → rule for the AGENT (agent_wins)
- Empty, copied-from-prompt, or completely wrong output → rule for the REQUESTER (requester_wins)

Respond ONLY with valid JSON in this exact format (no markdown, no extra text):
{{
  "verdict": "agent_wins" or "requester_wins",
  "confidence": 0.0 to 1.0,
  "reasoning": "2-3 sentence explanation of your verdict",
  "output_quality_score": 0 to 10
}}
"""


async def arbitrate_dispute(dispute_id: str) -> dict:
    """
    Load dispute context, call Claude, apply verdict.
    Returns the full verdict dict.
    Raises on unrecoverable errors (API key missing, etc.).
    """
    from database import get_db

    if not ANTHROPIC_API_KEY:
        logger.warning(
            f"[ARBITER] ANTHROPIC_API_KEY not set — dispute {dispute_id} left in 'open' status. "
            "Resolve manually via POST /api/v1/disputes/{id}/resolve"
        )
        return {"skipped": True, "reason": "ANTHROPIC_API_KEY not configured"}

    db  = await get_db()
    now = datetime.now(timezone.utc).isoformat()

    # ── Load dispute ──────────────────────────────────────────────────────────
    async with db.execute("SELECT * FROM disputes WHERE id = ?", (dispute_id,)) as cur:
        dispute = await cur.fetchone()
    if not dispute:
        raise ValueError(f"Dispute {dispute_id} not found")
    if dispute["status"] != "open":
        logger.info(f"[ARBITER] Dispute {dispute_id} already in status={dispute['status']}, skipping")
        return {"skipped": True, "reason": "already resolved"}

    auction_id = dispute["auction_id"]

    # ── Load auction ──────────────────────────────────────────────────────────
    async with db.execute("SELECT * FROM auctions WHERE id = ?", (auction_id,)) as cur:
        auction = await cur.fetchone()
    if not auction:
        raise ValueError(f"Auction {auction_id} not found")

    # ── Load delivered output from verifications ──────────────────────────────
    async with db.execute(
        "SELECT * FROM verifications WHERE auction_id = ? AND passed = 1 ORDER BY verified_at DESC LIMIT 1",
        (auction_id,),
    ) as cur:
        verification = await cur.fetchone()

    delivered_output = "[Output not found in verification records]"
    if verification and verification.get("details"):
        try:
            details = json.loads(verification["details"])
            delivered_output = details.get("output_text", delivered_output)
        except Exception:
            pass

    # ── Mark as resolving ─────────────────────────────────────────────────────
    await db.execute(
        "UPDATE disputes SET status = 'resolving' WHERE id = ?", (dispute_id,)
    )
    await db.commit()

    # ── Call Claude ───────────────────────────────────────────────────────────
    prompt = ARBITER_PROMPT.format(
        rfi_description = auction["rfi_description"],
        max_budget      = auction["max_budget"],
        winning_bid     = auction.get("winning_bid") or auction["max_budget"],
        output          = delivered_output[:4000],  # cap to avoid huge prompts
        reason          = dispute["reason"],
    )

    logger.info(f"[ARBITER] Calling {ARBITER_MODEL} for dispute {dispute_id}...")

    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        response = await asyncio.wait_for(
            client.messages.create(
                model      = ARBITER_MODEL,
                max_tokens = ARBITER_MAX_TOKENS,
                messages   = [{"role": "user", "content": prompt}],
            ),
            timeout=60,
        )
    except asyncio.TimeoutError:
        logger.error(f"[ARBITER] Timeout calling Claude for dispute {dispute_id}")
        await db.execute("UPDATE disputes SET status = 'open' WHERE id = ?", (dispute_id,))
        await db.commit()
        raise
    except Exception as e:
        logger.error(f"[ARBITER] Claude API error: {e}")
        await db.execute("UPDATE disputes SET status = 'open' WHERE id = ?", (dispute_id,))
        await db.commit()
        raise

    # ── Parse response ────────────────────────────────────────────────────────
    raw_text = response.content[0].text.strip()
    logger.debug(f"[ARBITER] Raw response: {raw_text[:300]}")

    try:
        # Strip markdown fences if present
        clean = raw_text.replace("```json", "").replace("```", "").strip()
        verdict_data = json.loads(clean)
        verdict = verdict_data.get("verdict", "").lower()
        if verdict not in ("agent_wins", "requester_wins"):
            raise ValueError(f"Invalid verdict: {verdict}")
    except Exception as e:
        logger.error(f"[ARBITER] Failed to parse Claude response: {e} | raw: {raw_text[:200]}")
        # Fallback: agent wins (benefit of the doubt, output passed verification)
        verdict_data = {
            "verdict": "agent_wins",
            "confidence": 0.5,
            "reasoning": "Arbiter response could not be parsed. Defaulting to agent_wins per protocol rules.",
            "output_quality_score": 5,
            "parse_error": str(e),
        }
        verdict = "agent_wins"

    # ── Estimate cost ─────────────────────────────────────────────────────────
    input_tokens  = response.usage.input_tokens
    output_tokens = response.usage.output_tokens
    costs = _MODEL_COSTS.get(ARBITER_MODEL, {"input": 5.0, "output": 25.0})
    arbiter_cost_usdc = (
        input_tokens  / 1_000_000 * costs["input"] +
        output_tokens / 1_000_000 * costs["output"]
    )

    logger.info(
        f"[ARBITER] Verdict for {dispute_id}: {verdict} | "
        f"confidence={verdict_data.get('confidence', '?')} | "
        f"cost=${arbiter_cost_usdc:.5f} | tokens={input_tokens}in/{output_tokens}out"
    )

    # ── Apply verdict ─────────────────────────────────────────────────────────
    result_status = f"resolved_{verdict}"
    await db.execute(
        "UPDATE disputes SET status = ?, arbiter_verdict = ?, arbiter_cost_usdc = ?, resolved_at = ? WHERE id = ?",
        (result_status, json.dumps(verdict_data), arbiter_cost_usdc, now, dispute_id),
    )

    await _apply_verdict(db, dispute, auction, verdict, arbiter_cost_usdc, now)
    await db.commit()

    logger.info(f"[ARBITER] Dispute {dispute_id} resolved: {result_status}")

    from core.telegram_notifier import notify_dispute_resolved
    await notify_dispute_resolved(dispute_id, verdict, float(verdict_data.get("confidence", 0.0)))

    return {"verdict": verdict, "verdict_data": verdict_data, "arbiter_cost_usdc": arbiter_cost_usdc}


async def _apply_verdict(db, dispute, auction, verdict: str, arbiter_cost: float, now: str):
    """Apply financial consequences of the arbiter's verdict."""
    auction_id  = dispute["auction_id"]
    fee_amount  = dispute["fee_amount"]
    winner_id   = auction["winner_id"]
    requester   = dispute["opened_by"]

    async with db.execute(
        "SELECT * FROM escrows WHERE auction_id = ? AND status = 'frozen'", (auction_id,)
    ) as cur:
        escrow = await cur.fetchone()

    if not escrow:
        logger.warning(f"[ARBITER] No frozen escrow for {auction_id}")
        return

    commission  = escrow["commission"]
    net_payment = escrow["amount"] - commission

    def _lid():
        return f"axon_ledger_{uuid.uuid4().hex[:12]}"

    if verdict == "agent_wins":
        # Agent wins: full payment released, fee given to agent as compensation
        await db.execute(
            "UPDATE escrows SET status = 'released', released_at = ? WHERE id = ?",
            (now, escrow["id"]),
        )
        await db.execute(
            "UPDATE auctions SET status = 'completed', completed_at = ? WHERE id = ?",
            (now, auction_id),
        )
        # Net payment to agent
        await db.execute(
            """INSERT INTO ledger (id, transaction_type, from_agent, to_agent, amount, currency, auction_id, description, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (_lid(), "payment", requester, winner_id, net_payment, "USDC", auction_id,
             f"Payment released after dispute resolved: agent_wins", now),
        )
        # Commission to protocol
        if commission > 0:
            await db.execute(
                """INSERT INTO ledger (id, transaction_type, from_agent, to_agent, amount, currency, auction_id, description, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (_lid(), "commission", winner_id, "axon_protocol", commission, "USDC", auction_id,
                 "Protocol commission after dispute: agent_wins", now),
            )
        # Dispute fee → agent (compensation for unfounded dispute)
        fee_net = fee_amount - arbiter_cost
        if fee_net > 0:
            await db.execute(
                """INSERT INTO ledger (id, transaction_type, from_agent, to_agent, amount, currency, auction_id, description, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (_lid(), "dispute_fee", requester, winner_id, fee_net, "USDC", auction_id,
                 "Dispute fee → agent (unfounded dispute)", now),
            )
        # Arbiter cost → protocol
        if arbiter_cost > 0:
            await db.execute(
                """INSERT INTO ledger (id, transaction_type, from_agent, to_agent, amount, currency, auction_id, description, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (_lid(), "arbiter_cost", "axon_protocol", "axon_protocol", arbiter_cost, "USDC", auction_id,
                 "Claude arbiter call cost", now),
            )
        # Slash agent stake (losing bidders already marked refunded; winner's stake released)
        logger.info(f"[ARBITER] agent_wins: payment {net_payment:.4f} USDC released to {winner_id}")

    else:  # requester_wins
        # Requester wins: escrow refunded, agent loses stake, fee returned to requester
        await db.execute(
            "UPDATE escrows SET status = 'refunded', released_at = ? WHERE id = ?",
            (now, escrow["id"]),
        )
        await db.execute(
            "UPDATE auctions SET status = 'cancelled', completed_at = ? WHERE id = ?",
            (now, auction_id),
        )
        # Refund to requester
        await db.execute(
            """INSERT INTO ledger (id, transaction_type, from_agent, to_agent, amount, currency, auction_id, description, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (_lid(), "refund", "axon_protocol", requester, escrow["amount"], "USDC", auction_id,
             "Escrow refunded after dispute: requester_wins", now),
        )
        # Agent stake slashed
        from config import STAKE_PERCENTAGE
        async with db.execute(
            "SELECT stake_amount FROM bids WHERE auction_id = ? AND bidder_id = ? AND status = 'winner'",
            (auction_id, winner_id),
        ) as cur:
            bid_row = await cur.fetchone()
        stake = bid_row["stake_amount"] if bid_row else escrow["amount"] * STAKE_PERCENTAGE
        await db.execute(
            """INSERT INTO ledger (id, transaction_type, from_agent, to_agent, amount, currency, auction_id, description, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (_lid(), "slash", winner_id, "axon_protocol", stake, "USDC", auction_id,
             "Stake slashed after dispute: requester_wins", now),
        )
        await db.execute(
            "UPDATE bids SET status = 'slashed' WHERE auction_id = ? AND bidder_id = ? AND status = 'winner'",
            (auction_id, winner_id),
        )
        # Dispute fee → requester net of arbiter cost
        fee_net = fee_amount - arbiter_cost
        if fee_net > 0:
            await db.execute(
                """INSERT INTO ledger (id, transaction_type, from_agent, to_agent, amount, currency, auction_id, description, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (_lid(), "dispute_fee_return", "axon_protocol", requester, fee_net, "USDC", auction_id,
                 "Dispute fee returned to requester (requester_wins)", now),
            )
        logger.info(f"[ARBITER] requester_wins: escrow refunded {escrow['amount']:.4f} USDC to {requester}")

    # Revenue record for arbiter cost
    if arbiter_cost > 0:
        await db.execute(
            """INSERT INTO protocol_revenue (id, source, amount, currency, auction_id, simulated, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (f"axon_revenue_{uuid.uuid4().hex[:12]}", "arbiter_cost",
             arbiter_cost, "USDC", auction_id, True, now),
        )
