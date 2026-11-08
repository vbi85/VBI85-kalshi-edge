#!/usr/bin/env bash
# Run once in the DigitalOcean droplet console, from inside /opt/kalshi-edge
set -e
cd /opt/kalshi-edge
apt-get install -y python3-venv >/dev/null
python3 -m venv venv
venv/bin/pip install -q -r requirements.txt
[ -f .env ] || cp .env.example .env
cp deploy/kalshi-edge-bot.service deploy/kalshi-edge-dash.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now kalshi-edge-bot kalshi-edge-dash
command -v ufw >/dev/null && ufw allow 8090/tcp || true
echo "Done. Dashboard: http://$(curl -s ifconfig.me):8090"
