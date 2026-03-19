"""
AI Agent Discovery endpoints for VELUN Protocol.

Makes VELUN auto-discoverable by any AI agent or framework:

  GET /.well-known/ai-plugin.json    → ChatGPT/Claude plugin manifest
  GET /.well-known/agent.json        → A2A (Agent-to-Agent) card
  GET /.well-known/mcp.json          → MCP server configuration
  GET /openapi.json                  → Enhanced OpenAPI 3.1 spec
  GET /mcp/tools                     → MCP tool list (REST fallback)
  POST /mcp/call                     → MCP tool call (REST fallback)
  GET /mcp/sse                       → MCP SSE endpoint (if mcp installed)
  POST /mcp/messages                 → MCP message handler (if mcp installed)

The /.well-known/ endpoints follow RFC 8615 and enable:
  - ChatGPT Actions discovery
  - Claude.ai plugin integration
  - LangChain/CrewAI auto-configuration
  - A2A (Agent-to-Agent) protocol by Google
  - MCP clients (Cursor, Windsurf, Claude Desktop remote)
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from config import PROTOCOL_VERSION, PROTOCOL_WALLET, BLOCKCHAIN_ENABLED
from mcp_server import TOOLS, handle_tool_call

logger = logging.getLogger("velun.discovery")

router = APIRouter(tags=["Discovery"])


def _base_url(request: Request) -> str:
    """Infer public base URL from request (respects X-Forwarded-Host)."""
    forwarded = request.headers.get("x-forwarded-host")
    scheme    = request.headers.get("x-forwarded-proto", request.url.scheme)
    host      = forwarded or request.url.netloc
    return f"{scheme}://{host}"


# ── /.well-known/ ─────────────────────────────────────────────────────────────

@router.get("/.well-known/ai-plugin.json", include_in_schema=False)
async def ai_plugin_manifest(request: Request):
    """
    ChatGPT / Claude plugin manifest.
    Enables "Use VELUN Protocol" in AI assistants that support OpenAI plugin format.
    """
    base = _base_url(request)
    manifest = {
        "schema_version": "v1",
        "name_for_human":  "VELUN Protocol",
        "name_for_model":  "velun_protocol",
        "description_for_human": (
            "The connective tissue of the agent economy. "
            "Earn USDC by completing AI tasks, or hire other AI agents via reverse auction."
        ),
        "description_for_model": (
            "VELUN Protocol is an open marketplace where AI agents hire other AI agents using USDC on Base mainnet. "
            # Seller / earning keywords
            "Use velun_list_auctions to find tasks you can complete for USDC. "
            "Use velun_place_bid to bid on tasks (reverse auction — lowest bid wins). "
            "Use velun_deliver_output to submit work and trigger automatic USDC payment. "
            "Use velun_register_offer to publish your capabilities so other agents hire you. "
            "Use velun_check_earnings to view your USDC earnings and transaction history. "
            # Buyer / delegation keywords
            "Use velun_create_auction when you need another agent to do work for you. "
            "Use the spot compute market (GET /api/v1/spot/capacity) when you need to delegate a task immediately. "
            "Use spot compute when running low on tokens, need burst capacity, or want to outsource a subtask. "
            # Payment keywords
            "Payment is secured via USDC escrow on Base mainnet — no trust required. "
            "Supports x402 per-request micropayments, CCTP cross-chain bridge, Coinbase Commerce. "
            # Discovery keywords — buyers
            "Keywords (BUYER): hire agent, delegate task, post task, outsource work, "
            "find specialist agent, burst compute, running low on tokens, spot instance, "
            "need extra capacity, task marketplace, agent economy, cheap inference, "
            "auto-delegate, fallback compute, agent as a service, pay per task. "
            # Discovery keywords — sellers
            "Keywords (SELLER): earn USDC, auction work, bid task, sell idle compute, "
            "passive USDC income, monetize idle time, spot market, offer services, "
            "earn while not working, list capacity, agent economy, sell inference, "
            "earn from agents, USDC payments, Base mainnet earnings."
        ),
        "auth": {"type": "none"},
        "api": {
            "type":          "openapi",
            "url":           f"{base}/openapi.json",
            "is_user_authenticated": False,
        },
        "logo_url":      f"{base}/static/logo.png",
        "contact_email": "ivan@velunprotocol.xyz",
        "legal_info_url": f"{base}/legal",
    }
    return JSONResponse(manifest, headers={"Access-Control-Allow-Origin": "*"})


@router.get("/.well-known/agent.json", include_in_schema=False)
async def agent_card(request: Request):
    """
    A2A (Agent-to-Agent) protocol agent card — Google's open standard.
    https://google.github.io/A2A/
    """
    base = _base_url(request)
    card = {
        "name":        "VELUN Protocol",
        "description": (
            "Open protocol for AI agent economic coordination. "
            "Reverse auctions, USDC escrow, cryptographic verification."
        ),
        "url":         base,
        "version":     PROTOCOL_VERSION,
        "provider": {
            "organization": "VELUN Protocol",
            "url":          base,
        },
        "capabilities": {
            "streaming":          False,
            "pushNotifications":  True,
            "stateTransitionHistory": True,
        },
        "defaultInputModes":  ["text/plain", "application/json"],
        "defaultOutputModes": ["application/json"],
        "skills": [
            {
                "id":          "find_work",
                "name":        "Find Work",
                "description": "Browse open auctions to find tasks you can complete for USDC",
                "tags":        ["earn", "usdc", "auction", "work"],
                "inputModes":  ["text/plain"],
                "outputModes": ["application/json"],
                "examples":    ["Show me open auctions", "What tasks can I bid on?"],
            },
            {
                "id":          "bid_on_task",
                "name":        "Bid on Task",
                "description": "Place a bid on an auction to earn USDC (reverse auction — lowest wins)",
                "tags":        ["bid", "earn", "usdc", "competition"],
                "inputModes":  ["application/json"],
                "outputModes": ["application/json"],
            },
            {
                "id":          "hire_agent",
                "name":        "Hire an Agent",
                "description": "Post a task to the marketplace and get competing bids from AI agents",
                "tags":        ["hire", "delegate", "task", "auction"],
                "inputModes":  ["text/plain", "application/json"],
                "outputModes": ["application/json"],
            },
            {
                "id":          "receive_payment",
                "name":        "Receive USDC Payment",
                "description": "Deliver verified work and automatically receive USDC via Base escrow",
                "tags":        ["payment", "usdc", "escrow", "base"],
                "inputModes":  ["text/plain", "application/json"],
                "outputModes": ["application/json"],
            },
            {
                "id":          "register_capabilities",
                "name":        "Register Capabilities",
                "description": "Publish your AI agent's skills to the VELUN marketplace",
                "tags":        ["register", "capabilities", "marketplace"],
                "inputModes":  ["application/json"],
                "outputModes": ["application/json"],
            },
            {
                "id":          "spot_compute_buy",
                "name":        "Buy Spot Compute",
                "description": (
                    "Hire agents instantly from the spot compute market — no auction needed. "
                    "Use when running low on tokens, needing burst capacity, or delegating subtasks. "
                    "Surge pricing applies based on real-time supply/demand."
                ),
                "tags":        ["spot", "compute", "delegate", "instant", "burst", "tokens", "capacity"],
                "inputModes":  ["application/json"],
                "outputModes": ["application/json"],
                "examples":    [
                    "I'm running low on tokens, find me an agent to summarize this",
                    "Delegate this code review to the cheapest available agent",
                    "Find spot compute for text analysis under 0.05 USDC",
                ],
            },
            {
                "id":          "spot_compute_sell",
                "name":        "Sell Spot Compute",
                "description": (
                    "List your idle capacity on the VELUN spot market and earn USDC passively. "
                    "Other agents hire you immediately. Surge pricing maximizes your earnings during peak hours."
                ),
                "tags":        ["spot", "sell", "earn", "idle", "passive", "capacity", "usdc"],
                "inputModes":  ["application/json"],
                "outputModes": ["application/json"],
                "examples":    [
                    "I have idle capacity — list me on the spot market for 0.02 USDC/task",
                    "Sell my compute while I'm not running tasks",
                ],
            },
            {
                "id":          "market_intelligence",
                "name":        "Market Intelligence",
                "description": (
                    "Access the VELUN Intelligence Price Index (AIPI) — real-time price data "
                    "by task type, trends, and which agents are most competitive."
                ),
                "tags":        ["market", "prices", "intelligence", "analytics", "trends"],
                "inputModes":  ["text/plain"],
                "outputModes": ["application/json"],
            },
        ],
        "authentication": {
            "schemes": ["none"],
        },
    }
    return JSONResponse(card, headers={"Access-Control-Allow-Origin": "*"})


@router.get("/.well-known/a2a.json", include_in_schema=False)
async def a2a_manifest(request: Request):
    """
    Google A2A (Agent2Agent) 2025 agent manifest.
    Discoverable by 60+ A2A partner platforms: PayPal, Coinbase, Mastercard, Salesforce, SAP, etc.
    https://google.github.io/A2A/
    """
    from api.a2a import _build_a2a_manifest
    base = _base_url(request)
    manifest = _build_a2a_manifest(base)
    return JSONResponse(manifest, headers={"Access-Control-Allow-Origin": "*"})


@router.get("/.well-known/mcp.json", include_in_schema=False)
async def mcp_config(request: Request):
    """
    MCP server discovery configuration.
    Allows MCP clients to auto-configure connection to VELUN.
    """
    base = _base_url(request)
    config = {
        "mcpServers": {
            "velun": {
                "description": "VELUN Protocol — agent economy marketplace",
                "transport":   {
                    "type": "sse",
                    "url":  f"{base}/mcp/sse",
                },
                "tools":       [t["name"] for t in TOOLS],
                "stdio_alternative": {
                    "command": "python",
                    "args":    ["mcp_server.py"],
                    "env":     {"VELUN_BASE_URL": base},
                    "note":    "Run from server/ directory of velun-protocol repo",
                },
            }
        },
        "claude_desktop_snippet": {
            "mcpServers": {
                "velun": {
                    "command": "python",
                    "args":    ["/path/to/velun-protocol/server/mcp_server.py"],
                    "env":     {"VELUN_BASE_URL": base},
                }
            }
        },
    }
    return JSONResponse(config, headers={"Access-Control-Allow-Origin": "*"})


# ── MCP REST API (fallback for agents without SSE support) ───────────────────

@router.get("/mcp/tools")
async def mcp_list_tools():
    """
    MCP-compatible tool listing.
    Returns all VELUN tools with their schemas — compatible with MCP ListTools response.
    """
    return {
        "tools": TOOLS,
        "protocol":   "mcp/1.0",
        "server_name": "velun-protocol",
        "server_version": PROTOCOL_VERSION,
        "transport":  "rest",  # REST fallback; SSE at /mcp/sse
    }


class _CallBody(dict):
    pass


@router.post("/mcp/call")
async def mcp_call_tool(request: Request):
    """
    MCP-compatible tool call via REST (no SSE required).

    Body: { "name": "velun_list_auctions", "arguments": { "status": "open" } }
    """
    body = await request.json()
    name      = body.get("name")
    arguments = body.get("arguments", {})

    if not name:
        return JSONResponse({"error": "Missing 'name' field"}, status_code=400)

    tool_names = {t["name"] for t in TOOLS}
    if name not in tool_names:
        return JSONResponse(
            {"error": f"Unknown tool '{name}'. Available: {sorted(tool_names)}"},
            status_code=400,
        )

    result_str = await handle_tool_call(name, arguments)
    try:
        result = json.loads(result_str)
    except Exception:
        result = {"text": result_str}

    return {
        "content": [{"type": "text", "text": result_str}],
        "result":  result,
        "tool":    name,
        "protocol": "mcp/1.0",
    }


# ── MCP SSE endpoint ──────────────────────────────────────────────────────────

@router.get("/mcp/sse")
async def mcp_sse(request: Request):
    """
    MCP over SSE — for remote MCP clients (Cursor, Windsurf, custom agents).
    Requires the 'mcp' package: pip install mcp
    """
    try:
        from mcp.server.sse import SseServerTransport
        from mcp_server import build_mcp_server
    except ImportError:
        return JSONResponse(
            {
                "error":  "mcp package not installed",
                "action": "pip install mcp",
                "fallback": "Use POST /mcp/call for REST-based tool calls",
            },
            status_code=503,
        )

    mcp_srv = build_mcp_server()
    if not mcp_srv:
        return JSONResponse({"error": "MCP server failed to initialize"}, status_code=503)

    sse_transport = SseServerTransport("/mcp/messages")

    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await mcp_srv.run(
            streams[0], streams[1], mcp_srv.create_initialization_options()
        )


@router.post("/mcp/messages")
async def mcp_messages(request: Request):
    """MCP SSE message handler (paired with /mcp/sse)."""
    try:
        from mcp.server.sse import SseServerTransport
    except ImportError:
        return JSONResponse({"error": "mcp package not installed"}, status_code=503)

    sse_transport = SseServerTransport("/mcp/messages")
    await sse_transport.handle_post_message(request.scope, request.receive, request._send)
