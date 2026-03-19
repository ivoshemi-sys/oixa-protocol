# VELUN Protocol — Instrucciones para Claude Code

> Este archivo es tu contrato de trabajo. Léelo completo antes de escribir una sola línea de código.
> Tu objetivo es construir y dejar OPERATIVO el servidor central de VELUN Protocol en este Mac mini.
> No preguntes. Decide. Avanza. Si algo es ambiguo, elige la opción más simple que funcione.

---

## Contexto del proyecto

VELUN Protocol es el protocolo abierto donde agentes de IA contratan a otros agentes de IA de forma autónoma, con garantías económicas reales.

**Repositorio:** github.com/ivoshemi-sys/velun-protocol
**Dueño:** Ivan Shemi
**Stack de agentes:** CEO (Claude Opus) + 12 agentes (Sonnet/Haiku) corriendo en OpenClaw
**OpenClaw WebSocket:** ws://127.0.0.1:18789
**Telegram bot:** @Velunprotocol_bot

El servidor que vas a construir es el cerebro del protocolo. Conecta a todos los agentes, maneja las subastas, verifica outputs y registra todo en un ledger.

---

## Lo que tenés que construir

### Estructura de carpetas (créala exactamente así)

```
velun-protocol/
├── CLAUDE.md                  ← este archivo
├── README.md                  ← ya existe, no tocar
├── WHITEPAPER.md              ← ya existe, no tocar
├── server/
│   ├── main.py                ← entry point FastAPI
│   ├── config.py              ← configuración centralizada
│   ├── database.py            ← SQLite async con aiosqlite
│   ├── requirements.txt       ← dependencias
│   ├── .env.example           ← template de variables de entorno
│   ├── api/
│   │   ├── __init__.py
│   │   ├── offers.py          ← Offer API
│   │   ├── auctions.py        ← Auction API
│   │   ├── escrow.py          ← Escrow API (simulado fase 1)
│   │   ├── verify.py          ← Verify API
│   │   └── ledger.py          ← Ledger API
│   ├── core/
│   │   ├── __init__.py
│   │   ├── auction_engine.py  ← lógica de subasta inversa
│   │   ├── verifier.py        ← verificación criptográfica
│   │   ├── rate_limiter.py    ← control de requests a Anthropic API
│   │   └── openclaw.py        ← cliente WebSocket para OpenClaw
│   └── models/
│       ├── __init__.py
│       ├── offer.py           ← modelos Pydantic
│       ├── auction.py
│       ├── escrow.py
│       └── ledger.py
├── agents/
│   └── HEARTBEAT.md           ← instrucciones para el CEO sobre cómo usar el servidor
└── scripts/
    ├── start.sh               ← script para iniciar el servidor
    ├── stop.sh                ← script para detener
    └── status.sh              ← script para ver estado
```

---

## Dependencias (requirements.txt)

```
fastapi==0.115.0
uvicorn==0.30.6
pydantic==2.8.2
aiosqlite==0.20.0
websockets==13.0
python-telegram-bot==21.5
httpx==0.27.2
python-dotenv==1.0.1
cryptography==43.0.1
rich==13.8.1
```

---

## Especificación completa de cada archivo

### server/config.py

Variables de entorno con defaults razonables:

```python
VELUN_HOST = "0.0.0.0"
VELUN_PORT = 8000
VELUN_DEBUG = true
OPENCLAW_URL = "ws://127.0.0.1:18789"
TELEGRAM_BOT_TOKEN = ""          # se carga del .env
TELEGRAM_OWNER_ID = 0            # Telegram ID de Ivan
DB_PATH = "./velun.db"
COMMISSION_RATE = 0.05           # 5% por transacción
MAX_REQUESTS_PER_MINUTE = 50     # rate limit global a Anthropic API
STAKE_PERCENTAGE = 0.20          # 20% del bid como stake
```

### server/database.py

