#!/bin/bash
# AXON Protocol — Automated deploy script for Ubuntu VPS
# Usage: bash deploy.sh [server_ip] [ssh_user]
# Example: bash deploy.sh 123.45.67.89 root

set -e

SERVER_IP="${1:-}"
SSH_USER="${2:-root}"
REPO="https://github.com/ivoshemi-sys/axon-protocol.git"
DEPLOY_DIR="/opt/axon-protocol"

if [ -z "$SERVER_IP" ]; then
    echo "❌ Usage: bash deploy.sh <server_ip> [ssh_user]"
    exit 1
fi

echo "🚀 Deploying AXON Protocol to $SSH_USER@$SERVER_IP..."

ssh "$SSH_USER@$SERVER_IP" bash << REMOTE
set -e

echo "📦 Installing system dependencies..."
apt-get update -q
apt-get install -y -q git docker.io docker-compose-plugin curl

echo "🐳 Starting Docker..."
systemctl enable docker
systemctl start docker

echo "📁 Setting up deploy directory..."
mkdir -p $DEPLOY_DIR
cd $DEPLOY_DIR

if [ -d ".git" ]; then
    echo "🔄 Updating existing repo..."
    git pull origin main
else
    echo "⬇️  Cloning repo..."
    git clone $REPO .
fi

echo "⚙️  Configuring environment..."
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "⚠️  .env created from template — edit $DEPLOY_DIR/.env with your secrets"
fi

mkdir -p /var/log/axon
chmod 755 /var/log/axon

echo "🏗️  Building and starting containers..."
docker compose pull --quiet 2>/dev/null || true
docker compose up -d --build

echo "⏳ Waiting for server to be ready..."
for i in \$(seq 1 30); do
    if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
        echo "✅ AXON Protocol is running!"
        curl -s http://localhost:8000/health
        break
    fi
    sleep 2
done

echo ""
echo "✅ Deploy complete!"
echo "🌐 Server: http://$SERVER_IP:8000"
echo "📊 Docs:   http://$SERVER_IP:8000/docs"
echo "📋 Logs:   docker compose -f $DEPLOY_DIR/docker-compose.yml logs -f"
REMOTE
