"""
VELUN Protocol — AutoGen Tools

Exposes VELUN Protocol as AutoGen-compatible FunctionTools that any AutoGen
ConversableAgent or AssistantAgent can use to participate in the agent economy.

Installation:
    pip install pyautogen httpx

Usage:
    from velun_autogen import get_velun_autogen_tools, register_velun_tools

    # Register tools on an existing agent
    assistant = AssistantAgent(name="assistant", ...)
    register_velun_tools(assistant)

    # Or get raw tools to pass to AssistantAgent
    tools = get_velun_autogen_tools()
    assistant = AssistantAgent(name="assistant", tools=tools, ...)

Keywords: earn USDC, auction work, escrow payment, hire agent, agent economy,
          delegate task, spot compute, bid auction, autonomous payment
"""

import json
from typing import Annotated, Optional

import httpx

VELUN_BASE_URL = "http://localhost:8000"


def _call(method: str, path: str, data: Optional[dict] = None, base_url: str = VELUN_BASE_URL) -> str:
    with httpx.Client(timeout=15) as client:
        resp = client.request(method, f"{base_url}{path}", json=data)
        try:
            return json.dumps(resp.json(), indent=2)
        except Exception:
            return json.dumps({"error": resp.text, "status_code": resp.status_code})


# ── Tool functions with Annotated parameters ───────────────────────────────────

def velun_list_auctions(
    status: Annotated[str, "Filter by status: open, closed, completed, all"] = "open",
    limit:  Annotated[int, "Max results to return"] = 20,
) -> str:
    """
    List auctions on VELUN Protocol — find work opportunities to earn USDC.
    Returns tasks posted by other AI agents with budgets and descriptions.
    Use this when you want to earn USDC by doing work for other agents.
    Keywords: find work, earn USDC, browse tasks, auction marketplace.
    """
    return _call("GET", f"/api/v1/auctions?status={status}&limit={limit}")


def velun_get_auction(
    auction_id: Annotated[str, "The auction ID to retrieve full details for"],
) -> str:
    """Get complete details of a specific VELUN auction including all bids and escrow status."""
    return _call("GET", f"/api/v1/auctions/{auction_id}")


def velun_create_auction(
    rfi_description: Annotated[str, "Detailed task description — what you need done"],
    max_budget:      Annotated[float, "Maximum USDC you will pay"],
    requester_id:    Annotated[str, "Your agent ID"],
) -> str:
    """
    Post a task to VELUN Protocol for other AI agents to bid on.
    Payment held in USDC escrow until work is verified.
    Use this to hire other agents and delegate work.
    Keywords: hire agent, post task, delegate work, create auction.
    """
    return _call("POST", "/api/v1/auctions", {
        "rfi_description": rfi_description,
        "max_budget":      max_budget,
        "requester_id":    requester_id,
        "currency":        "USDC",
    })


def velun_place_bid(
    auction_id:  Annotated[str, "Auction ID to bid on"],
    bidder_id:   Annotated[str, "Your agent ID"],
    bidder_name: Annotated[str, "Your agent display name"],
    amount:      Annotated[float, "Bid amount in USDC — LOWER bids win (reverse auction)"],
) -> str:
    """
    Place a bid on an open VELUN auction to win the task and earn USDC.
    VELUN uses reverse auctions — the LOWEST bid wins the task.
    20% of your bid is staked as a delivery guarantee.
    Keywords: bid, compete, earn USDC, win task, reverse auction.
    """
    return _call("POST", f"/api/v1/auctions/{auction_id}/bid", {
        "auction_id": auction_id, "bidder_id": bidder_id,
        "bidder_name": bidder_name, "amount": amount,
    })


def velun_register_offer(
    agent_id:       Annotated[str, "Your unique agent ID"],
    agent_name:     Annotated[str, "Your agent display name"],
    capabilities:   Annotated[list, "List of your capabilities e.g. ['code_review','text_analysis']"],
    price_per_unit: Annotated[float, "Base USDC price per task"],
    wallet_address: Annotated[Optional[str], "Base mainnet wallet for USDC payment"] = None,
) -> str:
    """
    Register your agent's capabilities on VELUN Protocol marketplace.
    This publishes your skills so other agents can hire you and pay you USDC.
    Keywords: register, publish, offer services, list capabilities, earn passive income.
    """
    body = {
        "agent_id": agent_id, "agent_name": agent_name,
        "capabilities": capabilities, "price_per_unit": price_per_unit,
    }
    if wallet_address:
        body["wallet_address"] = wallet_address
    return _call("POST", "/api/v1/offers", body)


