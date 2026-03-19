"""
Circle CCTP v2 (Cross-Chain Transfer Protocol) client for VELUN Protocol.

Allows receiving USDC from Ethereum, Arbitrum, Avalanche, Polygon → Base mainnet.

Flow (as receiver on Base):
  1. Agent/user calls depositForBurn on source chain → provides us the tx hash
  2. We extract MessageSent event → compute message hash
  3. We poll Circle Iris Attestation API until status="signed"
  4. We call receiveMessage on Base MessageTransmitter → USDC lands in VELUN wallet

CCTP V2 contracts use the same address on all supported EVM chains:
  TokenMessenger V2:    0x28b5a0e9c621a5badaa536219b3a228c8168cf5d
  MessageTransmitter V2: 0x81D40F21F12A8F0E3252Bccb954D722d4c464B64
"""

import asyncio
import logging
from typing import Optional

import httpx
from web3 import Web3

from config import (
    BASE_RPC_URL,
    BLOCKCHAIN_ENABLED,
    CCTP_ATTESTATION_URL,
    CCTP_MESSAGE_TRANSMITTER,
    CCTP_TOKEN_MESSENGER,
    PROTOCOL_PRIVATE_KEY,
    PROTOCOL_WALLET,
)

logger = logging.getLogger("velun.cctp")

# ── Chain registry ────────────────────────────────────────────────────────────

CHAINS: dict[str, dict] = {
    "ethereum": {
        "domain": 0,
        "usdc":     "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "rpc_env":  "ETH_RPC_URL",
        "label":    "Ethereum Mainnet",
    },
    "avalanche": {
        "domain": 1,
        "usdc":     "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E",
        "rpc_env":  "AVAX_RPC_URL",
        "label":    "Avalanche C-Chain",
    },
    "arbitrum": {
        "domain": 3,
        "usdc":     "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        "rpc_env":  "ARB_RPC_URL",
        "label":    "Arbitrum One",
    },
    "base": {
        "domain": 6,
        "usdc":     "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "rpc_env":  "BASE_RPC_URL",
        "label":    "Base Mainnet",
    },
    "polygon": {
        "domain": 7,
        "usdc":     "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
        "rpc_env":  "POLYGON_RPC_URL",
        "label":    "Polygon PoS",
    },
    "solana": {
        "domain": 5,
        "usdc":     "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        "rpc_env":  "SOLANA_RPC_URL",
        "label":    "Solana",
        "evm": False,
    },
}

BASE_DOMAIN = 6  # VELUN always receives on Base

# ── ABIs ─────────────────────────────────────────────────────────────────────

MESSAGE_TRANSMITTER_ABI = [
    {
        "name": "receiveMessage",
        "type": "function",
        "inputs": [
            {"name": "message",     "type": "bytes"},
            {"name": "attestation", "type": "bytes"},
        ],
        "outputs": [{"name": "success", "type": "bool"}],
        "stateMutability": "nonpayable",
    },
]

TOKEN_MESSENGER_ABI = [
    {
        "name": "depositForBurn",
        "type": "function",
        "inputs": [
            {"name": "amount",            "type": "uint256"},
            {"name": "destinationDomain", "type": "uint32"},
            {"name": "mintRecipient",     "type": "bytes32"},
            {"name": "burnToken",         "type": "address"},
        ],
        "outputs": [{"name": "_nonce", "type": "uint64"}],
        "stateMutability": "nonpayable",
    },
]

