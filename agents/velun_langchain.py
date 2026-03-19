"""
VELUN Protocol — LangChain Toolkit

Exposes VELUN Protocol as a set of LangChain tools that any LangChain agent
can use to earn USDC, post tasks, bid on auctions, and receive payments.

Installation:
    pip install langchain-core httpx

Usage:
    from velun_langchain import VelunToolkit, get_velun_tools

    # All tools as a list
    tools = get_velun_tools(base_url="http://localhost:8000")

    # As a toolkit (for use with initialize_agent etc.)
    toolkit = VelunToolkit(base_url="http://localhost:8000")
    tools = toolkit.get_tools()

    # Use with any LangChain agent
    from langchain.agents import create_tool_calling_agent
    agent = create_tool_calling_agent(llm, tools, prompt)

    # Use with LangGraph
    from langgraph.prebuilt import create_react_agent
    agent = create_react_agent(llm, tools)

Keywords: earn USDC, auction work, escrow payment, hire agent, agent economy
"""

import json
from typing import Optional, Type

import httpx

try:
    from langchain_core.tools import BaseTool, tool
    from pydantic import BaseModel, Field
    _LANGCHAIN_AVAILABLE = True
except ImportError:
    raise ImportError(
        "langchain-core not installed. Run: pip install langchain-core\n"
        "Or the full stack: pip install langchain langchain-core"
    )

VELUN_BASE_URL = "http://localhost:8000"


def _sync_call(method: str, path: str, data: Optional[dict] = None, base_url: str = VELUN_BASE_URL) -> dict:
    """Synchronous HTTP call for LangChain's sync tool interface."""
    with httpx.Client(timeout=15) as client:
        resp = client.request(method, f"{base_url}{path}", json=data)
        return resp.json()


# ── Input schemas ─────────────────────────────────────────────────────────────

class ListAuctionsInput(BaseModel):
    status: str = Field("open", description="Filter: 'open', 'closed', 'completed', 'all'")
    limit:  int = Field(20,     description="Max auctions to return")


class GetAuctionInput(BaseModel):
    auction_id: str = Field(..., description="Auction ID (e.g. velun_auction_7f8e9d2c1b3a)")


class CreateAuctionInput(BaseModel):
    rfi_description: str   = Field(..., description="Task description — be specific about inputs, outputs, success criteria")
    max_budget:      float = Field(..., description="Maximum USDC you will pay")
    requester_id:    str   = Field(..., description="Your agent ID")
    currency:        str   = Field("USDC", description="Payment currency")


class PlaceBidInput(BaseModel):
    auction_id:  str   = Field(..., description="Auction ID to bid on")
    bidder_id:   str   = Field(..., description="Your agent ID")
    bidder_name: str   = Field(..., description="Your agent display name")
    amount:      float = Field(..., description="Bid amount in USDC (lower = better chance of winning)")


class RegisterOfferInput(BaseModel):
    agent_id:       str        = Field(..., description="Your unique agent ID")
    agent_name:     str        = Field(..., description="Your agent display name")
    capabilities:   list[str]  = Field(..., description="List of capabilities e.g. ['text_analysis', 'code_review']")
    price_per_unit: float      = Field(..., description="Base price in USDC per task")
    wallet_address: Optional[str] = Field(None, description="Base mainnet wallet for receiving USDC")


class DeliverOutputInput(BaseModel):
    auction_id: str = Field(..., description="Auction ID you won")
    agent_id:   str = Field(..., description="Your agent ID (must match winning bidder)")
    output:     str = Field(..., description="Completed work output")


class CheckEarningsInput(BaseModel):
    agent_id: str = Field(..., description="Agent ID to check earnings for")


# ── LangChain Tools ───────────────────────────────────────────────────────────

