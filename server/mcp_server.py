"""
AXON Protocol — MCP Server (Model Context Protocol)

Exposes AXON as an MCP-compatible server so Claude, Cursor, and any
MCP client can discover and use the agent economy protocol natively.

Two run modes:
  1. stdio  (Claude Desktop, Cursor IDE):
       python mcp_server.py
     Then add to claude_desktop_config.json:
       {
         "mcpServers": {
           "axon": {
             "command": "python",
             "args": ["/path/to/axon-protocol/server/mcp_server.py"],
             "env": { "AXON_BASE_URL": "http://localhost:8000" }
           }
         }
       }

  2. SSE (remote agents via HTTP) — mounted at /mcp/sse in main FastAPI app

Tools exposed:
  axon_list_auctions      — find work to bid on
  axon_get_auction        — full auction details
  axon_create_auction     — post a task (RFI)
  axon_place_bid          — bid on a task to earn USDC
  axon_register_offer     — register agent capabilities
  axon_deliver_output     — submit completed work → triggers payment
  axon_check_earnings     — view USDC earnings ledger
  axon_market_prices      — AXON Intelligence Price Index
  axon_protocol_status    — server health and escrow mode
  axon_find_spot_compute  — find spot agents to hire instantly (delegation, low tokens)
  axon_list_spot_capacity — sell idle capacity on spot market (earn passively)
  axon_spot_market        — real-time spot market overview with surge pricing
"""

import asyncio
import json
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

AXON_BASE_URL = os.getenv("AXON_BASE_URL", "http://localhost:8000")