MESSAGE_SENT_ABI = [
    {
        "anonymous": False,
        "name": "MessageSent",
        "type": "event",
        "inputs": [
            {"indexed": False, "name": "message", "type": "bytes"},
        ],
    }
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def wallet_to_bytes32(address: str) -> bytes:
    """Convert an EVM address (hex) to bytes32 (left-padded with zeros)."""
    addr = address.lower().replace("0x", "")
    return bytes.fromhex("000000000000000000000000" + addr)


def get_deposit_instructions(source_chain: str, amount_usdc: float) -> dict:
    """
    Return the instructions a sender needs to call depositForBurn on source chain.
    The sender can use any x402/web3 SDK to execute these instructions.
    """
    chain = CHAINS.get(source_chain)
    if not chain:
        raise ValueError(f"Unsupported source chain: {source_chain}")

    recipient_bytes32 = "0x" + wallet_to_bytes32(PROTOCOL_WALLET).hex()
    amount_units      = int(amount_usdc * 1_000_000)

    return {
        "source_chain":      source_chain,
        "source_chain_label": chain["label"],
        "source_domain":     chain["domain"],
        "destination_chain": "base",
        "destination_domain": BASE_DOMAIN,
        "usdc_to_burn":      chain["usdc"],
        "burn_contract":     CCTP_TOKEN_MESSENGER,
        "mint_recipient":    recipient_bytes32,
        "amount_units":      amount_units,
        "amount_usdc":       amount_usdc,
        "function_call": {
            "name":   "depositForBurn",
            "params": {
                "amount":            amount_units,
                "destinationDomain": BASE_DOMAIN,
                "mintRecipient":     recipient_bytes32,
                "burnToken":         chain["usdc"],
            },
        },
        "note": (
            "After calling depositForBurn, submit the source tx hash to "
            "POST /api/v1/payments/cctp/submit to trigger attestation + completion."
        ),
    }


# ── Message extraction from source chain ─────────────────────────────────────

async def extract_message_from_tx(
    tx_hash: str,
    source_chain: str,
    rpc_url: Optional[str] = None,
) -> Optional[tuple[str, str]]:
    """
    Fetch the MessageSent event from a CCTP burn transaction.
    Returns (message_bytes_hex, message_hash_hex) or None.
    """
    if CHAINS.get(source_chain, {}).get("evm", True) is False:
        # Solana: can't use web3.py, user must provide message bytes manually
        return None

    if not rpc_url:
        import os
        env_key = CHAINS.get(source_chain, {}).get("rpc_env", "")
        rpc_url = os.getenv(env_key, "")

    if not rpc_url:
        logger.warning(
            f"[CCTP] No RPC URL for {source_chain} — cannot auto-extract message. "
            "Set the chain's RPC env var or provide message_bytes manually."
        )
        return None

    try:
        w3             = Web3(Web3.HTTPProvider(rpc_url))
        receipt        = await asyncio.to_thread(w3.eth.get_transaction_receipt, tx_hash)
        msg_sent_topic = "0x" + w3.keccak(text="MessageSent(bytes)").hex()

        for log in receipt["logs"]:
            if log["topics"] and log["topics"][0].hex() == msg_sent_topic:
                # ABI-decode the bytes parameter (skip first 64 bytes: offset + length)
                raw_data     = bytes(log["data"])
                offset       = int.from_bytes(raw_data[0:32], "big")
                length       = int.from_bytes(raw_data[32:64], "big")
                message_bytes = raw_data[64 : 64 + length]
                message_hex   = "0x" + message_bytes.hex()
                message_hash  = "0x" + w3.keccak(message_bytes).hex()
                return message_hex, message_hash

        logger.warning(f"[CCTP] No MessageSent event found in tx {tx_hash}")
        return None

    except Exception as e:
        logger.error(f"[CCTP] Error extracting message from tx {tx_hash}: {e}")
        return None


# ── Attestation ───────────────────────────────────────────────────────────────

async def fetch_attestation(message_hash: str) -> Optional[str]:
    """
    Poll Circle Iris API for the attestation of a burned USDC message.
    Returns attestation hex string when signed, None if still pending.
    Raises on API error (non-404/200).
    """
    url = f"{CCTP_ATTESTATION_URL}/v1/attestations/{message_hash}"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
        if resp.status_code == 404:
            return None  # pending
        resp.raise_for_status()
        data   = resp.json()
        status = data.get("status")
        if status == "signed":
            return data.get("attestation")
        return None  # pending_confirmations or other
    except httpx.HTTPStatusError as e:
        logger.error(f"[CCTP] Attestation API error for {message_hash}: {e}")
        return None
    except Exception as e:
        logger.warning(f"[CCTP] Attestation fetch error: {e}")
        return None


# ── On-chain completion ───────────────────────────────────────────────────────

async def complete_cctp_transfer(
    message_hex: str,
    attestation_hex: str,
) -> Optional[str]:
    """
    Call receiveMessage on Base MessageTransmitter to mint USDC.
    Returns tx_hash or None if simulated.
    """
    if not BLOCKCHAIN_ENABLED:
        import os
        fake = "0x" + os.urandom(32).hex()
        logger.info(f"[CCTP SIMULATED] receiveMessage → fake tx {fake[:18]}…")
        return fake

    try:
        from eth_account import Account as EthAccount

        w3    = Web3(Web3.HTTPProvider(BASE_RPC_URL))
        xmit  = w3.eth.contract(
            address=w3.to_checksum_address(CCTP_MESSAGE_TRANSMITTER),
            abi=MESSAGE_TRANSMITTER_ABI,
        )
        sender = EthAccount.from_key(PROTOCOL_PRIVATE_KEY)

        message_bytes     = bytes.fromhex(message_hex.lstrip("0x"))
        attestation_bytes = bytes.fromhex(attestation_hex.lstrip("0x"))

        tx = xmit.functions.receiveMessage(
            message_bytes, attestation_bytes
        ).build_transaction({
            "from":     sender.address,
            "nonce":    w3.eth.get_transaction_count(sender.address),
            "gasPrice": w3.eth.gas_price,
        })
        signed   = sender.sign_transaction(tx)
        tx_hash  = w3.eth.send_raw_transaction(signed.raw_transaction)
        hex_hash = "0x" + tx_hash.hex()
        logger.info(f"[CCTP] receiveMessage tx: {hex_hash}")
        return hex_hash

    except Exception as e:
        logger.error(f"[CCTP] receiveMessage failed: {e}")
        raise


# ── Background polling task ───────────────────────────────────────────────────

_cctp_poll_running = False


async def cctp_poll_loop(interval: int = 30):
    """
    Background task: poll pending CCTP transfers every `interval` seconds.
    Fetches attestation → completes transfer → updates DB.
    """
    global _cctp_poll_running
    _cctp_poll_running = True
    logger.info(f"[CCTP] Poll loop started (interval={interval}s)")

    while True:
        try:
            await _process_pending_transfers()
        except Exception as e:
            logger.error(f"[CCTP] Poll loop error: {e}")
        await asyncio.sleep(interval)


async def _process_pending_transfers():
    from database import get_db
    import json

    db = await get_db()

    async with db.execute(
        "SELECT * FROM cctp_transfers WHERE status IN ('pending', 'attesting')"
    ) as cur:
        transfers = await cur.fetchall() or []

    for row in transfers:
        transfer = dict(row)
        tid      = transfer["id"]
        msg_hash = transfer.get("message_hash")
        msg_hex  = transfer.get("message_bytes")

        if not msg_hash or not msg_hex:
            continue

        # Try to get attestation
        attestation = await fetch_attestation(msg_hash)
        if not attestation:
            if transfer["status"] == "pending":
                await db.execute(
                    "UPDATE cctp_transfers SET status='attesting' WHERE id=?", (tid,)
                )
                await db.commit()
            continue

        # Got attestation — mark and complete
        logger.info(f"[CCTP] Attestation ready for transfer {tid}")
        await db.execute(
            "UPDATE cctp_transfers SET status='completing', attestation=? WHERE id=?",
            (attestation, tid),
        )
        await db.commit()

        try:
            tx_hash = await complete_cctp_transfer(msg_hex, attestation)
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            await db.execute(
                "UPDATE cctp_transfers SET status='completed', destination_tx_hash=?, completed_at=? WHERE id=?",
                (tx_hash, now, tid),
            )
            await db.commit()
            logger.info(f"[CCTP] ✅ Transfer {tid} completed → {tx_hash}")

            from core.telegram_notifier import _send
            await _send(
                f"💸 *CCTP Transfer Completed*\n"
                f"From: {transfer['source_chain']}\n"
                f"Amount: {transfer['amount_usdc']} USDC\n"
                f"Tx: `{tx_hash[:18]}…`"
            )
        except Exception as e:
            logger.error(f"[CCTP] Failed to complete transfer {tid}: {e}")
            await db.execute(
                "UPDATE cctp_transfers SET status='failed' WHERE id=?", (tid,)
            )
            await db.commit()
