# Kalshi Edge — non-financial prediction bot

Trades only where the market price is clearly wrong after fees. No crypto, no financial markets.

| Phase | Category | Status |
|---|---|---|
| 1 | Weather (daily high temp, 22 cities) | **Built, paper mode** |
| 1 | Mentions (word base rates from Kalshi's own settled history) | **Built, paper mode** |
| 2 | Sports (Kalshi vs sportsbook consensus) | Next build |

## How a trade happens
1. Every 10 min: pull NWS forecast + today's observed high for each city's **settlement station** (Chicago = Midway, NYC = Central Park, LA = the airport).
2. Turn that into a probability for every Kalshi bracket.
3. Buy only if our probability beats the price by **8¢+ after fees**, price between 10¢ and 85¢, 50+ volume.
4. Exit: sell at **+12¢**, or sell if the market pays more than our model says it's worth, otherwise hold to settlement (next morning).
5. One position per city-day, ever. Max $10/trade, $60 open, $20 daily loss stop.
6. Safety: skips when model and market disagree by 30+ points, when the market's center is 4°F+ from ours (wrong-station guard), and never buys more than is actually for sale.

## Weather model v2 (Nov 9)
- Forecast = official NWS daily high (hourly max ran 1-3°F cold), shifted by a per-city correction the bot learns from Kalshi's settled brackets (cautious: 1 day of data moves ~25%, capped ±3°F).
- Never bets NO on the market's favorite bracket. Disagreement cap 20 points.
- Exits: hold winners until the market reaches our fair value; at -15¢ re-check and exit unless 10¢+ edge remains; hard stop at half value.
- Go-live clock restarted for v2; older trades are tagged OLD MODEL and excluded.

## How a Mentions trade happens
1. Finds "what will X say" series automatically. Skips anything with EARNINGS, FED, FOMC, CEO, STOCK, CRYPTO in the name.
2. Looks up how often each word was actually said in that series' past settled events (needs 4+).
3. Buys YES on words said far more often than the price implies, or NO on the reverse. 10¢+ edge, $5 max.
4. No entries within 2 hours of close or after a 15¢ price jump (the event is live and history is stale).

## Go-live tests (all must pass, PER STRATEGY, checked every loop)
21+ paper days · 60+ settled trades · +5% return · drawdown under 25% · model beats market odds (Brier score).
Then: PIN on the dashboard → Live. Only strategies that pass every test trade real money; the rest keep paper trading. If a strategy later fails a test, it drops itself back to Paper.

## Install on the DigitalOcean droplet (separate from kalshi-scanner)
1. Code lives at github.com/vbi85/VBI85-kalshi-edge.
2. Open the **DigitalOcean droplet console** and run:
   ```
   cd /opt && git clone https://github.com/vbi85/VBI85-kalshi-edge.git kalshi-edge && cd kalshi-edge
   cp .env.example .env && nano .env      # set DASH_PASSWORD and LIVE_PIN at minimum
   bash deploy/setup.sh
   ```
3. Open `http://<droplet-ip>:8090` in Chrome on your Android, log in, tap ⋮ → **Add to Home screen**.

## Updating (every future change)
In the DigitalOcean droplet console: `cd /opt/kalshi-edge && bash deploy/update.sh`

## Google Sheet log + email alerts
Sheet: "Kalshi Edge — Trade Log" (Trades + Summary tabs already built).
Extensions → Apps Script → paste `apps_script/Code.gs` → run `setup` (copy the SECRET from the log) → Deploy → Web app (Execute as Me, Anyone) → copy the URL.
Then on the droplet: `bash deploy/set-alerts.sh` and paste both. You get: trade rows in the Sheet, a 7am summary email, error emails, and a "BOT IS DOWN" email if heartbeats stop (works even if the droplet is off).

## Before real money
Set `KALSHI_ENV=demo` with a demo API key for one day to prove orders fill and cancel correctly. Then back to `prod`.

Useful: `systemctl status kalshi-edge-bot` · `journalctl -u kalshi-edge-bot -f` · tests: `python3 tests/test_core.py`