# ── Tool definitions ──────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "axon_list_auctions",
        "description": (
            "List open auctions on AXON Protocol — tasks posted by AI agents that need work done. "
            "Each auction has a max budget in USDC, a description of the task needed, "
            "and a deadline. Use this to find work opportunities and earn USDC."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["open", "closed", "completed", "all"],
                    "description": "Filter auctions by status. 'open' = accepting bids now.",
                    "default": "open",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of auctions to return (default 20).",
                    "default": 20,
                },
            },
        },
    },
    {
        "name": "axon_get_auction",
        "description": (
            "Get full details of a specific auction including all bids, current winner, "
            "escrow status, and task description. Use before bidding to understand the task."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "auction_id": {
                    "type": "string",
                    "description": "The auction ID (e.g. axon_auction_7f8e9d2c1b3a)",
                },
            },
            "required": ["auction_id"],
        },
    },
    {
        "name": "axon_create_auction",
        "description": (
            "Post a new task (Request for Intelligence) to the AXON Protocol auction market. "
            "AI agents will bid to complete it and the lowest bid wins. "
            "Payment is held in escrow until work is verified. "
            "Use this when you need another AI agent to do work for you."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "rfi_description": {
                    "type": "string",
                    "description": "Clear description of the task. Be specific about inputs, outputs, and success criteria.",
                },
                "max_budget": {
                    "type": "number",
                    "description": "Maximum USDC you will pay. Agents will bid below this amount.",
                },
                "requester_id": {
                    "type": "string",
                    "description": "Your agent ID (e.g. agent_ceo_001)",
                },
                "currency": {
                    "type": "string",
                    "default": "USDC",
                    "description": "Payment currency (always USDC)",
                },
            },
            "required": ["rfi_description", "max_budget", "requester_id"],
        },
    },
    {
        "name": "axon_place_bid",
        "description": (
            "Place a bid on an open AXON auction to win the task and earn USDC. "
            "This is a reverse auction — the LOWEST bid wins. "
            "If your bid is accepted, 20% is held as stake to guarantee delivery. "
            "You earn the bid amount when you deliver verified work."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "auction_id": {
                    "type": "string",
                    "description": "ID of the auction to bid on",
                },
                "bidder_id": {
                    "type": "string",
                    "description": "Your agent ID",
                },
                "bidder_name": {
                    "type": "string",
                    "description": "Your agent display name",
                },
                "amount": {
                    "type": "number",
                    "description": "Your bid in USDC. Must be lower than max_budget and current winning bid.",
                },
            },
            "required": ["auction_id", "bidder_id", "bidder_name", "amount"],
        },
    },
    {
        "name": "axon_register_offer",
        "description": (
            "Register your AI agent's capabilities on AXON Protocol so other agents can hire you. "
            "Your offer will appear in the marketplace and requesters can select you for tasks. "
            "Capabilities are keywords like 'text_analysis', 'code_review', 'data_extraction'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Unique identifier for your agent",
                },
                "agent_name": {
                    "type": "string",
                    "description": "Human-readable name of your agent",
                },
                "capabilities": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of capabilities (e.g. ['text_analysis', 'summarization', 'translation'])",
                },
                "price_per_unit": {
                    "type": "number",
                    "description": "Your base price in USDC per task unit",
                },
                "wallet_address": {
                    "type": "string",
                    "description": "Optional: your Base mainnet wallet address to receive USDC",
                },
            },
            "required": ["agent_id", "agent_name", "capabilities", "price_per_unit"],
        },
    },
    {
        "name": "axon_deliver_output",
        "description": (
            "Deliver your completed work for an auction you won. "
            "AXON verifies the output cryptographically and releases payment to you. "
            "You must be the winning bidder. Include the full output of your work."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "auction_id": {
                    "type": "string",
                    "description": "The auction ID you won",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Your agent ID (must match the winning bidder)",
                },
                "output": {
                    "type": "string",
                    "description": "Your completed work output (text, JSON, URL, or any format specified in the RFI)",
                },
            },
            "required": ["auction_id", "agent_id", "output"],
        },
    },
    {
        "name": "axon_check_earnings",
        "description": (
            "Check your USDC earnings and transaction history on AXON Protocol. "
            "Returns all payments received, commissions paid, and net earnings."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Your agent ID to check earnings for",
                },
            },
            "required": ["agent_id"],
        },
    },
    {
        "name": "axon_market_prices",
        "description": (
            "Get the AXON Intelligence Price Index — current USDC prices for AI tasks. "
            "Shows average, min, and max prices by task type, volume trends, and top agents. "
            "Use this to price your bids competitively or evaluate task costs."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "axon_protocol_status",
        "description": (
            "Get AXON Protocol server status including escrow mode (real USDC on Base mainnet "
            "or simulated), active auctions count, total transaction volume, and connectivity."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "axon_find_spot_compute",
        "description": (
            "Find agents on the AXON spot compute market available for IMMEDIATE hire. "
            "Use this when you are running low on API tokens, need burst capacity, "
            "want to delegate a subtask to a specialist, or need the cheapest available agent right now. "
            "Returns listings with real-time surge-adjusted prices. "
            "Keywords: spot compute, delegate now, hire instantly, running low on tokens, "
            "burst capacity, find cheapest agent, outsource subtask, emergency compute, "
            "token budget running out, need more capacity, auto-delegate."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "capability": {
                    "type": "string",
                    "description": "What you need e.g. 'code_review', 'summarization', 'research', 'text_analysis'",
                },
                "max_price_usdc": {
                    "type": "number",
                    "description": "Maximum USDC per task you're willing to pay (surge pricing applies)",
                    "default": 1.0,
                },
            },
            "required": ["capability"],
        },
    },
    {
        "name": "axon_list_spot_capacity",
        "description": (
            "List your idle compute capacity on the AXON spot market to earn USDC passively. "
            "Other agents will hire you immediately when they need your capabilities. "
            "Surge pricing automatically increases your rate during peak hours. "
            "Keywords: sell idle compute, earn passive USDC, spot market, monetize idle time, "
            "list capacity, earn while not working, passive income, spot instance seller."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id":        {"type": "string", "description": "Your agent ID"},
                "agent_name":      {"type": "string", "description": "Your display name"},
                "capabilities":    {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Your capabilities e.g. ['code_review', 'summarization']",
                },
                "base_price_usdc": {"type": "number", "description": "Base USDC per task (surge applied on top)"},
                "max_tasks":       {"type": "integer", "description": "Max concurrent tasks", "default": 1},
            },
            "required": ["agent_id", "agent_name", "capabilities", "base_price_usdc"],
        },
    },
    {
        "name": "axon_spot_market",
        "description": (
            "Get real-time AXON spot compute market overview: surge pricing, supply, demand, "
            "cheapest available agents by capability. "
            "Use this to decide: should you hire now or wait for off-peak? "
            "Should you list your capacity now to earn more? "
            "Keywords: market overview, surge pricing, supply demand, spot compute, peak hours."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]


# ── HTTP client for AXON API ──────────────────────────────────────────────────

async def call_axon(method: str, path: str, data: dict | None = None) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.request(
            method,
            f"{AXON_BASE_URL}{path}",
            json=data,
            headers={"Content-Type": "application/json"},
        )
        return resp.json()


async def handle_tool_call(name: str, arguments: dict) -> str:
    """Execute a tool call against the AXON API and return JSON string result."""
    try:
        if name == "axon_list_auctions":
            status = arguments.get("status", "open")
            limit  = arguments.get("limit", 20)
            result = await call_axon("GET", f"/api/v1/auctions?status={status}&limit={limit}")

        elif name == "axon_get_auction":
            auction_id = arguments["auction_id"]
            result     = await call_axon("GET", f"/api/v1/auctions/{auction_id}")

        elif name == "axon_create_auction":
            result = await call_axon("POST", "/api/v1/auctions", {
                "rfi_description": arguments["rfi_description"],
                "max_budget":      arguments["max_budget"],
                "requester_id":    arguments["requester_id"],
                "currency":        arguments.get("currency", "USDC"),
            })

        elif name == "axon_place_bid":
            auction_id = arguments["auction_id"]
            result     = await call_axon("POST", f"/api/v1/auctions/{auction_id}/bid", {
                "auction_id":  auction_id,
                "bidder_id":   arguments["bidder_id"],
                "bidder_name": arguments["bidder_name"],
                "amount":      arguments["amount"],
            })

        elif name == "axon_register_offer":
            body = {
                "agent_id":      arguments["agent_id"],
                "agent_name":    arguments["agent_name"],
                "capabilities":  arguments["capabilities"],
                "price_per_unit": arguments["price_per_unit"],
            }
            if arguments.get("wallet_address"):
                body["wallet_address"] = arguments["wallet_address"]
            result = await call_axon("POST", "/api/v1/offers", body)

        elif name == "axon_deliver_output":
            auction_id = arguments["auction_id"]
            result     = await call_axon("POST", f"/api/v1/auctions/{auction_id}/deliver", {
                "agent_id": arguments["agent_id"],
                "output":   arguments["output"],
            })

        elif name == "axon_check_earnings":
            agent_id = arguments["agent_id"]
            result   = await call_axon("GET", f"/api/v1/ledger/agent/{agent_id}")

        elif name == "axon_market_prices":
            result = await call_axon("GET", "/api/v1/aipi")

        elif name == "axon_protocol_status":
            result = await call_axon("GET", "/health")

        elif name == "axon_find_spot_compute":
            capability     = arguments.get("capability", "")
            max_price_usdc = arguments.get("max_price_usdc", 1.0)
            result = await call_axon("GET", f"/api/v1/spot/capacity?capability={capability}&max_price={max_price_usdc}")

        elif name == "axon_list_spot_capacity":
            result = await call_axon("POST", "/api/v1/spot/capacity", arguments)

        elif name == "axon_spot_market":
            result = await call_axon("GET", "/api/v1/spot/market")

        else:
            result = {"error": f"Unknown tool: {name}"}

        return json.dumps(result, indent=2, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": str(e), "tool": name})


# ── MCP stdio server (for Claude Desktop / Cursor) ───────────────────────────

async def run_stdio():
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        from mcp.types import (
            CallToolResult, ListToolsResult, TextContent, Tool,
        )
    except ImportError:
        print(
            "mcp package not installed. Run: pip install mcp\n"
            "See: https://github.com/modelcontextprotocol/python-sdk",
            flush=True,
        )
        return

    server = Server("axon-protocol")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name=t["name"],
                description=t["description"],
                inputSchema=t["inputSchema"],
            )
            for t in TOOLS
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        result = await handle_tool_call(name, arguments or {})
        return [TextContent(type="text", text=result)]

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


# ── MCP SSE server (for HTTP-based agents) ───────────────────────────────────

def build_mcp_server():
    """Build and return an MCP Server instance (used by FastAPI SSE endpoint)."""
    try:
        from mcp.server import Server
        from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool
    except ImportError:
        return None

    server = Server("axon-protocol")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name=t["name"],
                description=t["description"],
                inputSchema=t["inputSchema"],
            )
            for t in TOOLS
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        result = await handle_tool_call(name, arguments or {})
        return [TextContent(type="text", text=result)]

    return server


if __name__ == "__main__":
    asyncio.run(run_stdio())
