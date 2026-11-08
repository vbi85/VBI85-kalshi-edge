#!/usr/bin/env bash
# One command for every future update: bash deploy/update.sh
set -e
cd /opt/kalshi-edge
git pull -q
venv/bin/pip install -q -r requirements.txt
# add any new settings from .env.example that this .env doesn't have yet (never overwrites yours)
venv/bin/python - <<'EOF'
import re
env = open(".env").read()
have = set(re.findall(r"^([A-Z_]+)=", env, re.M))
added = []
for line in open(".env.example"):
    m = re.match(r"^([A-Z_]+)=", line)
    if m and m.group(1) not in have:
        env += ("" if env.endswith("\n") else "\n") + line.rstrip("\n") + "\n"
        added.append(m.group(1))
# the old 7-city default becomes ALL cities
env = re.sub(r"^WEATHER_CITIES=NYC,CHI,MIA,AUS,LAX,DEN,PHIL[ \t]*$", "WEATHER_CITIES=ALL", env, flags=re.M)
open(".env", "w").write(env)
print("new settings added:", ", ".join(added) or "none")
EOF
cp deploy/kalshi-edge-bot.service deploy/kalshi-edge-dash.service /etc/systemd/system/
systemctl daemon-reload
systemctl restart kalshi-edge-bot kalshi-edge-dash
sleep 3
echo "bot: $(systemctl is-active kalshi-edge-bot)   dashboard: $(systemctl is-active kalshi-edge-dash)"
# live check: can we read Kalshi's order book? (if not, paper trading would silently stop)
venv/bin/python - <<'PY'
from app.kalshi import KalshiClient, book_asks
c = KalshiClient()
ms = [m for m in c.markets(series_ticker="KXHIGHNY", status="open") if m.get("yes_bid_dollars") not in (None, "0.0000")]
if not ms:
    print("order book check: no active NYC market to test right now (fine)")
else:
    m = ms[0]
    b = c.orderbook(m["ticker"])
    a = book_asks(b, "yes")
    if b["yes"] or b["no"]:
        print(f"order book check OK: {m['ticker']} YES asks {a[:3]}")
    else:
        print("ORDER BOOK CHECK FAILED: empty book — tell Claude")
PY
