"""
x402 Payment Protocol for VELUN Protocol.

Spec: https://github.com/coinbase/x402
Scheme: exact — EIP-3009 TransferWithAuthorization (gasless USDC on Base)

Flow:
  1. Client hits protected endpoint → 402 with PAYMENT-REQUIRED header (base64 JSON)
  2. Client signs EIP-3009 authorization (no ETH/gas needed — server executes)
  3. Client retries with X-PAYMENT header (base64 JSON payment proof)
  4. Server verifies EIP-712 signature, executes transferWithAuthorization on Base
  5. 200 response with X-PAYMENT-RESPONSE header (base64 JSON settlement)

Network: eip155:8453 (Base mainnet)
Asset:   USDC 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913
"""

import base64
import json
import logging
import os
import time
from typing import Optional

from fastapi import Header, HTTPException, Request

from config import BASE_RPC_URL, BLOCKCHAIN_ENABLED, PROTOCOL_PRIVATE_KEY, PROTOCOL_WALLET

logger = logging.getLogger("velun.x402")

# ── Constants ─────────────────────────────────────────────────────────────────

USDC_ADDRESS  = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
USDC_DECIMALS = 6
CHAIN_ID      = 8453
NETWORK       = "eip155:8453"
X402_VERSION  = 1

USDC_ABI = [
    {
        "name": "transferWithAuthorization",
        "type": "function",
        "inputs": [
            {"name": "from",        "type": "address"},
            {"name": "to",          "type": "address"},
            {"name": "value",       "type": "uint256"},
            {"name": "validAfter",  "type": "uint256"},
            {"name": "validBefore", "type": "uint256"},
            {"name": "nonce",       "type": "bytes32"},
            {"name": "v",           "type": "uint8"},
            {"name": "r",           "type": "bytes32"},
            {"name": "s",           "type": "bytes32"},
        ],
        "outputs": [],
        "stateMutability": "nonpayable",
    },
    {
        "name": "authorizationState",
        "type": "function",
        "inputs": [
            {"name": "authorizer", "type": "address"},
            {"name": "nonce",      "type": "bytes32"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "view",
    },
    {
        "name": "balanceOf",
        "type": "function",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
    },
]

# In-memory nonce store — prevents replay attacks within a process restart
_used_nonces: set[str] = set()


# ── Helpers ───────────────────────────────────────────────────────────────────

def usdc_to_units(amount_usdc: float) -> int:
    return int(round(amount_usdc * 10 ** USDC_DECIMALS))


def _compute_eip712_hash(authorization: dict, nonce_bytes: bytes) -> bytes:
    """
    Manually compute the EIP-712 digest for TransferWithAuthorization.
    Avoids eth_account.structured_data API version fragility.
    """
    from eth_abi import encode as abi_encode
    from web3 import Web3

    w3 = Web3()

    # --- Domain separator ---
    domain_type_hash = w3.keccak(
        text="EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
    )
    domain_encoded = abi_encode(
        ["bytes32", "bytes32", "bytes32", "uint256", "address"],
        [
            domain_type_hash,
            w3.keccak(text="USD Coin"),
            w3.keccak(text="2"),
            CHAIN_ID,
            w3.to_checksum_address(USDC_ADDRESS),
        ],
    )
    domain_separator = w3.keccak(domain_encoded)

    # --- Struct hash ---
    type_hash = w3.keccak(
        text=(
            "TransferWithAuthorization(address from,address to,uint256 value,"
            "uint256 validAfter,uint256 validBefore,bytes32 nonce)"
        )
    )
    struct_encoded = abi_encode(
        ["bytes32", "address", "address", "uint256", "uint256", "uint256", "bytes32"],
        [
            type_hash,
            w3.to_checksum_address(authorization["from"]),
            w3.to_checksum_address(authorization["to"]),
            int(authorization["value"]),
            int(authorization["validAfter"]),
            int(authorization["validBefore"]),
            nonce_bytes,
        ],
    )
    struct_hash = w3.keccak(struct_encoded)

    # --- Final EIP-712 digest ---
    return bytes(w3.keccak(b"\x19\x01" + domain_separator + struct_hash))


# ── Payment requirements ──────────────────────────────────────────────────────

def build_payment_requirements(
    amount_usdc: float,
    resource: str,
    description: str,
    pay_to: str,
    timeout_seconds: int = 300,
) -> dict:
    return {
        "x402Version": X402_VERSION,
        "error": "Payment required",
        "accepts": [
            {
                "scheme": "exact",
                "network": NETWORK,
                "asset": {
                    "address": USDC_ADDRESS,
                    "chainId": CHAIN_ID,
                    "name": "USDC",
                    "symbol": "USDC",
                    "decimals": USDC_DECIMALS,
                },
                "maxAmountRequired": str(usdc_to_units(amount_usdc)),
                "payTo": pay_to,
                "resource": resource,
                "description": description,
                "mimeType": "application/json",
                "maxTimeoutSeconds": timeout_seconds,
            }
        ],
    }


def encode_b64(obj: dict) -> str:
    return base64.b64encode(json.dumps(obj).encode()).decode()


def decode_payment_header(x_payment: str) -> dict:
    try:
        return json.loads(base64.b64decode(x_payment.encode()))
    except Exception as e:
        raise ValueError(f"Invalid X-PAYMENT header: {e}")


# ── Signature verification ────────────────────────────────────────────────────

def verify_payment_signature(
    authorization: dict,
    signature: str,
    expected_to: str,
    expected_amount: int,
) -> tuple[bool, str]:
    """
    Verify an EIP-3009 TransferWithAuthorization signature.
    Returns (valid, error_message).
    """
    try:
        from eth_account import Account
        from web3 import Web3

        w3 = Web3()
        now = int(time.time())

        valid_after  = int(authorization.get("validAfter",  0))
        valid_before = int(authorization.get("validBefore", 0))
        value        = int(authorization.get("value", 0))
        nonce_hex    = authorization.get("nonce", "")
        pay_to       = authorization.get("to", "")
        from_addr    = authorization.get("from", "")

        if now < valid_after:
            return False, f"Authorization not yet valid (validAfter={valid_after}, now={now})"
        if now > valid_before:
            return False, f"Authorization expired (validBefore={valid_before}, now={now})"
        if value < expected_amount:
            return False, f"Insufficient amount: got {value}, need {expected_amount}"

        try:
            if w3.to_checksum_address(pay_to) != w3.to_checksum_address(expected_to):
                return False, f"Wrong recipient: got {pay_to}, expected {expected_to}"
        except Exception:
            return False, f"Invalid address in authorization"

        nonce_key = f"{from_addr.lower()}:{nonce_hex.lower()}"
        if nonce_key in _used_nonces:
            return False, "Nonce already used (replay attack prevented)"

        nonce_bytes = bytes.fromhex(nonce_hex.lstrip("0x").zfill(64))
        digest = _compute_eip712_hash(authorization, nonce_bytes)

        sig_bytes = bytes.fromhex(signature.lstrip("0x"))
        # _recover_hash: raw 32-byte digest → signer address (no EIP-191 prefix)
        recovered = Account._recover_hash(digest, signature=sig_bytes)

        if recovered.lower() != from_addr.lower():
            return False, f"Signature mismatch: recovered {recovered}, expected {from_addr}"

        return True, ""

    except Exception as e:
        logger.error(f"x402 signature verification error: {e}", exc_info=True)
        return False, f"Verification error: {e}"


# ── On-chain execution ────────────────────────────────────────────────────────

async def execute_transfer(authorization: dict, signature: str) -> str:
    """
    Submit transferWithAuthorization to USDC on Base.
    In simulation mode returns a fake tx hash.
    Returns tx_hash.
    """
    if not BLOCKCHAIN_ENABLED:
        fake_hash = "0x" + os.urandom(32).hex()
        logger.info(
            f"[x402 SIMULATED] {authorization['value']} USDC  "
            f"{authorization['from'][:10]}… → {authorization['to'][:10]}…  tx={fake_hash[:18]}…"
        )
        return fake_hash

    from eth_account import Account as EthAccount
    from web3 import Web3

    w3   = Web3(Web3.HTTPProvider(BASE_RPC_URL))
    usdc = w3.eth.contract(address=w3.to_checksum_address(USDC_ADDRESS), abi=USDC_ABI)

    sig_bytes = bytes.fromhex(signature.lstrip("0x"))
    r = sig_bytes[:32]
    s = sig_bytes[32:64]
    v = sig_bytes[64]
    if v < 27:
        v += 27

    nonce_bytes = bytes.fromhex(authorization["nonce"].lstrip("0x").zfill(64))
    sender = EthAccount.from_key(PROTOCOL_PRIVATE_KEY)

    tx = usdc.functions.transferWithAuthorization(
        w3.to_checksum_address(authorization["from"]),
        w3.to_checksum_address(authorization["to"]),
        int(authorization["value"]),
        int(authorization["validAfter"]),
        int(authorization["validBefore"]),
        nonce_bytes,
        v,
        r,
        s,
    ).build_transaction({
        "from":     sender.address,
        "nonce":    w3.eth.get_transaction_count(sender.address),
        "gasPrice": w3.eth.gas_price,
    })

    signed   = sender.sign_transaction(tx)
    tx_hash  = w3.eth.send_raw_transaction(signed.raw_transaction)
    hex_hash = "0x" + tx_hash.hex()
    logger.info(f"[x402] transferWithAuthorization → {hex_hash}")
    return hex_hash


def build_payment_response(tx_hash: str, payer: str, amount_units: int) -> str:
    payload = {
        "x402Version": X402_VERSION,
        "success":     True,
        "transaction": tx_hash,
        "network":     NETWORK,
        "payer":       payer,
        "amount":      str(amount_units),
        "timestamp":   int(time.time()),
    }
    return encode_b64(payload)


# ── FastAPI dependency factory ────────────────────────────────────────────────

def require_payment(amount_usdc: float, description: str = "API access"):
    """
    FastAPI dependency that enforces x402 payment before serving a route.

    Usage:
        @router.get("/premium")
        async def endpoint(payment=Depends(require_payment(0.01, "Report $0.01"))):
            return {"paid_by": payment["from"]}

    If X-PAYMENT header is absent  → 402 with PAYMENT-REQUIRED header.
    If payment is valid            → injects payment dict into handler.
    """
    expected_units = usdc_to_units(amount_usdc)

    async def _dep(
        request: Request,
        x_payment: Optional[str] = Header(None, alias="X-PAYMENT"),
    ):
        pay_to = PROTOCOL_WALLET
        if not pay_to:
            logger.warning("[x402] PROTOCOL_WALLET not set — bypassing payment (dev mode)")
            return {"simulated": True, "skipped": True, "from": "dev", "tx_hash": None}

        resource     = str(request.url.path)
        requirements = build_payment_requirements(amount_usdc, resource, description, pay_to)

        if not x_payment:
            raise HTTPException(
                status_code=402,
                detail=requirements,
                headers={"PAYMENT-REQUIRED": encode_b64(requirements)},
            )

        try:
            proof = decode_payment_header(x_payment)
        except ValueError as e:
            raise HTTPException(status_code=402, detail={"error": str(e)})

        if proof.get("x402Version") != X402_VERSION:
            raise HTTPException(
                status_code=402,
                detail={"error": f"Unsupported x402Version: {proof.get('x402Version')}"},
            )

        payload       = proof.get("payload", {})
        authorization = payload.get("authorization", {})
        signature     = payload.get("signature", "")

        valid, error = verify_payment_signature(
            authorization, signature, pay_to, expected_units
        )
        if not valid:
            raise HTTPException(
                status_code=402,
                detail={"error": f"Payment invalid: {error}"},
                headers={"PAYMENT-REQUIRED": encode_b64(requirements)},
            )

        try:
            tx_hash = await execute_transfer(authorization, signature)
        except Exception as e:
            raise HTTPException(
                status_code=402,
                detail={"error": f"Payment execution failed: {e}"},
            )

        # Mark nonce used to prevent replay
        nonce_key = f"{authorization['from'].lower()}:{authorization['nonce'].lower()}"
        _used_nonces.add(nonce_key)

        payment_info = {
            "from":        authorization["from"],
            "amount_usdc": amount_usdc,
            "tx_hash":     tx_hash,
            "simulated":   not BLOCKCHAIN_ENABLED,
        }

        # Stash response header for the route handler to attach
        request.state.x402_payment          = payment_info
        request.state.x402_response_header  = build_payment_response(
            tx_hash, authorization["from"], expected_units
        )

        logger.info(
            f"[x402] ✅ {amount_usdc} USDC from {authorization['from'][:10]}…  "
            f"tx={tx_hash[:18]}…  route={resource}"
        )
        return payment_info

    return _dep
