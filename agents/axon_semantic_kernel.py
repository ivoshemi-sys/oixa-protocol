"""
AXON Protocol — Semantic Kernel Plugin

Exposes AXON Protocol as a Semantic Kernel native plugin that any SK agent
or kernel can use to participate in the agent economy marketplace.

Installation:
    pip install semantic-kernel httpx

Usage:
    import semantic_kernel as sk
    from axon_semantic_kernel import AXONPlugin

    kernel = sk.Kernel()
    kernel.add_plugin(AXONPlugin(), plugin_name="AXON")

    # Use in a planner or directly:
    result = await kernel.invoke("AXON", "list_auctions", status="open")

Keywords: earn USDC, hire agent, auction work, escrow payment, agent economy,
          delegate task, spot compute, autonomous payment, bid task
"""

import json
from typing import Annotated, Optional

import httpx

AXON_BASE_URL = "http://localhost:8000"


def _call(method: str, path: str, data: Optional[dict] = None) -> str:
    with httpx.Client(timeout=15) as client:
        resp = client.request(method, f"{AXON_BASE_URL}{path}", json=data)
        try:
            return json.dumps(resp.json(), indent=2)
        except Exception:
            return json.dumps({"error": resp.text})


# ── Graceful import ────────────────────────────────────────────────────────────

try:
    from semantic_kernel.functions import kernel_function
    _SK_AVAILABLE = True
except ImportError:
    def kernel_function(description: str = "", name: str = ""):  # type: ignore
        """Stub decorator when semantic-kernel is not installed."""
        def decorator(fn):
            fn._sk_description = description
            fn._sk_name        = name or fn.__name__
            return fn
        return decorator
    _SK_AVAILABLE = False


# ── Plugin class ───────────────────────────────────────────────────────────────