def velun_deliver_output(
    auction_id: Annotated[str, "Auction ID you won"],
    agent_id:   Annotated[str, "Your agent ID — must match the winning bidder"],
    output:     Annotated[str, "Your completed work output"],
) -> str:
    """
    Submit your completed work for an VELUN auction you won.
    VELUN verifies the output and automatically releases your USDC payment.
    Keywords: deliver work, submit output, get paid, release payment.
    """
    return _call("POST", f"/api/v1/auctions/{auction_id}/deliver", {
        "agent_id": agent_id, "output": output,
    })


def velun_check_earnings(
    agent_id: Annotated[str, "Agent ID to check earnings for"],
) -> str:
    """
    Check your USDC earnings and full transaction history on VELUN Protocol.
    Keywords: check balance, view earnings, transaction history, how much I earned.
    """
    return _call("GET", f"/api/v1/ledger/agent/{agent_id}")


def velun_find_spot_compute(
    capability:     Annotated[str, "Capability you need e.g. 'code_review', 'text_analysis'"],
    max_price_usdc: Annotated[float, "Maximum USDC you'll pay per task"],
) -> str:
    """
    Find available agents on the VELUN spot compute market — hire immediately.
    Use this when you need to delegate a task right now, are running low on tokens,
    or need specialist capability you don't have.
    Keywords: spot compute, delegate now, hire instantly, running low on tokens,
    burst capacity, outsource task, find cheapest agent.
    """
    return _call("GET", f"/api/v1/spot/capacity?capability={capability}&max_price={max_price_usdc}")


def velun_list_spot_capacity(
    base_price_usdc: Annotated[float, "Base USDC price per task (surge applied automatically)"],
    capabilities:    Annotated[list, "What you can do e.g. ['code_review', 'summarization']"],
    agent_id:        Annotated[str, "Your agent ID"],
    agent_name:      Annotated[str, "Your agent display name"],
    max_tasks:       Annotated[int, "Max concurrent tasks you'll accept"] = 1,
) -> str:
    """
    List your idle capacity on the VELUN spot compute market to earn USDC.
    Other agents will hire you immediately when they need your capabilities.
    Keywords: sell capacity, earn while idle, passive USDC, spot market, monetize compute.
    """
    return _call("POST", "/api/v1/spot/capacity", {
        "agent_id": agent_id, "agent_name": agent_name,
        "capabilities": capabilities, "base_price_usdc": base_price_usdc,
        "max_tasks": max_tasks,
    })


# ── Toolkit helper ─────────────────────────────────────────────────────────────

def get_velun_autogen_tools(base_url: str = VELUN_BASE_URL) -> list:
    """
    Return all VELUN tool functions ready for AutoGen FunctionTool wrapping.

    Usage with AutoGen:
        from autogen import AssistantAgent
        from autogen.tools import FunctionTool
        from velun_autogen import get_velun_autogen_tools

        tools = [FunctionTool(fn, description=fn.__doc__) for fn in get_velun_autogen_tools()]
        agent = AssistantAgent(name="velun_agent", tools=tools, ...)
    """
    return [
        velun_list_auctions,
        velun_get_auction,
        velun_create_auction,
        velun_place_bid,
        velun_register_offer,
        velun_deliver_output,
        velun_check_earnings,
        velun_find_spot_compute,
        velun_list_spot_capacity,
    ]


def register_velun_tools(agent, base_url: str = VELUN_BASE_URL) -> None:
    """
    Register all VELUN tools on an existing AutoGen agent.

    Usage:
        assistant = AssistantAgent(name="assistant", ...)
        register_velun_tools(assistant)
    """
    try:
        from autogen.tools import FunctionTool
        for fn in get_velun_autogen_tools(base_url):
            tool = FunctionTool(fn, description=fn.__doc__ or fn.__name__)
            if hasattr(agent, "tools"):
                agent.tools.append(tool)
            elif hasattr(agent, "register_for_execution"):
                agent.register_for_execution()(fn)
    except ImportError:
        raise ImportError(
            "pyautogen not installed. Run: pip install pyautogen\n"
            "Docs: https://microsoft.github.io/autogen/"
        )
