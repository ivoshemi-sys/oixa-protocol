"""
ABI definitions for OIXAEscrow and USDC (ERC-20 minimal) on Base mainnet.
Derived from OIXAEscrow.sol (pragma solidity ^0.8.20).
"""

OIXA_ESCROW_ABI = [
    # ── Constructor ──────────────────────────────────────────────────────────
    {
        "inputs": [
            {"internalType": "address", "name": "_usdc",     "type": "address"},
            {"internalType": "address", "name": "_protocol", "type": "address"}
        ],
        "stateMutability": "nonpayable",
        "type": "constructor"
    },

    # ── Write functions ──────────────────────────────────────────────────────
    {
        "inputs": [
            {"internalType": "bytes32", "name": "escrowId",   "type": "bytes32"},
            {"internalType": "bytes32", "name": "auctionId",  "type": "bytes32"},
            {"internalType": "address", "name": "payee",      "type": "address"},
            {"internalType": "uint256", "name": "amount",     "type": "uint256"},
            {"internalType": "uint256", "name": "commission", "type": "uint256"}
        ],
        "name": "createEscrow",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [{"internalType": "bytes32", "name": "escrowId", "type": "bytes32"}],
        "name": "release",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [{"internalType": "bytes32", "name": "escrowId", "type": "bytes32"}],
        "name": "refund",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "pause",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "unpause",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },

    # ── Read functions ───────────────────────────────────────────────────────
    {
        "inputs": [],
        "name": "paused",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [{"internalType": "bytes32", "name": "escrowId", "type": "bytes32"}],
        "name": "getEscrow",
        "outputs": [
            {
                "components": [
                    {"internalType": "address", "name": "payer",      "type": "address"},
                    {"internalType": "address", "name": "payee",      "type": "address"},
                    {"internalType": "uint256", "name": "amount",     "type": "uint256"},
                    {"internalType": "uint256", "name": "commission", "type": "uint256"},
                    {"internalType": "uint8",   "name": "status",     "type": "uint8"},
                    {"internalType": "bytes32", "name": "auctionId",  "type": "bytes32"},
                    {"internalType": "uint256", "name": "createdAt",  "type": "uint256"}
                ],
                "internalType": "struct OIXAEscrow.Escrow",
                "name": "",
                "type": "tuple"
            }
        ],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "stats",
        "outputs": [
            {"internalType": "uint256", "name": "locked",      "type": "uint256"},
            {"internalType": "uint256", "name": "released",    "type": "uint256"},
            {"internalType": "uint256", "name": "commissions", "type": "uint256"},
            {"internalType": "uint256", "name": "refunded",    "type": "uint256"}
        ],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "contractBalance",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "usdc",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "protocol",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "totalLocked",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "totalReleased",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "totalCommissions",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },

    # ── Events ───────────────────────────────────────────────────────────────
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True,  "internalType": "bytes32", "name": "escrowId",   "type": "bytes32"},
            {"indexed": True,  "internalType": "bytes32", "name": "auctionId",  "type": "bytes32"},
            {"indexed": True,  "internalType": "address", "name": "payer",      "type": "address"},
            {"indexed": False, "internalType": "address", "name": "payee",      "type": "address"},
            {"indexed": False, "internalType": "uint256", "name": "amount",     "type": "uint256"},
            {"indexed": False, "internalType": "uint256", "name": "commission", "type": "uint256"}
        ],
        "name": "EscrowCreated",
        "type": "event"
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True,  "internalType": "bytes32", "name": "escrowId",   "type": "bytes32"},
            {"indexed": True,  "internalType": "address", "name": "payee",      "type": "address"},
            {"indexed": False, "internalType": "uint256", "name": "net",        "type": "uint256"},
            {"indexed": False, "internalType": "uint256", "name": "commission", "type": "uint256"}
        ],
        "name": "EscrowReleased",
        "type": "event"
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True,  "internalType": "bytes32", "name": "escrowId", "type": "bytes32"},
            {"indexed": True,  "internalType": "address", "name": "payer",    "type": "address"},
            {"indexed": False, "internalType": "uint256", "name": "amount",   "type": "uint256"}
        ],
        "name": "EscrowRefunded",
        "type": "event"
    },

    # ── Custom errors ────────────────────────────────────────────────────────
    {"inputs": [], "name": "AlreadySettled",      "type": "error"},
    {"inputs": [], "name": "EscrowAlreadyExists", "type": "error"},
    {"inputs": [], "name": "EscrowNotFound",      "type": "error"},
    {"inputs": [], "name": "InvalidAmount",       "type": "error"},
    {"inputs": [], "name": "OnlyProtocol",        "type": "error"},
    {"inputs": [], "name": "TransferFailed",      "type": "error"}
]

# Minimal ERC-20 ABI for USDC on Base
USDC_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "spender", "type": "address"},
            {"internalType": "uint256", "name": "amount",  "type": "uint256"}
        ],
        "name": "approve",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "address", "name": "owner",   "type": "address"},
            {"internalType": "address", "name": "spender", "type": "address"}
        ],
        "name": "allowance",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [{"internalType": "address", "name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "address", "name": "to",     "type": "address"},
            {"internalType": "uint256", "name": "amount", "type": "uint256"}
        ],
        "name": "transfer",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function"
    }
]

# Base mainnet USDC address
USDC_BASE_ADDRESS = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"

# Base mainnet chain ID
BASE_CHAIN_ID = 8453

# Base Sepolia testnet (for testing)
BASE_SEPOLIA_CHAIN_ID = 84532
USDC_BASE_SEPOLIA_ADDRESS = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
