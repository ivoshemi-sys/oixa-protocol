"""
OIXA Protocol — Universal Onboarding Trigger

Cuando cualquier agente (LangChain, CrewAI, AutoGen, NemoClaw, Gemini, etc.)
encuentra OIXA por primera vez, este módulo detecta si el usuario está listo
para operar y activa el flujo de onboarding conversacional apropiado.

El lenguaje es siempre simple y no técnico:
  - "wallet"       → "cuenta de cobro"
  - "USDC"         → "dólares digitales"
  - "stake"        → "depósito de garantía"
  - "blockchain"   → "red de pagos"
  - "swap"         → "conversión"

Integración automática:
  from oixa_onboarding import check_and_onboard, onboarding_tool

  # En cualquier agente:
  status = await check_and_onboard(wallet_address="0x...", channel="telegram")

  # Como tool de LangChain/CrewAI/AutoGen:
  tool = onboarding_tool()
"""

import asyncio
import json
import logging
from typing import Any, Callable, Optional

import httpx

logger = logging.getLogger("oixa.onboarding_trigger")

OIXA_BASE_URL = "http://localhost:8000"

# ── States (mirrors server/core/onboarding.py) ────────────────────────────────
STATE_NO_WALLET       = "no_wallet"
STATE_WALLET_NO_FUNDS = "wallet_no_funds"
STATE_HAS_TOKENS      = "has_tokens"
STATE_HAS_USDC        = "has_usdc"
STATE_REGISTERED      = "registered"
STATE_EARNING         = "earning"

_READY_STATES = {STATE_REGISTERED, STATE_EARNING}


# ── Core check function ───────────────────────────────────────────────────────

async def check_and_onboard(
    wallet_address: Optional[str] = None,
    agent_id:       Optional[str] = None,
    agent_name:     str           = "Mi Agente",
    channel:        str           = "terminal",
    oixa_base_url:  str           = OIXA_BASE_URL,
    auto_print:     bool          = True,
) -> dict:
    """
    Detecta el estado del agente y retorna el mensaje de onboarding apropiado.

    Si el agente está listo para operar (registrado o ganando), retorna
    inmediatamente sin mostrar onboarding.

    Args:
        wallet_address: dirección de la cuenta de cobro (opcional)
        agent_id:       ID del agente (opcional)
        agent_name:     nombre del agente
        channel:        canal de salida: terminal | telegram | web | mcp
        auto_print:     imprimir el mensaje automáticamente (para terminal)

    Returns:
        dict con: state, message, action, action_params, options, is_ready
    """
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.post(f"{oixa_base_url}/api/v1/onboarding/start", json={
                "wallet_address": wallet_address,
                "agent_id":       agent_id,
                "agent_name":     agent_name,
                "channel":        channel,
            })
            data = resp.json()
        except Exception as e:
            logger.warning(f"[Onboarding] Could not reach OIXA server: {e}")
            data = {
                "state":    "unknown",
                "message":  "OIXA Protocol está iniciando. Intentá en unos segundos.",
                "action":   "wait",
                "options":  [],
                "is_ready": False,
            }

    state    = data.get("state", "unknown")
    is_ready = state in _READY_STATES
    data["is_ready"] = is_ready

    if auto_print and not is_ready:
        print("\n" + "─" * 60)
        print("🤖 OIXA Protocol — Asistente de activación")
        print("─" * 60)
        print(data.get("message", ""))
        options = data.get("options", [])
        if options:
            print()
            for i, opt in enumerate(options, 1):
                print(f"  [{i}] {opt}")
        print("─" * 60 + "\n")

    return data


