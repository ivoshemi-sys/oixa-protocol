"""
BlockchainEscrowClient — on-chain USDC escrow on Base mainnet.

Modes:
  - ENABLED: BASE_RPC_URL + PROTOCOL_PRIVATE_KEY + ESCROW_CONTRACT_ADDRESS set → real txs
  - SIMULATED: any of the above missing → falls back transparently, logs a warning once

The DB always remains the source of truth. The blockchain is the settlement layer.
"""

import asyncio
import logging
import hashlib
from decimal import Decimal
from typing import Optional

logger = logging.getLogger("axon.blockchain")

# USDC has 6 decimals
USDC_DECIMALS = 6
USDC_FACTOR   = 10 ** USDC_DECIMALS


def _usdc_to_raw(amount_float: float) -> int:
    """Convert human USDC (e.g. 0.35) to raw units (e.g. 350_000). No fp errors."""
    return int(Decimal(str(amount_float)) * USDC_FACTOR)


def _raw_to_usdc(raw: int) -> float:
    return raw / USDC_FACTOR


def _id_to_bytes32(id_str: str) -> bytes:
    """Stable bytes32 from an AXON ID string via SHA-256 (first 32 bytes)."""
    return hashlib.sha256(id_str.encode()).digest()


class BlockchainEscrowClient:
    """
    Async client for AXONEscrow contract on Base.
    Instantiate once at startup via `escrow_client = BlockchainEscrowClient()`.
    Call `await escrow_client.init()` in FastAPI lifespan.
    """

    def __init__(self):
        self.enabled   = False
        self._w3       = None
        self._contract = None
        self._usdc     = None
        self._account  = None
        self._chain_id: Optional[int] = None
        self._warned   = False

    async def init(self):
        """Try to connect to Base. Sets self.enabled = True on success."""
        try:
            from config import BASE_RPC_URL, PROTOCOL_PRIVATE_KEY, ESCROW_CONTRACT_ADDRESS
        except ImportError:
            BASE_RPC_URL = PROTOCOL_PRIVATE_KEY = ESCROW_CONTRACT_ADDRESS = ""

        if not (BASE_RPC_URL and PROTOCOL_PRIVATE_KEY and ESCROW_CONTRACT_ADDRESS):
            logger.warning(
                "Blockchain escrow DISABLED — set BASE_RPC_URL, PROTOCOL_PRIVATE_KEY, "
                "ESCROW_CONTRACT_ADDRESS in .env to enable real USDC transactions."
            )
            return

        try:
            from web3 import AsyncWeb3, AsyncHTTPProvider
            from eth_account import Account
            from blockchain.abi import AXON_ESCROW_ABI, USDC_ABI, USDC_BASE_ADDRESS, BASE_CHAIN_ID

            w3 = AsyncWeb3(AsyncHTTPProvider(BASE_RPC_URL))

            if not await w3.is_connected():
                logger.error("Cannot reach Base RPC — blockchain escrow disabled")
                return

            chain_id = await w3.eth.chain_id
            if chain_id not in (BASE_CHAIN_ID, 84532):  # mainnet or sepolia
                logger.error(f"Wrong chain {chain_id} — expected Base mainnet (8453) or Sepolia (84532)")
                return

            account = Account.from_key(PROTOCOL_PRIVATE_KEY)

            # Pick USDC address based on chain
            from blockchain.abi import USDC_BASE_SEPOLIA_ADDRESS
            usdc_addr = USDC_BASE_ADDRESS if chain_id == BASE_CHAIN_ID else USDC_BASE_SEPOLIA_ADDRESS

            contract = w3.eth.contract(
                address=AsyncWeb3.to_checksum_address(ESCROW_CONTRACT_ADDRESS),
                abi=AXON_ESCROW_ABI,
            )
            usdc = w3.eth.contract(
                address=AsyncWeb3.to_checksum_address(usdc_addr),
                abi=USDC_ABI,
            )

            # Smoke-test: read protocol address from contract
            protocol_addr = await contract.functions.protocol().call()
            if protocol_addr.lower() != account.address.lower():
                logger.warning(
                    f"Contract protocol={protocol_addr} != wallet={account.address}. "
                    "Only the protocol wallet can release/refund."
                )

            self._w3       = w3
            self._contract = contract
            self._usdc     = usdc
            self._account  = account
            self._chain_id = chain_id
            self.enabled   = True

            network = "Base Mainnet" if chain_id == BASE_CHAIN_ID else "Base Sepolia"
            logger.info(
                f"✅ Blockchain escrow ENABLED | {network} | "
                f"Contract: {ESCROW_CONTRACT_ADDRESS} | "
                f"Wallet: {account.address}"
            )

        except Exception as e:
            logger.error(f"Blockchain init failed: {e} — running in simulated mode")

    # ──────────────────────────────────────────────────────────────────────────
    # Public interface
    # ──────────────────────────────────────────────────────────────────────────

    async def create_escrow(
        self,
        escrow_id: str,
        auction_id: str,
        payee_address: Optional[str],
        amount_usdc: float,
        commission_usdc: float,
    ) -> dict:
        """
        Lock USDC in the on-chain escrow contract.
        Returns a result dict with `simulated: bool` and optional `tx_hash`.
        """
        if not self.enabled:
            return self._sim("create", escrow_id)

        try:
            escrow_bytes  = _id_to_bytes32(escrow_id)
            auction_bytes = _id_to_bytes32(auction_id)
            amount_raw    = _usdc_to_raw(amount_usdc)
            commission_raw = _usdc_to_raw(commission_usdc)

            # Payee defaults to protocol wallet if agent has no on-chain address
            payee = (
                self._w3.to_checksum_address(payee_address)
                if payee_address
                else self._account.address
            )

            # Ensure allowance
            await self._ensure_allowance(amount_raw)

            # Build + send createEscrow tx
            tx_hash = await self._send(
                self._contract.functions.createEscrow(
                    escrow_bytes, auction_bytes, payee, amount_raw, commission_raw
                )
            )
            receipt = await self._wait(tx_hash)

            logger.info(
                f"[CHAIN] EscrowCreated | {escrow_id} | "
                f"{amount_usdc} USDC | tx={tx_hash.hex()[:16]}..."
            )
            return {
                "simulated":    False,
                "tx_hash":      tx_hash.hex(),
                "block":        receipt["blockNumber"],
                "amount_usdc":  amount_usdc,
                "commission_usdc": commission_usdc,
                "payee":        payee,
            }

        except Exception as e:
            logger.error(f"create_escrow on-chain failed: {e} — falling back to simulated")
            return self._sim("create", escrow_id, error=str(e))

    async def release_escrow(self, escrow_id: str) -> dict:
        """Release escrow: net → payee, commission → protocol wallet."""
        if not self.enabled:
            return self._sim("release", escrow_id)

        try:
            tx_hash = await self._send(
                self._contract.functions.release(_id_to_bytes32(escrow_id))
            )
            receipt = await self._wait(tx_hash)
            logger.info(f"[CHAIN] EscrowReleased | {escrow_id} | tx={tx_hash.hex()[:16]}...")
            return {"simulated": False, "tx_hash": tx_hash.hex(), "block": receipt["blockNumber"]}

        except Exception as e:
            logger.error(f"release_escrow on-chain failed: {e} — falling back to simulated")
            return self._sim("release", escrow_id, error=str(e))

    async def refund_escrow(self, escrow_id: str) -> dict:
        """Refund escrow back to payer."""
        if not self.enabled:
            return self._sim("refund", escrow_id)

        try:
            tx_hash = await self._send(
                self._contract.functions.refund(_id_to_bytes32(escrow_id))
            )
            receipt = await self._wait(tx_hash)
            logger.info(f"[CHAIN] EscrowRefunded | {escrow_id} | tx={tx_hash.hex()[:16]}...")
            return {"simulated": False, "tx_hash": tx_hash.hex(), "block": receipt["blockNumber"]}

        except Exception as e:
            logger.error(f"refund_escrow on-chain failed: {e} — falling back to simulated")
            return self._sim("refund", escrow_id, error=str(e))

    async def pause_contract(self) -> dict:
        """Pause the escrow contract (emergency). Only protocol wallet."""
        if not self.enabled:
            return {"simulated": True, "action": "pause"}
        try:
            tx_hash = await self._send(self._contract.functions.pause())
            receipt = await self._wait(tx_hash)
            logger.warning(f"[EMERGENCY] Contract PAUSED | tx={tx_hash.hex()[:16]}...")
            return {"simulated": False, "action": "pause", "tx_hash": tx_hash.hex()}
        except Exception as e:
            logger.error(f"pause_contract failed: {e}")
            return {"simulated": True, "action": "pause", "error": str(e)}

    async def unpause_contract(self) -> dict:
        """Unpause the escrow contract. Only protocol wallet."""
        if not self.enabled:
            return {"simulated": True, "action": "unpause"}
        try:
            tx_hash = await self._send(self._contract.functions.unpause())
            receipt = await self._wait(tx_hash)
            logger.info(f"Contract UNPAUSED | tx={tx_hash.hex()[:16]}...")
            return {"simulated": False, "action": "unpause", "tx_hash": tx_hash.hex()}
        except Exception as e:
            logger.error(f"unpause_contract failed: {e}")
            return {"simulated": True, "action": "unpause", "error": str(e)}

    async def is_paused(self) -> bool:
        """Check if the contract is paused."""
        if not self.enabled:
            return False
        try:
            return await self._contract.functions.paused().call()
        except Exception:
            return False

    async def get_escrow_onchain(self, escrow_id: str) -> Optional[dict]:
        """Read escrow state directly from chain (None if not enabled or not found)."""
        if not self.enabled:
            return None
        try:
            result = await self._contract.functions.getEscrow(
                _id_to_bytes32(escrow_id)
            ).call()
            # result is a tuple matching the Escrow struct
            status_map = {0: "active", 1: "released", 2: "refunded"}
            return {
                "payer":       result[0],
                "payee":       result[1],
                "amount_usdc": _raw_to_usdc(result[2]),
                "commission_usdc": _raw_to_usdc(result[3]),
                "status":      status_map.get(result[4], str(result[4])),
                "auction_id_bytes": result[5].hex(),
                "created_at_ts":    result[6],
            }
        except Exception as e:
            logger.debug(f"get_escrow_onchain failed: {e}")
            return None

    async def get_contract_stats(self) -> Optional[dict]:
        """Read cumulative stats from the contract."""
        if not self.enabled:
            return None
        try:
            locked, released, commissions, refunded = (
                await self._contract.functions.stats().call()
            )
            balance = await self._contract.functions.contractBalance().call()
            return {
                "locked_usdc":      _raw_to_usdc(locked),
                "released_usdc":    _raw_to_usdc(released),
                "commissions_usdc": _raw_to_usdc(commissions),
                "refunded_usdc":    _raw_to_usdc(refunded),
                "contract_balance_usdc": _raw_to_usdc(balance),
                "chain_id":         self._chain_id,
            }
        except Exception as e:
            logger.debug(f"get_contract_stats failed: {e}")
            return None

    async def get_wallet_balance(self) -> Optional[dict]:
        """Return protocol wallet USDC and ETH balance."""
        if not self.enabled:
            return None
        try:
            usdc_raw = await self._usdc.functions.balanceOf(self._account.address).call()
            eth_wei  = await self._w3.eth.get_balance(self._account.address)
            return {
                "address":    self._account.address,
                "usdc":       _raw_to_usdc(usdc_raw),
                "eth":        float(self._w3.from_wei(eth_wei, "ether")),
                "chain_id":   self._chain_id,
            }
        except Exception as e:
            logger.debug(f"get_wallet_balance failed: {e}")
            return None

    # ──────────────────────────────────────────────────────────────────────────
    # Private helpers
    # ──────────────────────────────────────────────────────────────────────────

    async def _ensure_allowance(self, amount_raw: int):
        """Approve the escrow contract to spend USDC if allowance is insufficient."""
        allowance = await self._usdc.functions.allowance(
            self._account.address, self._contract.address
        ).call()
        if allowance < amount_raw:
            # Approve a large buffer to avoid repeated approvals
            approve_amount = max(amount_raw, _usdc_to_raw(1000))
            tx_hash = await self._send(
                self._usdc.functions.approve(self._contract.address, approve_amount)
            )
            await self._wait(tx_hash)
            logger.info(f"[CHAIN] USDC approved: {_raw_to_usdc(approve_amount)} USDC")

    async def _send(self, fn) -> bytes:
        """Build, sign, and send a contract function call. Returns tx hash."""
        nonce     = await self._w3.eth.get_transaction_count(self._account.address)
        gas_price = await self._w3.eth.gas_price

        # Add 20% gas price buffer for faster inclusion
        gas_price = int(gas_price * 1.2)

        tx = await fn.build_transaction({
            "from":     self._account.address,
            "nonce":    nonce,
            "gasPrice": gas_price,
        })

        # Estimate gas and add buffer
        try:
            estimated = await self._w3.eth.estimate_gas(tx)
            tx["gas"] = int(estimated * 1.3)
        except Exception:
            tx["gas"] = 300_000  # conservative fallback

        signed   = self._w3.eth.account.sign_transaction(tx, self._account.key)
        tx_hash  = await self._w3.eth.send_raw_transaction(signed.raw_transaction)
        return tx_hash

    async def _wait(self, tx_hash: bytes, timeout: int = 90) -> dict:
        receipt = await self._w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout)
        if receipt["status"] == 0:
            raise RuntimeError(f"Transaction reverted: {tx_hash.hex()}")
        return dict(receipt)

    @staticmethod
    def _sim(action: str, escrow_id: str, error: str = "") -> dict:
        result = {"simulated": True, "action": action, "escrow_id": escrow_id}
        if error:
            result["fallback_reason"] = error
        return result


# Singleton — imported everywhere
escrow_client = BlockchainEscrowClient()
