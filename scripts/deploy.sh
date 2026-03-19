#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# OIXA Protocol — Production deploy to Ubuntu 24.04 VPS
# Usage:  bash scripts/deploy.sh [server_ip] [ssh_user]
# Default: bash scripts/deploy.sh 64.23.235.34 root
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
SERVER_IP="${1:-64.23.235.34}"
SSH_USER="${2:-root}"
REPO="https://github.com/ivoshemi-sys/oixa-protocol.git"
DEPLOY_DIR="/opt/oixa-protocol"
SERVICE_NAME="oixa-protocol"
LOCAL_ENV="$(dirname "$0")/../.env"

# ── Pre-flight checks ─────────────────────────────────────────────────────────
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " OIXA Protocol — Deploy to $SSH_USER@$SERVER_IP"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ ! -f "$LOCAL_ENV" ]; then
    echo "❌ .env not found at $LOCAL_ENV — aborting"
    exit 1
fi

echo "▶ Testing SSH connectivity..."
if ! ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new \
        "$SSH_USER@$SERVER_IP" "echo OK" &>/dev/null; then
    echo "❌ Cannot reach $SERVER_IP via SSH"
    echo "   Make sure your public key is in the server's authorized_keys:"
    echo "   ssh-copy-id $SSH_USER@$SERVER_IP"
    exit 1
fi
echo "  SSH ✅"

# ── Copy .env securely (before any git operations) ────────────────────────────
echo "▶ Copying .env to server (via SCP)..."
ssh "$SSH_USER@$SERVER_IP" "mkdir -p $DEPLOY_DIR"
scp -q "$LOCAL_ENV" "$SSH_USER@$SERVER_IP:$DEPLOY_DIR/.env"
echo "  .env ✅"

# ── Remote provisioning ───────────────────────────────────────────────────────
echo "▶ Running remote provisioning..."

ssh "$SSH_USER@$SERVER_IP" bash << REMOTE
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

echo ""
echo "── 1/6  System packages ─────────────────────────────"
apt-get update -qq
apt-get install -y \
    python3 python3-pip python3-venv python3.12-venv \
    git curl wget \
    nginx ufw

echo "  packages ✅"

echo ""
echo "── 2/6  Clone / update repo ─────────────────────────"
if [ -d "$DEPLOY_DIR/.git" ]; then
    cd $DEPLOY_DIR
    git fetch origin main --quiet
    git reset --hard origin/main --quiet
    echo "  repo updated ✅"
else
    # .env was already copied — preserve it
    mv $DEPLOY_DIR/.env /tmp/oixa_env_backup 2>/dev/null || true
    git clone --quiet $REPO $DEPLOY_DIR
    mv /tmp/oixa_env_backup $DEPLOY_DIR/.env 2>/dev/null || true
    echo "  repo cloned ✅"
fi
cd $DEPLOY_DIR

echo ""
echo "── 3/6  Python venv + dependencies ──────────────────"
rm -rf venv
python3 -m venv venv --upgrade-deps
venv/bin/pip install -r server/requirements.txt -q --disable-pip-version-check
echo "  dependencies ✅"

echo ""
echo "── 4/6  Directories & permissions ───────────────────"
mkdir -p logs
chown -R root:root $DEPLOY_DIR
echo "  dirs ✅"

echo ""
echo "── 5/6  systemd service ──────────────────────────────"
cat > /etc/systemd/system/${SERVICE_NAME}.service << 'UNIT'
[Unit]
Description=OIXA Protocol Server
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/oixa-protocol/server
Environment=PATH=/opt/oixa-protocol/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin
EnvironmentFile=/opt/oixa-protocol/.env
ExecStart=/opt/oixa-protocol/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
Restart=always
RestartSec=5
StandardOutput=append:/opt/oixa-protocol/logs/oixa.log
StandardError=append:/opt/oixa-protocol/logs/oixa.log

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable ${SERVICE_NAME}
systemctl restart ${SERVICE_NAME}
echo "  systemd ✅"

echo ""
echo "── 6/6  Firewall ─────────────────────────────────────"
ufw --force reset > /dev/null 2>&1
ufw default deny incoming > /dev/null 2>&1
ufw default allow outgoing > /dev/null 2>&1
ufw allow 22/tcp  > /dev/null 2>&1   # SSH
ufw allow 80/tcp  > /dev/null 2>&1   # HTTP
ufw allow 443/tcp > /dev/null 2>&1   # HTTPS
ufw allow 8000/tcp > /dev/null 2>&1  # OIXA API
ufw --force enable > /dev/null 2>&1
echo "  firewall ✅  (22, 80, 443, 8000 open)"

echo ""
echo "── Waiting for server to start ──────────────────────"
for i in \$(seq 1 20); do
    if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
        echo "  server ready ✅"
        break
    fi
    sleep 2
done

echo ""
echo "── Health check ─────────────────────────────────────"
curl -s http://localhost:8000/health

REMOTE

# ── Final status from local machine ───────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " ✅  Deploy complete"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo " API:   http://$SERVER_IP:8000"
echo " Docs:  http://$SERVER_IP:8000/docs"
echo " Health: http://$SERVER_IP:8000/health"
echo ""
echo " Comandos útiles:"
echo "   ssh $SSH_USER@$SERVER_IP 'systemctl status oixa-protocol'"
echo "   ssh $SSH_USER@$SERVER_IP 'journalctl -u oixa-protocol -f'"
echo "   ssh $SSH_USER@$SERVER_IP 'tail -f /opt/oixa-protocol/logs/oixa.log'"
echo "   ssh $SSH_USER@$SERVER_IP 'systemctl restart oixa-protocol'"
echo ""