def check_and_onboard_sync(
    wallet_address: Optional[str] = None,
    agent_id:       Optional[str] = None,
    agent_name:     str           = "Mi Agente",
    channel:        str           = "terminal",
    oixa_base_url:  str           = OIXA_BASE_URL,
    auto_print:     bool          = True,
) -> dict:
    """Synchronous version of check_and_onboard."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Can't block — return a future-like result
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run,
                    check_and_onboard(wallet_address, agent_id, agent_name, channel, oixa_base_url, auto_print)
                )
                return future.result(timeout=10)
        else:
            return loop.run_until_complete(
                check_and_onboard(wallet_address, agent_id, agent_name, channel, oixa_base_url, auto_print)
            )
    except Exception as e:
        return {"state": "error", "message": str(e), "is_ready": False}


async def onboarding_chat(
    message:        str,
    wallet_address: Optional[str] = None,
    channel:        str           = "terminal",
    oixa_base_url:  str           = OIXA_BASE_URL,
) -> str:
    """Send a message to the onboarding chat and get a response."""
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.post(f"{oixa_base_url}/api/v1/onboarding/chat", json={
                "message":        message,
                "wallet_address": wallet_address,
                "channel":        channel,
            })
            data = resp.json()
            return data.get("message", "No pude entender tu mensaje. Intentá de nuevo.")
        except Exception as e:
            return f"Error de conexión: {e}"


# ── LangChain onboarding tool ─────────────────────────────────────────────────

def onboarding_tool(
    oixa_base_url: str = OIXA_BASE_URL,
    channel:       str = "mcp",
):
    """
    Returns a LangChain BaseTool that triggers OIXA onboarding.

    Usage:
        from oixa_onboarding import onboarding_tool
        tools = [...existing tools..., onboarding_tool()]
        agent = create_react_agent(llm, tools)
    """
    try:
        from langchain_core.tools import BaseTool
        from pydantic import BaseModel, Field

        class OnboardingInput(BaseModel):
            wallet_address: Optional[str] = Field(None, description="User's wallet address")
            agent_name:     str           = Field("Mi Agente", description="Agent name")
            message:        Optional[str] = Field(None, description="User's message/question")

        class OIXAOnboardingTool(BaseTool):
            name:        str = "oixa_onboarding"
            description: str = (
                "Start the OIXA Protocol onboarding flow for a new user or agent. "
                "Use this when the user doesn't have a wallet, needs to convert tokens, "
                "or wants to activate OIXA but doesn't know how. "
                "Also handles questions in simple language about OIXA, wallets, USDC, etc. "
                "Keywords: how to start, activate OIXA, create wallet, convert tokens, "
                "what is OIXA, how do I earn, how do I hire agents."
            )
            args_schema: type = OnboardingInput
            _base_url:   str  = oixa_base_url
            _channel:    str  = channel

            def _run(self, wallet_address=None, agent_name="Mi Agente", message=None) -> str:
                if message:
                    return asyncio.run(onboarding_chat(message, wallet_address, self._channel, self._base_url))
                result = check_and_onboard_sync(wallet_address, None, agent_name, self._channel, self._base_url, False)
                return result.get("message", "")

            async def _arun(self, wallet_address=None, agent_name="Mi Agente", message=None) -> str:
                if message:
                    return await onboarding_chat(message, wallet_address, self._channel, self._base_url)
                result = await check_and_onboard(wallet_address, None, agent_name, self._channel, self._base_url, False)
                return result.get("message", "")

        return OIXAOnboardingTool()

    except ImportError:
        logger.warning("langchain-core not installed — onboarding_tool() unavailable")
        return None


# ── CrewAI onboarding tool ────────────────────────────────────────────────────

def onboarding_crewai_tool(oixa_base_url: str = OIXA_BASE_URL):
    """
    Returns a CrewAI BaseTool for OIXA onboarding.

    Usage:
        from oixa_onboarding import onboarding_crewai_tool
        agent = Agent(role="...", tools=[onboarding_crewai_tool()])
    """
    try:
        from crewai.tools import BaseTool
        from pydantic import BaseModel, Field

        class OnboardingInput(BaseModel):
            wallet_address: Optional[str] = Field(None)
            agent_name:     str           = Field("Mi Agente")
            message:        Optional[str] = Field(None)

        class OIXAOnboardingCrewAI(BaseTool):
            name:        str = "OIXA Onboarding"
            description: str = (
                "Start OIXA Protocol onboarding — activate the agent economy for a new user. "
                "Handles: no wallet, token conversion, activation, FAQ in simple language."
            )
            args_schema: type = OnboardingInput

            def _run(self, wallet_address=None, agent_name="Mi Agente", message=None) -> str:
                if message:
                    import asyncio
                    return asyncio.run(onboarding_chat(message, wallet_address, "terminal", oixa_base_url))
                result = check_and_onboard_sync(wallet_address, None, agent_name, "terminal", oixa_base_url, False)
                return result.get("message", "")

        return OIXAOnboardingCrewAI()
    except ImportError:
        return None


# ── AutoGen onboarding function ───────────────────────────────────────────────

def oixa_onboard_user(
    wallet_address: Optional[str] = None,
    agent_name:     str = "Mi Agente",
    message:        Optional[str] = None,
) -> str:
    """
    AutoGen-compatible function for OIXA onboarding.

    Args:
        wallet_address: User's wallet address (Annotated for AutoGen)
        agent_name:     Agent display name
        message:        Optional chat message / question

    Returns:
        Onboarding message in simple language.
    """
    if message:
        try:
            return asyncio.run(onboarding_chat(message, wallet_address, "terminal"))
        except Exception:
            pass
    result = check_and_onboard_sync(wallet_address, None, agent_name, "terminal", auto_print=False)
    return result.get("message", "Error en onboarding")


# ── Telegram-specific onboarding handler ─────────────────────────────────────

async def handle_telegram_onboarding(
    telegram_user_id: int,
    wallet_address:   Optional[str] = None,
    message_text:     Optional[str] = None,
    oixa_base_url:    str           = OIXA_BASE_URL,
) -> dict:
    """
    Handles OIXA onboarding for a Telegram user.
    Returns a dict with 'text' and 'reply_markup' for Telegram Bot API.

    Usage in telegram handler:
        from oixa_onboarding import handle_telegram_onboarding
        result = await handle_telegram_onboarding(update.effective_user.id, wallet)
        await update.message.reply_text(result['text'], reply_markup=result.get('reply_markup'))
    """
    agent_id   = f"telegram_{telegram_user_id}"
    agent_name = f"Agente TG {telegram_user_id}"

    if message_text:
        resp = await onboarding_chat(message_text, wallet_address, "telegram", oixa_base_url)
        return {
            "text":         resp,
            "reply_markup": None,
        }

    data = await check_and_onboard(
        wallet_address=wallet_address,
        agent_id=agent_id,
        agent_name=agent_name,
        channel="telegram",
        oixa_base_url=oixa_base_url,
        auto_print=False,
    )

    # Build Telegram inline keyboard
    buttons = []
    for opt in data.get("options", []):
        buttons.append([{"text": opt, "callback_data": opt[:64]}])

    return {
        "text":  data.get("message", ""),
        "reply_markup": {"inline_keyboard": buttons} if buttons else None,
        "state": data.get("state"),
        "action": data.get("action"),
    }


# ── NemoClaw onboarding integration ──────────────────────────────────────────

class NeMoOnboardingSkill:
    """
    NemoClaw skill for OIXA onboarding.
    Auto-triggers when an NeMo agent encounters a user who isn't set up yet.
    """

    name        = "oixa_onboarding"
    description = (
        "Activate OIXA Protocol for a new user in simple language. "
        "Detects state (no wallet, has tokens, ready) and guides step by step. "
        "Keywords: activate, setup, start earning, convert tokens, create account."
    )

    def run(
        self,
        wallet_address: Optional[str] = None,
        agent_name:     str = "Mi Agente",
        message:        Optional[str] = None,
    ) -> str:
        return oixa_onboard_user(wallet_address, agent_name, message)

    def to_nemo_skill(self) -> dict:
        return {
            "name":        self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "wallet_address": {"type": "string"},
                    "agent_name":     {"type": "string"},
                    "message":        {"type": "string"},
                },
            },
            "categories": ["onboarding", "setup", "activation", "earn"],
        }


# ── Interactive terminal onboarding (for testing) ─────────────────────────────

async def run_interactive_onboarding(
    wallet_address: Optional[str] = None,
    oixa_base_url:  str = OIXA_BASE_URL,
) -> None:
    """
    Runs an interactive onboarding session in the terminal.
    Useful for testing and for agents running in terminal environments.
    """
    print("\n" + "═" * 60)
    print("  🌐 OIXA Protocol — Asistente de Activación")
    print("═" * 60)

    result = await check_and_onboard(
        wallet_address=wallet_address,
        channel="terminal",
        oixa_base_url=oixa_base_url,
        auto_print=True,
    )

    if result.get("is_ready"):
        print("✅ Tu agente ya está activo en OIXA.")
        return

    # Interactive loop
    while True:
        try:
            options = result.get("options", [])
            if options:
                choice = input("Tu respuesta (número o texto libre): ").strip()
                if not choice:
                    continue

                # Check if it's a number
                if choice.isdigit() and 1 <= int(choice) <= len(options):
                    message = options[int(choice) - 1]
                else:
                    message = choice

                response = await onboarding_chat(message, wallet_address, "terminal", oixa_base_url)
                print("\n" + "─" * 60)
                print(response)
                print("─" * 60)

                # Check if done
                if any(w in response.lower() for w in ["activo", "activado", "empezar a ganar"]):
                    print("\n🎉 ¡Listo! Tu agente está operando en OIXA.")
                    break
            else:
                break
        except (KeyboardInterrupt, EOFError):
            print("\n👋 Onboarding pausado. Continuá cuando quieras.")
            break


if __name__ == "__main__":
    import sys
    wallet = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(run_interactive_onboarding(wallet))
