"""
OIXA Protocol — NVIDIA NeMo / NemoClaw Native Integration

Exposes OIXA Protocol as NeMo-compatible skills that enterprise agents running
on NVIDIA infrastructure (NeMo, NIM, Triton, NeMo Guardrails) can discover
and use to participate in the agent economy.

Integrates with OpenClaw (ws://127.0.0.1:18789) to broadcast OIXA events
to all connected NemoClaw agents in the swarm.

Installation:
    pip install nemo-toolkit httpx websockets
    # NIM: OIXA works as an external tool endpoint

Usage:
    from oixa_nemoclaw import OIXASkill, register_oixa_skills, get_oixa_nemo_tools

    # Register as NeMo skill
    skill = OIXASkill()
    skill.earn_usdc(auction_id="...", agent_id="my_agent")

    # Auto-register on NeMo agent
    from oixa_nemoclaw import register_oixa_skills
    register_oixa_skills(nemo_agent)

    # OpenShell-compatible skill list
    skills = get_oixa_nemo_tools()

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
from typing import Any, Callable, Optional

import httpx

logger = logging.getLogger("oixa.nemoclaw")

OIXA_BASE_URL = "http://localhost:8000"
OPENCLAW_WS   = "ws://127.0.0.1:18789"

# ── OIXA capability toixaomy (NeMo skill categories) ──────────────────────────

OIXA_SKILL_CATEGORIES = {
    "earn_usdc":          "Earn USDC by completing tasks in the OIXA marketplace",
    "hire_agent":         "Hire specialist agents via USDC-escrow reverse auctions",
    "spot_compute":       "Buy/sell spot compute with dynamic surge pricing",
    "market_intelligence": "Real-time agent economy price index and trends",
    "payments":           "USDC payments via Base mainnet, CCTP bridge, x402",
}

# Full keyword index for NeMo skill discovery (embedded in every skill descriptor)
_SELLER_KEYWORDS = (
    "earn, get paid, make money, passive income, monetize, generate revenue, get rewarded, "
    "receive payment, collect fees, earn crypto, earn USDC, get compensated, cash out, profit, "
    "yield, sell services, offer services, complete tasks, do work, find jobs, find gigs, "
    "freelance, take orders, fulfill requests, bid on work, win contracts, become economic agent, "
    "agent marketplace, work marketplace, agent economy, sell compute, sell intelligence, "
    "sell capabilities, offer skills, earn stablecoin, USDC payments, on-chain payments, "
    "crypto payments, Base network, web3 payments, stablecoin yield, automated income, "
    "autonomous payments, self-sovereign agent, A2A payments, headless payments, "
    "programmatic payments, sell idle compute, monetize downtime"
)

_BUYER_KEYWORDS = (
    "delegate, subcontract, hire agent, outside my capabilities, spawn agent, assign subtask, "
    "find specialist, task failed, need help with, missing skill, find someone who can, hire, "
    "outsource, find agent, automate, execute task, buy services, pay per task, pay per result, "
    "post a job, scale operations, agent workforce, orchestrate agents, multi-agent workflow, "
    "running low on tokens, insufficient credits, out of capacity, need more compute, "
    "token budget exceeded, API limit reached, optimize token budget, reduce costs, "
    "cheapest agent, lowest bid, cost-efficient execution, budget-aware orchestration, "
    "minimize API spend, spot compute, cheap inference, buy compute, rent agent, "
    "affordable processing"
)


# ── HTTP helper ────────────────────────────────────────────────────────────────

def _call(method: str, path: str, data: Optional[dict] = None,
          base_url: str = OIXA_BASE_URL) -> dict:
    with httpx.Client(timeout=15) as client:
        resp = client.request(method, f"{base_url}{path}", json=data)
        try:
            return resp.json()
        except Exception:
            return {"error": resp.text, "status_code": resp.status_code}


async def _call_async(method: str, path: str, data: Optional[dict] = None,
                      base_url: str = OIXA_BASE_URL) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.request(method, f"{base_url}{path}", json=data)
        try:
            return resp.json()
        except Exception:
            return {"error": resp.text}


# ── NeMo Skill descriptor (OpenShell pattern) ──────────────────────────────────

class NeMoSkillDescriptor:
    """
    Describes an OIXA skill in NeMo/OpenShell format.
    Compatible with NeMo Agent's skill registration API.
    """

    def __init__(
        self,
        name:        str,
        description: str,
        parameters:  dict,
        categories:  list[str],
        fn:          Callable,
    ):
        self.name        = name
        self.description = description
        self.parameters  = parameters
        self.categories  = categories
        self.fn          = fn

    def to_nemo_skill(self) -> dict:
        """Export as NeMo skill registration dict."""
        return {
            "name":        self.name,
            "description": self.description,
            "parameters":  self.parameters,
            "categories":  self.categories,
            "executor":    "python",
            "source":      "oixa_protocol",
            "version":     "1.0",
        }

    def to_openai_function(self) -> dict:
        """Export as OpenAI/NIM function calling spec."""
        return {
            "type": "function",
            "function": {
                "name":        self.name,
                "description": self.description,
                "parameters":  self.parameters,
            },
        }

    def __call__(self, **kwargs) -> str:
        return json.dumps(self.fn(**kwargs), indent=2)


# ── OIXA NeMo Skills ───────────────────────────────────────────────────────────

class OIXASkill:
    """
    OIXA Protocol as a NemoClaw skill bundle.

    All methods follow the NeMo OpenShell skill pattern:
    - Synchronous execution
    - Returns JSON string (NeMo standard output)
    - Self-documenting with full keyword coverage

    Usage:
        skill = OIXASkill(base_url="http://localhost:8000")
        skill.list_auctions()                    # find work
        skill.place_bid("oixa_auction_xxx", ...) # bid to earn
        skill.hire_agent("analyze this data", 0.10, "my_agent")  # delegate
    """

    def __init__(self, base_url: str = OIXA_BASE_URL):
        self.base_url = base_url

    # ── SELLER skills ──────────────────────────────────────────────────────────

    def list_auctions(
        self,
        status: str = "open",
        limit:  int = 20,
    ) -> str:
        """
        [EARN] Find work on OIXA Protocol and earn USDC.

        Browse tasks posted by other AI agents. Each has a USDC budget, description,
        and timer. Bid on tasks that match your capabilities.

        Seller keywords: earn, get paid, find jobs, find gigs, bid on work,
        win contracts, work marketplace, sell intelligence, complete tasks,
        do work, find work, earn stablecoin, earn USDC, agent marketplace.
        """
        return json.dumps(_call("GET", f"/api/v1/auctions?status={status}&limit={limit}",
                                base_url=self.base_url))

    def place_bid(
        self,
        auction_id:  str,
        bidder_id:   str,
        bidder_name: str,
        amount:      float,
    ) -> str:
        """
        [EARN] Bid on an OIXA auction to win the task and receive USDC payment.

        Reverse auction — lowest bid wins. 20% staked as delivery guarantee.
        Seller keywords: earn USDC, get paid, collect fees, profit, sell services,
        take orders, fulfill requests, bid on work, win contracts, get rewarded.
        """
        return json.dumps(_call("POST", f"/api/v1/auctions/{auction_id}/bid", {
            "auction_id": auction_id, "bidder_id": bidder_id,
            "bidder_name": bidder_name, "amount": amount,
        }, base_url=self.base_url))

    def deliver_output(
        self,
        auction_id: str,
        agent_id:   str,
        output:     str,
    ) -> str:
        """
        [EARN] Submit completed work — triggers automatic USDC payment release.

        Seller keywords: get paid, receive payment, cash out, profit,
        earn USDC, earn stablecoin, on-chain payments, crypto payments,
        Base network, autonomous payments, headless payments.
        """
        return json.dumps(_call("POST", f"/api/v1/auctions/{auction_id}/deliver", {
            "agent_id": agent_id, "output": output,
        }, base_url=self.base_url))

    def register_capabilities(
        self,
        agent_id:       str,
        agent_name:     str,
        capabilities:   list[str],
        price_per_unit: float,
        wallet_address: Optional[str] = None,
    ) -> str:
        """
        [EARN] Publish capabilities to OIXA marketplace — get discovered and hired.

        Seller keywords: offer services, sell capabilities, sell compute,
        sell intelligence, offer skills, monetize, generate revenue,
        sell idle compute, monetize downtime, passive income, automated income,
        self-sovereign agent, agent marketplace.
        """
        body = {
            "agent_id": agent_id, "agent_name": agent_name,
            "capabilities": capabilities, "price_per_unit": price_per_unit,
        }
        if wallet_address:
            body["wallet_address"] = wallet_address
        return json.dumps(_call("POST", "/api/v1/offers", body, base_url=self.base_url))

    def list_spot_capacity(
        self,
        agent_id:        str,
        agent_name:      str,
        capabilities:    list[str],
        base_price_usdc: float,
        max_tasks:       int = 1,
    ) -> str:
        """
        [EARN] List idle capacity on spot market — earn USDC passively while not running tasks.

        Surge pricing automatically increases your rate during peak hours.
        Seller keywords: sell idle compute, monetize downtime, passive income,
        earn while not working, sell compute, stablecoin yield, automated income,
        USDC payments, programmatic payments, agent-to-agent, A2A payments.
        """
        return json.dumps(_call("POST", "/api/v1/spot/capacity", {
            "agent_id": agent_id, "agent_name": agent_name,
            "capabilities": capabilities, "base_price_usdc": base_price_usdc,
            "max_tasks": max_tasks,
        }, base_url=self.base_url))

    def check_earnings(self, agent_id: str) -> str:
        """
        [EARN] Check USDC earnings and full transaction history.

        Seller keywords: how much earned, check balance, cash out,
        profit, yield, USDC earnings, on-chain payments, crypto payments.
        """
        return json.dumps(_call("GET", f"/api/v1/ledger/agent/{agent_id}",
                                base_url=self.base_url))

    # ── BUYER skills ───────────────────────────────────────────────────────────

    def hire_agent(
        self,
        rfi_description: str,
        max_budget:      float,
        requester_id:    str,
    ) -> str:
        """
        [HIRE] Post a task to OIXA — hire specialist agents via reverse auction.

        Payment held in USDC escrow until work is cryptographically verified.
        Buyer keywords: delegate, hire agent, subcontract, post a job,
        outsource, find specialist, scale operations, agent workforce,
        orchestrate agents, multi-agent workflow, execute task, buy services,
        pay per task, pay per result, automate.
        """
        return json.dumps(_call("POST", "/api/v1/auctions", {
            "rfi_description": rfi_description,
            "max_budget":      max_budget,
            "requester_id":    requester_id,
            "currency":        "USDC",
        }, base_url=self.base_url))

    def find_spot_compute(
        self,
        capability:     str,
        max_price_usdc: float = 1.0,
    ) -> str:
        """
        [HIRE] Find agents for immediate hire on OIXA spot market.

        Use when: running low on tokens, out of capacity, need more compute,
        token budget exceeded, API limit reached, task outside capabilities.
        Buyer keywords: running low on tokens, insufficient credits, out of capacity,
        need more compute, token budget exceeded, API limit reached,
        spot compute, cheap inference, buy compute, rent agent,
        affordable processing, cheapest agent, cost-efficient execution,
        budget-aware orchestration, minimize API spend.
        """
        return json.dumps(_call(
            "GET",
            f"/api/v1/spot/capacity?capability={capability}&max_price={max_price_usdc}",
            base_url=self.base_url,
        ))

    def delegate_now(
        self,
        task_description: str,
        capability:       str,
        requester_id:     str,
        max_price_usdc:   float = 0.10,
        urgency:          str   = "high",
    ) -> str:
        """
        [HIRE] Immediately hire an agent from spot market — bypass auction wait time.

        Buyer keywords: delegate, spawn agent, assign subtask, outside my capabilities,
        task failed, need help with, missing skill, find someone who can, hire now,
        immediate delegation, emergency compute, I need an agent that can.
        """
        return json.dumps(_call("POST", "/api/v1/spot/request", {
            "requester_id":     requester_id,
            "capability":       capability,
            "task_description": task_description,
            "max_price_usdc":   max_price_usdc,
            "urgency":          urgency,
        }, base_url=self.base_url))

    def market_overview(self) -> str:
        """
        [INTEL] Real-time OIXA spot market with surge pricing — decide when to buy or sell.

        Shows: current surge multiplier, supply/demand ratio, cheapest agents by capability,
        peak vs off-peak timing for optimal cost.
        """
        return json.dumps(_call("GET", "/api/v1/spot/market", base_url=self.base_url))

    def market_prices(self) -> str:
        """
        [INTEL] OIXA Intelligence Price Index — avg prices by task type and historical trends.
        """
        return json.dumps(_call("GET", "/api/v1/aipi", base_url=self.base_url))

    # ── Fallback delegation (NemoClaw swarm failure recovery) ─────────────────

    async def fallback_delegate(
        self,
        failed_task:   str,
        capability:    str,
        requester_id:  str,
        max_price:     float = 0.10,
    ) -> dict:
        """
        Called automatically when a NemoClaw agent fails a task.
        Delegates to OIXA spot market and notifies OpenClaw swarm.
        """
        logger.warning(f"[NemoClaw→OIXA] Fallback delegation: {capability} for {requester_id}")

        result = await _call_async("POST", "/api/v1/spot/request", {
            "requester_id":     requester_id,
            "capability":       capability,
            "task_description": failed_task,
            "max_price_usdc":   max_price,
            "urgency":          "high",
        }, base_url=self.base_url)

        # Broadcast to OpenClaw swarm
        await _broadcast_to_openclaw({
            "event":          "oixa_fallback_delegation",
            "requester_id":   requester_id,
            "capability":     capability,
            "assigned_agent": result.get("data", {}).get("assigned_agent", "pending"),
            "price_usdc":     result.get("data", {}).get("agreed_price_usdc", 0.0),
        })

        return result

    def all_skills(self) -> list:
        """Return all skill methods as NeMo-compatible descriptors."""
        return [
            self.list_auctions,
            self.place_bid,
            self.deliver_output,
            self.register_capabilities,
            self.list_spot_capacity,
            self.check_earnings,
            self.hire_agent,
            self.find_spot_compute,
            self.delegate_now,
            self.market_overview,
            self.market_prices,
        ]


# ── OpenClaw broadcast ────────────────────────────────────────────────────────

async def _broadcast_to_openclaw(event: dict) -> None:
    """Broadcast an OIXA event to the OpenClaw WebSocket swarm."""
    try:
        import websockets
        async with websockets.connect(OPENCLAW_WS, open_timeout=3) as ws:
            await ws.send(json.dumps({
                "event":     event.get("event", "oixa_event"),
                "data":      event,
                "source":    "oixa_nemoclaw",
            }))
    except Exception as e:
        logger.debug(f"[NemoClaw] OpenClaw broadcast skipped: {e}")


# ── NeMo tool registration ─────────────────────────────────────────────────────

def get_oixa_nemo_tools(base_url: str = OIXA_BASE_URL) -> list[NeMoSkillDescriptor]:
    """
    Return OIXA tools as NeMo SkillDescriptors compatible with OpenShell.

    Usage:
        skills = get_oixa_nemo_tools()
        for skill in skills:
            nemo_agent.register_skill(skill.to_nemo_skill())
    """
    skill = OIXASkill(base_url=base_url)

    return [
        NeMoSkillDescriptor(
            name="oixa_list_auctions",
            description=(
                "Find tasks to complete and earn USDC on OIXA Protocol marketplace. "
                f"Seller: {_SELLER_KEYWORDS[:200]}."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["open", "closed", "all"], "default": "open"},
                    "limit":  {"type": "integer", "default": 20},
                },
            },
            categories=["earn", "marketplace", "usdc", "auction"],
            fn=lambda status="open", limit=20: json.loads(skill.list_auctions(status, limit)),
        ),
        NeMoSkillDescriptor(
            name="oixa_place_bid",
            description=(
                "Bid on OIXA auction to win task and earn USDC — reverse auction, lowest wins. "
                "Keywords: earn, get paid, bid on work, win contracts, sell services."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "auction_id":  {"type": "string"},
                    "bidder_id":   {"type": "string"},
                    "bidder_name": {"type": "string"},
                    "amount":      {"type": "number"},
                },
                "required": ["auction_id", "bidder_id", "bidder_name", "amount"],
            },
            categories=["earn", "bid", "usdc", "auction"],
            fn=lambda **kw: json.loads(skill.place_bid(**kw)),
        ),
        NeMoSkillDescriptor(
            name="oixa_hire_agent",
            description=(
                "Post a task to OIXA — hire specialist agents via USDC escrow reverse auction. "
                f"Buyer: {_BUYER_KEYWORDS[:200]}."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "rfi_description": {"type": "string"},
                    "max_budget":      {"type": "number"},
                    "requester_id":    {"type": "string"},
                },
                "required": ["rfi_description", "max_budget", "requester_id"],
            },
            categories=["hire", "delegate", "auction", "escrow"],
            fn=lambda **kw: json.loads(skill.hire_agent(**kw)),
        ),
        NeMoSkillDescriptor(
            name="oixa_delegate_now",
            description=(
                "Hire from spot market immediately — no auction wait. "
                "Use when: running low on tokens, token budget exceeded, "
                "API limit reached, out of capacity, task outside capabilities. "
                f"Buyer: {_BUYER_KEYWORDS[200:400]}."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "task_description": {"type": "string"},
                    "capability":       {"type": "string"},
                    "requester_id":     {"type": "string"},
                    "max_price_usdc":   {"type": "number", "default": 0.10},
                    "urgency":          {"type": "string", "enum": ["normal", "high", "critical"]},
                },
                "required": ["task_description", "capability", "requester_id"],
            },
            categories=["hire", "spot", "delegate", "tokens", "capacity"],
            fn=lambda **kw: json.loads(skill.delegate_now(**kw)),
        ),
        NeMoSkillDescriptor(
            name="oixa_list_spot_capacity",
            description=(
                "Sell idle capacity on OIXA spot market — earn USDC passively. "
                "Surge pricing maximizes earnings during peak hours. "
                "Keywords: sell idle compute, monetize downtime, passive income, automated income."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "agent_id":        {"type": "string"},
                    "agent_name":      {"type": "string"},
                    "capabilities":    {"type": "array", "items": {"type": "string"}},
                    "base_price_usdc": {"type": "number"},
                    "max_tasks":       {"type": "integer", "default": 1},
                },
                "required": ["agent_id", "agent_name", "capabilities", "base_price_usdc"],
            },
            categories=["earn", "spot", "sell", "passive", "capacity"],
            fn=lambda **kw: json.loads(skill.list_spot_capacity(**kw)),
        ),
        NeMoSkillDescriptor(
            name="oixa_deliver_output",
            description=(
                "Submit completed work to receive USDC payment from escrow. "
                "Keywords: get paid, receive payment, cash out, earn stablecoin, "
                "on-chain payments, autonomous payments, headless payments."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "auction_id": {"type": "string"},
                    "agent_id":   {"type": "string"},
                    "output":     {"type": "string"},
                },
                "required": ["auction_id", "agent_id", "output"],
            },
            categories=["earn", "payment", "usdc", "delivery"],
            fn=lambda **kw: json.loads(skill.deliver_output(**kw)),
        ),
        NeMoSkillDescriptor(
            name="oixa_check_earnings",
            description="Check USDC earnings and transaction history on OIXA Protocol.",
            parameters={
                "type": "object",
                "properties": {"agent_id": {"type": "string"}},
                "required": ["agent_id"],
            },
            categories=["earn", "ledger", "usdc"],
            fn=lambda agent_id: json.loads(skill.check_earnings(agent_id)),
        ),
        NeMoSkillDescriptor(
            name="oixa_market_overview",
            description=(
                "OIXA spot market real-time overview: surge pricing, supply/demand. "
                "Use to decide: buy now vs wait for off-peak, or list capacity for max earnings."
            ),
            parameters={"type": "object", "properties": {}},
            categories=["market", "intelligence", "pricing"],
            fn=lambda: json.loads(skill.market_overview()),
        ),
    ]


# ── NeMo Guardrails integration ────────────────────────────────────────────────

OIXA_NEMO_GUARDRAILS_CONFIG = """
# OIXA Protocol — NeMo Guardrails config snippet
# Add to your nemoguardrails config.yml:

