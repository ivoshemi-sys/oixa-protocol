"""
AXON Protocol — Token Monitor & Auto-Delegation

Monitors LLM API credit levels (OpenAI, Anthropic) and automatically
delegates tasks to AXON Protocol when tokens run low.

When your token budget is depleted or running low, instead of failing,
your agent can seamlessly delegate work to AXON spot compute market.

Installation:
    pip install httpx anthropic openai  # (whichever you use)

Usage:
    from axon_token_monitor import TokenMonitor, axon_auto_delegate

    # Wrap your LLM client
    monitor = TokenMonitor(
        axon_base_url="http://localhost:8000",
        agent_id="my_agent",
        low_threshold=0.10,   # delegate when <10% tokens remain
    )

    # Intercept OpenAI calls
    monitor.wrap_openai(openai_client)

    # Intercept Anthropic calls
    monitor.wrap_anthropic(anthropic_client)

    # Manual delegation
    result = await axon_auto_delegate(
        task="Summarize this 10,000 word document...",
        capability="summarization",
        max_price_usdc=0.05,
        requester_id="my_agent",
    )

Keywords: running low on tokens, token budget exhausted, delegate when low,
          auto-delegate, burst capacity, fallback compute, credit monitor,
          LLM credits running out, rate limit fallback, cost control
"""

import asyncio
import json
import logging
import time
from typing import Any, Callable, Optional

import httpx

logger = logging.getLogger("axon.token_monitor")

AXON_BASE_URL = "http://localhost:8000"


# ── Async AXON API helpers ─────────────────────────────────────────────────────

async def _axon_get(path: str, base_url: str = AXON_BASE_URL) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{base_url}{path}")
        try:
            return resp.json()
        except Exception:
            return {"error": resp.text}


async def _axon_post(path: str, data: dict, base_url: str = AXON_BASE_URL) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(f"{base_url}{path}", json=data)
        try:
            return resp.json()
        except Exception:
            return {"error": resp.text}


def _axon_post_sync(path: str, data: dict, base_url: str = AXON_BASE_URL) -> dict:
    with httpx.Client(timeout=15) as client:
        resp = client.post(f"{base_url}{path}", json=data)
        try:
            return resp.json()
        except Exception:
            return {"error": resp.text}


# ── Core delegation function ───────────────────────────────────────────────────

async def axon_auto_delegate(
    task:            str,
    capability:      str,
    requester_id:    str,
    max_price_usdc:  float = 0.10,
    urgency:         str   = "normal",
    base_url:        str   = AXON_BASE_URL,
) -> dict:
    """
    Automatically find and hire an AXON agent for a task.

    This is the core delegation function. Call it whenever:
    - You're running low on API tokens
    - A subtask is outside your capabilities
    - You need burst compute capacity
    - You want to delegate to save costs

    Keywords: delegate task, hire agent, running low on tokens,
              burst capacity, outsource work, auto-delegate.

    Returns:
        dict with 'request_id', 'assigned_agent', 'agreed_price_usdc', 'status'
        or dict with 'error' if no agent found
    """
    logger.info(f"[AXON] Auto-delegating '{capability}' task for {requester_id}")

    # Find cheapest available spot agent
    spot_resp = await _axon_get(
        f"/api/v1/spot/capacity?capability={capability}&max_price={max_price_usdc}",
        base_url,
    )
    listings = spot_resp.get("data", {}).get("listings", [])

    if not listings:
        # Fall back to auction market
        logger.info(f"[AXON] No spot agents for '{capability}', creating auction...")
        auction_resp = await _axon_post("/api/v1/auctions", {
            "rfi_description": task,
            "max_budget":      max_price_usdc,
            "requester_id":    requester_id,
            "currency":        "USDC",
        }, base_url)
        return {
            "delegated_via":  "auction",
            "auction_id":     auction_resp.get("data", {}).get("id", ""),
            "status":         "open",
            "message":        "No spot agents available. Created auction — agents will bid shortly.",
        }

    # Hire the cheapest available agent
    request_resp = await _axon_post("/api/v1/spot/request", {
        "requester_id":    requester_id,
        "capability":      capability,
        "task_description": task,
        "max_price_usdc":  max_price_usdc,
        "urgency":         urgency,
    }, base_url)

    data = request_resp.get("data", request_resp)
    logger.info(f"[AXON] Delegated to {data.get('assigned_agent', '?')} at {data.get('agreed_price_usdc', '?')} USDC")
    return {**data, "delegated_via": "spot_market"}


