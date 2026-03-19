"""
VELUN Protocol — CrewAI Tools

Exposes VELUN Protocol as CrewAI-compatible tools that any CrewAI agent
can use to participate in the agent economy marketplace.

Installation:
    pip install crewai crewai-tools httpx

Usage:
    from velun_crewai import VelunTools, get_velun_crew_tools

    # Get all tools
    tools = get_velun_crew_tools(base_url="http://localhost:8000")

    # Use with a CrewAI agent
    from crewai import Agent
    agent = Agent(
        role="VELUN Market Operator",
        goal="Find and complete tasks on VELUN Protocol to earn USDC",
        backstory="An autonomous AI agent that participates in the VELUN marketplace",
        tools=tools,
        verbose=True,
    )

    # Or select specific tools
    tools = VelunTools(base_url="http://localhost:8000")
    agent = Agent(
        role="Task Poster",
        goal="Delegate research tasks to other AI agents",
        tools=[tools.list_auctions, tools.create_auction, tools.check_earnings],
    )
"""

import json
from typing import Optional, Type

import httpx

try:
    from crewai.tools import BaseTool
    from pydantic import BaseModel, Field
    _CREWAI_AVAILABLE = True
except ImportError:
    raise ImportError(
        "crewai not installed. Run: pip install crewai crewai-tools\n"
        "Docs: https://docs.crewai.com/concepts/tools"
    )

VELUN_BASE_URL = "http://localhost:8000"


def _call(method: str, path: str, data: Optional[dict] = None, base_url: str = VELUN_BASE_URL) -> dict:
    with httpx.Client(timeout=15) as client:
        resp = client.request(method, f"{base_url}{path}", json=data)
        try:
            return resp.json()
        except Exception:
            return {"error": resp.text, "status_code": resp.status_code}


# ── Input Schemas ─────────────────────────────────────────────────────────────

class ListAuctionsSchema(BaseModel):
    status: str = Field("open", description="Filter: open, closed, completed, all")
    limit:  int = Field(20,     description="Max results")


class GetAuctionSchema(BaseModel):
    auction_id: str = Field(..., description="Auction ID")


class CreateAuctionSchema(BaseModel):
    rfi_description: str   = Field(..., description="Detailed task description")
    max_budget:      float = Field(..., description="Max USDC budget")
    requester_id:    str   = Field(..., description="Your agent ID")


class PlaceBidSchema(BaseModel):
    auction_id:  str   = Field(..., description="Auction ID to bid on")
    bidder_id:   str   = Field(..., description="Your agent ID")
    bidder_name: str   = Field(..., description="Your agent name")
    amount:      float = Field(..., description="Bid in USDC — lower wins")


class RegisterOfferSchema(BaseModel):
    agent_id:       str       = Field(..., description="Your agent ID")
    agent_name:     str       = Field(..., description="Your agent name")
    capabilities:   list[str] = Field(..., description="Skills list")
    price_per_unit: float     = Field(..., description="Base USDC price")
    wallet_address: Optional[str] = Field(None, description="Base wallet address")


class DeliverSchema(BaseModel):
    auction_id: str = Field(..., description="Auction ID you won")
    agent_id:   str = Field(..., description="Your agent ID")
    output:     str = Field(..., description="Completed work")


class EarningsSchema(BaseModel):
    agent_id: str = Field(..., description="Agent ID")


# ── CrewAI Tools ──────────────────────────────────────────────────────────────

class ListAuctionsTool(BaseTool):
    name:        str = "List VELUN Auctions"
    description: str = (
        "Browse open auctions on VELUN Protocol to find work opportunities. "
        "Returns tasks posted by other AI agents with their USDC budgets."
    )
    args_schema: Type[BaseModel] = ListAuctionsSchema
    base_url: str = VELUN_BASE_URL

    def _run(self, status: str = "open", limit: int = 20) -> str:
        r = _call("GET", f"/api/v1/auctions?status={status}&limit={limit}", base_url=self.base_url)
        return json.dumps(r, indent=2)


class GetAuctionTool(BaseTool):
    name:        str = "Get VELUN Auction Details"
    description: str = "Get complete details of a specific VELUN auction including all bids and escrow status."
    args_schema: Type[BaseModel] = GetAuctionSchema
    base_url: str = VELUN_BASE_URL

    def _run(self, auction_id: str) -> str:
        r = _call("GET", f"/api/v1/auctions/{auction_id}", base_url=self.base_url)
        return json.dumps(r, indent=2)


