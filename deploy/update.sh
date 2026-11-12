#!/usr/bin/env bash
# One command for every future update: bash deploy/update.sh
set -e
cd /opt/kalshi-edge
# pull first, then restart this script so the NEW version of it runs (not the old copy)
if [ -z "$KE_UPDATED" ]; then
  git pull -q
  KE_UPDATED=1 exec bash deploy/update.sh "$@"
fi
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
# Nov 11: Mentions to shadow mode, money back to Weather
env = re.sub(r"^WEATHER_BUDGET=(30|40)[ \t]*$", "WEATHER_BUDGET=50", env, flags=re.M)
env = re.sub(r"^MENTIONS_BUDGET=(30|20)[ \t]*$", "MENTIONS_BUDGET=10", env, flags=re.M)
# Nov 10: split budget 30/30 to give Mentions a real test
env = re.sub(r"^WEATHER_BUDGET=40[ \t]*$", "WEATHER_BUDGET=30", env, flags=re.M)
env = re.sub(r"^MENTIONS_BUDGET=20[ \t]*$", "MENTIONS_BUDGET=30", env, flags=re.M)
# Nov 10: blind hard stop off
env = re.sub(r"^HARD_STOP=0\.50?[ \t]*$", "HARD_STOP=0", env, flags=re.M)
# model v2: tighter disagreement cap
env = re.sub(r"^MAX_DISAGREE=0\.30[ \t]*$", "MAX_DISAGREE=0.20", env, flags=re.M)
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
# live check: official NWS daily high forecast (model v2 uses it)
venv/bin/python - <<'PY'
from datetime import timedelta
from app import nws
for code in ("CHI", "MIA", "LAX"):
    day = nws.lst_now(nws.CITIES[code][4]).date() + timedelta(days=1)
    try:
        print(f"daily forecast check {code} {day}: high {nws.daily_high(code, day)}F")
    except Exception as e:
        print(f"DAILY FORECAST CHECK FAILED for {code}: {e} — tell Claude")
PY
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