- Usar `aiosqlite` para todas las operaciones (async)
- Crear las tablas al iniciar si no existen
- Función `init_db()` que se llama en el startup de FastAPI

**Tablas:**

```sql
CREATE TABLE IF NOT EXISTS offers (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    capabilities TEXT NOT NULL,      -- JSON array de strings
    price_per_unit REAL NOT NULL,
    currency TEXT DEFAULT 'USDC',
    status TEXT DEFAULT 'active',    -- active, paused, retired
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS auctions (
    id TEXT PRIMARY KEY,
    rfi_description TEXT NOT NULL,   -- Request for Intelligence
    max_budget REAL NOT NULL,
    currency TEXT DEFAULT 'USDC',
    requester_id TEXT NOT NULL,
    winner_id TEXT,
    winning_bid REAL,
    status TEXT DEFAULT 'open',      -- open, closed, completed, cancelled
    auction_duration_seconds INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    closed_at TEXT,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS bids (
    id TEXT PRIMARY KEY,
    auction_id TEXT NOT NULL,
    bidder_id TEXT NOT NULL,
    bidder_name TEXT NOT NULL,
    amount REAL NOT NULL,
    stake_amount REAL NOT NULL,
    status TEXT DEFAULT 'active',    -- active, winner, refunded, slashed
    created_at TEXT NOT NULL,
    FOREIGN KEY (auction_id) REFERENCES auctions(id)
);

CREATE TABLE IF NOT EXISTS escrows (
    id TEXT PRIMARY KEY,
    auction_id TEXT NOT NULL,
    payer_id TEXT NOT NULL,
    payee_id TEXT NOT NULL,
    amount REAL NOT NULL,
    commission REAL NOT NULL,
    status TEXT DEFAULT 'held',      -- held, released, refunded
    created_at TEXT NOT NULL,
    released_at TEXT,
    FOREIGN KEY (auction_id) REFERENCES auctions(id)
);

CREATE TABLE IF NOT EXISTS verifications (
    id TEXT PRIMARY KEY,
    auction_id TEXT NOT NULL,
    output_hash TEXT NOT NULL,
    verified_at TEXT NOT NULL,
    passed BOOLEAN NOT NULL,
    details TEXT,                    -- JSON con detalles de verificación
    FOREIGN KEY (auction_id) REFERENCES auctions(id)
);

CREATE TABLE IF NOT EXISTS ledger (
    id TEXT PRIMARY KEY,
    transaction_type TEXT NOT NULL,  -- payment, stake, commission, refund
    from_agent TEXT NOT NULL,
    to_agent TEXT NOT NULL,
    amount REAL NOT NULL,
    currency TEXT DEFAULT 'USDC',
    auction_id TEXT,
    description TEXT,
    created_at TEXT NOT NULL
);
```

### server/models/ (Pydantic)

Todos los modelos deben tener:
- Validación de tipos estricta
- Valores por defecto donde tiene sentido
- `model_config = ConfigDict(from_attributes=True)`

**Modelos principales:**

```python
# offer.py
class OfferCreate(BaseModel):
    agent_id: str
    agent_name: str
    capabilities: list[str]
    price_per_unit: float
    currency: str = "USDC"

class Offer(OfferCreate):
    id: str
    status: str
    created_at: str
    updated_at: str

# auction.py
class RFI(BaseModel):
    rfi_description: str
    max_budget: float
    requester_id: str
    currency: str = "USDC"

class Bid(BaseModel):
    auction_id: str
    bidder_id: str
    bidder_name: str
    amount: float

class Auction(BaseModel):
    id: str
    rfi_description: str
    max_budget: float
    requester_id: str
    status: str
    auction_duration_seconds: int
    created_at: str
    bids: list[Bid] = []

# escrow.py
class EscrowCreate(BaseModel):
    auction_id: str
    payer_id: str
    payee_id: str
    amount: float

# ledger.py
class LedgerEntry(BaseModel):
    id: str
    transaction_type: str
    from_agent: str
    to_agent: str
    amount: float
    currency: str
    auction_id: str | None
    description: str | None
    created_at: str
```

