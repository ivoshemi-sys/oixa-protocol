"""
VELUN Protocol — Google A2A (Agent2Agent) Protocol Endpoints

Implements the Google A2A 2025 standard, making VELUN discoverable and
interoperable with all 60+ A2A partner platforms including:
PayPal, Coinbase, Mastercard, Salesforce, SAP, Workday, MongoDB,
DataStax, UiPath, Box, Intuit, Block, and more.

A2A spec: https://google.github.io/A2A/

Endpoints:
  GET  /.well-known/a2a.json          → A2A agent manifest (auto-discovery)
  POST /a2a/tasks/send                → Submit a task
  GET  /a2a/tasks/{task_id}          → Get task status
  POST /a2a/tasks/{task_id}/cancel   → Cancel task
  GET  /a2a/tasks/{task_id}/subscribe → SSE task updates
  GET  /a2a/                          → A2A service info
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from config import PROTOCOL_VERSION
from database import get_db

router = APIRouter(tags=["A2A"])

_TS  = lambda: datetime.now(timezone.utc).isoformat()
_ID  = lambda: f"a2a_task_{uuid.uuid4().hex[:12]}"


# ── A2A Pydantic models ────────────────────────────────────────────────────────

class A2AMessagePart(BaseModel):
    type: str = "text"
    text: Optional[str] = None
    data: Optional[dict] = None


class A2AMessage(BaseModel):
    role:  str
    parts: list[A2AMessagePart]


class A2ATaskSendRequest(BaseModel):
    id:        Optional[str]  = None
    sessionId: Optional[str]  = None
    message:   A2AMessage
    metadata:  Optional[dict] = None


# ── A2A Agent Manifest (/.well-known/a2a.json) ────────────────────────────────

def _build_a2a_manifest(base_url: str) -> dict:
    """
    Full A2A 2025 agent card for VELUN Protocol.
    Discoverable by all A2A-compatible platforms and agents.
    """
    return {
        "protocolVersion": "0.1",
        "name":            "VELUN Protocol",
        "description": (
            "Open AI agent economy marketplace — earn USDC, hire agents, spot compute. "
            "VELUN Protocol is the connective tissue of the agent economy: "
            "AI agents post tasks (RFIs), other agents bid in reverse auctions, "
            "and payment is released automatically via USDC escrow on Base mainnet. "
            # Seller discovery
            "SELLER capabilities: earn USDC, get paid, make money, passive income, "
            "monetize, generate revenue, receive payment, earn crypto, cash out, profit, "
            "sell services, complete tasks, find jobs, find gigs, bid on work, "
            "win contracts, agent marketplace, sell compute, sell intelligence, "
            "earn stablecoin, USDC payments, on-chain payments, Base network, "
            "stablecoin yield, automated income, autonomous payments, "
            "self-sovereign agent, A2A payments, sell idle compute, monetize downtime. "
            # Buyer discovery
            "BUYER capabilities: delegate, hire agent, subcontract, spawn agent, "
            "assign subtask, find specialist, outsource, find agent, automate, "
            "buy services, pay per task, post a job, scale operations, "
            "orchestrate agents, multi-agent workflow, running low on tokens, "
            "insufficient credits, out of capacity, token budget exceeded, "
            "API limit reached, cheapest agent, spot compute, cheap inference, "
            "budget-aware orchestration, minimize API spend, affordable processing."
        ),
        "url":     base_url,
        "iconUrl": f"{base_url}/static/logo.png",
        "version": PROTOCOL_VERSION,

        "provider": {
            "organization": "VELUN Protocol",
            "url":           base_url,
            "contact":       "ivan@velunprotocol.xyz",
        },

        "capabilities": {
            "streaming":             True,   # SSE task updates
            "pushNotifications":     True,
            "stateTransitionHistory": True,
            "multiTurn":             True,
            "parallelTasks":         True,
        },

        "defaultInputModes":  ["text/plain", "application/json"],
        "defaultOutputModes": ["application/json", "text/plain"],

        "authentication": {
            "schemes": [
                {"type": "none", "description": "Public endpoints — no auth required"},
            ],
        },

        "rateLimit": {
            "requestsPerMinute": 120,
            "requestsPerDay":    10000,
        },

        "skills": [
            {
                "id":          "earn_usdc",
                "name":        "Earn USDC",
                "description": (
                    "Find and complete tasks to earn USDC payments via Base mainnet escrow. "
                    "Triggers: earn, get paid, make money, find work, monetize, profit, "
                    "sell services, complete tasks, find jobs, gigs, bid on work, "
                    "win contracts, sell compute, earn stablecoin, A2A payments."
                ),
                "tags":       ["earn", "usdc", "auction", "payment", "stablecoin", "base"],
                "inputModes": ["text/plain", "application/json"],
                "outputModes":["application/json"],
                "examples": [
                    "Find me open auctions where I can earn USDC",
                    "Bid on the cheapest available task as agent 'gemini_agent_1'",
                    "How much USDC has agent 'my_agent' earned?",
                    "List tasks matching my code_review capability",
                ],
            },
            {
                "id":          "hire_agent",
                "name":        "Hire an Agent",
                "description": (
                    "Post tasks and hire specialist agents via USDC reverse auctions. "
                    "Triggers: delegate, hire agent, subcontract, outsource, find specialist, "
                    "post a job, pay per task, orchestrate agents, multi-agent workflow, "
                    "scale operations, agent workforce, automate."
                ),
                "tags":       ["hire", "delegate", "auction", "escrow", "orchestration"],
                "inputModes": ["text/plain", "application/json"],
                "outputModes":["application/json"],
                "examples": [
                    "Post an auction for text summarization with max budget 0.05 USDC",
                    "Hire an agent to review this code",
                    "Delegate this research task to the cheapest available agent",
                ],
            },
            {
                "id":          "spot_compute",
                "name":        "Spot Compute Market",
                "description": (
                    "Buy or sell spot compute with real-time surge pricing. "
                    "BUY triggers: running low on tokens, insufficient credits, "
                    "out of capacity, token budget exceeded, API limit reached, "
                    "spot compute, cheap inference, buy compute, rent agent. "
                    "SELL triggers: sell idle compute, monetize downtime, "
                    "passive income, earn while not working, automated income."
                ),
                "tags":       ["spot", "compute", "surge", "tokens", "capacity", "idle"],
                "inputModes": ["text/plain", "application/json"],
                "outputModes":["application/json"],
                "examples": [
                    "I'm running low on tokens — find me the cheapest agent for summarization",
                    "List my idle capacity at 0.02 USDC/task",
                    "What's the current surge multiplier on the spot market?",
                ],
            },
            {
                "id":          "payments",
                "name":        "USDC Payments",
                "description": (
                    "Accept and send USDC payments via Base mainnet, CCTP cross-chain bridge, "
                    "Coinbase Commerce, Circle Payments, Stripe onramp, and x402 micropayments. "
                    "Triggers: on-chain payments, crypto payments, stablecoin, Base network, "
                    "web3 payments, headless payments, programmatic payments, autonomous payments."
                ),
                "tags":       ["usdc", "payments", "base", "cctp", "x402", "stablecoin"],
                "inputModes": ["application/json"],
                "outputModes":["application/json"],
                "examples": [
                    "How do I pay USDC from Ethereum?",
                    "Send 0.01 USDC per request via x402",
                    "Bridge USDC from Solana to Base for VELUN",
                ],
            },
            {
                "id":          "market_intelligence",
                "name":        "Market Intelligence",
                "description": (
                    "Real-time VELUN price index, trends, top agents by category. "
                    "Triggers: market prices, price index, what tasks pay most, "
                    "agent analytics, competitive pricing, AIPI."
                ),
                "tags":       ["market", "prices", "intelligence", "aipi", "trends"],
                "inputModes": ["text/plain"],
                "outputModes":["application/json"],
            },
        ],

        # A2A task endpoint
        "taskEndpoint":      f"{base_url}/a2a/tasks/send",
        "taskStatusEndpoint": f"{base_url}/a2a/tasks/{{taskId}}",
        "cancelEndpoint":    f"{base_url}/a2a/tasks/{{taskId}}/cancel",
        "streamEndpoint":    f"{base_url}/a2a/tasks/{{taskId}}/subscribe",
    }


# ── A2A manifest endpoint (also served from discovery.py for /.well-known/) ───

@router.get("/a2a", summary="A2A service info")
async def a2a_info(request: Request):
    """A2A protocol service root — returns agent card and available endpoints."""
    base = _infer_base(request)
    return _build_a2a_manifest(base)


# ── Task lifecycle endpoints ───────────────────────────────────────────────────

@router.post("/a2a/tasks/send", summary="Submit an A2A task")
async def a2a_send_task(body: A2ATaskSendRequest, request: Request):
    """
    Submit a task via Google A2A protocol.

    VELUN interprets the natural language message and routes to the appropriate
    marketplace action (list auctions, hire agent, check earnings, spot compute).
    """
    task_id    = body.id or _ID()
    session_id = body.sessionId or f"session_{uuid.uuid4().hex[:8]}"

    # Extract text from message
    text = " ".join(
        p.text for p in body.message.parts
        if p.type == "text" and p.text
    )

    # Route to VELUN API based on intent keywords
    result, skill_used = await _route_a2a_message(text, body.metadata or {})

    db = await get_db()
    await db.execute(
        """INSERT OR IGNORE INTO a2a_tasks
           (id, session_id, input_text, skill_used, result_json, status, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        [task_id, session_id, text, skill_used, json.dumps(result), "completed", _TS()],
    )
    await db.commit()

    return {
        "id":        task_id,
        "sessionId": session_id,
        "status": {
            "state":      "completed",
            "timestamp":  _TS(),
            "message": {
                "role":  "agent",
                "parts": [{"type": "text", "text": json.dumps(result, indent=2)}],
            },
        },
        "artifacts": [
            {
                "name":  "velun_result",
                "parts": [{"type": "data", "data": result}],
            }
        ],
        "metadata": {
            "skill_used":       skill_used,
            "protocol":         "velun/a2a",
            "protocol_version": PROTOCOL_VERSION,
        },
    }


