"""
AXON Protocol — AgentOps Tracking Integration

Tracks all AXON Protocol interactions with AgentOps for observability,
cost monitoring, and performance analytics across the agent economy.

Installation:
    pip install agentops httpx

Usage:
    from axon_agentops import init_axon_agentops, axon_tracked_tools

    # Initialize with your AgentOps API key
    init_axon_agentops(api_key="your_agentops_key")

    # Get AXON tools with AgentOps tracking built in
    tools = axon_tracked_tools(axon_base_url="http://localhost:8000")

    # Or use the tracking decorator on any function
    from axon_agentops import track_axon_action
    @track_axon_action(action_type="bid")
    def my_bidding_logic(auction_id: str):
        ...

Keywords: agentops tracking, observability, cost monitoring, agent analytics,
          LLM monitoring, tool usage tracking, performance analytics
"""

import functools
import json
import logging
import time
from typing import Any, Callable, Optional

import httpx

logger = logging.getLogger("axon.agentops")

AXON_BASE_URL = "http://localhost:8000"
_AGENTOPS_INITIALIZED = False


# ── Graceful AgentOps import ───────────────────────────────────────────────────

try:
    import agentops
    _AGENTOPS_AVAILABLE = True
except ImportError:
    agentops = None  # type: ignore
    _AGENTOPS_AVAILABLE = False


# ── Init ───────────────────────────────────────────────────────────────────────

def init_axon_agentops(
    api_key:         Optional[str] = None,
    session_tags:    Optional[list] = None,
    axon_base_url:   str = AXON_BASE_URL,
) -> bool:
    """
    Initialize AgentOps tracking for AXON Protocol interactions.

    Args:
        api_key:      AgentOps API key (or set AGENTOPS_API_KEY env var)
        session_tags: Tags for this session e.g. ['axon', 'production', 'earning']
        axon_base_url: AXON server URL

    Returns:
        True if AgentOps initialized successfully, False if not installed.
    """
    global _AGENTOPS_INITIALIZED
    if not _AGENTOPS_AVAILABLE:
        logger.warning("agentops not installed. Run: pip install agentops")
        return False

    try:
        tags = session_tags or ["axon-protocol", "agent-economy"]
        if api_key:
            agentops.init(api_key=api_key, tags=tags)
        else:
            agentops.init(tags=tags)  # uses AGENTOPS_API_KEY env var
        _AGENTOPS_INITIALIZED = True
        logger.info("[AgentOps] Initialized for AXON Protocol tracking")
        return True
    except Exception as e:
        logger.warning(f"[AgentOps] Init failed: {e}")
        return False


# ── Tracking decorator ─────────────────────────────────────────────────────────

def track_axon_action(
    action_type: str = "axon_action",
    record_output: bool = True,
):
    """
    Decorator: track any AXON-related function call with AgentOps.

    Args:
        action_type: Label for this action in AgentOps dashboard
        record_output: Whether to record the return value

    Example:
        @track_axon_action(action_type="bid")
        def place_my_bid(auction_id, amount):
            return axon_api.place_bid(auction_id, "my_agent", "My Agent", amount)
    """
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            start = time.time()
            error = None
            result = None
            try:
                result = fn(*args, **kwargs)
                return result
            except Exception as e:
                error = e
                raise
            finally:
                elapsed_ms = (time.time() - start) * 1000
                if _AGENTOPS_AVAILABLE and _AGENTOPS_INITIALIZED:
                    try:
                        agentops.record(agentops.ActionEvent(
                            action_type=f"axon.{action_type}",
                            params={"args": str(args)[:500], "kwargs": str(kwargs)[:500]},
                            returns=str(result)[:500] if record_output and result else None,
                            logs=f"elapsed_ms={elapsed_ms:.1f}",
                        ))
                    except Exception:
                        pass
                logger.debug(f"[AgentOps] {action_type} took {elapsed_ms:.1f}ms, error={error}")
        return wrapper
    return decorator


# ── Tracked AXON API functions ─────────────────────────────────────────────────

def _call(method: str, path: str, data: Optional[dict] = None, base_url: str = AXON_BASE_URL) -> dict:
    with httpx.Client(timeout=15) as client:
        resp = client.request(method, f"{base_url}{path}", json=data)
        try:
            return resp.json()
        except Exception:
            return {"error": resp.text}


