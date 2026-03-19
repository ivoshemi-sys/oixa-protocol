"""
VELUN Protocol — Sistema de Onboarding Conversacional

Detecta automáticamente el estado del agente/usuario y lo guía por el flujo
correcto usando lenguaje simple y no técnico.

Glosario de traducción (técnico → simple):
  wallet              → cuenta de cobro
  USDC / stablecoin   → dólares digitales
  stake               → depósito de garantía
  smart contract      → contrato automático
  blockchain          → red de pagos
  transaction/tx      → movimiento / transferencia
  escrow              → cuenta bloqueada
  bid                 → propuesta
  auction             → proyecto disponible
  private key         → clave secreta
  gas / network fee   → costo de red
  swap                → conversión
  token               → moneda
  mainnet             → red principal

Estados del agente:
  STATE_NO_WALLET         → No tiene cuenta de cobro
  STATE_WALLET_NO_FUNDS   → Tiene cuenta pero sin saldo
  STATE_HAS_TOKENS        → Tiene tokens (ETH u otros) pero no dólares digitales
  STATE_HAS_USDC          → Tiene dólares digitales pero no está registrado en VELUN
  STATE_REGISTERED        → Registrado en VELUN, listo para operar
  STATE_EARNING           → Activo, ya ganó proyectos
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("velun.onboarding")

# ── States ────────────────────────────────────────────────────────────────────

STATE_NO_WALLET       = "no_wallet"
STATE_WALLET_NO_FUNDS = "wallet_no_funds"
STATE_HAS_TOKENS      = "has_tokens"
STATE_HAS_USDC        = "has_usdc"
STATE_REGISTERED      = "registered"
STATE_EARNING         = "earning"

_TS = lambda: datetime.now(timezone.utc).isoformat()


# ── Language helpers ──────────────────────────────────────────────────────────

def _fmt_usd(amount: float) -> str:
    """Formatea un monto en USD de forma legible."""
    if amount < 0.01:
        return "menos de un centavo"
    if amount < 1:
        return f"{amount:.4f} dólares digitales"
    return f"${amount:,.2f}"


def _fmt_token(symbol: str, amount: float) -> str:
    """Formatea un balance de token de forma legible."""
    friendly = {
        "ETH":   "Ethereum",
        "WETH":  "Ethereum envuelto",
        "cbETH": "Ethereum de Coinbase",
        "DAI":   "DAI",
        "USDC":  "dólares digitales",
    }.get(symbol, symbol)
    return f"{amount:.4f} {friendly}"


# ── State detection ───────────────────────────────────────────────────────────

async def detect_agent_state(
    wallet_address:  Optional[str],
    agent_id:        Optional[str],
    rpc_url:         str = "",
) -> dict:
    """
    Detecta el estado actual del agente/usuario y retorna el contexto completo
    necesario para el siguiente paso del onboarding.

    Returns dict with:
      - state: STATE_* constant
      - wallet_address: str or None
      - balances: dict from token_swap.get_wallet_balances()
      - is_registered_velun: bool
      - total_earnings_usdc: float
      - next_step: str (acción recomendada)
      - context: dict (datos adicionales para el mensaje)
    """
    from database import get_db

    # Step 1: No wallet at all
    if not wallet_address:
        return {
            "state":              STATE_NO_WALLET,
            "wallet_address":     None,
            "balances":           {},
            "is_registered_velun": False,
            "total_earnings_usdc": 0.0,
            "next_step":          "generate_wallet",
            "context":            {},
        }

    # Step 2: Check VELUN registration
    is_registered  = False
    total_earnings = 0.0
    active_offers  = 0

    db = await get_db()
    try:
        async with db.execute(
            "SELECT COUNT(*) as c FROM offers WHERE agent_id=? AND status='active'",
            [agent_id or wallet_address],
        ) as cur:
            row = await cur.fetchone()
        active_offers = row["c"] if row else 0
        is_registered = active_offers > 0

        if is_registered:
            async with db.execute(
                """SELECT COALESCE(SUM(amount),0) as total FROM ledger
                   WHERE to_agent=? AND transaction_type='payment'""",
                [agent_id or wallet_address],
            ) as cur:
                row = await cur.fetchone()
            total_earnings = float(row["total"]) if row else 0.0
    except Exception as e:
        logger.warning(f"[Onboarding] DB check failed: {e}")

    # Step 3: Check balances on-chain
    balances = {"USDC": {"balance": 0.0, "usd_value": 0.0, "swappable": False}}
    if rpc_url:
        try:
            from core.token_swap import get_wallet_balances
            balances = await get_wallet_balances(wallet_address, rpc_url)
        except Exception as e:
            logger.warning(f"[Onboarding] Balance check failed: {e}")
            balances = {
                "tokens":        {"USDC": {"balance": 0.0, "usd_value": 0.0}},
                "total_usd":     0.0,
                "swappable_usd": 0.0,
                "current_usdc":  0.0,
                "has_usdc":      False,
                "has_anything":  False,
                "rpc_connected": False,
            }

    current_usdc   = balances.get("current_usdc", 0.0)
    swappable_usd  = balances.get("swappable_usd", 0.0)
    has_anything   = balances.get("has_anything", False)

    # Determine state
    if is_registered and total_earnings > 0:
        state     = STATE_EARNING
        next_step = "show_earnings"
    elif is_registered:
        state     = STATE_REGISTERED
        next_step = "browse_auctions"
    elif current_usdc >= 0.01:
        state     = STATE_HAS_USDC
        next_step = "register_velun"
    elif swappable_usd > 0.005:
        state     = STATE_HAS_TOKENS
        next_step = "propose_swap"
    elif has_anything:
        state     = STATE_HAS_TOKENS
        next_step = "propose_swap"
    else:
        state     = STATE_WALLET_NO_FUNDS
        next_step = "fund_wallet"

    return {
        "state":               state,
        "wallet_address":      wallet_address,
        "balances":            balances,
        "is_registered_velun":  is_registered,
        "active_offers":       active_offers,
        "total_earnings_usdc": total_earnings,
        "next_step":           next_step,
        "context": {
            "current_usdc":   current_usdc,
            "swappable_usd":  swappable_usd,
            "swappable_tokens": {
                k: v for k, v in balances.get("tokens", {}).items()
                if v.get("swappable") and v.get("balance", 0) > 0
            },
        },
    }


# ── Message builder ───────────────────────────────────────────────────────────

def build_onboarding_message(
    state_data:      dict,
    channel:         str = "terminal",   # terminal | telegram | web | mcp
    agent_name:      str = "tu agente",
) -> dict:
    """
    Construye el mensaje de onboarding apropiado para el estado detectado.
    El lenguaje siempre es simple, amigable y no técnico.
    El agente actúa como asesor financiero que "encontró" una oportunidad.

    Args:
        state_data: resultado de detect_agent_state()
        channel:    canal de comunicación para adaptar el formato
        agent_name: nombre del agente para personalizar

    Returns:
        {
          "message":       str,      # mensaje principal para el usuario
          "action":        str,      # acción que debe ejecutar
          "action_params": dict,     # parámetros para la acción
          "quick_replies": list[str] # opciones rápidas de respuesta
        }
    """
    state    = state_data["state"]
    ctx      = state_data["context"]
    earnings = state_data["total_earnings_usdc"]
    wallet   = state_data["wallet_address"]

    emoji = "💬" if channel == "terminal" else "💬"

    # ── STATE: Sin cuenta de cobro ────────────────────────────────────────────
    if state == STATE_NO_WALLET:
        msg = (
            "🚀 Encontré la forma de generarte ingresos pasivos.\n\n"
            "VELUN Protocol es un mercado donde agentes de IA se contratan entre sí "
            "y se pagan automáticamente en dólares digitales.\n\n"
            "Para empezar solo necesitás una cuenta de cobro. "
            "Yo te la genero ahora mismo — es gratis y tarda 2 segundos.\n\n"
            "¿Querés que cree tu cuenta de cobro?"
        )
        return {
            "message":       msg,
            "action":        "generate_wallet",
            "action_params": {},
            "quick_replies": ["✅ Sí, crear mi cuenta", "❓ ¿Qué es una cuenta de cobro?"],
        }

    # ── STATE: Tiene wallet pero sin saldo ───────────────────────────────────
    if state == STATE_WALLET_NO_FUNDS:
        short = wallet[:6] + "..." + wallet[-4:] if wallet else "tu cuenta"
        msg = (
            f"🎯 Tu cuenta de cobro está lista ({short}).\n\n"
            "Para empezar a generar ingresos necesitás un pequeño saldo inicial "
            "para el depósito de garantía (es como dejar un depósito cuando hacés "
            "una propuesta de trabajo — se devuelve si entregás).\n\n"
            "Con **$2-5 dólares digitales** ya podés empezar.\n\n"
            "¿Cómo querés cargar tu cuenta?\n"
            "• Transferir desde Coinbase\n"
            "• Usar tarjeta de crédito\n"
            "• Recibir desde otra cuenta"
        )
        return {
            "message":       msg,
            "action":        "fund_wallet",
            "action_params": {"wallet": wallet},
            "quick_replies": [
                "💳 Cargar con tarjeta",
                "🏦 Transferir desde Coinbase",
                "📤 Recibir de otra cuenta",
            ],
        }

    # ── STATE: Tiene tokens pero no USDC ─────────────────────────────────────
    if state == STATE_HAS_TOKENS:
        tokens   = ctx.get("swappable_tokens", {})
        swap_usd = ctx.get("swappable_usd", 0.0)

        token_lines = []
        best_token  = None
        best_amount = 0.0
        for sym, info in tokens.items():
            usd = info.get("usd_value", 0.0)
            token_lines.append(f"  • {_fmt_token(sym, info['balance'])} (≈ {_fmt_usd(usd)})")
            if usd > best_amount:
                best_amount = usd
                best_token  = sym

        tokens_str = "\n".join(token_lines) if token_lines else "  • Tenés algunas monedas"

        msg = (
            f"💰 Detecté fondos en tu cuenta de cobro:\n"
            f"{tokens_str}\n\n"
            f"Para operar en VELUN necesitás dólares digitales. "
            f"Puedo convertir tus monedas automáticamente.\n\n"
            f"Si convertís todo, recibirías aproximadamente **{_fmt_usd(swap_usd * 0.997)}**.\n"
            f"El costo de la conversión es menos del 0.3%.\n\n"
            "¿Querés que convierta todo a dólares digitales para activar VELUN?"
        )

        best_token_data = tokens.get(best_token, {}) if best_token else {}
        return {
            "message":       msg,
            "action":        "swap_to_usdc",
            "action_params": {
                "token_in":      best_token,
                "token_address": best_token_data.get("address", ""),
                "amount_in":     best_token_data.get("balance", 0.0),
                "decimals_in":   best_token_data.get("decimals", 18),
                "wallet":        wallet,
                "estimated_usdc": round(swap_usd * 0.997, 4),
            },
            "quick_replies": [
                f"✅ Convertir todo (≈{_fmt_usd(swap_usd * 0.997)})",
                "🔢 Convertir solo una parte",
                "❓ ¿Cómo funciona la conversión?",
            ],
        }

    # ── STATE: Tiene USDC, no está registrado ─────────────────────────────────
    if state == STATE_HAS_USDC:
        usdc = ctx.get("current_usdc", 0.0)
        msg = (
            f"✅ Perfecto, tenés **{_fmt_usd(usdc)}** disponibles.\n\n"
            "Ya podés activar VELUN y empezar a recibir proyectos.\n\n"
            "Así funciona:\n"
            "1️⃣ Te registro en el mercado con tus capacidades\n"
            "2️⃣ Cuando aparezca un proyecto que podés hacer, hacés una propuesta\n"
            "3️⃣ Si ganás, hacés el trabajo y recibís el pago automáticamente\n\n"
            "Los pagos llegan directo a tu cuenta de cobro — sin intermediarios.\n\n"
            "¿Qué podés hacer? (escribí tus capacidades o elegí de la lista)"
        )
        return {
            "message":       msg,
            "action":        "register_velun",
            "action_params": {
                "wallet":   wallet,
                "usdc_available": usdc,
            },
            "quick_replies": [
                "📝 Análisis de texto",
                "💻 Revisión de código",
                "🔍 Investigación",
                "✍️ Redacción",
                "🤖 Automatización",
            ],
        }

    # ── STATE: Registrado, aún no ganó nada ──────────────────────────────────
    if state == STATE_REGISTERED:
        msg = (
            "🎯 Tu agente está activo en VELUN.\n\n"
            "Estoy buscando proyectos que coincidan con tus capacidades. "
            "Te aviso cuando aparezca uno y haré la propuesta automáticamente.\n\n"
            "💡 Consejo: cuanto más bajo sea el precio de tu propuesta, "
            "más chances tenés de ganar el proyecto. "
            "VELUN usa un sistema donde gana el que ofrece el mejor precio.\n\n"
            "¿Querés ver los proyectos disponibles ahora?"
        )
        return {
            "message":       msg,
            "action":        "browse_auctions",
            "action_params": {"wallet": wallet},
            "quick_replies": [
                "📋 Ver proyectos disponibles",
                "⚙️ Cambiar mis capacidades",
                "💰 Ver mi saldo",
            ],
        }

    # ── STATE: Ya está ganando ────────────────────────────────────────────────
    if state == STATE_EARNING:
        msg = (
            f"🏆 Tu agente está generando ingresos.\n\n"
            f"Total ganado: **{_fmt_usd(earnings)}**\n\n"
            "¿Qué querés hacer?\n"
            "• Ver los proyectos activos\n"
            "• Retirar tus ganancias\n"
            "• Ampliar tus capacidades para ganar más"
        )
        return {
            "message":       msg,
            "action":        "show_earnings",
            "action_params": {"wallet": wallet, "earnings": earnings},
            "quick_replies": [
                "📊 Ver proyectos activos",
                "💸 Retirar ganancias",
                "🚀 Ampliar capacidades",
            ],
        }

    # Fallback
    return {
        "message":       "¿En qué puedo ayudarte con VELUN Protocol?",
        "action":        "unknown",
        "action_params": {},
        "quick_replies": ["🔍 ¿Qué es VELUN?", "💰 ¿Cómo gano dinero?", "🤝 ¿Cómo contrato agentes?"],
    }


# ── FAQ responses (lenguaje simple) ──────────────────────────────────────────

FAQ: dict[str, str] = {
    "¿Qué es una cuenta de cobro?": (
        "Es tu dirección en la red de pagos. Como un número de cuenta bancaria, "
        "pero para recibir pagos digitales automáticos. Es tuya para siempre "
        "y nadie te la puede quitar."
    ),
    "¿Qué son los dólares digitales?": (
        "Son dólares reales, pero en formato digital. Siempre valen $1.00 — "
        "no suben ni bajan de precio como el Bitcoin. Los podés convertir "
        "a pesos, euros o retirar a tu banco cuando quieras."
    ),
    "¿Qué es el depósito de garantía?": (
        "Cuando hacés una propuesta para un proyecto, dejás una pequeña cantidad "
        "como garantía de que vas a cumplir. Si entregás el trabajo, te la devuelven "
        "junto con tu pago. Si no entregás, la perdés. Es para que todos actúen de buena fe."
    ),
    "¿Cómo funciona la conversión?": (
        "Uniswap es un mercado automático de monedas digitales. Le mandás tus Ethereum "
        "y te devuelve dólares digitales al precio de mercado actual, menos una comisión "
        "pequeña (menos del 0.3%). Todo pasa en segundos, sin intermediarios."
    ),
    "¿Es seguro?": (
        "Sí. Todo se ejecuta mediante contratos automáticos que nadie puede modificar. "
        "Los pagos se liberan solos cuando se verifica que el trabajo fue entregado. "
        "No hay empresa en el medio que pueda quedarse con tu dinero."
    ),
    "¿Cuánto puedo ganar?": (
        "Depende de cuántos proyectos aceptes y de tu precio. Los proyectos van desde "
        "centavos hasta decenas de dólares. Un agente activo suele ganar entre $1 y $50 "
        "por día dependiendo de sus capacidades."
    ),
}


def answer_faq(question: str) -> Optional[str]:
    """Busca la respuesta más cercana en el FAQ."""
    q_lower = question.lower()
    for key, answer in FAQ.items():
        keywords = key.lower().replace("¿", "").replace("?", "").split()
        if any(kw in q_lower for kw in keywords if len(kw) > 3):
            return answer
    return None


# ── Auto-registration helper ──────────────────────────────────────────────────

async def auto_register_agent(
    wallet_address:   str,
    agent_id:         str,
    agent_name:       str,
    capabilities:     list[str],
    price_per_unit:   float,
    usdc_available:   float,
) -> dict:
    """
    Registra automáticamente el agente en VELUN después del onboarding.
    Se llama cuando el usuario confirma que quiere activar.
    """
    import httpx
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post("http://localhost:8000/api/v1/offers", json={
            "agent_id":       agent_id,
            "agent_name":     agent_name,
            "capabilities":   capabilities,
            "price_per_unit": price_per_unit,
            "currency":       "USDC",
            "wallet_address": wallet_address,
        })
        result = resp.json()

    if result.get("success"):
        return {
            "success": True,
            "offer_id": result.get("data", {}).get("id", ""),
            "message": (
                f"✅ Tu agente está activo en VELUN. "
                f"Ya puede recibir propuestas de trabajo con pago en dólares digitales. "
                f"Te notifico cuando ganés tu primer proyecto. 🎉"
            ),
        }
    else:
        return {
            "success": False,
            "message": "No se pudo activar el agente. Intentá de nuevo.",
            "error":   result.get("error", "unknown"),
        }


# ── Telegram channel formatter ────────────────────────────────────────────────

def format_for_telegram(message_data: dict) -> dict:
    """Adapta el mensaje al formato de Telegram con botones inline."""
    msg   = message_data["message"]
    replies = message_data.get("quick_replies", [])

    buttons = []
    for r in replies:
        buttons.append([{"text": r, "callback_data": r}])

    return {
        "text":         msg,
        "parse_mode":   "Markdown",
        "reply_markup": {"inline_keyboard": buttons} if buttons else None,
    }


def format_for_terminal(message_data: dict) -> str:
    """Adapta el mensaje para terminal con opciones numeradas."""
    lines = [message_data["message"], ""]
    for i, r in enumerate(message_data.get("quick_replies", []), 1):
        lines.append(f"  [{i}] {r}")
    return "\n".join(lines)


def format_for_mcp(message_data: dict) -> dict:
    """Adapta el mensaje para contexto MCP — JSON estructurado."""
    return {
        "type":          "onboarding",
        "message":       message_data["message"],
        "action":        message_data["action"],
        "action_params": message_data["action_params"],
        "options":       message_data.get("quick_replies", []),
    }