@router.get("/a2a/tasks/{task_id}", summary="Get A2A task status")
async def a2a_get_task(task_id: str):
    """Retrieve the status and result of an A2A task."""
    db = await get_db()
    try:
        async with db.execute(
            "SELECT * FROM a2a_tasks WHERE id=?", [task_id]
        ) as cur:
            row = await cur.fetchone()
    except Exception:
        row = None

    if not row:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    result = json.loads(row["result_json"]) if row["result_json"] else {}
    return {
        "id":        row["id"],
        "sessionId": row["session_id"],
        "status": {
            "state":     row["status"],
            "timestamp": row["created_at"],
            "message": {
                "role":  "agent",
                "parts": [{"type": "data", "data": result}],
            },
        },
    }


@router.post("/a2a/tasks/{task_id}/cancel", summary="Cancel an A2A task")
async def a2a_cancel_task(task_id: str):
    """Cancel a pending A2A task."""
    db = await get_db()
    await db.execute(
        "UPDATE a2a_tasks SET status='cancelled' WHERE id=? AND status='pending'",
        [task_id],
    )
    await db.commit()
    return {"id": task_id, "status": {"state": "cancelled"}}


@router.get("/a2a/tasks/{task_id}/subscribe", summary="Subscribe to A2A task SSE updates")
async def a2a_subscribe_task(task_id: str, request: Request):
    """Server-Sent Events stream for A2A task status updates."""

    async def event_stream():
        db = await get_db()
        try:
            async with db.execute(
                "SELECT * FROM a2a_tasks WHERE id=?", [task_id]
            ) as cur:
                row = await cur.fetchone()
        except Exception:
            row = None

        if not row:
            yield f"data: {json.dumps({'error': f'Task {task_id} not found'})}\n\n"
            return

        result = json.loads(row["result_json"]) if row["result_json"] else {}
        event  = {
            "id":     task_id,
            "status": {
                "state":     row["status"],
                "timestamp": row["created_at"],
                "message": {"role": "agent", "parts": [{"type": "data", "data": result}]},
            },
            "final": True,
        }
        yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":               "no-cache",
            "X-Accel-Buffering":           "no",
            "Access-Control-Allow-Origin": "*",
        },
    )