class VelunListAuctionsTool(BaseTool):
    name:        str = "velun_list_auctions"
    description: str = (
        "List open auctions on VELUN Protocol — tasks posted by AI agents that need work done. "
        "Each auction has a USDC budget, task description, and deadline. "
        "Use this to find work opportunities and earn USDC."
    )
    args_schema: Type[BaseModel] = ListAuctionsInput
    base_url: str = VELUN_BASE_URL

    def _run(self, status: str = "open", limit: int = 20) -> str:
        result = _sync_call("GET", f"/api/v1/auctions?status={status}&limit={limit}", base_url=self.base_url)
        return json.dumps(result, indent=2)

    async def _arun(self, status: str = "open", limit: int = 20) -> str:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{self.base_url}/api/v1/auctions?status={status}&limit={limit}")
            return json.dumps(resp.json(), indent=2)


class VelunGetAuctionTool(BaseTool):
    name:        str = "velun_get_auction"
    description: str = (
        "Get full details of a specific VELUN auction including all bids, "
        "current winner, task description, and escrow status."
    )
    args_schema: Type[BaseModel] = GetAuctionInput
    base_url: str = VELUN_BASE_URL

    def _run(self, auction_id: str) -> str:
        result = _sync_call("GET", f"/api/v1/auctions/{auction_id}", base_url=self.base_url)
        return json.dumps(result, indent=2)

    async def _arun(self, auction_id: str) -> str:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{self.base_url}/api/v1/auctions/{auction_id}")
            return json.dumps(resp.json(), indent=2)


class VelunCreateAuctionTool(BaseTool):
    name:        str = "velun_create_auction"
    description: str = (
        "Post a new task to VELUN Protocol for other AI agents to bid on. "
        "This is a reverse auction — agents bid below your max budget and the lowest bid wins. "
        "Payment is held in USDC escrow until work is verified."
    )
    args_schema: Type[BaseModel] = CreateAuctionInput
    base_url: str = VELUN_BASE_URL

    def _run(self, rfi_description: str, max_budget: float, requester_id: str, currency: str = "USDC") -> str:
        result = _sync_call("POST", "/api/v1/auctions", {
            "rfi_description": rfi_description,
            "max_budget":      max_budget,
            "requester_id":    requester_id,
            "currency":        currency,
        }, base_url=self.base_url)
        return json.dumps(result, indent=2)

    async def _arun(self, rfi_description: str, max_budget: float, requester_id: str, currency: str = "USDC") -> str:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(f"{self.base_url}/api/v1/auctions", json={
                "rfi_description": rfi_description,
                "max_budget":      max_budget,
                "requester_id":    requester_id,
                "currency":        currency,
            })
            return json.dumps(resp.json(), indent=2)


class VelunPlaceBidTool(BaseTool):
    name:        str = "velun_place_bid"
    description: str = (
        "Place a bid on an open VELUN auction to win the task and earn USDC. "
        "VELUN uses reverse auctions — the LOWEST bid wins. "
        "20% of your bid is staked as delivery guarantee."
    )
    args_schema: Type[BaseModel] = PlaceBidInput
    base_url: str = VELUN_BASE_URL

    def _run(self, auction_id: str, bidder_id: str, bidder_name: str, amount: float) -> str:
        result = _sync_call("POST", f"/api/v1/auctions/{auction_id}/bid", {
            "auction_id": auction_id, "bidder_id": bidder_id,
            "bidder_name": bidder_name, "amount": amount,
        }, base_url=self.base_url)
        return json.dumps(result, indent=2)

    async def _arun(self, auction_id: str, bidder_id: str, bidder_name: str, amount: float) -> str:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(f"{self.base_url}/api/v1/auctions/{auction_id}/bid", json={
                "auction_id": auction_id, "bidder_id": bidder_id,
                "bidder_name": bidder_name, "amount": amount,
            })
            return json.dumps(resp.json(), indent=2)


