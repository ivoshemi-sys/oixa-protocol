"""
OIXA Protocol — Token Swap Engine (Uniswap V3 on Base)

Detecta el balance completo de una wallet en Base mainnet y ejecuta swaps
a USDC vía Uniswap V3. Toda comunicación con el usuario usa lenguaje simple:
  - "wallet"       → "cuenta de cobro"
  - "USDC"         → "dólares digitales"
  - "swap"         → "conversión"
  - "transaction"  → "movimiento"
  - "token"        → "moneda"
  - "ERC-20"       → (nunca se dice)
"""

import json
import logging
from typing import Optional

logger = logging.getLogger("oixa.token_swap")

# ── Uniswap V3 on Base ────────────────────────────────────────────────────────

UNISWAP_ROUTER      = "0x2626664c2603336E57B271c5C0b26F421741e481"  # SwapRouter02
UNISWAP_QUOTER      = "0x3d4e44Eb1374240CE5F1B136041Ca7A1a42a3F79"  # QuoterV2
USDC_BASE           = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
WETH_BASE           = "0x4200000000000000000000000000000000000006"
ETH_PLACEHOLDER     = "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"
POOL_FEE_LOW        = 500    # 0.05% — stable pairs
POOL_FEE_MEDIUM     = 3000   # 0.3%  — most pairs
POOL_FEE_HIGH       = 10000  # 1%    — exotic pairs

# Well-known Base tokens with friendly names
KNOWN_TOKENS: dict[str, dict] = {
    "ETH": {
        "address":  ETH_PLACEHOLDER,
        "decimals": 18,
        "symbol":   "ETH",
        "name":     "Ether",
        "label":    "Ethereum",
        "coingecko": "ethereum",
    },
    "WETH": {
        "address":  WETH_BASE,
        "decimals": 18,
        "symbol":   "WETH",
        "name":     "Wrapped Ether",
        "label":    "Ethereum envuelto",
        "coingecko": "weth",
    },
    "USDC": {
        "address":  USDC_BASE,
        "decimals": 6,
        "symbol":   "USDC",
        "name":     "USD Coin",
        "label":    "dólares digitales",
        "coingecko": "usd-coin",
    },
    "cbETH": {
        "address":  "0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0DEc22",
        "decimals": 18,
        "symbol":   "cbETH",
        "name":     "Coinbase Wrapped Staked ETH",
        "label":    "Ethereum de Coinbase",
        "coingecko": "coinbase-wrapped-staked-eth",
    },
    "DAI": {
        "address":  "0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb",
        "decimals": 18,
        "symbol":   "DAI",
        "name":     "Dai",
        "label":    "DAI",
        "coingecko": "dai",
    },
}

