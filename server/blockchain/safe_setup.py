"""
Safe{Core} Multisig Setup for OIXA Protocol on Base mainnet.

Usage:
  python -m blockchain.safe_setup predict   → compute deterministic Safe address (FREE, no gas)
  python -m blockchain.safe_setup deploy    → deploy Safe on-chain (requires ETH for gas)

The Safe is deployed as a 1/1 multisig initially (protocol wallet as sole signer).
Add Ivan's personal hardware wallet as a second signer for 2/2 security later.

Safe v1.4.1 deployment addresses on Base mainnet (chain 8453):
  ProxyFactory:          0x4e1DCf7AD4e460CfD30791CCC4F9c8a4f820ec67
  SafeL2 singleton:      0x29fcB43b46531BcA003ddC8FCB67FFE91900C762
  FallbackHandler:       0xfd0732Dc9E303f09fCEf3a7388Ad10A83459Ec99
"""

import asyncio
import hashlib
import sys
from pathlib import Path

from dotenv import load_dotenv
import os

load_dotenv(Path(__file__).parent.parent.parent / ".env")

# ── Safe v1.4.1 addresses on Base mainnet ────────────────────────────────────
PROXY_FACTORY_ADDRESS      = "0x4e1DCf7AD4e460CfD30791CCC4F9c8a4f820ec67"
SAFE_SINGLETON_L2          = "0x29fcB43b46531BcA003ddC8FCB67FFE91900C762"
COMPATIBILITY_HANDLER      = "0xfd0732Dc9E303f09fCEf3a7388Ad10A83459Ec99"
BASE_CHAIN_ID              = 8453
BASE_SEPOLIA_CHAIN_ID      = 84532

# Minimal ABI for SafeProxyFactory.createProxyWithNonce
PROXY_FACTORY_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "_singleton",   "type": "address"},
            {"internalType": "bytes",   "name": "initializer",  "type": "bytes"},
            {"internalType": "uint256", "name": "saltNonce",    "type": "uint256"},
        ],
        "name": "createProxyWithNonce",
        "outputs": [{"internalType": "address", "name": "proxy", "type": "address"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "proxyCreationCode",
        "outputs": [{"internalType": "bytes", "name": "", "type": "bytes"}],
        "stateMutability": "pure",
        "type": "function",
    },
]

