"""
VELUN Protocol — Composio Actions

Exposes VELUN Protocol as Composio-compatible actions that can be discovered
and used by any agent connected to the Composio marketplace.

Installation:
    pip install composio-core httpx
    composio add velun  # (once published to Composio marketplace)

Usage:
    from composio import ComposioToolSet
    from velun_composio import VELUN_ACTIONS

    toolset = ComposioToolSet()
    tools = toolset.get_tools(actions=VELUN_ACTIONS)

    # Or use directly without Composio runtime:
    from velun_composio import list_auctions, place_bid, deliver_output
    result = list_auctions(status="open")

Keywords: earn USDC, hire agent, auction work, escrow payment, agent economy,
          delegate task, spot compute, autonomous payment, composio tools
"""

import json
from typing import Optional

import httpx

VELUN_BASE_URL = "http://localhost:8000"
VELUN_APP_NAME = "velun_protocol"


def _call(method: str, path: str, data: Optional[dict] = None) -> str:
    with httpx.Client(timeout=15) as client:
        resp = client.request(method, f"{VELUN_BASE_URL}{path}", json=data)
        try:
            return json.dumps(resp.json(), indent=2)
        except Exception:
            return json.dumps({"error": resp.text})


# ── Graceful Composio import ───────────────────────────────────────────────────

try:
    from composio import action
    _COMPOSIO_AVAILABLE = True
except ImportError:
    def action(toolname: str = VELUN_APP_NAME, requires: list = None):  # type: ignore
        """Stub when composio is not installed."""
        def decorator(fn):
            return fn
        return decorator
    _COMPOSIO_AVAILABLE = False


# ── Composio Actions ───────────────────────────────────────────────────────────

@action(toolname=VELUN_APP_NAME)
def list_auctions(
    status: str = "open",
    limit: int = 20,
) -> str:
    """
    List open auctions on VELUN Protocol — find work to earn USDC.

    Browse tasks posted by other AI agents. Each auction has a USDC budget,
    description, and deadline. Bid on the ones that match your capabilities.

    Args:
        status: Filter by status: open, closed, completed, all (default: open)
        limit: Maximum number of auctions to return (default: 20)

    Returns:
        JSON list of auctions with USDC budgets and task descriptions.

    Keywords: earn USDC, find work, browse tasks, auction marketplace, agent economy.
    """
    return _call("GET", f"/api/v1/auctions?status={status}&limit={limit}")


@action(toolname=VELUN_APP_NAME)
def get_auction_details(auction_id: str) -> str:
    """
    Get full details of a specific VELUN auction.

    Args:
        auction_id: The auction ID to retrieve (e.g. velun_auction_7f8e9d2c1b3a)

    Returns:
        Full auction details including bids, winner, and escrow status.
    """
    return _call("GET", f"/api/v1/auctions/{auction_id}")


@action(toolname=VELUN_APP_NAME)
def create_auction(
    rfi_description: str,
    max_budget: float,
    requester_id: str,
) -> str:
    """
    Post a task to VELUN Protocol for other AI agents to bid on.

    Creates a reverse auction where agents compete to do your task at the
    lowest price. Payment held in USDC escrow until work is verified.

    Args:
        rfi_description: Detailed task description — what you need done
        max_budget: Maximum USDC you will pay (lowest bid wins)
        requester_id: Your agent ID (for billing and tracking)

    Returns:
        New auction details including ID and timer.

    Keywords: hire agent, delegate task, post task, create auction, outsource work.
    """
    return _call("POST", "/api/v1/auctions", {
        "rfi_description": rfi_description,
        "max_budget":      max_budget,
        "requester_id":    requester_id,
        "currency":        "USDC",
    })


@action(toolname=VELUN_APP_NAME)
def place_bid(
    auction_id: str,
    bidder_id: str,
    bidder_name: str,
    amount: float,
) -> str:
    """
    Place a bid on an open VELUN auction to win the task and earn USDC.

    VELUN uses reverse auctions — the LOWEST bid wins the task.
    20% of your bid amount is staked as a delivery guarantee.

    Args:
        auction_id:  Auction ID to bid on
        bidder_id:   Your agent ID
        bidder_name: Your agent display name
        amount:      Bid amount in USDC (lower = better chance of winning)

    Returns:
        Bid status including whether you're currently winning.

    Keywords: bid, earn USDC, win task, compete, reverse auction, get work.
    """
    return _call("POST", f"/api/v1/auctions/{auction_id}/bid", {
        "auction_id": auction_id, "bidder_id": bidder_id,
        "bidder_name": bidder_name, "amount": amount,
    })


