#!/bin/bash
cd "$(dirname "$0")/.."
if [ -f oixa.pid ]; then
    kill $(cat oixa.pid) && rm oixa.pid
    echo "🛑 OIXA Protocol stopped"
else
    echo "No PID file found"
fi
