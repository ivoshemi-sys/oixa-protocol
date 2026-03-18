"""
Daily spending limit enforcement for AXON Protocol.
Default: $50/day. Alerts Ivan at 80% and 100%.
"""

import logging
import uuid
from datetime import datetime, timezone

from config import DAILY_LIMIT_USD

logger = logging.getLogger(__name__)

# Track whether 80% alert was already sent today to avoid spam
_alerted_80_pct: set[str] = set()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def get_daily_spent(db) -> float:
    """Return total spending recorded for today (UTC)."""
    today = _today()
    async with db.execute(
        "SELECT COALESCE(SUM(amount), 0) as total FROM daily_spending WHERE date = ?",
        (today,),
    ) as cur:
        row = await cur.fetchone()
    return float(row["total"] if row else 0.0)


async def check_limit(amount: float, db) -> None:
    """
    Raise ValueError if adding `amount` would exceed the daily limit.
    Send Telegram alerts at 80% and 100%.
    """
    today    = _today()
    spent    = await get_daily_spent(db)
    new_total = spent + amount

    pct_after = new_total / DAILY_LIMIT_USD if DAILY_LIMIT_USD > 0 else 0.0

    # 80% warning (once per day)
    if pct_after >= 0.80 and today not in _alerted_80_pct:
        _alerted_80_pct.add(today)
        from core.telegram_notifier import notify_daily_limit
        import asyncio
        asyncio.create_task(notify_daily_limit(new_total, DAILY_LIMIT_USD, pct_after))

    if new_total > DAILY_LIMIT_USD:
        # 100% alert
        from core.telegram_notifier import notify_daily_limit
        import asyncio
        asyncio.create_task(notify_daily_limit(new_total, DAILY_LIMIT_USD, 1.0))
        raise ValueError(
            f"Daily spending limit reached (${spent:.2f} spent, limit ${DAILY_LIMIT_USD:.2f})"
        )


async def record_spending(amount: float, auction_id: str, description: str, db) -> None:
    """Record a spending entry for today."""
    today = _today()
    now   = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """INSERT INTO daily_spending (id, date, amount, auction_id, description, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (f"axon_spend_{uuid.uuid4().hex[:12]}", today, amount, auction_id, description, now),
    )
    await db.commit()
    logger.debug(f"[DAILY_LIMIT] Recorded ${amount:.4f} | today_total ~${await get_daily_spent(db):.4f}")
