#!/bin/bash
cd "$(dirname "$0")/.."
echo "🚀 Starting OIXA Protocol server..."
source .env 2>/dev/null || true
cd server
pip install -r requirements.txt -q
uvicorn main:app --host 0.0.0.0 --port 8000 --reload &
echo $! > ../oixa.pid
echo "✅ OIXA Protocol running on http://localhost:8000"
echo "📊 Docs: http://localhost:8000/docs"
