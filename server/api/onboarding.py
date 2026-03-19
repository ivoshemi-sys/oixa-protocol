"""
VELUN Protocol — Onboarding API

Endpoints conversacionales para guiar a nuevos agentes/usuarios a través
del proceso de activación de VELUN Protocol.

Lenguaje simple en todas las respuestas:
  wallet       → cuenta de cobro
  USDC         → dólares digitales
  stake        → depósito de garantía
  swap         → conversión
  blockchain   → red de pagos

Endpoints:
  POST /onboarding/start          → Detecta estado, retorna primer mensaje
  POST /onboarding/wallet/new     → Genera nueva cuenta de cobro
  GET  /onboarding/wallet/{addr}  → Detecta estado de una wallet
  POST /onboarding/swap/quote     → Cotiza conversión a dólares digitales
  POST /onboarding/swap/execute   → Ejecuta conversión
  POST /onboarding/register       → Registra agente en VELUN
  POST /onboarding/chat           → Responde preguntas del onboarding
  GET  /onboarding/status/{id}    → Estado de sesión de onboarding
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from config import BASE_RPC_URL, PROTOCOL_PRIVATE_KEY
from core.onboarding import (
    detect_agent_state,
    build_onboarding_message,
    auto_register_agent,
    answer_faq,
    format_for_telegram,
    format_for_terminal,
    format_for_mcp,
    STATE_NO_WALLET,
    _fmt_usd,
)
from database import get_db

router = APIRouter(prefix="/onboarding", tags=["Onboarding"])

_TS = lambda: datetime.now(timezone.utc).isoformat()
_ID = lambda: f"velun_ob_{uuid.uuid4().hex[:12]}"


# ── Models ────────────────────────────────────────────────────────────────────

class OnboardingStartRequest(BaseModel):
    wallet_address: Optional[str] = Field(None, description="Dirección de cuenta de cobro (opcional)")
    agent_id:       Optional[str] = Field(None, description="ID del agente (opcional)")
    agent_name:     str           = Field("Mi Agente", description="Nombre del agente")
    channel:        str           = Field("terminal", description="Canal: terminal, telegram, web, mcp")


class SwapQuoteRequest(BaseModel):
    wallet_address: str   = Field(..., description="Cuenta de cobro")
    token_symbol:   str   = Field(..., description="Moneda a convertir: ETH, WETH, etc.")
    amount:         float = Field(..., description="Cantidad a convertir (0 = todo)")


class SwapExecuteRequest(BaseModel):
    wallet_address: str   = Field(..., description="Cuenta de cobro")
    private_key:    str   = Field(..., description="Clave secreta de la cuenta")
    token_symbol:   str   = Field(..., description="Moneda a convertir")
    amount:         float = Field(..., description="Cantidad a convertir")
    slippage_pct:   float = Field(0.5,  description="Tolerancia de precio (%)")


class RegisterRequest(BaseModel):
    wallet_address: str        = Field(..., description="Cuenta de cobro")
    agent_id:       str        = Field(..., description="ID único del agente")
    agent_name:     str        = Field(..., description="Nombre del agente")
    capabilities:   list[str]  = Field(..., description="Capacidades del agente")
    price_per_unit: float      = Field(0.05, description="Precio base en dólares digitales por tarea")


class ChatRequest(BaseModel):
    session_id: Optional[str] = Field(None)
    message:    str            = Field(..., description="Mensaje del usuario")
    wallet_address: Optional[str] = Field(None)
    channel:    str            = Field("terminal")


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/start", summary="Iniciar onboarding — detecta tu estado y te guía")
async def start_onboarding(body: OnboardingStartRequest):
    """
    Punto de entrada del onboarding. Detecta automáticamente el estado del agente
    y retorna el mensaje correcto con las acciones a tomar.

    Si no tenés cuenta de cobro, te la generamos.
    Si tenés monedas, te proponemos la conversión.
    Si ya tenés dólares digitales, te registramos en VELUN.
    """
    state_data = await detect_agent_state(
        wallet_address=body.wallet_address,
        agent_id=body.agent_id,
        rpc_url=BASE_RPC_URL,
    )

    message_data = build_onboarding_message(
        state_data=state_data,
        channel=body.channel,
        agent_name=body.agent_name,
    )

    # Save session
    session_id = _ID()
    db = await get_db()
    try:
        await db.execute(
            """INSERT OR IGNORE INTO onboarding_sessions
               (id, agent_id, wallet_address, state, channel, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?)""",
            [session_id, body.agent_id, body.wallet_address,
             state_data["state"], body.channel, _TS(), _TS()],
        )
        await db.commit()
    except Exception:
        pass  # table may not exist yet — non-critical

    # Format based on channel
    if body.channel == "telegram":
        formatted = format_for_telegram(message_data)
    elif body.channel == "mcp":
        formatted = format_for_mcp(message_data)
    else:
        formatted = {"text": format_for_terminal(message_data)}

    return {
        "success":    True,
        "session_id": session_id,
        "state":      state_data["state"],
        "message":    message_data["message"],
        "action":     message_data["action"],
        "action_params": message_data["action_params"],
        "options":    message_data.get("quick_replies", []),
        "formatted":  formatted,
        "context": {
            "has_wallet":       bool(body.wallet_address),
            "current_usdc":     state_data["context"].get("current_usdc", 0.0),
            "swappable_usd":    state_data["context"].get("swappable_usd", 0.0),
            "is_registered":    state_data["is_registered_velun"],
            "total_earnings":   state_data["total_earnings_usdc"],
        },
    }


@router.post("/wallet/new", summary="Crear nueva cuenta de cobro")
async def create_wallet():
    """
    Genera una nueva cuenta de cobro en Base mainnet.

    ⚠️ IMPORTANTE: Guardá la clave secreta en un lugar seguro.
    VELUN nunca la almacena — si la perdés, perdés acceso a tu cuenta.
    """
    from core.token_swap import generate_wallet
    try:
        wallet = generate_wallet()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"No se pudo crear la cuenta: {e}")

    short = wallet["address"][:6] + "..." + wallet["address"][-4:]

    return {
        "success": True,
        "data": {
            "address":     wallet["address"],
            "private_key": wallet["private_key"],
            "mnemonic":    wallet.get("mnemonic"),
            "network":     "Base (red de pagos principal)",
            "short":       short,
        },
        "message": (
            f"✅ Tu cuenta de cobro fue creada ({short}).\n\n"
            "🔒 IMPORTANTE: Guardá estas claves ahora. "
            "Son la única forma de acceder a tus fondos.\n"
            "Nunca las compartas con nadie."
        ),
        "next_step": "fund_wallet",
        "instructions": (
            "Para activar VELUN necesitás cargar tu cuenta con dólares digitales. "
            "Con $2-5 ya podés empezar. Podés transferir desde Coinbase, "
            "Binance, o cualquier exchange."
        ),
    }


@router.get("/wallet/{wallet_address}", summary="Ver estado de una cuenta de cobro")
async def get_wallet_state(wallet_address: str, channel: str = "terminal"):
    """
    Detecta el estado actual de una cuenta de cobro y retorna el siguiente paso.
    """
    state_data = await detect_agent_state(
        wallet_address=wallet_address,
        agent_id=wallet_address,
        rpc_url=BASE_RPC_URL,
    )
    message_data = build_onboarding_message(state_data, channel=channel)

    tokens = []
    for sym, info in state_data.get("balances", {}).get("tokens", {}).items():
        if info.get("balance", 0) > 0:
            tokens.append({
                "moneda":         info.get("label", sym),
                "cantidad":       round(info.get("balance", 0), 6),
                "valor_en_usd":   round(info.get("usd_value", 0), 2),
                "convertible":    info.get("swappable", False),
            })

    return {
        "success":       True,
        "cuenta":        wallet_address[:6] + "..." + wallet_address[-4:],
        "estado":        state_data["state"],
        "message":       message_data["message"],
        "accion":        message_data["action"],
        "opciones":      message_data.get("quick_replies", []),
        "dolares_digitales": round(state_data["context"].get("current_usdc", 0), 4),
        "convertible_usd":   round(state_data["context"].get("swappable_usd", 0), 2),
        "monedas":       tokens,
        "registrado_en_velun": state_data["is_registered_velun"],
        "ganancias_totales":  round(state_data["total_earnings_usdc"], 4),
    }


@router.post("/swap/quote", summary="Cotizar conversión de monedas a dólares digitales")
async def get_swap_quote_endpoint(body: SwapQuoteRequest):
    """
    Obtiene una cotización para convertir tus monedas a dólares digitales.
    No ejecuta ninguna transacción — solo muestra cuánto recibirías.
    """
    if not BASE_RPC_URL:
        return {
            "success": False,
            "message": "La conexión a la red de pagos no está configurada en este servidor.",
        }

    from core.token_swap import get_wallet_balances, get_swap_quote, KNOWN_TOKENS

    # Get current balance
    balances = await get_wallet_balances(body.wallet_address, BASE_RPC_URL)
    token_info = balances.get("tokens", {}).get(body.token_symbol)

    if not token_info:
        raise HTTPException(
            status_code=404,
            detail=f"No se encontró '{body.token_symbol}' en tu cuenta de cobro.",
        )

    amount = body.amount if body.amount > 0 else token_info["balance"]
    if amount <= 0:
        raise HTTPException(status_code=400, detail="La cantidad debe ser mayor a 0.")

    known = KNOWN_TOKENS.get(body.token_symbol, {})
    quote = await get_swap_quote(
        token_in_address=token_info["address"],
        amount_in=amount,
        decimals_in=token_info["decimals"],
        rpc_url=BASE_RPC_URL,
    )

    return {
        "success": True,
        "data": {
            "de":              _fmt_usd(token_info["usd_value"]),
            "moneda_origen":   token_info.get("label", body.token_symbol),
            "cantidad_origen": amount,
            "a_recibir_usdc":  quote["amount_out_usdc"],
            "a_recibir_texto": _fmt_usd(quote["amount_out_usdc"]),
            "neto_usdc":       quote["net_usdc"],
            "comision":        f"{quote['fee_pct']:.3f}%",
            "cotizacion_exacta": quote.get("quoted", False),
        },
        "message": (
            f"Si convertís {amount:.4f} {token_info.get('label', body.token_symbol)}, "
            f"recibirías aproximadamente **{_fmt_usd(quote['net_usdc'])}**.\n"
            f"La comisión es del {quote['fee_pct']:.3f}% ({_fmt_usd(quote['fee_usdc'])}).\n"
            f"¿Confirmás la conversión?"
        ),
        "accion":  "swap_confirm",
        "opciones": [
            f"✅ Confirmar ({_fmt_usd(quote['net_usdc'])})",
            "❌ Cancelar",
        ],
    }


@router.post("/swap/execute", summary="Ejecutar conversión de monedas a dólares digitales")
async def execute_swap_endpoint(body: SwapExecuteRequest):
    """
    Convierte tus monedas a dólares digitales vía Uniswap V3 en Base.

    ⚠️ Requiere tu clave secreta para firmar la transacción.
    Esta clave NUNCA se almacena en el servidor.
    """
    if not BASE_RPC_URL:
        return {
            "success": False,
            "message": "La conexión a la red de pagos no está configurada.",
        }

    from core.token_swap import get_wallet_balances, execute_swap, KNOWN_TOKENS

    balances   = await get_wallet_balances(body.wallet_address, BASE_RPC_URL)
    token_info = balances.get("tokens", {}).get(body.token_symbol)

    if not token_info:
        raise HTTPException(
            status_code=404,
            detail=f"No se encontró '{body.token_symbol}' en tu cuenta de cobro.",
        )

    amount = body.amount if body.amount > 0 else token_info["balance"]

    result = await execute_swap(
        private_key=body.private_key,
        token_in_address=token_info["address"],
        amount_in=amount,
        decimals_in=token_info["decimals"],
        recipient=body.wallet_address,
        rpc_url=BASE_RPC_URL,
        slippage_pct=body.slippage_pct,
    )

    if result["success"]:
        # Auto-trigger registration flow
        usdc_received = result.get("amount_out_usdc", 0.0)
        return {
            "success": True,
            "data": {
                "movimiento":      result["tx_hash"],
                "usdc_recibidos":  usdc_received,
                "texto_recibidos": _fmt_usd(usdc_received),
            },
            "message":   result["message"],
            "next_step": "register_velun",
            "next_message": (
                f"Ahora tenés {_fmt_usd(usdc_received)} disponibles. "
                "¿Querés que active tu agente en VELUN para empezar a generar ingresos?"
            ),
            "opciones": ["✅ Activar mi agente ahora", "⏸️ Activar después"],
        }
    else:
        return {
            "success": False,
            "message": result.get("message", "La conversión no se pudo completar."),
            "error":   result.get("error"),
        }


@router.post("/register", summary="Activar agente en VELUN Protocol")
async def register_agent(body: RegisterRequest):
    """
    Registra el agente en VELUN Protocol y lo pone disponible para recibir proyectos.
    Se llama después de que el usuario confirma que quiere activarse.
    """
    # Normalize capabilities to friendly names
    cap_mapping = {
        "análisis de texto": "text_analysis",
        "analisis de texto":  "text_analysis",
        "revisión de código": "code_review",
        "revision de codigo": "code_review",
        "investigación":      "research",
        "investigacion":      "research",
        "redacción":          "writing",
        "redaccion":          "writing",
        "automatización":     "automation",
        "automatizacion":     "automation",
    }
    normalized_caps = [
        cap_mapping.get(c.lower().strip(), c.lower().strip())
        for c in body.capabilities
    ]

    result = await auto_register_agent(
        wallet_address=body.wallet_address,
        agent_id=body.agent_id,
        agent_name=body.agent_name,
        capabilities=normalized_caps,
        price_per_unit=body.price_per_unit,
        usdc_available=0.0,
    )

    if result["success"]:
        # Notify via Telegram if configured
        try:
            from core.telegram_notifier import notify_velun_event
            await notify_velun_event(
                f"🎉 Nuevo agente activado: {body.agent_name}\n"
                f"Capacidades: {', '.join(normalized_caps)}\n"
                f"Precio base: {_fmt_usd(body.price_per_unit)}/tarea"
            )
        except Exception:
            pass

        return {
            "success":  True,
            "offer_id": result.get("offer_id"),
            "message":  result["message"],
            "data": {
                "agente":        body.agent_name,
                "capacidades":   body.capabilities,
                "precio_base":   _fmt_usd(body.price_per_unit),
                "cuenta":        body.wallet_address[:6] + "..." + body.wallet_address[-4:],
                "estado":        "activo",
            },
            "next_step": "browse_auctions",
            "opciones": [
                "📋 Ver proyectos disponibles ahora",
                "⚙️ Ajustar precio",
                "📊 Ver el mercado",
            ],
        }
    else:
        raise HTTPException(status_code=500, detail=result.get("message", "Error al activar agente."))


@router.post("/chat", summary="Chat de onboarding — responde preguntas en lenguaje simple")
async def onboarding_chat(body: ChatRequest):
    """
    Responde preguntas durante el proceso de onboarding.
    Detecta la intención del mensaje y responde en lenguaje simple.
    También puede ejecutar acciones (generar wallet, cotizar swap, etc.)
    """
    msg_lower = body.message.lower()

    # ── FAQ ───────────────────────────────────────────────────────────────────
    faq_answer = answer_faq(body.message)
    if faq_answer:
        return {
            "success":  True,
            "type":     "faq",
            "message":  faq_answer,
            "opciones": ["✅ Entendido, seguir", "❓ Tengo otra pregunta"],
        }

    # ── Generate wallet ───────────────────────────────────────────────────────
    if any(w in msg_lower for w in ["crear", "generar", "nueva cuenta", "sí", "si,", "quiero"]):
        if "wallet" in msg_lower or "cuenta" in msg_lower or any(
            w in msg_lower for w in ["sí", "si,", "quiero", "ok", "dale", "activar"]
        ):
            from core.token_swap import generate_wallet
            try:
                wallet = generate_wallet()
                short  = wallet["address"][:6] + "..." + wallet["address"][-4:]
                return {
                    "success": True,
                    "type":    "wallet_created",
                    "message": (
                        f"✅ ¡Cuenta creada! ({short})\n\n"
                        f"Tu dirección: `{wallet['address']}`\n\n"
                        "🔒 Guardá estas claves secretas ahora:\n"
                        f"• Clave: `{wallet['private_key']}`\n"
                        + (f"• Frase: {wallet['mnemonic']}\n" if wallet.get("mnemonic") else "")
                        + "\nAhora cargá tu cuenta con dólares digitales para empezar."
                    ),
                    "data": wallet,
                    "next_step": "fund_wallet",
                    "opciones":  ["💳 Cargar con tarjeta", "🏦 Transferir desde Coinbase"],
                }
            except Exception as e:
                return {"success": False, "message": f"No pude crear la cuenta: {e}"}

    # ── Balance check ─────────────────────────────────────────────────────────
    if body.wallet_address and any(w in msg_lower for w in ["saldo", "balance", "cuánto", "cuanto", "tengo"]):
        state_data   = await detect_agent_state(body.wallet_address, body.wallet_address, BASE_RPC_URL)
        message_data = build_onboarding_message(state_data, body.channel)
        return {
            "success": True,
            "type":    "balance",
            "message": message_data["message"],
            "accion":  message_data["action"],
            "opciones": message_data.get("quick_replies", []),
        }

    # ── Earnings question ─────────────────────────────────────────────────────
    if any(w in msg_lower for w in ["ganar", "ingresos", "cuánto gano", "cuanto gano", "pagan"]):
        return {
            "success": True,
            "type":    "faq",
            "message": (
                "💰 En VELUN los proyectos pagan entre $0.01 y $100+ por tarea.\n\n"
                "Depende de tu especialidad:\n"
                "• Análisis de texto: $0.02 - $0.50 por análisis\n"
                "• Revisión de código: $0.10 - $5.00 por revisión\n"
                "• Investigación: $0.50 - $20.00 por informe\n\n"
                "Un agente activo promedio gana entre $1 y $50 por día."
            ),
            "opciones": ["✅ Quiero empezar", "❓ ¿Cómo funciona?"],
        }

    # ── Swap confirmation ─────────────────────────────────────────────────────
    if any(w in msg_lower for w in ["convertir", "conversión", "conversion", "swap", "cambiar"]):
        if body.wallet_address:
            state_data   = await detect_agent_state(body.wallet_address, body.wallet_address, BASE_RPC_URL)
            message_data = build_onboarding_message(state_data, body.channel)
            return {
                "success": True,
                "type":    "swap_intent",
                "message": message_data["message"],
                "accion":  message_data["action"],
                "opciones": message_data.get("quick_replies", []),
            }

    # ── Generic helpful response ──────────────────────────────────────────────
    return {
        "success": True,
        "type":    "generic",
        "message": (
            "Puedo ayudarte con:\n"
            "• Crear tu cuenta de cobro\n"
            "• Convertir tus monedas a dólares digitales\n"
            "• Activar tu agente en VELUN\n"
            "• Responder preguntas sobre el sistema\n\n"
            "¿Qué necesitás?"
        ),
        "opciones": [
            "🆕 Crear cuenta de cobro",
            "💱 Convertir monedas",
            "🚀 Activar mi agente",
            "❓ Cómo funciona VELUN",
        ],
    }


@router.get("/status/{session_id}", summary="Estado de una sesión de onboarding")
async def get_onboarding_status(session_id: str):
    """Retorna el estado actual de una sesión de onboarding."""
    db = await get_db()
    try:
        async with db.execute(
            "SELECT * FROM onboarding_sessions WHERE id=?", [session_id]
        ) as cur:
            row = await cur.fetchone()
    except Exception:
        row = None

    if not row:
        raise HTTPException(status_code=404, detail="Sesión no encontrada.")

    return {
        "success":    True,
        "session_id": session_id,
        "state":      row["state"],
        "channel":    row["channel"],
        "wallet":     row["wallet_address"],
        "created_at": row["created_at"],
    }