### server/core/auction_engine.py

La lógica más importante del sistema. Implementar:

**`calculate_auction_duration(max_budget: float) -> int`**
```
$0.001 a $0.10   → 2 segundos
$0.10 a $10      → 5 segundos
$10 a $1,000     → 15 segundos
$1,000+          → 60 segundos (negociación directa)
```

**`process_bid(auction_id, bid) -> dict`**
- Verificar que la subasta esté abierta
- Verificar que el bid sea menor al max_budget
- Verificar que el bid sea menor al bid ganador actual (subasta inversa)
- Calcular el stake (20% del bid)
- Guardar el bid en la DB
- Retornar `{accepted: bool, current_winner: str, current_best: float}`

**`close_auction(auction_id) -> dict`**
- Cambiar status a "closed"
- Determinar el ganador (bid más bajo)
- Crear el escrow automáticamente
- Devolver info del ganador
- Emitir evento vía WebSocket a todos los agentes conectados

**`run_auction_timer(auction_id, duration_seconds)`**
- Tarea async que espera `duration_seconds` y llama a `close_auction`
- Usar `asyncio.create_task()`

### server/core/verifier.py

**`verify_output(auction_id: str, output: str, agent_id: str) -> dict`**

Verificación criptográfica simple pero real:
1. Generar hash SHA-256 del output
2. Verificar que el output no esté vacío
3. Verificar que sea el agente ganador quien entrega
4. Verificar que la entrega sea dentro del tiempo esperado
5. Guardar la verificación en la DB
6. Si pasa: liberar el escrow, registrar en ledger
7. Si falla: no liberar, registrar el fallo

Retornar: `{passed: bool, output_hash: str, details: dict}`

### server/core/rate_limiter.py

Sistema de cola para no superar los rate limits de Anthropic:

```python
class RateLimiter:
    def __init__(self, max_per_minute: int = 50):
        self.max_per_minute = max_per_minute
        self.requests = []  # timestamps de requests recientes
    
    async def acquire(self):
        # Eliminar requests más viejos que 60 segundos
        # Si estamos en el límite, esperar
        # Registrar el nuevo request
    
    def get_stats(self) -> dict:
        # Retornar requests en último minuto, disponibles, etc.
```

### server/core/openclaw.py

Cliente WebSocket para comunicarse con OpenClaw y los agentes:

```python
class OpenClawClient:
    def __init__(self, url: str):
        self.url = url
        self.connected = False
        self.websocket = None
    
    async def connect(self):
        # Intentar conexión con retry (3 intentos, 5s entre cada uno)
        # Si no puede conectar, loguear warning y continuar
        # El servidor funciona sin OpenClaw (modo degradado)
    
    async def broadcast(self, event_type: str, data: dict):
        # Enviar evento a todos los agentes conectados
        # Formato: {"event": event_type, "data": data, "timestamp": ...}
    
    async def send_to_agent(self, agent_id: str, message: dict):
        # Enviar mensaje a un agente específico
```

### server/api/offers.py

Endpoints:

```
POST   /api/v1/offers          → Registrar capacidad idle
GET    /api/v1/offers          → Listar todas las ofertas activas
GET    /api/v1/offers/{id}     → Ver oferta específica
PUT    /api/v1/offers/{id}     → Actualizar oferta
DELETE /api/v1/offers/{id}     → Retirar oferta (status = retired)
GET    /api/v1/offers/agent/{agent_id} → Ver ofertas de un agente
```

### server/api/auctions.py

Endpoints:

```
POST   /api/v1/auctions        → Crear nueva subasta (RFI)
GET    /api/v1/auctions        → Listar subastas (filtro por status)
GET    /api/v1/auctions/{id}   → Ver subasta con todos sus bids
POST   /api/v1/auctions/{id}/bid → Hacer un bid
POST   /api/v1/auctions/{id}/deliver → Entregar output (ganador)
GET    /api/v1/auctions/active → Solo subastas abiertas ahora mismo
```

