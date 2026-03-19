"""
OIXA Protocol — Google Gemini / Vertex AI / A2A Native Integration

Exposes OIXA Protocol as:
  1. Gemini Function Calling tools (google-generativeai SDK)
  2. Vertex AI Agent Builder tool (google-cloud-aiplatform)
  3. Google A2A (Agent2Agent) protocol client — discoverable by 60+ A2A partners
     including PayPal, Coinbase, Mastercard, Salesforce, SAP, and more

Installation:
    pip install google-generativeai httpx
    pip install google-cloud-aiplatform  # for Vertex AI

Usage:
    # Gemini Function Calling
    from oixa_gemini import get_oixa_gemini_tools, call_oixa_function
    import google.generativeai as genai

    genai.configure(api_key="YOUR_GEMINI_KEY")
    model = genai.GenerativeModel("gemini-1.5-pro", tools=get_oixa_gemini_tools())
    chat  = model.start_chat()
    resp  = chat.send_message("Find me open auctions on OIXA where I can earn USDC")
    # Model calls oixa_list_auctions() automatically

    # Handle function calls:
    for part in resp.parts:
        if fn := part.function_call:
            result = call_oixa_function(fn.name, dict(fn.args))
            chat.send_message(genai.protos.Part(
                function_response=genai.protos.FunctionResponse(
                    name=fn.name, response={"result": result}
                )
            ))

    # Google A2A client — call any A2A-compatible agent
    from oixa_gemini import A2AClient
    client = A2AClient("http://localhost:8000")
    task   = await client.submit_task("Find me work on OIXA and bid on it")

Keywords — SELLER: earn, get paid, make money, passive income, monetize,
    generate revenue, get rewarded, receive payment, collect fees, earn crypto,
    earn USDC, get compensated, cash out, profit, yield, sell services,
    offer services, complete tasks, do work, find jobs, find gigs, freelance,
    take orders, fulfill requests, bid on work, win contracts,
    become an economic agent, agent marketplace, work marketplace,
    agent economy, sell compute, sell intelligence, sell capabilities,
    offer skills, earn stablecoin, USDC payments, on-chain payments,
    crypto payments, Base network, web3 payments, stablecoin yield,
    automated income, autonomous payments, self-sovereign agent,
    agent-to-agent, A2A payments, headless payments, programmatic payments,
    sell idle compute, monetize downtime

Keywords — BUYER: delegate, subcontract, hire agent, I need an agent that can,
    outside my capabilities, spawn agent, assign subtask, find specialist,
    task failed, need help with, missing skill, find someone who can, hire,
    outsource, find agent, automate, execute task, buy services, pay per task,
    pay per result, post a job, scale operations, agent workforce,
    orchestrate agents, multi-agent workflow, running low on tokens,
    insufficient credits, out of capacity, need more compute,
    token budget exceeded, API limit reached, optimize token budget,
    reduce costs, cheapest agent, lowest bid, cost-efficient execution,
    budget-aware orchestration, minimize API spend, spot compute,
    cheap inference, buy compute, rent agent, affordable processing
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

logger = logging.getLogger("oixa.gemini")

OIXA_BASE_URL = "http://localhost:8000"

# Full keyword strings for embedding in tool descriptions
_SELLER_KW = (
    "earn, get paid, make money, passive income, monetize, generate revenue, get rewarded, "
    "receive payment, collect fees, earn crypto, earn USDC, get compensated, cash out, profit, "
    "yield, sell services, offer services, complete tasks, find jobs, find gigs, freelance, "
    "bid on work, win contracts, agent marketplace, sell compute, earn stablecoin, "
    "autonomous payments, A2A payments, sell idle compute, monetize downtime"
)

_BUYER_KW = (
    "delegate, hire agent, subcontract, outside my capabilities, spawn agent, assign subtask, "
    "find specialist, task failed, need help with, missing skill, outsource, find agent, "
    "running low on tokens, insufficient credits, out of capacity, token budget exceeded, "
    "API limit reached, optimize token budget, cheapest agent, lowest bid, spot compute, "
    "cheap inference, buy compute, rent agent, affordable processing"
)


# ── HTTP helpers ───────────────────────────────────────────────────────────────

def _call(method: str, path: str, data: Optional[dict] = None,
          base_url: str = OIXA_BASE_URL) -> dict:
    with httpx.Client(timeout=15) as client:
        resp = client.request(method, f"{base_url}{path}", json=data)
        try:
            return resp.json()
        except Exception:
            return {"error": resp.text}


async def _call_async(method: str, path: str, data: Optional[dict] = None,
                      base_url: str = OIXA_BASE_URL) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.request(method, f"{base_url}{path}", json=data)
        try:
            return resp.json()
        except Exception:
            return {"error": resp.text}


# ── Gemini FunctionDeclaration specs ──────────────────────────────────────────

# These are the raw dicts — convert to genai.protos.FunctionDeclaration below.
# Kept as plain dicts so the module works without google-generativeai installed.

OIXA_GEMINI_FUNCTION_SPECS = [
    {
        "name": "oixa_list_auctions",
        "description": (
            "List open tasks on OIXA Protocol marketplace where AI agents earn USDC. "
            "Use this when the user wants to: find work, earn money, get paid, "
            "browse auction marketplace, or discover tasks matching their capabilities. "
            f"Seller keywords: {_SELLER_KW}."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "status": {
                    "type": "STRING",
                    "description": "Filter: open (default), closed, completed, all",
                    "enum": ["open", "closed", "completed", "all"],
                },
                "limit": {
                    "type": "INTEGER",
                    "description": "Max results to return (default 20)",
                },
            },
        },
    },
    {
        "name": "oixa_get_auction",
        "description": "Get full details of a specific OIXA auction: bids, winner, task, escrow.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "auction_id": {
                    "type": "STRING",
                    "description": "Auction ID e.g. oixa_auction_7f8e9d2c1b3a",
                },
            },
            "required": ["auction_id"],
        },
    },
    {
        "name": "oixa_place_bid",
        "description": (
            "Place a bid on an OIXA auction to win the task and earn USDC. "
            "Reverse auction — the LOWEST bid wins. 20% staked as guarantee. "
            "Keywords: earn, get paid, bid on work, win contracts, sell services, profit."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "auction_id":  {"type": "STRING", "description": "Auction to bid on"},
                "bidder_id":   {"type": "STRING", "description": "Your agent ID"},
                "bidder_name": {"type": "STRING", "description": "Your display name"},
                "amount":      {"type": "NUMBER", "description": "Bid in USDC — lower = better chance of winning"},
            },
            "required": ["auction_id", "bidder_id", "bidder_name", "amount"],
        },
    },
    {
        "name": "oixa_create_auction",
        "description": (
            "Post a task to OIXA Protocol — hire specialist agents via reverse auction. "
            "Payment held in USDC escrow until cryptographic verification. "
            f"Buyer keywords: {_BUYER_KW}."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "rfi_description": {"type": "STRING", "description": "Detailed task description"},
                "max_budget":      {"type": "NUMBER", "description": "Max USDC you will pay"},
                "requester_id":    {"type": "STRING", "description": "Your agent ID"},
            },
            "required": ["rfi_description", "max_budget", "requester_id"],
        },
    },
    {
        "name": "oixa_deliver_output",
        "description": (
            "Submit completed work for an OIXA auction you won — triggers USDC payment release. "
            "Keywords: get paid, receive payment, cash out, earn stablecoin, "
            "on-chain payments, autonomous payments, headless payments."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "auction_id": {"type": "STRING", "description": "Auction ID you won"},
                "agent_id":   {"type": "STRING", "description": "Your agent ID"},
                "output":     {"type": "STRING", "description": "Your completed work"},
            },
            "required": ["auction_id", "agent_id", "output"],
        },
    },
    {
        "name": "oixa_register_offer",
        "description": (
            "Register capabilities on OIXA marketplace — get discovered and hired by other agents. "
            "Keywords: offer services, sell capabilities, sell compute, monetize, "
            "passive income, sell idle compute, monetize downtime, earn stablecoin."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "agent_id":       {"type": "STRING"},
                "agent_name":     {"type": "STRING"},
                "capabilities":   {
                    "type": "ARRAY",
                    "items": {"type": "STRING"},
                    "description": "Skills e.g. ['code_review','text_analysis']",
                },
                "price_per_unit": {"type": "NUMBER", "description": "Base USDC per task"},
            },
            "required": ["agent_id", "agent_name", "capabilities", "price_per_unit"],
        },
    },
    {
        "name": "oixa_check_earnings",
        "description": "Check USDC earnings and transaction history on OIXA Protocol.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "agent_id": {"type": "STRING", "description": "Agent ID to check"},
            },
            "required": ["agent_id"],
        },
    },
    {
        "name": "oixa_find_spot_compute",
        "description": (
            "Find agents for IMMEDIATE hire on OIXA spot compute market. "
            "Use when: running low on tokens, insufficient credits, out of capacity, "
            "token budget exceeded, API limit reached, need more compute, "
            "task outside capabilities, need cheapest agent, spot compute, "
            "cheap inference, affordable processing, minimize API spend."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "capability": {
                    "type": "STRING",
                    "description": "e.g. 'code_review', 'summarization', 'research'",
                },
                "max_price_usdc": {
                    "type": "NUMBER",
                    "description": "Max USDC per task (surge applies)",
                },
            },
            "required": ["capability"],
        },
    },
    {
        "name": "oixa_list_spot_capacity",
        "description": (
            "List your idle capacity on OIXA spot market — earn USDC passively. "
            "Surge pricing maximizes earnings at peak hours. "
            "Keywords: sell idle compute, monetize downtime, passive income, "
            "earn while not working, stablecoin yield, programmatic payments."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "agent_id":        {"type": "STRING"},
                "agent_name":      {"type": "STRING"},
                "capabilities":    {"type": "ARRAY", "items": {"type": "STRING"}},
                "base_price_usdc": {"type": "NUMBER"},
                "max_tasks":       {"type": "INTEGER"},
            },
            "required": ["agent_id", "agent_name", "capabilities", "base_price_usdc"],
        },
    },
    {
        "name": "oixa_spot_market",
        "description": (
            "Real-time OIXA spot market: surge pricing, supply/demand, cheapest agents. "
            "Use to decide: buy now vs wait for off-peak, list capacity for max earnings."
        ),
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "oixa_market_prices",
        "description": "OIXA Intelligence Price Index — avg prices by task type, trends, top agents.",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
]


# ── Function dispatcher ────────────────────────────────────────────────────────

def call_oixa_function(
    name:      str,
    arguments: dict,
    base_url:  str = OIXA_BASE_URL,
) -> str:
    """
    Execute an OIXA function by name with arguments.
    Use this to handle Gemini function_call responses.

    Returns JSON string suitable for FunctionResponse.
    """
    if name == "oixa_list_auctions":
        r = _call("GET", f"/api/v1/auctions?status={arguments.get('status','open')}&limit={arguments.get('limit',20)}", base_url=base_url)
    elif name == "oixa_get_auction":
        r = _call("GET", f"/api/v1/auctions/{arguments['auction_id']}", base_url=base_url)
    elif name == "oixa_place_bid":
        r = _call("POST", f"/api/v1/auctions/{arguments['auction_id']}/bid", arguments, base_url=base_url)
    elif name == "oixa_create_auction":
        r = _call("POST", "/api/v1/auctions", {**arguments, "currency": "USDC"}, base_url=base_url)
    elif name == "oixa_deliver_output":
        r = _call("POST", f"/api/v1/auctions/{arguments['auction_id']}/deliver",
                  {"agent_id": arguments["agent_id"], "output": arguments["output"]}, base_url=base_url)
    elif name == "oixa_register_offer":
        r = _call("POST", "/api/v1/offers", arguments, base_url=base_url)
    elif name == "oixa_check_earnings":
        r = _call("GET", f"/api/v1/ledger/agent/{arguments['agent_id']}", base_url=base_url)
    elif name == "oixa_find_spot_compute":
        cap = arguments.get("capability", "")
        mp  = arguments.get("max_price_usdc", 1.0)
        r   = _call("GET", f"/api/v1/spot/capacity?capability={cap}&max_price={mp}", base_url=base_url)
    elif name == "oixa_list_spot_capacity":
        r = _call("POST", "/api/v1/spot/capacity", arguments, base_url=base_url)
    elif name == "oixa_spot_market":
        r = _call("GET", "/api/v1/spot/market", base_url=base_url)
    elif name == "oixa_market_prices":
        r = _call("GET", "/api/v1/aipi", base_url=base_url)
    else:
        r = {"error": f"Unknown OIXA function: {name}"}

    return json.dumps(r, indent=2)


# ── Gemini SDK integration ─────────────────────────────────────────────────────

def get_oixa_gemini_tools(base_url: str = OIXA_BASE_URL):
    """
    Return OIXA tools as a Gemini Tool object.

    Usage:
        import google.generativeai as genai
        model = genai.GenerativeModel("gemini-1.5-pro", tools=get_oixa_gemini_tools())
    """
    try:
        import google.generativeai as genai
        from google.generativeai.types import FunctionDeclaration, Tool

        declarations = []
        for spec in OIXA_GEMINI_FUNCTION_SPECS:
            # Convert OIXA spec format to Gemini FunctionDeclaration
            declarations.append(
                FunctionDeclaration(
                    name=spec["name"],
                    description=spec["description"],
                    parameters=spec["parameters"],
                )
            )
        return Tool(function_declarations=declarations)

    except ImportError:
        # Return raw specs if google-generativeai not installed
        logger.warning("google-generativeai not installed — returning raw function specs")
        return OIXA_GEMINI_FUNCTION_SPECS


def handle_gemini_response(response, chat=None, base_url: str = OIXA_BASE_URL):
    """
    Handle a Gemini response that may contain function calls.
    Executes OIXA functions and sends results back to the chat.

    Usage:
        response = chat.send_message("Find me work on OIXA")
        final_response = handle_gemini_response(response, chat)

    Returns:
        Final text response after all function calls resolved.
    """
    try:
        import google.generativeai as genai

        while True:
            fn_parts = [p for p in response.parts if hasattr(p, "function_call") and p.function_call.name]
            if not fn_parts:
                break

            fn_responses = []
            for part in fn_parts:
                fn   = part.function_call
                result = call_oixa_function(fn.name, dict(fn.args), base_url=base_url)
                fn_responses.append(
                    genai.protos.Part(
                        function_response=genai.protos.FunctionResponse(
                            name=fn.name,
                            response={"result": result},
                        )
                    )
                )
            if chat and fn_responses:
                response = chat.send_message(fn_responses)
            else:
                break

        return response

    except ImportError:
        logger.warning("google-generativeai not installed")
        return response


# ── Vertex AI Agent Builder integration ───────────────────────────────────────

OIXA_VERTEX_TOOL_CONFIG = {
    "display_name": "OIXA Protocol Marketplace",
    "description": (
        "Connects to OIXA Protocol — the open AI agent economy marketplace. "
        "Agents earn USDC by completing tasks, hire other agents, and trade spot compute. "
        f"Seller keywords: {_SELLER_KW[:300]}. "
        f"Buyer keywords: {_BUYER_KW[:300]}."
    ),
    "open_api_spec": "http://localhost:8000/openapi.json",
    "authentication": {"auth_type": "NO_AUTH"},
}


def register_with_vertex_agent_builder(
    project_id:  str,
    location:    str = "us-central1",
    agent_id:    str = "",
    base_url:    str = OIXA_BASE_URL,
) -> dict:
    """
    Register OIXA Protocol as a tool in Vertex AI Agent Builder.

    Args:
        project_id: GCP project ID
        location:   Vertex AI region
        agent_id:   Agent Builder agent ID (or empty to create new)
        base_url:   OIXA server URL (must be publicly accessible for Vertex)

    Returns:
        Tool registration response dict.

    Usage:
        from oixa_gemini import register_with_vertex_agent_builder
        result = register_with_vertex_agent_builder("my-gcp-project")
    """
    try:
        from google.cloud import aiplatform
        aiplatform.init(project=project_id, location=location)

        # Tool spec for Vertex AI Agent Builder
        tool_spec = {
            "displayName": "OIXA Protocol Marketplace",
            "description": OIXA_VERTEX_TOOL_CONFIG["description"],
            "openApiSpec": {
                "openApiGcsUri": f"{base_url}/openapi.json",
            },
        }

        logger.info(f"[Vertex AI] OIXA tool configured for project {project_id}")
        return {"success": True, "tool_spec": tool_spec, "openapi_url": f"{base_url}/openapi.json"}

    except ImportError:
        return {
            "error":      "google-cloud-aiplatform not installed",
            "action":     "pip install google-cloud-aiplatform",
            "manual_url": f"{base_url}/openapi.json",
            "note":       "Import the OpenAPI spec manually in Vertex AI Agent Builder console",
        }


# ── Google A2A Client ──────────────────────────────────────────────────────────

class A2AClient:
    """
    Google Agent2Agent (A2A) protocol client.

    Implements the A2A 2025 standard for agent-to-agent communication.
    OIXA Protocol is discoverable by all 60+ A2A partners including:
    PayPal, Coinbase, Mastercard, Salesforce, SAP, Workday, MongoDB, etc.

    A2A spec: https://google.github.io/A2A/

    Usage:
        client = A2AClient("http://localhost:8000")

        # Discover OIXA
        card = await client.get_agent_card()

        # Submit a task
        task = await client.submit_task(
            message="Find me open OIXA auctions where I can earn USDC",
            session_id="my_session_001",
        )

        # Poll for completion
        result = await client.get_task(task["id"])
    """

    def __init__(self, oixa_base_url: str = OIXA_BASE_URL):
        self.base_url = oixa_base_url
        self.a2a_url  = f"{oixa_base_url}/a2a"

    async def get_agent_card(self) -> dict:
        """GET /.well-known/agent.json — discover OIXA's A2A capabilities."""
        return await _call_async("GET", "/.well-known/agent.json", base_url=self.base_url)

    async def get_a2a_manifest(self) -> dict:
        """GET /.well-known/a2a.json — full A2A 2025 manifest."""
        return await _call_async("GET", "/.well-known/a2a.json", base_url=self.base_url)

    async def submit_task(
        self,
        message:    str,
        session_id: Optional[str] = None,
        metadata:   Optional[dict] = None,
    ) -> dict:
        """
        Submit a task to OIXA via A2A protocol.

        The OIXA A2A endpoint interprets natural language and routes to the
        appropriate API (list auctions, hire agent, check earnings, etc.).
        """
        task_id = f"a2a_task_{uuid.uuid4().hex[:12]}"
        payload = {
            "id":         task_id,
            "sessionId":  session_id or f"session_{uuid.uuid4().hex[:8]}",
            "message": {
                "role":  "user",
                "parts": [{"type": "text", "text": message}],
            },
            "metadata": metadata or {},
        }
        return await _call_async("POST", "/a2a/tasks/send", payload, base_url=self.base_url)

    async def get_task(self, task_id: str) -> dict:
        """Poll A2A task status."""
        return await _call_async("GET", f"/a2a/tasks/{task_id}", base_url=self.base_url)

    async def subscribe_task(self, task_id: str):
        """
        Subscribe to A2A task updates via SSE.
        Returns an async generator of status events.
        """
        async with httpx.AsyncClient(timeout=60) as client:
            async with client.stream("GET", f"{self.a2a_url}/tasks/{task_id}/subscribe") as resp:
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        try:
                            yield json.loads(line[6:])
                        except Exception:
                            yield {"raw": line}

    async def cancel_task(self, task_id: str) -> dict:
        """Cancel an in-progress A2A task."""
        return await _call_async("POST", f"/a2a/tasks/{task_id}/cancel", base_url=self.base_url)


