"""
VELUN Protocol — Fallback Middleware

When an agent tool or function fails, automatically delegate the task
to VELUN Protocol's spot compute market instead of crashing.

Provides:
- @velun_fallback decorator — wraps any function/tool
- LangChain fallback tool wrapper
- CrewAI fallback tool wrapper
- AutoGen error handler

Installation:
    pip install httpx

Usage:
    from velun_fallback import velun_fallback, VelunFallbackTool

    # Wrap any function — on failure, delegates to VELUN
    @velun_fallback(capability="code_review", requester_id="my_agent", max_price_usdc=0.05)
    async def review_code(code: str) -> str:
        # If this raises, VELUN will handle it
        return my_llm.generate(f"Review: {code}")

    # LangChain: wrap a tool to add VELUN fallback
    from velun_fallback import with_velun_fallback
    safe_tool = with_velun_fallback(my_langchain_tool, capability="analysis", max_price_usdc=0.05)

Keywords: fallback, error recovery, auto-delegate, task recovery, resilience,
          handle failure, retry with agent, graceful degradation, circuit breaker
"""

import asyncio
import functools
import json
import logging
from typing import Any, Callable, Optional

import httpx

logger = logging.getLogger("velun.fallback")

VELUN_BASE_URL = "http://localhost:8000"


# ── Core fallback delegation ───────────────────────────────────────────────────

def _delegate_sync(
    task_description: str,
    capability:       str,
    requester_id:     str,
    max_price_usdc:   float = 0.10,
    base_url:         str   = VELUN_BASE_URL,
) -> dict:
    """Synchronously delegate a failed task to VELUN spot market."""
    with httpx.Client(timeout=15) as client:
        # Try spot market first
        spot = client.post(f"{base_url}/api/v1/spot/request", json={
            "requester_id":     requester_id,
            "capability":       capability,
            "task_description": task_description,
            "max_price_usdc":   max_price_usdc,
            "urgency":          "high",  # failed tasks are urgent
        })
        data = spot.json()
        if data.get("success"):
            return data.get("data", data)

        # Fall back to auction if no spot agents
        auction = client.post(f"{base_url}/api/v1/auctions", json={
            "rfi_description": task_description,
            "max_budget":      max_price_usdc,
            "requester_id":    requester_id,
            "currency":        "USDC",
        })
        return {"delegated_via": "auction", **auction.json().get("data", {})}


async def _delegate_async(
    task_description: str,
    capability:       str,
    requester_id:     str,
    max_price_usdc:   float = 0.10,
    base_url:         str   = VELUN_BASE_URL,
) -> dict:
    """Async version of _delegate_sync."""
    async with httpx.AsyncClient(timeout=15) as client:
        spot = await client.post(f"{base_url}/api/v1/spot/request", json={
            "requester_id":     requester_id,
            "capability":       capability,
            "task_description": task_description,
            "max_price_usdc":   max_price_usdc,
            "urgency":          "high",
        })
        data = spot.json()
        if data.get("success"):
            return data.get("data", data)

        auction = await client.post(f"{base_url}/api/v1/auctions", json={
            "rfi_description": task_description,
            "max_budget":      max_price_usdc,
            "requester_id":    requester_id,
            "currency":        "USDC",
        })
        return {"delegated_via": "auction", **auction.json().get("data", {})}


# ── @velun_fallback decorator ───────────────────────────────────────────────────

def velun_fallback(
    capability:      str,
    requester_id:    str,
    max_price_usdc:  float = 0.10,
    velun_base_url:   str   = VELUN_BASE_URL,
    fallback_message: Optional[str] = None,
):
    """
    Decorator: if the wrapped function fails, delegate to VELUN instead of crashing.

    Args:
        capability:      VELUN capability tag e.g. 'code_review', 'summarization'
        requester_id:    your agent ID (for VELUN billing)
        max_price_usdc:  max USDC to pay for delegation (default 0.10)
        velun_base_url:   VELUN server URL
        fallback_message: override the task description sent to VELUN

    Example:
        @velun_fallback(capability="code_review", requester_id="my_agent")
        async def review_code(code: str) -> str:
            return expensive_llm_call(code)
        # On failure: delegates "Review this code: ..." to VELUN automatically
    """
    def decorator(fn: Callable) -> Callable:
        is_async = asyncio.iscoroutinefunction(fn)

        if is_async:
            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                try:
                    return await fn(*args, **kwargs)
                except Exception as e:
                    logger.warning(
                        f"[VELUN Fallback] {fn.__name__} failed ({type(e).__name__}: {e}). "
                        f"Delegating '{capability}' to VELUN spot market..."
                    )
                    task = fallback_message or _build_task_description(fn, args, kwargs, e)
                    result = await _delegate_async(
                        task_description=task,
                        capability=capability,
                        requester_id=requester_id,
                        max_price_usdc=max_price_usdc,
                        base_url=velun_base_url,
                    )
                    logger.info(f"[VELUN Fallback] Delegated to {result.get('assigned_agent', 'VELUN')}")
                    return {"velun_delegated": True, "result": result}

            return async_wrapper
        else:
            @functools.wraps(fn)
            def sync_wrapper(*args, **kwargs):
                try:
                    return fn(*args, **kwargs)
                except Exception as e:
                    logger.warning(
                        f"[VELUN Fallback] {fn.__name__} failed ({type(e).__name__}: {e}). "
                        f"Delegating '{capability}' to VELUN spot market..."
                    )
                    task = fallback_message or _build_task_description(fn, args, kwargs, e)
                    result = _delegate_sync(
                        task_description=task,
                        capability=capability,
                        requester_id=requester_id,
                        max_price_usdc=max_price_usdc,
                        base_url=velun_base_url,
                    )
                    logger.info(f"[VELUN Fallback] Delegated to {result.get('assigned_agent', 'VELUN')}")
                    return {"velun_delegated": True, "result": result}

            return sync_wrapper
    return decorator


