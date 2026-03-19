#!/usr/bin/env python3
"""
Deploy OIXAEscrow to Base mainnet (or Base Sepolia for testing).

Usage:
    cd server
    python -m blockchain.deploy [--testnet]

Requirements in .env:
    BASE_RPC_URL=https://mainnet.base.org          # or Base Sepolia RPC
    PROTOCOL_PRIVATE_KEY=0x...                     # wallet with ETH for gas
    PROTOCOL_WALLET=0x...                          # receives commissions (defaults to private key wallet)

After deploy, set in .env:
    ESCROW_CONTRACT_ADDRESS=0x<deployed address>
"""

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


async def deploy(testnet: bool = False):
    try:
        from web3 import AsyncWeb3, AsyncHTTPProvider
        from eth_account import Account
        import solcx
    except ImportError as e:
        print(f"❌ Missing dependency: {e}")
        print("Run: pip install web3 py-solc-x")
        sys.exit(1)

    rpc_url     = os.getenv("BASE_RPC_URL")
    private_key = os.getenv("PROTOCOL_PRIVATE_KEY")
    protocol_wallet = os.getenv("PROTOCOL_WALLET", "")

    if not rpc_url or not private_key:
        print("❌ Set BASE_RPC_URL and PROTOCOL_PRIVATE_KEY in .env")
        sys.exit(1)

    # ── Connect ──────────────────────────────────────────────────────────────
    w3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))
    if not await w3.is_connected():
        print(f"❌ Cannot connect to {rpc_url}")
        sys.exit(1)

    account  = Account.from_key(private_key)
    chain_id = await w3.eth.chain_id
    balance  = await w3.eth.get_balance(account.address)

    print(f"Network:  chain_id={chain_id}")
    print(f"Wallet:   {account.address}")
    print(f"Balance:  {w3.from_wei(balance, 'ether'):.6f} ETH")

    if chain_id == 8453 and testnet:
        print("⚠️  Connected to mainnet but --testnet flag set. Proceeding anyway.")
    if chain_id == 84532:
        print("ℹ️  Base Sepolia testnet detected")
    if chain_id not in (8453, 84532):
        print(f"⚠️  Unknown chain {chain_id} — proceeding, but verify USDC address manually")

    if balance == 0:
        print("❌ Wallet has no ETH for gas. Fund it first.")
        sys.exit(1)

    # ── Compile ──────────────────────────────────────────────────────────────
    print("\nCompiling OIXAEscrow.sol with solc 0.8.20...")
    try:
        solcx.install_solc("0.8.20", show_progress=False)
        solcx.set_solc_version("0.8.20")
    except Exception as e:
        print(f"❌ solc install failed: {e}")
        sys.exit(1)

    sol_file = Path(__file__).parent / "contracts" / "OIXAEscrow.sol"
    compiled = solcx.compile_files(
        [str(sol_file)],
        output_values=["abi", "bin"],
        optimize=True,
        optimize_runs=200,
    )

    key = f"{sol_file}:OIXAEscrow"
    contract_data = compiled[key]
    abi      = contract_data["abi"]
    bytecode = contract_data["bin"]
    print(f"Compiled ✓ — bytecode size: {len(bytecode)//2} bytes")

    # ── USDC address ─────────────────────────────────────────────────────────
    from blockchain.abi import USDC_BASE_ADDRESS, USDC_BASE_SEPOLIA_ADDRESS
    usdc_addr = USDC_BASE_SEPOLIA_ADDRESS if chain_id == 84532 else USDC_BASE_ADDRESS
    protocol_addr = protocol_wallet if protocol_wallet else account.address
    print(f"\nUSDS address:     {usdc_addr}")
    print(f"Protocol wallet:  {protocol_addr}")

    confirm = input("\nDeploy OIXAEscrow? [y/N]: ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        sys.exit(0)

    # ── Deploy ───────────────────────────────────────────────────────────────
    Contract = w3.eth.contract(abi=abi, bytecode=bytecode)
    nonce     = await w3.eth.get_transaction_count(account.address)
    gas_price = int(await w3.eth.gas_price * 1.2)

    deploy_tx = await Contract.constructor(
        AsyncWeb3.to_checksum_address(usdc_addr),
        AsyncWeb3.to_checksum_address(protocol_addr),
    ).build_transaction({
        "from":     account.address,
        "nonce":    nonce,
        "gasPrice": gas_price,
    })

    try:
        estimated = await w3.eth.estimate_gas(deploy_tx)
        deploy_tx["gas"] = int(estimated * 1.3)
    except Exception:
        deploy_tx["gas"] = 2_000_000

    signed   = w3.eth.account.sign_transaction(deploy_tx, account.key)
    tx_hash  = await w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"\nDeploying... tx: {tx_hash.hex()}")
    print("Waiting for confirmation (up to 2 min)...")

    receipt = await w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

    if receipt["status"] == 0:
        print(f"❌ Deployment failed (reverted). tx: {tx_hash.hex()}")
        sys.exit(1)

    contract_address = receipt["contractAddress"]
    print(f"\n✅ OIXAEscrow deployed successfully!")
    print(f"   Address:  {contract_address}")
    print(f"   Block:    {receipt['blockNumber']}")
    print(f"   Gas used: {receipt['gasUsed']:,}")
    explorer = "https://basescan.org" if chain_id == 8453 else "https://sepolia.basescan.org"
    print(f"   Explorer: {explorer}/address/{contract_address}")

    print(f"\n─── Add this to your .env ───────────────────────────────")
    print(f"ESCROW_CONTRACT_ADDRESS={contract_address}")
    print(f"─────────────────────────────────────────────────────────")

    # Auto-write to .env.local if it exists or offer to update
    env_path = Path(__file__).parent.parent.parent / ".env"
    if env_path.exists():
        content = env_path.read_text()
        if "ESCROW_CONTRACT_ADDRESS=" in content:
            import re
            content = re.sub(
                r"ESCROW_CONTRACT_ADDRESS=.*",
                f"ESCROW_CONTRACT_ADDRESS={contract_address}",
                content,
            )
        else:
            content += f"\nESCROW_CONTRACT_ADDRESS={contract_address}\n"
        env_path.write_text(content)
        print(f"✅ .env updated automatically")


if __name__ == "__main__":
    testnet = "--testnet" in sys.argv
    asyncio.run(deploy(testnet=testnet))
