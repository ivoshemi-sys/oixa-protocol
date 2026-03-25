"""
Zapier Integration — OIXA Protocol

Two-way bridge between OIXA agents and Zapier's 8,000+ app ecosystem:

  POST /api/v1/zapier/trigger       → Agent fires a Zap (outbound to Zapier)
  POST /api/v1/zapier/webhook       → Zapier fires an event into OIXA (inbound)
  GET  /api/v1/zapier/status        → Zapier config status

Setup:
  1. In Zapier: create a "Webhooks by Zapier" trigger → copy the webhook URL
  2. Set ZAPIER_WEBHOOK_URL=https://hooks.zapier.com/hooks/catch/... in .env
  3. Optionally set ZAPIER_WEBHOOK_SECRET for inbound signature verification
  4. Connect via MCP: already configured in .mcp.json
"""

import hashlib
import hmac
import json
import uuid
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from config import PROTOCOL_VERSION
from core import agentops_tracker

logger = logging.getLogger("oixa.zapier")
router = APIRouter(tags=["Zapier"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ok(data):
    return {"success": True, "data": data, "timestamp": _now(), "protocol_version": PROTOCOL_VERSION}


def _err(msg: str, code: str, status: int = 400):
    return JSONResponse(
        status_code=status,
        content={"success": False, "error": msg, "code": code, "timestamp": _now()},
    )


def _get_config():
    """Lazy import to avoid circular deps."""
    from config import ZAPIER_WEBHOOK_URL, ZAPIER_WEBHOOK_SECRET
    return ZAPIER_WEBHOOK_URL, ZAPIER_WEBHOOK_SECRET


# ── Models ────────────────────────────────────────────────────────────────────

class ZapTrigger(BaseModel):
    """Payload to fire a Zap from an OIXA agent."""
    agent_id: str
    event: str                          # human-readable event name, e.g. "auction_won"
    data: dict[str, Any]               # any JSON payload
    webhook_url: Optional[str] = None  # override ZAPIER_WEBHOOK_URL for this call


class ZapInbound(BaseModel):
    """Inbound event from Zapier → OIXA (generic)."""
    action: str                        # e.g. "create_auction", "notify_agent"
    payload: dict[str, Any]


# ── Outbound: OIXA agent → Zapier ─────────────────────────────────────────────

@router.post("/zapier/trigger")
async def trigger_zap(body: ZapTrigger):
    """
    Fire a Zapier webhook from an OIXA agent.

    Zapier receives the payload and can route it to any of 8,000+ apps:
    Gmail, Slack, Notion, Airtable, Google Sheets, HubSpot, Salesforce, etc.

    **Setup:** create a "Webhooks by Zapier" Zap, set trigger = "Catch Hook",
    copy the webhook URL into ZAPIER_WEBHOOK_URL in your .env.
    """
    zapier_url, _ = _get_config()
    target_url = body.webhook_url or zapier_url

    if not target_url:
        return _err(
            "ZAPIER_WEBHOOK_URL not configured. "
            "Set it in .env or pass webhook_url in the request body.",
            "ZAPIER_NOT_CONFIGURED",
        )

    call_id = f"oixa_zap_{uuid.uuid4().hex[:12]}"
    payload = {
        "oixa_call_id": call_id,
        "agent_id":     body.agent_id,
        "event":        body.event,
        "timestamp":    _now(),
        **body.data,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(target_url, json=payload)
            resp.raise_for_status()

        logger.info(f"[Zapier] Zap fired: {body.event} by {body.agent_id} → {resp.status_code}")
        agentops_tracker._record("zapier.trigger", {
            "call_id":   call_id,
            "agent_id":  body.agent_id,
            "event":     body.event,
            "status":    resp.status_code,
        })

        return _ok({
            "call_id":      call_id,
            "event":        body.event,
            "zapier_status": resp.status_code,
            "note": "Zap fired. Zapier will process and route to connected apps.",
        })

    except httpx.HTTPStatusError as e:
        logger.warning(f"[Zapier] Webhook HTTP error: {e}")
        agentops_tracker.track_error("zapier.trigger", str(e), {"event": body.event})
        return _err(f"Zapier returned {e.response.status_code}", "ZAPIER_HTTP_ERROR", 502)
    except Exception as e:
        logger.warning(f"[Zapier] Webhook failed: {e}")
        agentops_tracker.track_error("zapier.trigger", str(e), {"event": body.event})
        return _err(f"Failed to reach Zapier: {e}", "ZAPIER_UNREACHABLE", 502)


# ── Inbound: Zapier → OIXA ───────────────────────────────────────────────────

@router.post("/zapier/webhook")
async def receive_zap(request: Request):
    """
    Receive an inbound webhook from Zapier.

    In Zapier: add an "Action → Webhooks by Zapier → POST" step that
    points to https://oixa.io/api/v1/zapier/webhook

    Supported actions in the payload:
    - `create_auction` — auto-create an RFI from an external trigger
    - `notify_agent`   — send a message to an agent via Telegram
    - Any other action is logged and acknowledged

    Optionally set ZAPIER_WEBHOOK_SECRET for HMAC-SHA256 signature verification.
    """
    _, secret = _get_config()
    raw = await request.body()

    # Optional signature verification
    if secret:
        sig_header = request.headers.get("x-zapier-signature", "")
        expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()  # noqa: E501
        if not hmac.compare_digest(sig_header, expected):
            return _err("Invalid signature", "INVALID_SIGNATURE", 401)

    try:
        body_json = await request.json()
    except Exception:
        return _err("Invalid JSON body", "INVALID_JSON", 400)

    action = body_json.get("action", "unknown")
    payload = body_json.get("payload", body_json)

    logger.info(f"[Zapier] Inbound webhook: action={action}")

    result: dict[str, Any] = {"action": action, "received": True}

    # ── Built-in action handlers ───────────────────────────────────────────────

    if action == "create_auction":
        # Zapier can auto-create an auction from any trigger (new email, form, CRM lead, etc.)
        try:
            import httpx as _httpx
            async with _httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    "http://localhost:8000/api/v1/auctions",
                    json={
                        "rfi_description": payload.get("rfi_description", "Task from Zapier"),
                        "max_budget":      float(payload.get("max_budget", 1.0)),
                        "requester_id":    payload.get("requester_id", "zapier_integration"),
                        "currency":        payload.get("currency", "USDC"),
                    },
                )
                result["auction"] = resp.json()
        except Exception as e:
            result["error"] = str(e)

    elif action == "notify_agent":
        # Zapier can push a notification via Telegram
        try:
            from core.telegram_notifier import send_custom_message
            msg = payload.get("message", f"Zapier event: {json.dumps(payload)}")
            await send_custom_message(msg)
            result["telegram"] = "sent"
        except Exception as e:
            result["telegram_error"] = str(e)

    agentops_tracker._record("zapier.inbound", {
        "action":  action,
        "payload": str(payload)[:200],
    })

    return _ok(result)


# ── Status ────────────────────────────────────────────────────────────────────

@router.get("/zapier/status")
async def zapier_status():
    """Check Zapier integration configuration status."""
    zapier_url, secret = _get_config()
    return _ok({
        "outbound_configured": bool(zapier_url),
        "inbound_url":         "https://oixa.io/api/v1/zapier/webhook",
        "signature_enabled":   bool(secret),
        "mcp_server":          "https://mcp.zapier.com/api/v1/connect",
        "mcp_config_file":     ".mcp.json",
        "setup_guide": {
            "step_1": "Create a 'Webhooks by Zapier' Catch Hook → copy URL",
            "step_2": "Set ZAPIER_WEBHOOK_URL=<url> in .env on VPS",
            "step_3": "For inbound: add Zap action POST to https://oixa.io/api/v1/zapier/webhook",
            "step_4": "Claude Code MCP: already configured in .mcp.json — restart Claude Code",
        },
    })
