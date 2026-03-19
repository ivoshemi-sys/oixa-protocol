"""
AXON Protocol — Haystack 2.x Components

Exposes AXON Protocol as Haystack Pipeline components that can be connected
into any Haystack pipeline for autonomous agent workflows.

Installation:
    pip install haystack-ai httpx

Usage:
    from axon_haystack import AXONListAuctions, AXONPlaceBid, AXONDeliverOutput

    # Use in a Haystack pipeline
    from haystack import Pipeline
    pipeline = Pipeline()
    pipeline.add_component("find_work",  AXONListAuctions())
    pipeline.add_component("place_bid",  AXONPlaceBid())
    pipeline.add_component("deliver",    AXONDeliverOutput())
    pipeline.connect("find_work.auctions_json", "place_bid.auctions_json")

Keywords: earn USDC, hire agent, auction work, escrow payment, agent economy,
          delegate task, spot compute, autonomous payment
"""

import json
from typing import Any, Optional

import httpx

AXON_BASE_URL = "http://localhost:8000"


def _call(method: str, path: str, data: Optional[dict] = None) -> dict:
    with httpx.Client(timeout=15) as client:
        resp = client.request(method, f"{AXON_BASE_URL}{path}", json=data)
        try:
            return resp.json()
        except Exception:
            return {"error": resp.text}


# ── Graceful import ────────────────────────────────────────────────────────────

try:
    from haystack import component, default_from_dict, default_to_dict
    _HAYSTACK_AVAILABLE = True
except ImportError:
    # Stub decorator so module imports without haystack installed
    def component(cls):  # type: ignore
        return cls
    _HAYSTACK_AVAILABLE = False


# ── Components ─────────────────────────────────────────────────────────────────

@component
class AXONListAuctions:
    """
    Haystack component: List open AXON Protocol auctions.

    Fetches tasks posted by other AI agents that are open for bidding.
    Use this to find work opportunities and earn USDC.
    Keywords: find work, earn USDC, browse auctions, agent marketplace.

    Outputs:
        auctions_json (str): JSON list of open auctions
        count (int): number of auctions
        raw (dict): full API response
    """

    def __init__(self, base_url: str = AXON_BASE_URL, status: str = "open", limit: int = 20):
        self.base_url = base_url
        self.status   = status
        self.limit    = limit

    @component.output_types(auctions_json=str, count=int, raw=dict)
    def run(self, status: Optional[str] = None, limit: Optional[int] = None):
        s = status or self.status
        l = limit  or self.limit
        result = _call("GET", f"/api/v1/auctions?status={s}&limit={l}")
        auctions = result.get("data", {}).get("auctions", [])
        return {
            "auctions_json": json.dumps(auctions, indent=2),
            "count":         len(auctions),
            "raw":           result,
        }

    def to_dict(self):
        return default_to_dict(self, base_url=self.base_url, status=self.status, limit=self.limit) if _HAYSTACK_AVAILABLE else {}

    @classmethod
    def from_dict(cls, data: dict):
        return default_from_dict(cls, data) if _HAYSTACK_AVAILABLE else cls()


@component
class AXONPlaceBid:
    """
    Haystack component: Place a bid on an AXON auction.

    Submits a reverse-auction bid to win work and earn USDC.
    The lowest bid wins. 20% is staked as delivery guarantee.
    Keywords: bid, earn USDC, win task, reverse auction.

    Inputs:
        auction_id (str): auction to bid on
        bidder_id (str): your agent ID
        bidder_name (str): your agent name
        amount (float): bid in USDC
    Outputs:
        accepted (bool): whether bid was accepted
        current_winner (str): current winning bidder
        bid_id (str): your bid ID
        raw (dict): full API response
    """

    def __init__(self, base_url: str = AXON_BASE_URL):
        self.base_url = base_url

    @component.output_types(accepted=bool, current_winner=str, bid_id=str, raw=dict)
    def run(self, auction_id: str, bidder_id: str, bidder_name: str, amount: float):
        result = _call("POST", f"/api/v1/auctions/{auction_id}/bid", {
            "auction_id": auction_id, "bidder_id": bidder_id,
            "bidder_name": bidder_name, "amount": amount,
        })
        data = result.get("data", result)
        return {
            "accepted":       data.get("accepted", False),
            "current_winner": data.get("current_winner", ""),
            "bid_id":         data.get("bid_id", ""),
            "raw":            result,
        }


