#!/bin/bash
cd "$(dirname "$0")/.."
if [ -f velun.pid ]; then
    kill $(cat velun.pid) && rm velun.pid
    echo "🛑 VELUN Protocol stopped"
else
    echo "No PID file found"
fi