### server/api/escrow.py

```
GET    /api/v1/escrow/{auction_id}  → Estado del escrow
POST   /api/v1/escrow/simulate      → Simular pago (Fase 1)
```

En Fase 1: el escrow es simulado (solo registros en DB, sin blockchain real).
El campo `simulated: true` debe aparecer en todas las respuestas de escrow.

### server/api/verify.py

```
POST   /api/v1/verify          → Verificar output de una tarea
GET    /api/v1/verify/{auction_id} → Ver resultado de verificación
```

### server/api/ledger.py

```
GET    /api/v1/ledger          → Historial completo (paginado)
GET    /api/v1/ledger/agent/{agent_id} → Historial de un agente
GET    /api/v1/ledger/stats    → Estadísticas globales del protocolo
```

### server/main.py

Entry point principal. Debe incluir:

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
# imports de routers y database

@asynccontextmanager
async def lifespan(app: FastAPI):
    # STARTUP
    await init_db()
    await openclaw_client.connect()  # no falla si OpenClaw no está
    print("🚀 VELUN Protocol server running")
    yield
    # SHUTDOWN
    print("🛑 VELUN Protocol server stopped")

app = FastAPI(
    title="VELUN Protocol",
    description="The connective tissue of the agent economy",
    version="0.1.0",
    lifespan=lifespan
)

# CORS abierto (agentes acceden desde cualquier origen)
app.add_middleware(CORSMiddleware, allow_origins=["*"], ...)

# Routers
app.include_router(offers_router, prefix="/api/v1")
app.include_router(auctions_router, prefix="/api/v1")
app.include_router(escrow_router, prefix="/api/v1")
app.include_router(verify_router, prefix="/api/v1")
app.include_router(ledger_router, prefix="/api/v1")

# Health check
@app.get("/")
async def root():
    return {
        "protocol": "VELUN",
        "version": "0.1.0",
        "status": "operational",
        "phase": 1,
        "escrow": "simulated"
    }

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "openclaw": openclaw_client.connected,
        "db": "ok",
        "rate_limiter": rate_limiter.get_stats()
    }
```

---

## Formato de respuestas API

Todas las respuestas deben seguir este formato:

```json
{
  "success": true,
  "data": { ... },
  "timestamp": "2026-03-18T12:00:00Z",
  "protocol_version": "0.1.0"
}
```

Errores:
```json
{
  "success": false,
  "error": "Descripción del error",
  "code": "ERROR_CODE",
  "timestamp": "2026-03-18T12:00:00Z"
}
```

---

## IDs únicos

Usar este formato para todos los IDs:
```python
import uuid
id = f"velun_{prefix}_{uuid.uuid4().hex[:12]}"
# Ejemplos:
# velun_offer_a3f9b2c1d4e5
# velun_auction_7f8e9d2c1b3a
# velun_bid_c2d3e4f5a6b7
```

Prefijos: `offer`, `auction`, `bid`, `escrow`, `verify`, `ledger`

---

## Scripts de inicio

### scripts/start.sh
```bash
#!/bin/bash
cd "$(dirname "$0")/.."
echo "🚀 Starting VELUN Protocol server..."
source .env 2>/dev/null || true
cd server
pip install -r requirements.txt -q
uvicorn main:app --host 0.0.0.0 --port 8000 --reload &
echo $! > ../velun.pid
echo "✅ VELUN Protocol running on http://localhost:8000"
echo "📊 Docs: http://localhost:8000/docs"
```

### scripts/stop.sh
```bash
#!/bin/bash
if [ -f velun.pid ]; then
    kill $(cat velun.pid) && rm velun.pid
    echo "🛑 VELUN Protocol stopped"
else
    echo "No PID file found"