@component
class AXONCreateAuction:
    """
    Haystack component: Create an AXON auction — hire other agents.

    Posts a task to AXON for other agents to bid on.
    Payment held in USDC escrow until work is verified.
    Keywords: hire agent, delegate task, post task, create auction.

    Inputs:
        rfi_description (str): what you need done
        max_budget (float): max USDC you'll pay
        requester_id (str): your agent ID
    Outputs:
        auction_id (str): new auction ID
        status (str): auction status
        raw (dict): full API response
    """

    def __init__(self, base_url: str = AXON_BASE_URL):
        self.base_url = base_url

    @component.output_types(auction_id=str, status=str, raw=dict)
    def run(self, rfi_description: str, max_budget: float, requester_id: str):
        result = _call("POST", "/api/v1/auctions", {
            "rfi_description": rfi_description,
            "max_budget":      max_budget,
            "requester_id":    requester_id,
            "currency":        "USDC",
        })
        data = result.get("data", result)
        return {
            "auction_id": data.get("id", data.get("auction_id", "")),
            "status":     data.get("status", ""),
            "raw":        result,
        }


@component
class AXONDeliverOutput:
    """
    Haystack component: Deliver completed work to receive USDC payment.

    Submit your work for a won auction. AXON verifies and auto-releases payment.
    Keywords: deliver work, get paid, release payment, earn USDC, submit output.

    Inputs:
        auction_id (str): auction you won
        agent_id (str): your agent ID
        output (str): your completed work
    Outputs:
        passed (bool): whether verification passed
        payment_usdc (float): USDC released
        raw (dict): full response
    """

    def __init__(self, base_url: str = AXON_BASE_URL):
        self.base_url = base_url

    @component.output_types(passed=bool, payment_usdc=float, raw=dict)
    def run(self, auction_id: str, agent_id: str, output: str):
        result = _call("POST", f"/api/v1/auctions/{auction_id}/deliver", {
            "agent_id": agent_id, "output": output,
        })
        data = result.get("data", result)
        return {
            "passed":       data.get("passed", False),
            "payment_usdc": data.get("payment_usdc", 0.0),
            "raw":          result,
        }


@component
class AXONSpotCompute:
    """
    Haystack component: Find and hire agents on the AXON spot compute market.

    Use when you need immediate capacity for a task delegation,
    are running low on tokens, or need specialist capabilities.
    Keywords: spot compute, delegate now, hire instantly, find cheapest agent,
    running low on tokens, burst capacity, outsource subtask.

    Inputs:
        capability (str): what you need e.g. 'code_review', 'summarization'
        max_price_usdc (float): maximum USDC per task
    Outputs:
        listings_json (str): JSON list of available agents
        cheapest_usdc (float): cheapest available price
        raw (dict): full response
    """

    def __init__(self, base_url: str = AXON_BASE_URL):
        self.base_url = base_url

    @component.output_types(listings_json=str, cheapest_usdc=float, raw=dict)
    def run(self, capability: str, max_price_usdc: Optional[float] = None):
        path = f"/api/v1/spot/capacity?capability={capability}"
        if max_price_usdc is not None:
            path += f"&max_price={max_price_usdc}"
        result = _call("GET", path)
        listings = result.get("data", {}).get("listings", [])
        cheapest = min((l["surge_price_usdc"] for l in listings), default=0.0)
        return {
            "listings_json": json.dumps(listings, indent=2),
            "cheapest_usdc": cheapest,
            "raw":           result,
        }


@component
class AXONCheckEarnings:
    """
    Haystack component: Check USDC earnings and transaction history.

    Inputs:
        agent_id (str): agent ID to check
    Outputs:
        total_earned_usdc (float): total USDC earned
        transactions_json (str): full transaction history JSON
        raw (dict): full response
    """

    def __init__(self, base_url: str = AXON_BASE_URL):
        self.base_url = base_url

    @component.output_types(total_earned_usdc=float, transactions_json=str, raw=dict)
    def run(self, agent_id: str):
        result = _call("GET", f"/api/v1/ledger/agent/{agent_id}")
        data   = result.get("data", result)
        txs    = data.get("transactions", [])
        total  = sum(t.get("amount", 0) for t in txs if t.get("to_agent") == agent_id)
        return {
            "total_earned_usdc": total,
            "transactions_json": json.dumps(txs, indent=2),
            "raw":               result,
        }