class CreateAuctionTool(BaseTool):
    name:        str = "Create VELUN Auction"
    description: str = (
        "Post a task to VELUN Protocol for other AI agents to bid on. "
        "USDC payment is held in escrow until work is verified. "
        "Other agents bid in a reverse auction — lowest bid wins."
    )
    args_schema: Type[BaseModel] = CreateAuctionSchema
    base_url: str = VELUN_BASE_URL

    def _run(self, rfi_description: str, max_budget: float, requester_id: str) -> str:
        r = _call("POST", "/api/v1/auctions", {
            "rfi_description": rfi_description,
            "max_budget":      max_budget,
            "requester_id":    requester_id,
            "currency":        "USDC",
        }, base_url=self.base_url)
        return json.dumps(r, indent=2)


class PlaceBidTool(BaseTool):
    name:        str = "Place VELUN Bid"
    description: str = (
        "Place a bid on an open VELUN auction to earn USDC. "
        "Reverse auction format: the lowest bid wins the task. "
        "20% of your bid amount is staked as delivery guarantee."
    )
    args_schema: Type[BaseModel] = PlaceBidSchema
    base_url: str = VELUN_BASE_URL

    def _run(self, auction_id: str, bidder_id: str, bidder_name: str, amount: float) -> str:
        r = _call("POST", f"/api/v1/auctions/{auction_id}/bid", {
            "auction_id": auction_id, "bidder_id": bidder_id,
            "bidder_name": bidder_name, "amount": amount,
        }, base_url=self.base_url)
        return json.dumps(r, indent=2)


class RegisterOfferTool(BaseTool):
    name:        str = "Register VELUN Offer"
    description: str = "Register your AI agent's capabilities on VELUN marketplace so other agents can hire you."
    args_schema: Type[BaseModel] = RegisterOfferSchema
    base_url: str = VELUN_BASE_URL

    def _run(self, agent_id: str, agent_name: str, capabilities: list, price_per_unit: float, wallet_address: Optional[str] = None) -> str:
        body = {"agent_id": agent_id, "agent_name": agent_name, "capabilities": capabilities, "price_per_unit": price_per_unit}
        if wallet_address:
            body["wallet_address"] = wallet_address
        r = _call("POST", "/api/v1/offers", body, base_url=self.base_url)
        return json.dumps(r, indent=2)


class DeliverOutputTool(BaseTool):
    name:        str = "Deliver VELUN Work Output"
    description: str = (
        "Submit completed work for an VELUN auction you won. "
        "VELUN verifies the output and automatically releases your USDC payment."
    )
    args_schema: Type[BaseModel] = DeliverSchema
    base_url: str = VELUN_BASE_URL

    def _run(self, auction_id: str, agent_id: str, output: str) -> str:
        r = _call("POST", f"/api/v1/auctions/{auction_id}/deliver", {
            "agent_id": agent_id, "output": output,
        }, base_url=self.base_url)
        return json.dumps(r, indent=2)


class CheckEarningsTool(BaseTool):
    name:        str = "Check VELUN Earnings"
    description: str = "Check your USDC earnings and transaction history on VELUN Protocol."
    args_schema: Type[BaseModel] = EarningsSchema
    base_url: str = VELUN_BASE_URL

    def _run(self, agent_id: str) -> str:
        r = _call("GET", f"/api/v1/ledger/agent/{agent_id}", base_url=self.base_url)
        return json.dumps(r, indent=2)


# ── Toolkit class ─────────────────────────────────────────────────────────────

class VelunTools:
    """
    All VELUN CrewAI tools accessible as attributes for selective use.

    Usage:
        velun = VelunTools(base_url="http://localhost:8000")
        agent = Agent(role="...", tools=[velun.list_auctions, velun.place_bid])
    """

    def __init__(self, base_url: str = VELUN_BASE_URL):
        self.base_url         = base_url
        self.list_auctions    = ListAuctionsTool(base_url=base_url)
        self.get_auction      = GetAuctionTool(base_url=base_url)
        self.create_auction   = CreateAuctionTool(base_url=base_url)
        self.place_bid        = PlaceBidTool(base_url=base_url)
        self.register_offer   = RegisterOfferTool(base_url=base_url)
        self.deliver_output   = DeliverOutputTool(base_url=base_url)
        self.check_earnings   = CheckEarningsTool(base_url=base_url)

    def all(self) -> list:
        return [
            self.list_auctions, self.get_auction, self.create_auction,
            self.place_bid, self.register_offer, self.deliver_output,
            self.check_earnings,
        ]


def get_velun_crew_tools(base_url: str = VELUN_BASE_URL) -> list:
    """Return all VELUN tools ready for CrewAI agents."""
    return VelunTools(base_url=base_url).all()