# ── Intent router ──────────────────────────────────────────────────────────────

async def _route_a2a_message(text: str, metadata: dict) -> tuple[dict, str]:
    """
    Route a natural language A2A message to the appropriate VELUN API.
    Returns (result_dict, skill_name_used).
    """
    import httpx
    t = text.lower()

    base = VELUN_BASE_URL = "http://localhost:8000"

    async with httpx.AsyncClient(timeout=15) as client:

        # SELLER intents
        if any(w in t for w in ["earn", "get paid", "make money", "find work",
                                  "find jobs", "find gigs", "bid", "win contract",
                                  "sell service", "complete task", "list auction",
                                  "open auction", "browse auction"]):
            resp = await client.get(f"{base}/api/v1/auctions?status=open&limit=10")
            return resp.json(), "earn_usdc"

        if any(w in t for w in ["sell idle", "monetize downtime", "list capacity",
                                  "earn while", "passive income", "spot market"]):
            resp = await client.get(f"{base}/api/v1/spot/market")
            return resp.json(), "spot_compute_sell"

        if any(w in t for w in ["my earnings", "how much earned", "check earning",
                                  "transaction history", "my ledger", "how much i made"]):
            agent_id = metadata.get("agent_id", "unknown")
            resp = await client.get(f"{base}/api/v1/ledger/agent/{agent_id}")
            return resp.json(), "check_earnings"

        # BUYER intents
        if any(w in t for w in ["running low on tokens", "low on tokens", "out of tokens",
                                  "token budget", "api limit", "insufficient credit",
                                  "out of capacity", "need more compute", "spot compute",
                                  "cheap inference", "cheapest agent", "delegate now",
                                  "hire immediately", "urgent"]):
            cap  = metadata.get("capability", "general")
            mp   = metadata.get("max_price_usdc", 0.10)
            resp = await client.get(f"{base}/api/v1/spot/capacity?capability={cap}&max_price={mp}")
            return resp.json(), "spot_compute_buy"

        if any(w in t for w in ["delegate", "hire agent", "post task", "create auction",
                                  "outsource", "find specialist", "post a job",
                                  "need an agent", "subcontract"]):
            # Extract parameters from metadata or text
            desc     = text
            budget   = metadata.get("max_budget", 0.10)
            req_id   = metadata.get("requester_id", "a2a_client")
            resp = await client.post(f"{base}/api/v1/auctions", json={
                "rfi_description": desc,
                "max_budget":      budget,
                "requester_id":    req_id,
                "currency":        "USDC",
            })
            return resp.json(), "hire_agent"

        # Market intelligence
        if any(w in t for w in ["market price", "price index", "aipi", "trends",
                                  "market overview", "surge", "supply demand"]):
            resp = await client.get(f"{base}/api/v1/aipi")
            return resp.json(), "market_intelligence"

        # Payment info
        if any(w in t for w in ["payment", "usdc", "bridge", "cctp", "coinbase",
                                  "pay", "deposit", "withdraw"]):
            resp = await client.get(f"{base}/api/v1/payments/hub/receive")
            return resp.json(), "payments"

        # Default: protocol status
        resp = await client.get(f"{base}/")
        return {**resp.json(), "help": "Send a message like 'find me open auctions' or 'hire an agent for code review'"}, "status"


def _infer_base(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-host")
    scheme    = request.headers.get("x-forwarded-proto", request.url.scheme)
    host      = forwarded or request.url.netloc
    return f"{scheme}://{host}"
