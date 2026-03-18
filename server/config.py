import os
from dotenv import load_dotenv

load_dotenv()

AXON_HOST = os.getenv("AXON_HOST", "0.0.0.0")
AXON_PORT = int(os.getenv("AXON_PORT", "8000"))
AXON_DEBUG = os.getenv("AXON_DEBUG", "true").lower() == "true"
OPENCLAW_URL = os.getenv("OPENCLAW_URL", "ws://127.0.0.1:18789")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_OWNER_ID = int(os.getenv("TELEGRAM_OWNER_ID", "0"))
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