# ERC-20 minimal ABI
ERC20_ABI = [
    {"inputs": [{"name": "account", "type": "address"}], "name": "balanceOf",
     "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "decimals",
     "outputs": [{"name": "", "type": "uint8"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "name": "approve",
     "outputs": [{"name": "", "type": "bool"}], "stateMutability": "nonpayable", "type": "function"},
]

# Uniswap V3 SwapRouter02 — exactInputSingle
ROUTER_ABI = [
    {
        "inputs": [{
            "components": [
                {"name": "tokenIn",           "type": "address"},
                {"name": "tokenOut",          "type": "address"},
                {"name": "fee",               "type": "uint24"},
                {"name": "recipient",         "type": "address"},
                {"name": "amountIn",          "type": "uint256"},
                {"name": "amountOutMinimum",  "type": "uint256"},
                {"name": "sqrtPriceLimitX96", "type": "uint160"},
            ],
            "name": "params", "type": "tuple",
        }],
        "name": "exactInputSingle",
        "outputs": [{"name": "amountOut", "type": "uint256"}],
        "stateMutability": "payable",
        "type": "function",
    },
]

# QuoterV2 — quoteExactInputSingle
QUOTER_ABI = [
    {
        "inputs": [{
            "components": [
                {"name": "tokenIn",            "type": "address"},
                {"name": "tokenOut",           "type": "address"},
                {"name": "amountIn",           "type": "uint256"},
                {"name": "fee",                "type": "uint24"},
                {"name": "sqrtPriceLimitX96",  "type": "uint160"},
            ],
            "name": "params", "type": "tuple",
        }],
        "name": "quoteExactInputSingle",
        "outputs": [
            {"name": "amountOut",               "type": "uint256"},
            {"name": "sqrtPriceX96After",       "type": "uint160"},
            {"name": "initializedTicksCrossed", "type": "uint32"},
            {"name": "gasEstimate",             "type": "uint256"},
        ],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]


def _get_web3(rpc_url: str):
    """Get a Web3 instance or raise ImportError/ValueError."""
    from web3 import Web3
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected():
        raise ConnectionError(f"Cannot connect to RPC: {rpc_url}")
    return w3


# ── Balance detection ─────────────────────────────────────────────────────────

async def get_wallet_balances(
    wallet_address: str,
    rpc_url:        str,
) -> dict:
    """
    Detecta el balance completo de una wallet en Base mainnet.

    Retorna dict con todos los tokens con balance > 0 y su valor estimado en USD.
    También calcula el total de "dólares digitales" disponibles para convertir.
    """
    try:
        from web3 import Web3
        w3 = _get_web3(rpc_url)
        addr = Web3.to_checksum_address(wallet_address)
    except Exception as e:
        logger.warning(f"[TokenSwap] Cannot connect to RPC: {e}")
        return _simulated_balances(wallet_address)

    balances = {}

    # ETH balance
    try:
        eth_wei = w3.eth.get_balance(addr)
        eth_bal = eth_wei / 10**18
        if eth_bal > 0.0001:
            balances["ETH"] = {
                "symbol":    "ETH",
                "label":     "Ethereum",
                "balance":   eth_bal,
                "decimals":  18,
                "address":   ETH_PLACEHOLDER,
                "usd_value": eth_bal * await _get_eth_price_usd(),
                "swappable": True,
            }
    except Exception as e:
        logger.debug(f"[TokenSwap] ETH balance error: {e}")

    # ERC-20 tokens
    for sym, info in KNOWN_TOKENS.items():
        if info["address"] in (ETH_PLACEHOLDER, USDC_BASE):
            continue
        try:
            contract = w3.eth.contract(
                address=Web3.to_checksum_address(info["address"]),
                abi=ERC20_ABI,
            )
            raw_bal = contract.functions.balanceOf(addr).call()
            balance = raw_bal / 10**info["decimals"]
            if balance > 0.0001:
                price = await _get_token_price_usd(info.get("coingecko", ""))
                balances[sym] = {
                    "symbol":    sym,
                    "label":     info["label"],
                    "balance":   balance,
                    "decimals":  info["decimals"],
                    "address":   info["address"],
                    "usd_value": balance * price,
                    "swappable": sym != "USDC",
                }
        except Exception as e:
            logger.debug(f"[TokenSwap] {sym} balance error: {e}")

    # USDC balance
    try:
        usdc_contract = w3.eth.contract(
            address=Web3.to_checksum_address(USDC_BASE),
            abi=ERC20_ABI,
        )
        raw_usdc = usdc_contract.functions.balanceOf(addr).call()
        usdc_bal = raw_usdc / 10**6
        balances["USDC"] = {
            "symbol":    "USDC",
            "label":     "dólares digitales",
            "balance":   usdc_bal,
            "decimals":  6,
            "address":   USDC_BASE,
            "usd_value": usdc_bal,
            "swappable": False,
        }
    except Exception as e:
        logger.debug(f"[TokenSwap] USDC balance error: {e}")
        balances["USDC"] = {"symbol": "USDC", "label": "dólares digitales",
                             "balance": 0.0, "usd_value": 0.0, "swappable": False}

    total_usd      = sum(v["usd_value"] for v in balances.values())
    swappable_usd  = sum(v["usd_value"] for v in balances.values() if v.get("swappable"))
    current_usdc   = balances.get("USDC", {}).get("balance", 0.0)

    return {
        "wallet":          wallet_address,
        "tokens":          balances,
        "total_usd":       round(total_usd, 4),
        "swappable_usd":   round(swappable_usd, 4),
        "current_usdc":    round(current_usdc, 6),
        "has_usdc":        current_usdc >= 0.01,
        "has_anything":    total_usd > 0.01,
        "rpc_connected":   True,
    }


def _simulated_balances(wallet_address: str) -> dict:
    """Fallback when RPC not configured — returns zero balances."""
    return {
        "wallet":        wallet_address,
        "tokens":        {"USDC": {"symbol": "USDC", "label": "dólares digitales",
                                    "balance": 0.0, "usd_value": 0.0, "swappable": False}},
        "total_usd":     0.0,
        "swappable_usd": 0.0,
        "current_usdc":  0.0,
        "has_usdc":      False,
        "has_anything":  False,
        "rpc_connected": False,
    }


# ── Price fetching (lightweight, no API key needed) ───────────────────────────

_PRICE_CACHE: dict[str, tuple[float, float]] = {}  # symbol → (price_usd, timestamp)
_PRICE_TTL = 60.0  # seconds

async def _get_eth_price_usd() -> float:
    return await _get_token_price_usd("ethereum")

async def _get_token_price_usd(coingecko_id: str) -> float:
    """Fetch USD price from CoinGecko (no API key, public endpoint)."""
    import time
    if not coingecko_id:
        return 0.0

    cached = _PRICE_CACHE.get(coingecko_id)
    if cached and (time.time() - cached[1]) < _PRICE_TTL:
        return cached[0]

    try:
        import httpx
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={coingecko_id}&vs_currencies=usd"
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(url)
            data = resp.json()
            price = data.get(coingecko_id, {}).get("usd", 0.0)
            _PRICE_CACHE[coingecko_id] = (price, time.time())
            return price
    except Exception:
        # Fallback hardcoded prices
        fallbacks = {"ethereum": 3500.0, "weth": 3500.0, "coinbase-wrapped-staked-eth": 3700.0, "dai": 1.0}
        return fallbacks.get(coingecko_id, 1.0)


# ── Quote (no gas, read-only) ─────────────────────────────────────────────────

async def get_swap_quote(
    token_in_address: str,
    amount_in:        float,
    decimals_in:      int,
    rpc_url:          str,
    fee_tier:         int = POOL_FEE_MEDIUM,
) -> dict:
    """
    Obtiene una cotización de conversión sin ejecutar el swap.
    Usa el QuoterV2 de Uniswap V3 en Base.

    Returns:
        {
          "amount_in_usd":   float,
          "amount_out_usdc": float,
          "price_impact_pct": float,
          "fee_usdc":        float,
          "net_usdc":        float,
          "fee_tier":        int,
        }
    """
    try:
        from web3 import Web3
        w3 = _get_web3(rpc_url)

        # Wrap ETH placeholder to WETH for quoting
        actual_token_in = (
            WETH_BASE if token_in_address.lower() == ETH_PLACEHOLDER.lower()
            else token_in_address
        )

        quoter = w3.eth.contract(
            address=Web3.to_checksum_address(UNISWAP_QUOTER),
            abi=QUOTER_ABI,
        )
        amount_in_raw = int(amount_in * 10**decimals_in)

        # Try quoteExactInputSingle (call, not send)
        result = quoter.functions.quoteExactInputSingle({
            "tokenIn":           Web3.to_checksum_address(actual_token_in),
            "tokenOut":          Web3.to_checksum_address(USDC_BASE),
            "amountIn":          amount_in_raw,
            "fee":               fee_tier,
            "sqrtPriceLimitX96": 0,
        }).call()

        amount_out_raw = result[0]
        amount_out_usdc = amount_out_raw / 10**6

        fee_pct = fee_tier / 1_000_000
        fee_usdc = amount_out_usdc * fee_pct

        return {
            "amount_out_usdc":  round(amount_out_usdc, 4),
            "fee_tier":         fee_tier,
            "fee_pct":          fee_pct * 100,
            "fee_usdc":         round(fee_usdc, 6),
            "net_usdc":         round(amount_out_usdc - fee_usdc, 4),
            "slippage_pct":     0.5,
            "min_out_usdc":     round(amount_out_usdc * 0.995, 4),  # 0.5% slippage
            "quoted":           True,
        }

    except Exception as e:
        logger.warning(f"[TokenSwap] Quote failed: {e} — using price estimate")
        # Fallback: estimate from CoinGecko price
        eth_price = await _get_eth_price_usd()
        amount_out_usdc = amount_in * eth_price
        return {
            "amount_out_usdc": round(amount_out_usdc, 4),
            "fee_tier":        fee_tier,
            "fee_pct":         fee_tier / 1_000_000 * 100,
            "fee_usdc":        round(amount_out_usdc * (fee_tier / 1_000_000), 6),
            "net_usdc":        round(amount_out_usdc * 0.997, 4),
            "slippage_pct":    0.5,
            "min_out_usdc":    round(amount_out_usdc * 0.995, 4),
            "quoted":          False,  # estimated
        }


# ── Execute swap ──────────────────────────────────────────────────────────────

async def execute_swap(
    private_key:      str,
    token_in_address: str,
    amount_in:        float,
    decimals_in:      int,
    recipient:        str,
    rpc_url:          str,
    slippage_pct:     float = 0.5,
    fee_tier:         int   = POOL_FEE_MEDIUM,
) -> dict:
    """
    Ejecuta la conversión de un token a USDC vía Uniswap V3 en Base.

    Returns:
        {
          "success": bool,
          "tx_hash": str,
          "amount_out_usdc": float,
          "message": str,           # mensaje para el usuario (lenguaje simple)
        }
    """
    try:
        from web3 import Web3
        from eth_account import Account

        w3   = _get_web3(rpc_url)
        acct = Account.from_key(private_key)
        addr = Web3.to_checksum_address(acct.address)

        is_eth = token_in_address.lower() == ETH_PLACEHOLDER.lower()
        amount_in_raw = int(amount_in * 10**decimals_in)

        # Quote first to get min out
        quote = await get_swap_quote(token_in_address, amount_in, decimals_in, rpc_url, fee_tier)
        min_out = int(quote["min_out_usdc"] * 10**6)

        router = w3.eth.contract(
            address=Web3.to_checksum_address(UNISWAP_ROUTER),
            abi=ROUTER_ABI,
        )

        actual_token_in = WETH_BASE if is_eth else token_in_address
        nonce    = w3.eth.get_transaction_count(addr)
        gas_price = w3.eth.gas_price

        params = {
            "tokenIn":           Web3.to_checksum_address(actual_token_in),
            "tokenOut":          Web3.to_checksum_address(USDC_BASE),
            "fee":               fee_tier,
            "recipient":         Web3.to_checksum_address(recipient),
            "amountIn":          amount_in_raw,
            "amountOutMinimum":  min_out,
            "sqrtPriceLimitX96": 0,
        }

        # Approve ERC-20 if not ETH
        if not is_eth:
            token_contract = w3.eth.contract(
                address=Web3.to_checksum_address(token_in_address),
                abi=ERC20_ABI,
            )
            approve_tx = token_contract.functions.approve(
                Web3.to_checksum_address(UNISWAP_ROUTER),
                amount_in_raw,
            ).build_transaction({
                "from":     addr,
                "nonce":    nonce,
                "gasPrice": gas_price,
                "gas":      100_000,
            })
            signed_approve = acct.sign_transaction(approve_tx)
            w3.eth.send_raw_transaction(signed_approve.raw_transaction)
            nonce += 1

        # Build swap tx
        swap_tx = router.functions.exactInputSingle(params).build_transaction({
            "from":     addr,
            "nonce":    nonce,
            "gasPrice": gas_price,
            "gas":      300_000,
            "value":    amount_in_raw if is_eth else 0,
        })

        signed_swap = acct.sign_transaction(swap_tx)
        tx_hash = w3.eth.send_raw_transaction(signed_swap.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

        if receipt.status == 1:
            actual_usdc = quote["amount_out_usdc"]
            return {
                "success":         True,
                "tx_hash":         tx_hash.hex(),
                "amount_out_usdc": actual_usdc,
                "gas_used":        receipt.gasUsed,
                "message": (
                    f"✅ Conversión exitosa. Recibiste {actual_usdc:.2f} dólares digitales "
                    f"en tu cuenta. Ya podés empezar a generar ingresos en OIXA."
                ),
            }
        else:
            return {
                "success": False,
                "tx_hash": tx_hash.hex(),
                "message": "La conversión no se completó. Intentá de nuevo.",
            }

    except Exception as e:
        logger.error(f"[TokenSwap] Swap failed: {e}")
        return {
            "success": False,
            "error":   str(e),
            "message": (
                "No se pudo completar la conversión ahora. "
                "Verificá que tenés suficiente ETH para el costo de red y volvé a intentar."
            ),
        }


# ── Wallet generation ─────────────────────────────────────────────────────────

def generate_wallet() -> dict:
    """
    Genera una nueva wallet de Base mainnet.
    Retorna private_key, address y mnemonic (si se pide).

    IMPORTANTE: El private_key NUNCA se guarda en la DB — solo se muestra al usuario.
    """
    try:
        from eth_account import Account
        Account.enable_unaudited_hdwallet_features()
        acct, mnemonic = Account.create_with_mnemonic()
        return {
            "address":     acct.address,
            "private_key": acct.key.hex(),
            "mnemonic":    mnemonic,
            "network":     "Base mainnet",
            "chain_id":    8453,
        }
    except Exception as e:
        # Fallback without mnemonic
        try:
            from eth_account import Account
            acct = Account.create()
            return {
                "address":     acct.address,
                "private_key": acct.key.hex(),
                "mnemonic":    None,
                "network":     "Base mainnet",
                "chain_id":    8453,
            }
        except Exception as e2:
            logger.error(f"[TokenSwap] Wallet generation failed: {e2}")
            raise