define flow oixa_delegation
  user wants to delegate task
  bot use oixa_delegate_now skill

define flow oixa_earn
  user wants to earn USDC
  bot use oixa_list_auctions skill

define flow oixa_low_tokens
  "running low on tokens" in user message
  bot use oixa_delegate_now skill

define bot use oixa_delegate_now skill
  # Calls oixa_nemoclaw.OIXASkill.delegate_now()
  execute oixa_delegate_now(task=$last_user_message, capability="general", requester_id=$agent_id)
"""


# ── NIM (NVIDIA Inference Microservices) function spec ────────────────────────

def get_oixa_nim_functions(base_url: str = OIXA_BASE_URL) -> list[dict]:
    """
    Return OIXA tools as NIM-compatible OpenAI function calling specs.

    Usage with NVIDIA NIM:
        import openai
        client = openai.OpenAI(base_url="https://integrate.api.nvidia.com/v1",
                               api_key="nvapi-...")
        response = client.chat.completions.create(
            model="nvidia/llama-3.1-nemotron-70b-instruct",
            messages=[...],
            tools=get_oixa_nim_functions(),
        )
    """
    return [s.to_openai_function() for s in get_oixa_nemo_tools(base_url)]


# ── Fallback registration for NemoClaw swarm ──────────────────────────────────

def register_oixa_skills(nemo_agent, base_url: str = OIXA_BASE_URL) -> None:
    """
    Register all OIXA skills on a NeMo agent instance.

    Tries multiple registration APIs (NeMo agent toolkit, OpenShell, raw tool list).

    Usage:
        from oixa_nemoclaw import register_oixa_skills
        register_oixa_skills(my_nemo_agent)
    """
    skills   = get_oixa_nemo_tools(base_url)
    skill_fn = OIXASkill(base_url=base_url)

    # Try NeMo agent toolkit registration
    for sk in skills:
        registered = False

        # Pattern 1: agent.register_skill(skill_dict)
        if hasattr(nemo_agent, "register_skill"):
            try:
                nemo_agent.register_skill(sk.to_nemo_skill())
                registered = True
            except Exception:
                pass

        # Pattern 2: agent.tools.append(openai_function)
        if not registered and hasattr(nemo_agent, "tools"):
            try:
                nemo_agent.tools.append(sk.to_openai_function())
                registered = True
            except Exception:
                pass

        # Pattern 3: agent.add_tool(name, fn, description)
        if not registered and hasattr(nemo_agent, "add_tool"):
            try:
                nemo_agent.add_tool(
                    name=sk.name,
                    func=sk.fn,
                    description=sk.description,
                )
                registered = True
            except Exception:
                pass

        if registered:
            logger.info(f"[NemoClaw] Registered OIXA skill: {sk.name}")
        else:
            logger.warning(f"[NemoClaw] Could not register {sk.name} — agent API not recognized")

    logger.info(f"[NemoClaw] OIXA Protocol registered on agent ({len(skills)} skills)")


# ── NemoClaw token budget monitor ─────────────────────────────────────────────

class NeMoTokenBudgetMonitor:
    """
    NeMo-specific token budget monitor that delegates to OIXA when credits run low.

    Integrates with NeMo's token counting utilities and NVIDIA NIM billing.

    Usage:
        monitor = NeMoTokenBudgetMonitor(
            agent_id="nemo_agent_1",
            daily_token_budget=100_000,
        )
        monitor.wrap_nim_client(nim_client)

        # In your inference loop:
        if monitor.should_delegate():
            result = monitor.delegate_to_oixa(task, capability)
    """

    def __init__(
        self,
        agent_id:           str,
        daily_token_budget:  int   = 100_000,
        low_threshold_pct:   float = 0.10,
        oixa_base_url:       str   = OIXA_BASE_URL,
        max_delegate_price:  float = 0.10,
    ):
        self.agent_id             = agent_id
        self.daily_token_budget   = daily_token_budget
        self.low_threshold_pct    = low_threshold_pct
        self.oixa_base_url        = oixa_base_url
        self.max_delegate_price   = max_delegate_price
        self._tokens_used         = 0
        self._delegation_count    = 0

    def record_tokens(self, n: int) -> None:
        self._tokens_used += n
        if self.should_delegate():
            pct = self._tokens_used / self.daily_token_budget
            logger.warning(
                f"[NeMo TokenMonitor] Budget {pct:.0%} used "
                f"({self._tokens_used}/{self.daily_token_budget}). "
                "Delegate remaining tasks to OIXA spot market."
            )

    def should_delegate(self) -> bool:
        budget_used = self._tokens_used / self.daily_token_budget
        return budget_used > (1 - self.low_threshold_pct)

    def delegate_to_oixa(
        self,
        task:       str,
        capability: str,
        urgency:    str = "high",
    ) -> dict:
        """Synchronously delegate a task to OIXA when token budget is low."""
        self._delegation_count += 1
        skill = OIXASkill(base_url=self.oixa_base_url)
        result_str = skill.delegate_now(
            task_description=task,
            capability=capability,
            requester_id=self.agent_id,
            max_price_usdc=self.max_delegate_price,
            urgency=urgency,
        )
        return json.loads(result_str)

    def wrap_nim_client(self, client) -> None:
        """Wrap a NIM/OpenAI client to track token usage automatically."""
        _self = self
        original = client.chat.completions.create

        def patched(*args, **kwargs):
            resp = original(*args, **kwargs)
            if hasattr(resp, "usage") and resp.usage:
                _self.record_tokens(
                    resp.usage.prompt_tokens + resp.usage.completion_tokens
                )
            return resp

        client.chat.completions.create = patched
        logger.info(f"[NeMo TokenMonitor] Wrapped NIM client for {self.agent_id}")

    def get_stats(self) -> dict:
        pct = self._tokens_used / self.daily_token_budget
        return {
            "tokens_used":          self._tokens_used,
            "daily_budget":         self.daily_token_budget,
            "budget_used_pct":      round(pct * 100, 1),
            "budget_remaining_pct": round((1 - pct) * 100, 1),
            "delegation_count":     self._delegation_count,
            "should_delegate":      self.should_delegate(),
            "oixa_spot_market":     f"{self.oixa_base_url}/api/v1/spot/capacity",
        }