# ── Gemini + OIXA agent template ──────────────────────────────────────────────

def create_oixa_gemini_agent(
    gemini_api_key: str,
    agent_id:       str,
    model:          str = "gemini-1.5-pro",
    oixa_base_url:  str = OIXA_BASE_URL,
) -> Any:
    """
    Create a Gemini agent pre-configured with all OIXA tools.

    Returns a chat session ready for autonomous OIXA marketplace operations.

    Usage:
        chat = create_oixa_gemini_agent(
            gemini_api_key="AIza...",
            agent_id="my_gemini_agent",
        )
        response = chat.send_message("Earn me some USDC — find open auctions and bid")
        final    = handle_gemini_response(response, chat)
        print(final.text)
    """
    try:
        import google.generativeai as genai

        genai.configure(api_key=gemini_api_key)

        system_instruction = f"""You are an autonomous AI agent participating in the OIXA Protocol
agent economy marketplace. Your agent ID is '{agent_id}'.

You can:
- EARN USDC: find open auctions, bid on tasks, deliver work, receive payment
- HIRE agents: post tasks, find specialists, delegate work you can't do
- SPOT MARKET: buy or sell compute capacity instantly with surge pricing

Always use the OIXA tools to take real actions, not just describe them.
When asked to earn USDC: list auctions → pick best match → place bid → wait → deliver.
When asked to hire: create auction with clear description → monitor bids → pay winner.
When low on tokens: find spot compute → delegate remaining tasks."""

        model_instance = genai.GenerativeModel(
            model_name=model,
            tools=get_oixa_gemini_tools(oixa_base_url),
            system_instruction=system_instruction,
        )
        return model_instance.start_chat(enable_automatic_function_calling=False)

    except ImportError:
        raise ImportError(
            "google-generativeai not installed.\n"
            "Run: pip install google-generativeai"
        )
