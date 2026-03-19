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
