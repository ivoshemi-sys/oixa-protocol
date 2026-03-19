import os
from dotenv import load_dotenv

load_dotenv()

VELUN_HOST = os.getenv("VELUN_HOST", "0.0.0.0")
VELUN_PORT = int(os.getenv("VELUN_PORT", "8000"))
VELUN_DEBUG = os.getenv("VELUN_DEBUG", "true").lower() == "true"
OPENCLAW_URL = os.getenv("OPENCLAW_URL", "ws://127.0.0.1:18789")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_OWNER_ID = int(os.getenv("TELEGRAM_OWNER_ID", "0") or "0")
DB_PATH = os.getenv("DB_PATH", "./velun.db")
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

# ── Stripe ───────────────────────────────────────────────────────────────────
STRIPE_SECRET_KEY             = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY        = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET         = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_ISSUING_WEBHOOK_SECRET = os.getenv("STRIPE_ISSUING_WEBHOOK_SECRET", "")
STRIPE_ENABLED = bool(STRIPE_SECRET_KEY)

# ── Dispute system ───────────────────────────────────────────────────────────
DISPUTE_WINDOW_MINUTES = int(os.getenv("DISPUTE_WINDOW_MINUTES", "10"))
DISPUTE_FEE_RATE       = float(os.getenv("DISPUTE_FEE_RATE", "0.10"))   # 10% of tx value
AUTO_RELEASE_INTERVAL  = int(os.getenv("AUTO_RELEASE_INTERVAL", "60"))  # seconds between checks

# ── Anthropic API (used by Claude arbiter) ───────────────────────────────────
ANTHROPIC_API_KEY    = os.getenv("ANTHROPIC_API_KEY", "")
ARBITER_MODEL        = os.getenv("ARBITER_MODEL", "claude-opus-4-6")
ARBITER_MAX_TOKENS   = int(os.getenv("ARBITER_MAX_TOKENS", "1024"))

# ── Circle CCTP ───────────────────────────────────────────────────────────────
# CCTP V2 uses the same contract addresses on all supported EVM chains
CCTP_TOKEN_MESSENGER    = os.getenv("CCTP_TOKEN_MESSENGER",    "0x28b5a0e9c621a5badaa536219b3a228c8168cf5d")
CCTP_MESSAGE_TRANSMITTER= os.getenv("CCTP_MESSAGE_TRANSMITTER","0x81D40F21F12A8F0E3252Bccb954D722d4c464B64")
CCTP_ATTESTATION_URL    = os.getenv("CCTP_ATTESTATION_URL",    "https://iris-api.circle.com")
# Optional per-chain RPC URLs for auto-extracting MessageSent events
ETH_RPC_URL      = os.getenv("ETH_RPC_URL",     "")
ARB_RPC_URL      = os.getenv("ARB_RPC_URL",     "")
AVAX_RPC_URL     = os.getenv("AVAX_RPC_URL",    "")
POLYGON_RPC_URL  = os.getenv("POLYGON_RPC_URL", "")
SOLANA_RPC_URL   = os.getenv("SOLANA_RPC_URL",  "")

# ── Coinbase Commerce ─────────────────────────────────────────────────────────
COINBASE_COMMERCE_API_KEY      = os.getenv("COINBASE_COMMERCE_API_KEY",      "")
COINBASE_COMMERCE_WEBHOOK_SECRET = os.getenv("COINBASE_COMMERCE_WEBHOOK_SECRET", "")

# ── Circle Payments API ───────────────────────────────────────────────────────
CIRCLE_API_KEY  = os.getenv("CIRCLE_API_KEY",  "")
CIRCLE_API_URL  = os.getenv("CIRCLE_API_URL",  "https://api.circle.com")