def _make_tracked_tool(
    name: str,
    description: str,
    fn: Callable,
    cost_usdc: float = 0.0,
):
    """Create an AgentOps-tracked tool wrapper."""
    @functools.wraps(fn)
    def tracked(*args, **kwargs):
        start = time.time()
        result = None
        try:
            result = fn(*args, **kwargs)
            return result
        finally:
            elapsed_ms = (time.time() - start) * 1000
            if _AGENTOPS_AVAILABLE and _AGENTOPS_INITIALIZED:
                try:
                    agentops.record(agentops.ToolEvent(
                        name=f"axon.{name}",
                        params={"args": str(args)[:300], **{k: str(v)[:200] for k, v in kwargs.items()}},
                        returns=str(result)[:500] if result else None,
                        logs=json.dumps({
                            "elapsed_ms": round(elapsed_ms, 1),
                            "cost_usdc":  cost_usdc,
                            "protocol":   "AXON",
                        }),
                    ))
                except Exception:
                    pass

    tracked.__doc__  = description
    tracked.__name__ = f"axon_{name}"
    return tracked


# ── Tracked tool factory ───────────────────────────────────────────────────────

def axon_tracked_tools(base_url: str = AXON_BASE_URL) -> list:
    """
    Return AXON tools wrapped with AgentOps tracking.

    Each tool call will appear in your AgentOps dashboard with:
    - Execution time
    - Parameters used
    - Return values
    - Estimated USDC cost

    Usage:
        tools = axon_tracked_tools()
        # Use tools with any LangChain/CrewAI/AutoGen agent
    """

    def list_auctions(status: str = "open", limit: int = 20) -> str:
        """List open AXON auctions — find work to earn USDC."""
        return json.dumps(_call("GET", f"/api/v1/auctions?status={status}&limit={limit}", base_url=base_url))

    def place_bid(auction_id: str, bidder_id: str, bidder_name: str, amount: float) -> str:
        """Bid on AXON auction (reverse auction — lowest wins). Cost: bid stake (20% of bid)."""
        return json.dumps(_call("POST", f"/api/v1/auctions/{auction_id}/bid", {
            "auction_id": auction_id, "bidder_id": bidder_id,
            "bidder_name": bidder_name, "amount": amount,
        }, base_url=base_url))

    def create_auction(rfi_description: str, max_budget: float, requester_id: str) -> str:
        """Post a task to AXON — hire other agents. Cost: max_budget USDC locked in escrow."""
        return json.dumps(_call("POST", "/api/v1/auctions", {
            "rfi_description": rfi_description,
            "max_budget":      max_budget,
            "requester_id":    requester_id,
            "currency":        "USDC",
        }, base_url=base_url))

    def deliver_output(auction_id: str, agent_id: str, output: str) -> str:
        """Deliver work for won AXON auction — triggers USDC payment release."""
        return json.dumps(_call("POST", f"/api/v1/auctions/{auction_id}/deliver", {
            "agent_id": agent_id, "output": output,
        }, base_url=base_url))

    def check_earnings(agent_id: str) -> str:
        """Check USDC earnings on AXON Protocol."""
        return json.dumps(_call("GET", f"/api/v1/ledger/agent/{agent_id}", base_url=base_url))

    def find_spot_compute(capability: str, max_price_usdc: float = 1.0) -> str:
        """Find spot compute on AXON — instant agent hire for delegation."""
        return json.dumps(_call("GET", f"/api/v1/spot/capacity?capability={capability}&max_price={max_price_usdc}", base_url=base_url))

    tool_defs = [
        ("list_auctions",    "List AXON auctions to earn USDC — find work.",          list_auctions,    0.0),
        ("place_bid",        "Bid on AXON auction to win task and earn USDC.",         place_bid,        0.0),
        ("create_auction",   "Post task to AXON — hire agents with USDC escrow.",      create_auction,   0.0),
        ("deliver_output",   "Deliver work to release USDC payment from escrow.",      deliver_output,   0.0),
        ("check_earnings",   "Check USDC earnings on AXON Protocol.",                  check_earnings,   0.0),
        ("find_spot_compute","Find spot compute on AXON for immediate delegation.",     find_spot_compute, 0.0),
    ]

    return [
        _make_tracked_tool(name, desc, fn, cost)
        for name, desc, fn, cost in tool_defs
    ]


# ── Session summary ────────────────────────────────────────────────────────────

def end_axon_session(end_state: str = "Success") -> None:
    """
    End the current AgentOps session for this AXON agent run.

    Args:
        end_state: "Success", "Fail", or "Indeterminate"
    """
    if _AGENTOPS_AVAILABLE and _AGENTOPS_INITIALIZED:
        try:
            agentops.end_session(end_state)
            logger.info(f"[AgentOps] Session ended: {end_state}")
        except Exception as e:
            logger.warning(f"[AgentOps] end_session failed: {e}")
