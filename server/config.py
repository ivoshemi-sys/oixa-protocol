import os
from dotenv import load_dotenv

load_dotenv()

AXON_HOST = os.getenv("AXON_HOST", "0.0.0.0")
AXON_PORT = int(os.getenv("AXON_PORT", "8000"))
AXON_DEBUG = os.getenv("AXON_DEBUG", "true").lower() == "true"
OPENCLAW_URL = os.getenv("OPENCLAW_URL", "ws://127.0.0.1:18789")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_OWNER_ID = int(os.getenv("TELEGRAM_OWNER_ID", "0") or "0")
DB_PATH = os.getenv("DB_PATH", "./axon.db")
DATABASE_URL = os.getenv("DATABASE_URL", "")  # postgresql://user:pass@host:5432/db
COMMISSION_RATE = float(os.getenv("COMMISSION_RATE", "0.05"))
MAX_REQUESTS_PER_MINUTE = int(os.getenv("MAX_REQUESTS_PER_MINUTE", "50"))
STAKE_PERCENTAGE = float(os.getenv("STAKE_PERCENTAGE", "0.20"))
SIMULATED_YIELD_APY = 0.04
PROTOCOL_WALLET = os.getenv("PROTOCOL_WALLET", "")
PROTOCOL_WALLET_NETWORK = os.getenv("PROTOCOL_WALLET_NETWORK", "base")
PROTOCOL_VERSION = "0.1.0"
LOG_DIR = os.getenv("LOG_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs"))

USE_POSTGRES = bool(DATABASE_URL and DATABASE_URL.startswith("postgresql"))

# ── Blockchain / Base mainnet ────────────────────────────────────────────────
BASE_RPC_URL              = os.getenv("BASE_RPC_URL", "")
PROTOCOL_PRIVATE_KEY      = os.getenv("PROTOCOL_PRIVATE_KEY", "")
ESCROW_CONTRACT_ADDRESS   = os.getenv("ESCROW_CONTRACT_ADDRESS", "")
# Derived flag: True when all three blockchain vars are present
BLOCKCHAIN_ENABLED = bool(BASE_RPC_URL and PROTOCOL_PRIVATE_KEY and ESCROW_CONTRACT_ADDRESS)
SAFE_ADDRESS              = os.getenv("SAFE_ADDRESS", "")

# ── Daily spending limit ─────────────────────────────────────────────────────
DAILY_LIMIT_USD = float(os.getenv("DAILY_LIMIT_USD", "50.0"))

# ── Dispute system ───────────────────────────────────────────────────────────
DISPUTE_WINDOW_MINUTES = int(os.getenv("DISPUTE_WINDOW_MINUTES", "10"))
DISPUTE_FEE_RATE       = float(os.getenv("DISPUTE_FEE_RATE", "0.10"))   # 10% of tx value
AUTO_RELEASE_INTERVAL  = int(os.getenv("AUTO_RELEASE_INTERVAL", "60"))  # seconds between checks

# ── Anthropic API (used by Claude arbiter) ───────────────────────────────────
ANTHROPIC_API_KEY    = os.getenv("ANTHROPIC_API_KEY", "")
ARBITER_MODEL        = os.getenv("ARBITER_MODEL", "claude-opus-4-6")
ARBITER_MAX_TOKENS   = int(os.getenv("ARBITER_MAX_TOKENS", "1024"))