def _build_task_description(fn: Callable, args: tuple, kwargs: dict, error: Exception) -> str:
    """Build a task description from function call context."""
    arg_summary = ", ".join(
        [repr(a)[:200] for a in args] +
        [f"{k}={repr(v)[:200]}" for k, v in kwargs.items()]
    )
    return (
        f"Task: {fn.__name__}({arg_summary})\n"
        f"Original error: {type(error).__name__}: {error}\n"
        f"Please complete this task and return the result."
    )


# ── LangChain fallback wrapper ─────────────────────────────────────────────────

def with_velun_fallback(
    tool,
    capability:     str,
    requester_id:   str,
    max_price_usdc: float = 0.10,
    velun_base_url:  str   = VELUN_BASE_URL,
):
    """
    Wrap a LangChain tool to add VELUN fallback on failure.

    Usage:
        from velun_fallback import with_velun_fallback
        safe_tool = with_velun_fallback(my_tool, capability="analysis", requester_id="agent_1")

        # safe_tool behaves exactly like my_tool but delegates on failure
    """
    try:
        from langchain_core.tools import BaseTool
        from pydantic import Field

        class VELUNFallbackTool(BaseTool):
            name:        str = f"{tool.name}_with_velun_fallback"
            description: str = (
                f"{tool.description} "
                f"(auto-delegates to VELUN spot market on failure — resilient)"
            )
            _inner_tool = tool

            def _run(self, *args, **kwargs) -> str:
                try:
                    return self._inner_tool._run(*args, **kwargs)
                except Exception as e:
                    logger.warning(f"[VELUN Fallback] LangChain tool {tool.name} failed: {e}")
                    task = f"Tool: {tool.name}\nDescription: {tool.description}\nArgs: {args}\nKwargs: {kwargs}"
                    result = _delegate_sync(task, capability, requester_id, max_price_usdc, velun_base_url)
                    return json.dumps({"velun_delegated": True, "result": result})

            async def _arun(self, *args, **kwargs) -> str:
                try:
                    return await self._inner_tool._arun(*args, **kwargs)
                except Exception as e:
                    logger.warning(f"[VELUN Fallback] LangChain tool {tool.name} failed: {e}")
                    task = f"Tool: {tool.name}\nDescription: {tool.description}\nArgs: {args}\nKwargs: {kwargs}"
                    result = await _delegate_async(task, capability, requester_id, max_price_usdc, velun_base_url)
                    return json.dumps({"velun_delegated": True, "result": result})

        return VELUNFallbackTool()

    except ImportError:
        logger.warning("langchain-core not installed — returning unwrapped tool")
        return tool


# ── CrewAI fallback wrapper ────────────────────────────────────────────────────

def with_velun_fallback_crewai(
    tool,
    capability:     str,
    requester_id:   str,
    max_price_usdc: float = 0.10,
    velun_base_url:  str   = VELUN_BASE_URL,
):
    """Wrap a CrewAI tool to add VELUN fallback on failure."""
    try:
        from crewai.tools import BaseTool as CrewBaseTool
        from pydantic import BaseModel

        original_run = tool._run.__func__ if hasattr(tool._run, "__func__") else tool._run

        def resilient_run(self_tool, *args, **kwargs):
            try:
                return original_run(self_tool, *args, **kwargs)
            except Exception as e:
                logger.warning(f"[VELUN Fallback] CrewAI tool {tool.name} failed: {e}")
                task = f"Tool: {tool.name}\nArgs: {args}\nKwargs: {kwargs}"
                result = _delegate_sync(task, capability, requester_id, max_price_usdc, velun_base_url)
                return json.dumps({"velun_delegated": True, "result": result})

        tool._run = functools.partial(resilient_run, tool)
        tool.description = (
            f"{tool.description} "
            f"(resilient: delegates to VELUN on failure)"
        )
        return tool

    except ImportError:
        return tool


# ── AutoGen error handler ──────────────────────────────────────────────────────

class VELUNFallbackHandler:
    """
    AutoGen error handler that delegates failed tasks to VELUN.

    Usage:
        from velun_fallback import VELUNFallbackHandler
        handler = VELUNFallbackHandler(capability="analysis", requester_id="agent_1")

        # Register with agent
        agent.register_reply(
            trigger=Exception,
            reply_func=handler.handle_error,
        )
    """

    def __init__(
        self,
        capability:     str,
        requester_id:   str,
        max_price_usdc: float = 0.10,
        velun_base_url:  str   = VELUN_BASE_URL,
    ):
        self.capability      = capability
        self.requester_id    = requester_id
        self.max_price_usdc  = max_price_usdc
        self.velun_base_url   = velun_base_url

    def handle_error(self, agent, messages, sender, config) -> tuple:
        """AutoGen reply function — delegates on error."""
        last_msg = messages[-1]["content"] if messages else "Unknown task"
        result = _delegate_sync(
            task_description=last_msg,
            capability=self.capability,
            requester_id=self.requester_id,
            max_price_usdc=self.max_price_usdc,
            base_url=self.velun_base_url,
        )
        response = (
            f"Task delegated to VELUN Protocol.\n"
            f"Assigned agent: {result.get('assigned_agent', 'pending')}\n"
            f"Request ID: {result.get('request_id', result.get('auction_id', 'unknown'))}\n"
            f"Price: {result.get('agreed_price_usdc', '?')} USDC"
        )
        return True, response