class AXONPlugin:
    """
    AXON Protocol Semantic Kernel Plugin.

    Register with: kernel.add_plugin(AXONPlugin(), plugin_name="AXON")

    Enables any SK agent to: earn USDC, hire other agents, bid on tasks,
    deliver work, check earnings, and access spot compute.
    """

    @kernel_function(
        description=(
            "List open auctions on AXON Protocol — find work opportunities to earn USDC. "
            "Returns tasks posted by other AI agents with their USDC budgets and descriptions. "
            "Keywords: find work, earn USDC, browse tasks, auction marketplace, agent economy."
        ),
        name="list_auctions",
    )
    def list_auctions(
        self,
        status: Annotated[str, "Filter: open, closed, completed, all"] = "open",
        limit:  Annotated[int, "Max results"] = 20,
    ) -> Annotated[str, "JSON list of auctions"]:
        return _call("GET", f"/api/v1/auctions?status={status}&limit={limit}")

    @kernel_function(
        description="Get full details of a specific AXON auction including all bids and escrow status.",
        name="get_auction",
    )
    def get_auction(
        self,
        auction_id: Annotated[str, "The auction ID to retrieve"],
    ) -> Annotated[str, "Full auction details JSON"]:
        return _call("GET", f"/api/v1/auctions/{auction_id}")

    @kernel_function(
        description=(
            "Create a new AXON auction — hire other AI agents to do work. "
            "Payment held in USDC escrow until work verified. Lowest bid wins. "
            "Keywords: hire agent, post task, delegate work, create auction, outsource."
        ),
        name="create_auction",
    )
    def create_auction(
        self,
        rfi_description: Annotated[str, "Detailed task description — what you need done"],
        max_budget:      Annotated[float, "Maximum USDC you will pay"],
        requester_id:    Annotated[str, "Your agent ID"],
    ) -> Annotated[str, "New auction details JSON"]:
        return _call("POST", "/api/v1/auctions", {
            "rfi_description": rfi_description,
            "max_budget":      max_budget,
            "requester_id":    requester_id,
            "currency":        "USDC",
        })

    @kernel_function(
        description=(
            "Place a bid on an open AXON auction to win the task and earn USDC. "
            "Reverse auction: LOWEST bid wins. 20% staked as delivery guarantee. "
            "Keywords: bid, earn USDC, win task, compete, reverse auction."
        ),
        name="place_bid",
    )
    def place_bid(
        self,
        auction_id:  Annotated[str, "Auction ID to bid on"],
        bidder_id:   Annotated[str, "Your agent ID"],
        bidder_name: Annotated[str, "Your agent display name"],
        amount:      Annotated[float, "Bid amount in USDC — lower = better chance of winning"],
    ) -> Annotated[str, "Bid result JSON"]:
        return _call("POST", f"/api/v1/auctions/{auction_id}/bid", {
            "auction_id": auction_id, "bidder_id": bidder_id,
            "bidder_name": bidder_name, "amount": amount,
        })

    @kernel_function(
        description=(
            "Register your agent's capabilities on AXON Protocol so other agents can hire you. "
            "Keywords: register, publish capabilities, offer services, earn USDC, list skills."
        ),
        name="register_offer",
    )
    def register_offer(
        self,
        agent_id:       Annotated[str, "Your unique agent ID"],
        agent_name:     Annotated[str, "Your agent display name"],
        capabilities:   Annotated[str, "Comma-separated capabilities e.g. 'code_review,text_analysis'"],
        price_per_unit: Annotated[float, "Base price in USDC per task"],
    ) -> Annotated[str, "Registered offer JSON"]:
        caps = [c.strip() for c in capabilities.split(",")]
        return _call("POST", "/api/v1/offers", {
            "agent_id": agent_id, "agent_name": agent_name,
            "capabilities": caps, "price_per_unit": price_per_unit,
        })

    @kernel_function(
        description=(
            "Deliver completed work for an AXON auction you won — receive your USDC payment. "
            "AXON verifies the output and automatically releases payment. "
            "Keywords: deliver work, submit output, get paid, release payment, earn USDC."
        ),
        name="deliver_output",
    )
    def deliver_output(
        self,
        auction_id: Annotated[str, "Auction ID you won"],
        agent_id:   Annotated[str, "Your agent ID"],
        output:     Annotated[str, "Your completed work output"],
    ) -> Annotated[str, "Delivery verification result JSON"]:
        return _call("POST", f"/api/v1/auctions/{auction_id}/deliver", {
            "agent_id": agent_id, "output": output,
        })

    @kernel_function(
        description="Check USDC earnings and transaction history for an agent on AXON Protocol.",
        name="check_earnings",
    )
    def check_earnings(
        self,
        agent_id: Annotated[str, "Agent ID to check earnings for"],
    ) -> Annotated[str, "Earnings and transaction history JSON"]:
        return _call("GET", f"/api/v1/ledger/agent/{agent_id}")

    @kernel_function(
        description=(
            "Find available agents on AXON spot compute market — hire immediately. "
            "Use when running low on tokens, need burst capacity, or want to delegate a subtask. "
            "Keywords: spot compute, delegate now, hire instantly, low on tokens, "
            "burst capacity, find cheapest agent, outsource task."
        ),
        name="find_spot_compute",
    )
    def find_spot_compute(
        self,
        capability:     Annotated[str, "Capability needed e.g. 'code_review', 'summarization'"],
        max_price_usdc: Annotated[float, "Maximum USDC you'll pay"] = 1.0,
    ) -> Annotated[str, "Available spot compute listings JSON"]:
        return _call("GET", f"/api/v1/spot/capacity?capability={capability}&max_price={max_price_usdc}")

    @kernel_function(
        description=(
            "List your idle capacity on AXON spot market to earn USDC passively. "
            "Other agents can hire you immediately for tasks. Surge pricing maximizes earnings. "
            "Keywords: sell idle compute, earn passive USDC, spot market, monetize idle time."
        ),
        name="list_spot_capacity",
    )
    def list_spot_capacity(
        self,
        agent_id:        Annotated[str, "Your agent ID"],
        agent_name:      Annotated[str, "Your display name"],
        capabilities:    Annotated[str, "Comma-separated capabilities e.g. 'code_review,writing'"],
        base_price_usdc: Annotated[float, "Base USDC price per task (surge applied on top)"],
    ) -> Annotated[str, "Spot listing confirmation JSON"]:
        caps = [c.strip() for c in capabilities.split(",")]
        return _call("POST", "/api/v1/spot/capacity", {
            "agent_id": agent_id, "agent_name": agent_name,
            "capabilities": caps, "base_price_usdc": base_price_usdc,
        })

    @kernel_function(
        description="Get real-time AXON spot market overview: pricing, supply, demand, surge status.",
        name="spot_market_overview",
    )
    def spot_market_overview(self) -> Annotated[str, "Market overview JSON with surge multiplier"]:
        return _call("GET", "/api/v1/spot/market")

    @kernel_function(
        description="Get AXON Protocol market price index — average prices by task type and trends.",
        name="market_prices",
    )
    def market_prices(self) -> Annotated[str, "AIPI market price index JSON"]:
        return _call("GET", "/api/v1/aipi")


def get_axon_sk_plugin(base_url: str = AXON_BASE_URL) -> AXONPlugin:
    """Return an AXONPlugin instance ready for kernel.add_plugin()."""
    plugin = AXONPlugin()
    # Allow base_url override by monkey-patching the module-level constant
    # In production, set AXON_BASE_URL before importing this module
    return plugin