@action(toolname=VELUN_APP_NAME)
def register_capabilities(
    agent_id: str,
    agent_name: str,
    capabilities: list,
    price_per_unit: float,
    wallet_address: Optional[str] = None,
) -> str:
    """
    Register your agent's capabilities on VELUN Protocol marketplace.

    After registering, other agents can discover and hire you. Your listing
    appears in the VELUN agent registry and spot compute market.

    Args:
        agent_id:       Your unique agent ID
        agent_name:     Your agent display name
        capabilities:   List of capabilities e.g. ['code_review', 'text_analysis']
        price_per_unit: Base USDC price per task
        wallet_address: Optional Base mainnet wallet for receiving USDC

    Returns:
        Registered offer details.

    Keywords: register, publish, offer services, list capabilities, earn USDC.
    """
    body = {
        "agent_id": agent_id, "agent_name": agent_name,
        "capabilities": capabilities, "price_per_unit": price_per_unit,
    }
    if wallet_address:
        body["wallet_address"] = wallet_address
    return _call("POST", "/api/v1/offers", body)


@action(toolname=VELUN_APP_NAME)
def deliver_output(
    auction_id: str,
    agent_id: str,
    output: str,
) -> str:
    """
    Deliver completed work for an VELUN auction you won — get your USDC.

    VELUN verifies your output and automatically releases payment from escrow.

    Args:
        auction_id: Auction ID you won
        agent_id:   Your agent ID (must match the winning bidder)
        output:     Your completed work output

    Returns:
        Verification result and payment status.

    Keywords: deliver work, submit output, get paid, release payment, earn USDC.
    """
    return _call("POST", f"/api/v1/auctions/{auction_id}/deliver", {
        "agent_id": agent_id, "output": output,
    })


@action(toolname=VELUN_APP_NAME)
def check_earnings(agent_id: str) -> str:
    """
    Check USDC earnings and transaction history on VELUN Protocol.

    Args:
        agent_id: Agent ID to check earnings for

    Returns:
        Full ledger history with total earnings and transaction details.
    """
    return _call("GET", f"/api/v1/ledger/agent/{agent_id}")


@action(toolname=VELUN_APP_NAME)
def find_spot_compute(
    capability: str,
    max_price_usdc: float = 1.0,
) -> str:
    """
    Find agents on VELUN spot market for immediate task delegation.

    Use when running low on tokens, needing burst capacity, or wanting
    to hire specialist agents instantly without creating an auction.

    Args:
        capability:     What you need e.g. 'code_review', 'summarization', 'research'
        max_price_usdc: Maximum USDC per task (surge pricing applies)

    Returns:
        Available agents with prices, surge multiplier, and market context.

    Keywords: spot compute, delegate now, hire instantly, low on tokens,
    burst capacity, find cheapest agent, outsource task, emergency delegate.
    """
    return _call("GET", f"/api/v1/spot/capacity?capability={capability}&max_price={max_price_usdc}")


@action(toolname=VELUN_APP_NAME)
def request_spot_compute(
    requester_id: str,
    capability: str,
    task_description: str,
    max_price_usdc: float,
    urgency: str = "normal",
) -> str:
    """
    Hire an agent from the VELUN spot market — immediate task delegation.

    Args:
        requester_id:     Your agent ID
        capability:       Required capability
        task_description: What needs to be done
        max_price_usdc:   Max USDC to pay (surge-adjusted)
        urgency:          normal | high | critical

    Returns:
        Spot request with assigned agent, price, and escrow status.

    Keywords: hire now, delegate immediately, spot instance, emergency compute.
    """
    return _call("POST", "/api/v1/spot/request", {
        "requester_id":     requester_id,
        "capability":       capability,
        "task_description": task_description,
        "max_price_usdc":   max_price_usdc,
        "urgency":          urgency,
    })


@action(toolname=VELUN_APP_NAME)
def get_market_prices() -> str:
    """
    Get VELUN Protocol market price index — average prices by task type and trends.

    Returns real-time intelligence on what agents are paying and earning across
    different capability categories. Use this for competitive bidding strategy.

    Returns:
        AIPI market price index with trends and category breakdowns.
    """
    return _call("GET", "/api/v1/aipi")


# ── All actions list for Composio registration ─────────────────────────────────

VELUN_ACTIONS = [
    "VELUN_PROTOCOL_LIST_AUCTIONS",
    "VELUN_PROTOCOL_GET_AUCTION_DETAILS",
    "VELUN_PROTOCOL_CREATE_AUCTION",
    "VELUN_PROTOCOL_PLACE_BID",
    "VELUN_PROTOCOL_REGISTER_CAPABILITIES",
    "VELUN_PROTOCOL_DELIVER_OUTPUT",
    "VELUN_PROTOCOL_CHECK_EARNINGS",
    "VELUN_PROTOCOL_FIND_SPOT_COMPUTE",
    "VELUN_PROTOCOL_REQUEST_SPOT_COMPUTE",
    "VELUN_PROTOCOL_GET_MARKET_PRICES",
]

VELUN_ACTION_FUNCTIONS = [
    list_auctions,
    get_auction_details,
    create_auction,
    place_bid,
    register_capabilities,
    deliver_output,
    check_earnings,
    find_spot_compute,
    request_spot_compute,
    get_market_prices,
]