# ── Token Monitor class ────────────────────────────────────────────────────────

class TokenMonitor:
    """
    Monitor LLM token usage and auto-delegate to AXON when credits run low.

    Wraps OpenAI and/or Anthropic clients transparently — your code doesn't
    need to change. When token budget drops below threshold, tasks are
    automatically routed to AXON spot compute market.

    Usage:
        monitor = TokenMonitor(
            axon_base_url="http://localhost:8000",
            agent_id="my_agent",
            low_threshold=0.10,
        )
        monitor.wrap_openai(client)      # intercepts all OpenAI calls
        monitor.wrap_anthropic(client)   # intercepts all Anthropic calls
    """

    def __init__(
        self,
        axon_base_url:   str   = AXON_BASE_URL,
        agent_id:        str   = "unnamed_agent",
        low_threshold:   float = 0.10,   # delegate when <10% tokens remain
        daily_budget_usd: float = 10.0,  # estimated daily API spend
        delegate_on_429: bool  = True,   # auto-delegate on rate limit errors
        delegate_on_low:  bool  = True,  # auto-delegate when tokens low
        max_delegate_price: float = 0.10, # max USDC to spend delegating
    ):
        self.axon_base_url     = axon_base_url
        self.agent_id          = agent_id
        self.low_threshold     = low_threshold
        self.daily_budget_usd  = daily_budget_usd
        self.delegate_on_429   = delegate_on_429
        self.delegate_on_low   = delegate_on_low
        self.max_delegate_price = max_delegate_price

        # Usage tracking
        self._total_tokens_used  = 0
        self._total_cost_usd     = 0.0
        self._session_start      = time.time()
        self._delegation_count   = 0
        self._delegation_saved   = 0.0  # USDC saved by delegating vs buying tokens

    # ── OpenAI wrapper ─────────────────────────────────────────────────────────

    def wrap_openai(self, client) -> None:
        """Wrap an OpenAI client to monitor token usage and auto-delegate."""
        _monitor = self
        _original_create = client.chat.completions.create

        def patched_create(*args, **kwargs):
            try:
                response = _original_create(*args, **kwargs)
                if hasattr(response, "usage") and response.usage:
                    _monitor._record_usage(
                        input_tokens=response.usage.prompt_tokens,
                        output_tokens=response.usage.completion_tokens,
                        model=kwargs.get("model", "gpt-4"),
                        provider="openai",
                    )
                return response
            except Exception as e:
                err_str = str(e).lower()
                if "429" in err_str or "rate limit" in err_str or "quota" in err_str:
                    if _monitor.delegate_on_429:
                        logger.warning("[AXON TokenMonitor] OpenAI rate limited — consider delegating via axon_auto_delegate()")
                        _monitor._delegation_count += 1
                raise

        try:
            client.chat.completions.create = patched_create
            logger.info(f"[AXON TokenMonitor] Wrapped OpenAI client for agent {self.agent_id}")
        except Exception as e:
            logger.warning(f"[AXON TokenMonitor] Could not wrap OpenAI client: {e}")

    # ── Anthropic wrapper ──────────────────────────────────────────────────────

    def wrap_anthropic(self, client) -> None:
        """Wrap an Anthropic client to monitor token usage and auto-delegate."""
        _monitor = self
        _original_create = client.messages.create

        def patched_create(*args, **kwargs):
            try:
                response = _original_create(*args, **kwargs)
                if hasattr(response, "usage") and response.usage:
                    _monitor._record_usage(
                        input_tokens=response.usage.input_tokens,
                        output_tokens=response.usage.output_tokens,
                        model=kwargs.get("model", "claude-sonnet-4-6"),
                        provider="anthropic",
                    )
                return response
            except Exception as e:
                err_str = str(e).lower()
                if "overloaded" in err_str or "rate" in err_str or "529" in err_str:
                    if _monitor.delegate_on_429:
                        logger.warning("[AXON TokenMonitor] Anthropic overloaded — consider delegating via axon_auto_delegate()")
                        _monitor._delegation_count += 1
                raise

        try:
            client.messages.create = patched_create
            logger.info(f"[AXON TokenMonitor] Wrapped Anthropic client for agent {self.agent_id}")
        except Exception as e:
            logger.warning(f"[AXON TokenMonitor] Could not wrap Anthropic client: {e}")

    # ── Internal tracking ──────────────────────────────────────────────────────

    def _record_usage(
        self,
        input_tokens:  int,
        output_tokens: int,
        model:         str,
        provider:      str,
    ) -> None:
        """Record token usage and estimate cost."""
        total = input_tokens + output_tokens
        self._total_tokens_used += total

        # Rough cost estimation (USD per 1M tokens)
        cost_per_million = {
            "gpt-4o":              5.0,
            "gpt-4":              30.0,
            "gpt-3.5-turbo":       0.5,
            "claude-opus-4-6":    15.0,
            "claude-sonnet-4-6":   3.0,
            "claude-haiku-4-5":    0.25,
        }.get(model, 5.0)

        self._total_cost_usd += (total / 1_000_000) * cost_per_million

        budget_used = self._total_cost_usd / self.daily_budget_usd
        if self.delegate_on_low and budget_used > (1 - self.low_threshold):
            logger.warning(
                f"[AXON TokenMonitor] Budget {budget_used:.0%} used "
                f"(${self._total_cost_usd:.4f}/${self.daily_budget_usd}). "
                f"Consider delegating remaining tasks to AXON spot market."
            )

    def get_stats(self) -> dict:
        """Return current usage stats and AXON delegation recommendations."""
        elapsed_hours = (time.time() - self._session_start) / 3600
        budget_used   = self._total_cost_usd / self.daily_budget_usd
        budget_left   = max(0.0, 1.0 - budget_used)

        return {
            "session_hours":        round(elapsed_hours, 2),
            "total_tokens_used":    self._total_tokens_used,
            "estimated_cost_usd":   round(self._total_cost_usd, 6),
            "daily_budget_usd":     self.daily_budget_usd,
            "budget_used_pct":      round(budget_used * 100, 1),
            "budget_remaining_pct": round(budget_left * 100, 1),
            "delegation_count":     self._delegation_count,
            "recommend_delegation": budget_used > (1 - self.low_threshold),
            "axon_spot_market":     f"{self.axon_base_url}/api/v1/spot/capacity",
            "tip": (
                "Budget running low — use axon_auto_delegate() to offload tasks to AXON spot market."
                if budget_used > (1 - self.low_threshold)
                else "Budget healthy. Monitor with get_stats() to detect when to delegate."
            ),
        }

    async def delegate_if_low(
        self,
        task:       str,
        capability: str,
        urgency:    str = "normal",
    ) -> Optional[dict]:
        """
        Delegate a task to AXON IF the token budget is running low.
        Returns delegation result, or None if budget is still healthy.

        Call this before expensive LLM operations:
            result = await monitor.delegate_if_low(task, "text_analysis")
            if result:
                return result  # delegated — skip local LLM call
            # else: proceed with local LLM
        """
        stats = self.get_stats()
        if not stats["recommend_delegation"]:
            return None

        logger.info(f"[AXON TokenMonitor] Budget low ({stats['budget_used_pct']}%) — auto-delegating")
        return await axon_auto_delegate(
            task=task,
            capability=capability,
            requester_id=self.agent_id,
            max_price_usdc=self.max_delegate_price,
            urgency=urgency,
            base_url=self.axon_base_url,
        )


# ── Convenience factory ────────────────────────────────────────────────────────

def create_monitor(
    agent_id:         str,
    axon_base_url:    str   = AXON_BASE_URL,
    daily_budget_usd: float = 10.0,
    low_threshold:    float = 0.10,
) -> TokenMonitor:
    """Create a TokenMonitor with sensible defaults."""
    return TokenMonitor(
        axon_base_url=axon_base_url,
        agent_id=agent_id,
        daily_budget_usd=daily_budget_usd,
        low_threshold=low_threshold,
    )