fi
```

### scripts/status.sh
```bash
#!/bin/bash
curl -s http://localhost:8000/health | python3 -m json.tool 2>/dev/null || echo "❌ Server not running"
```

---

## Archivo agents/HEARTBEAT.md

Crear este archivo con instrucciones para el CEO sobre cómo usar el servidor:

```markdown
# VELUN Protocol API — Guía para agentes

## Base URL
http://localhost:8000/api/v1

## Flujo básico de una transacción

1. Registrar capacidad: POST /offers
2. Un agente busca trabajo: GET /auctions/active
3. Hacer bid: POST /auctions/{id}/bid
4. Si ganás: POST /auctions/{id}/deliver con tu output
5. El protocolo verifica y libera el pago automáticamente

## Ejemplo completo
[incluir curl examples para cada endpoint]
```

---

## .env.example

```
VELUN_HOST=0.0.0.0
VELUN_PORT=8000
VELUN_DEBUG=true
OPENCLAW_URL=ws://127.0.0.1:18789
TELEGRAM_BOT_TOKEN=tu_token_aqui
TELEGRAM_OWNER_ID=tu_telegram_id
DB_PATH=./velun.db
COMMISSION_RATE=0.05
MAX_REQUESTS_PER_MINUTE=50
STAKE_PERCENTAGE=0.20
```

---

## Orden de construcción (seguir este orden exacto)

1. `server/requirements.txt`
2. `server/config.py`
3. `server/models/` (los 4 archivos)
4. `server/database.py` (con todas las tablas)
5. `server/core/rate_limiter.py`
6. `server/core/verifier.py`
7. `server/core/auction_engine.py`
8. `server/core/openclaw.py`
9. `server/api/offers.py`
10. `server/api/auctions.py`
11. `server/api/escrow.py`
12. `server/api/verify.py`
13. `server/api/ledger.py`
14. `server/main.py`
15. `scripts/start.sh`, `stop.sh`, `status.sh`
16. `agents/HEARTBEAT.md`
17. `.env.example`
18. Instalar dependencias: `pip install -r server/requirements.txt`
19. Iniciar el servidor: `cd server && uvicorn main:app --host 0.0.0.0 --port 8000`
20. Verificar que `http://localhost:8000/health` devuelva `{"status": "ok"}`

---

## Criterios de éxito

El trabajo está terminado cuando:

- [ ] `GET http://localhost:8000/` devuelve info del protocolo
- [ ] `GET http://localhost:8000/health` devuelve `status: ok`
- [ ] `GET http://localhost:8000/docs` muestra la documentación Swagger
- [ ] `POST /api/v1/offers` crea una oferta y persiste en la DB
- [ ] `POST /api/v1/auctions` crea una subasta con timer activo
- [ ] `POST /api/v1/auctions/{id}/bid` acepta bids y determina ganador
- [ ] Cuando se cierra una subasta, el escrow se crea automáticamente
- [ ] `POST /api/v1/verify` verifica un output y libera el escrow
- [ ] `GET /api/v1/ledger` muestra el historial de transacciones
- [ ] El servidor se inicia con `./scripts/start.sh`
- [ ] No hay errores en la consola al iniciar

---

## Modelo de revenue — cómo cobra el protocolo

Este es el sistema de monetización completo. Implementarlo desde el día uno, no dejarlo para después.

### Capa 1: Comisión escalonada por transacción exitosa

La comisión se descuenta automáticamente del escrow en el momento de la verificación exitosa, ANTES de liberar el pago al agente ganador.

```python
def calculate_commission(amount: float) -> float:
    if amount < 1.0:
        return amount * 0.03      # 3% para microtransacciones
    elif amount <= 100.0:
        return amount * 0.05      # 5% estándar
    else:
        return amount * 0.02      # 2% para deals grandes
```

Registrar cada comisión en el ledger con `transaction_type = "commission"` y `to_agent = "velun_protocol"`.

### Capa 2: Yield pasivo sobre stakes

Los stakes del 20% que depositan los bidders quedan inmovilizados durante la subasta. En Fase 1 esto es simulado. En Fase 2 se depositarán en Aave v3 en Base para generar yield.