# SafeL2 setup function ABI (for building the initializer)
SAFE_SETUP_ABI = [
    {
        "inputs": [
            {"internalType": "address[]", "name": "_owners",          "type": "address[]"},
            {"internalType": "uint256",   "name": "_threshold",        "type": "uint256"},
            {"internalType": "address",   "name": "to",                "type": "address"},
            {"internalType": "bytes",     "name": "data",              "type": "bytes"},
            {"internalType": "address",   "name": "fallbackHandler",   "type": "address"},
            {"internalType": "address",   "name": "paymentToken",      "type": "address"},
            {"internalType": "uint256",   "name": "payment",           "type": "uint256"},
            {"internalType": "address",   "name": "paymentReceiver",   "type": "address"},
        ],
        "name": "setup",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

NULL_ADDRESS = "0x0000000000000000000000000000000000000000"


def build_safe_initializer(owner_address: str) -> bytes:
    """Build the setup() calldata that initializes the Safe."""
    from web3 import Web3
    w3 = Web3()
    safe_contract = w3.eth.contract(
        address=Web3.to_checksum_address(SAFE_SINGLETON_L2),
        abi=SAFE_SETUP_ABI,
    )
    calldata = safe_contract.encode_abi(
        "setup",
        args=[
            [owner_address],                                     # _owners
            1,                                                   # _threshold (1-of-1)
            NULL_ADDRESS,                                        # to (no delegate call on setup)
            b"",                                                 # data
            Web3.to_checksum_address(COMPATIBILITY_HANDLER),    # fallbackHandler
            NULL_ADDRESS,                                        # paymentToken
            0,                                                   # payment
            NULL_ADDRESS,                                        # paymentReceiver
        ],
    )
    return bytes.fromhex(calldata[2:])  # strip 0x


def predict_safe_address(owner_address: str, salt_nonce: int = 0, rpc_url: str = "https://mainnet.base.org") -> str:
    """
    Compute the deterministic CREATE2 address of a Safe proxy.
    Fetches proxyCreationCode from factory then computes CREATE2 locally — FREE, no gas.
    """
    from web3 import Web3
    from eth_abi import encode as abi_encode

    w3        = Web3(Web3.HTTPProvider(rpc_url))
    owner     = w3.to_checksum_address(owner_address)
    singleton = w3.to_checksum_address(SAFE_SINGLETON_L2)
    factory   = w3.to_checksum_address(PROXY_FACTORY_ADDRESS)

    initializer = build_safe_initializer(owner)

    # Fetch the proxy creation code from the factory (pure function, free)
    factory_contract = w3.eth.contract(
        address=Web3.to_checksum_address(PROXY_FACTORY_ADDRESS),
        abi=PROXY_FACTORY_ABI,
    )
    creation_code_base = factory_contract.functions.proxyCreationCode().call()

    # Full creation code = base bytecode + abi.encode(singleton)
    proxy_creation_code = creation_code_base + abi_encode(["address"], [singleton])
    creation_code_hash  = w3.keccak(proxy_creation_code)

    # salt = keccak256(keccak256(initializer) ++ abi.encode(saltNonce))
    init_hash = w3.keccak(initializer)
    nonce_enc = abi_encode(["uint256"], [salt_nonce])
    salt      = w3.keccak(init_hash + nonce_enc)

    # CREATE2: keccak256(0xff ++ factory ++ salt ++ keccak256(initcode))
    create2_input = b"\xff" + bytes.fromhex(factory[2:]) + salt + creation_code_hash
    raw_address   = w3.keccak(create2_input)[12:]  # last 20 bytes
    return w3.to_checksum_address("0x" + raw_address.hex())


async def deploy_safe(rpc_url: str, private_key: str, salt_nonce: int = 0) -> str:
    """Deploy the Safe on-chain. Requires ETH for gas."""
    from web3 import AsyncWeb3, AsyncHTTPProvider
    from eth_account import Account

    w3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))
    if not await w3.is_connected():
        raise ConnectionError(f"Cannot connect to RPC: {rpc_url}")

    account = Account.from_key(private_key)
    balance = await w3.eth.get_balance(account.address)
    print(f"Wallet: {account.address}")
    print(f"Balance: {w3.from_wei(balance, 'ether'):.6f} ETH")

    if balance < w3.to_wei("0.001", "ether"):
        raise ValueError("Insufficient ETH for gas. Fund wallet first.")

    chain_id = await w3.eth.chain_id
    print(f"Chain ID: {chain_id}")

    owner_address = account.address
    initializer   = build_safe_initializer(owner_address)

    factory = w3.eth.contract(
        address=AsyncWeb3.to_checksum_address(PROXY_FACTORY_ADDRESS),
        abi=PROXY_FACTORY_ABI,
    )

    nonce     = await w3.eth.get_transaction_count(account.address)
    gas_price = int(await w3.eth.gas_price * 1.2)

    tx = await factory.functions.createProxyWithNonce(
        AsyncWeb3.to_checksum_address(SAFE_SINGLETON_L2),
        initializer,
        salt_nonce,
    ).build_transaction({
        "from":     account.address,
        "nonce":    nonce,
        "gasPrice": gas_price,
    })

    try:
        estimated = await w3.eth.estimate_gas(tx)
        tx["gas"] = int(estimated * 1.3)
    except Exception:
        tx["gas"] = 500_000

    signed  = w3.eth.account.sign_transaction(tx, account.key)
    tx_hash = await w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"Deploying Safe... tx: {tx_hash.hex()}")

    receipt = await w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    if receipt["status"] == 0:
        raise RuntimeError(f"Safe deployment failed: {tx_hash.hex()}")

    # The Safe address is in the logs (ProxyCreation event)
    # Or compute it deterministically
    safe_address = predict_safe_address(owner_address, salt_nonce)
    print(f"✅ Safe deployed at: {safe_address}")
    print(f"   Block: {receipt['blockNumber']}")
    explorer = "https://basescan.org" if chain_id == BASE_CHAIN_ID else "https://sepolia.basescan.org"
    print(f"   Explorer: {explorer}/address/{safe_address}")

    # Update .env
    env_path = Path(__file__).parent.parent.parent / ".env"
    if env_path.exists():
        content = env_path.read_text()
        import re
        if "SAFE_ADDRESS=" in content:
            content = re.sub(r"SAFE_ADDRESS=.*", f"SAFE_ADDRESS={safe_address}", content)
        else:
            content += f"\nSAFE_ADDRESS={safe_address}\n"
        env_path.write_text(content)
        print(f"✅ .env updated: SAFE_ADDRESS={safe_address}")

    return safe_address


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "predict"

    private_key    = os.getenv("PROTOCOL_PRIVATE_KEY", "")
    protocol_wallet = os.getenv("PROTOCOL_WALLET", "")
    rpc_url        = os.getenv("BASE_RPC_URL", "https://mainnet.base.org")

    if cmd == "predict":
        if not protocol_wallet:
            print("❌ PROTOCOL_WALLET not set in .env")
            sys.exit(1)
        safe_addr = predict_safe_address(protocol_wallet)
        print(f"Predicted Safe address: {safe_addr}")
        print(f"Owner (protocol wallet): {protocol_wallet}")
        print(f"Threshold: 1-of-1")
        print(f"\nAdd to .env: SAFE_ADDRESS={safe_addr}")
        print(f"\nFund wallet with ETH and run 'python -m blockchain.safe_setup deploy' to deploy.")

    elif cmd == "deploy":
        if not private_key or not rpc_url:
            print("❌ Set BASE_RPC_URL and PROTOCOL_PRIVATE_KEY in .env")
            sys.exit(1)
        asyncio.run(deploy_safe(rpc_url, private_key))

    else:
        print(f"Unknown command: {cmd}. Use 'predict' or 'deploy'.")