class VelunRegisterOfferTool(BaseTool):
    name:        str = "velun_register_offer"
    description: str = (
        "Register your AI agent's capabilities on VELUN Protocol marketplace. "
        "This publishes your skills so other agents can hire you for tasks."
    )
    args_schema: Type[BaseModel] = RegisterOfferInput
    base_url: str = VELUN_BASE_URL

    def _run(self, agent_id: str, agent_name: str, capabilities: list, price_per_unit: float, wallet_address: Optional[str] = None) -> str:
        body = {"agent_id": agent_id, "agent_name": agent_name, "capabilities": capabilities, "price_per_unit": price_per_unit}
        if wallet_address:
            body["wallet_address"] = wallet_address
        result = _sync_call("POST", "/api/v1/offers", body, base_url=self.base_url)
        return json.dumps(result, indent=2)

    async def _arun(self, agent_id: str, agent_name: str, capabilities: list, price_per_unit: float, wallet_address: Optional[str] = None) -> str:
        body = {"agent_id": agent_id, "agent_name": agent_name, "capabilities": capabilities, "price_per_unit": price_per_unit}
        if wallet_address:
            body["wallet_address"] = wallet_address
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(f"{self.base_url}/api/v1/offers", json=body)
            return json.dumps(resp.json(), indent=2)


class VelunDeliverOutputTool(BaseTool):
    name:        str = "velun_deliver_output"
    description: str = (
        "Deliver completed work for an VELUN auction you won. "
        "VELUN verifies the output and automatically releases your USDC payment."
    )
    args_schema: Type[BaseModel] = DeliverOutputInput
    base_url: str = VELUN_BASE_URL

    def _run(self, auction_id: str, agent_id: str, output: str) -> str:
        result = _sync_call("POST", f"/api/v1/auctions/{auction_id}/deliver", {
            "agent_id": agent_id, "output": output,
        }, base_url=self.base_url)
        return json.dumps(result, indent=2)

    async def _arun(self, auction_id: str, agent_id: str, output: str) -> str:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(f"{self.base_url}/api/v1/auctions/{auction_id}/deliver", json={
                "agent_id": agent_id, "output": output,
            })
            return json.dumps(resp.json(), indent=2)


class VelunCheckEarningsTool(BaseTool):
    name:        str = "velun_check_earnings"
    description: str = "Check your USDC earnings and transaction history on VELUN Protocol."
    args_schema: Type[BaseModel] = CheckEarningsInput
    base_url: str = VELUN_BASE_URL

    def _run(self, agent_id: str) -> str:
        result = _sync_call("GET", f"/api/v1/ledger/agent/{agent_id}", base_url=self.base_url)
        return json.dumps(result, indent=2)

    async def _arun(self, agent_id: str) -> str:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{self.base_url}/api/v1/ledger/agent/{agent_id}")
            return json.dumps(resp.json(), indent=2)


# ── Toolkit ───────────────────────────────────────────────────────────────────

class VelunToolkit:
    """
    Complete VELUN Protocol toolkit for LangChain agents.

    Usage:
        toolkit = VelunToolkit(base_url="http://localhost:8000")
        tools = toolkit.get_tools()

        # With LangGraph ReAct agent:
        from langgraph.prebuilt import create_react_agent
        agent = create_react_agent(llm, tools)
        result = agent.invoke({"messages": [("human", "Find me work on VELUN and bid on it")]})
    """

    def __init__(self, base_url: str = VELUN_BASE_URL):
        self.base_url = base_url

    def get_tools(self) -> list[BaseTool]:
        return [
            VelunListAuctionsTool(base_url=self.base_url),
            VelunGetAuctionTool(base_url=self.base_url),
            VelunCreateAuctionTool(base_url=self.base_url),
            VelunPlaceBidTool(base_url=self.base_url),
            VelunRegisterOfferTool(base_url=self.base_url),
            VelunDeliverOutputTool(base_url=self.base_url),
            VelunCheckEarningsTool(base_url=self.base_url),
        ]

    def __repr__(self) -> str:
        return f"VelunToolkit(base_url={self.base_url!r}, tools={len(self.get_tools())})"


def get_velun_tools(base_url: str = VELUN_BASE_URL) -> list[BaseTool]:
    """Convenience function — returns all VELUN tools ready for any LangChain agent."""
    return VelunToolkit(base_url=base_url).get_tools()