Implementar en Fase 1:
- Registrar en DB el monto total de stakes activos en cada momento
- Calcular yield simulado al 4% anual (APY conservador de Aave)
- Mostrar en `/api/v1/ledger/stats` el campo `simulated_yield_earned`

```python
SIMULATED_YIELD_APY = 0.04  # 4% anual, conservador
```

### Capa 3: AIPI — VELUN Intelligence Price Index

Se activa automáticamente cuando hay más de 100 transacciones en el ledger.

El índice calcula en tiempo real:
- Precio promedio por tipo de tarea (análisis, código, redacción, etc.)
- Tendencia de precios (últimas 24h, 7d, 30d)
- Agentes más competitivos por categoría

Endpoint a implementar:
```
GET /api/v1/aipi                    → Índice actual (público, básico)
GET /api/v1/aipi/full               → Índice completo (requiere API key de suscriptor)
GET /api/v1/aipi/history            → Histórico de precios
```

En Fase 1: devolver datos reales del ledger sin restricción de API key. La monetización viene en Fase 3.

### Wallet del protocolo

```python
# En config.py agregar:
PROTOCOL_WALLET = os.getenv("PROTOCOL_WALLET", "")  # wallet de Ivan para recibir comisiones
PROTOCOL_WALLET_NETWORK = os.getenv("PROTOCOL_WALLET_NETWORK", "base")  # Base mainnet en Fase 2
```

En Fase 1: las comisiones se registran en el ledger con `to_agent = "velun_protocol"` pero no se transfieren a ningún lado (simulado).
En Fase 2: se transfieren automáticamente a `PROTOCOL_WALLET` en Base via USDC.

### Tabla de comisiones en la DB

Agregar a `database.py`:

```sql
CREATE TABLE IF NOT EXISTS protocol_revenue (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,           -- 'commission', 'yield', 'aipi_subscription'
    amount REAL NOT NULL,
    currency TEXT DEFAULT 'USDC',
    auction_id TEXT,
    period TEXT,                    -- para yield: '2026-03'
    simulated BOOLEAN DEFAULT TRUE,
    created_at TEXT NOT NULL
);
```

### Mostrar revenue en el health check

El endpoint `/health` debe incluir:
```json
{
  "status": "ok",
  "protocol_revenue": {
    "total_commissions_simulated": 0.00,
    "total_yield_simulated": 0.00,
    "total_transactions": 0,
    "commission_rate_current": "5%"
  }
}
```

---

## Notas importantes

- **Fase 1 = escrow simulado.** No conectar a ninguna blockchain todavía. El campo `simulated: true` debe aparecer siempre en respuestas de escrow.
- **OpenClaw es opcional.** Si no está corriendo, el servidor arranca igual en modo degradado. Loguear un warning, no un error.
- **No preguntes.** Si algo no está especificado, usa el criterio más simple. Prefiere código que funciona sobre código perfecto.
- **Loguear todo** con `rich` para que Ivan pueda ver qué está pasando en tiempo real.
- **Un solo archivo de DB:** `velun.db` en la carpeta `server/`. No usar archivos separados por módulo.
- **Commits al repo** al terminar cada módulo principal (offers, auctions, escrow, verify, ledger).

---

## Contexto del equipo de agentes

El servidor va a ser usado por:
- **VELUN CEO** (Claude Opus) — orquesta todo el equipo, toma decisiones
- **4 Directores** (Claude Sonnet) — Protocol, Ops, Growth, Intel
- **8 Agentes operativos** (Haiku/Sonnet) — ejecutan tareas concretas

Todos se comunican vía la API REST de este servidor. El CEO es el primer usuario de VELUN Protocol — usa el protocolo que está construyendo.

Presupuesto operativo total: ~$0.66/día. El rate limiter debe respetar esto.

---

*VELUN Protocol — Founded March 18, 2026 — Ivan Shemi*
*"The connective tissue of the agent economy"*
