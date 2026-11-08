"""All settings live in .env. Defaults are deliberately conservative."""
import os
from dotenv import load_dotenv

load_dotenv()


def _f(name, default):
    return float(os.getenv(name, default))


def _i(name, default):
    return int(os.getenv(name, default))


def _b(name, default):
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


# ---- Mode ---------------------------------------------------------------
# MODE is the *requested* mode. Live only happens if the gate also passes.
MODE = os.getenv("MODE", "paper").lower()          # paper | live
KALSHI_ENV = os.getenv("KALSHI_ENV", "prod").lower()  # prod | demo (demo = Kalshi's fake-money exchange)

BASE_URLS = {
    "prod": "https://api.elections.kalshi.com/trade-api/v2",
    "demo": "https://demo-api.kalshi.co/trade-api/v2",
}
BASE_URL = os.getenv("KALSHI_BASE_URL", BASE_URLS.get(KALSHI_ENV, BASE_URLS["prod"]))
KALSHI_KEY_ID = os.getenv("KALSHI_KEY_ID", "")
KALSHI_PRIVATE_KEY_PATH = os.getenv("KALSHI_PRIVATE_KEY_PATH", "")

# ---- Bankroll & risk ----------------------------------------------------
PAPER_BANKROLL = _f("PAPER_BANKROLL", 200)        # matches the $200 live plan
MAX_PER_TRADE = _f("MAX_PER_TRADE", 10)           # dollars at risk per position
MAX_OPEN_EXPOSURE = _f("MAX_OPEN_EXPOSURE", 60)   # dollars at risk across all open positions
DAILY_LOSS_LIMIT = _f("DAILY_LOSS_LIMIT", 20)     # stop opening trades for the day after this
KELLY_FRACTION = _f("KELLY_FRACTION", 0.25)       # quarter-Kelly sizing

# ---- Edge rules ---------------------------------------------------------
MIN_EDGE = _f("MIN_EDGE", 0.08)          # model prob minus all-in cost, per contract
MIN_PRICE = _f("MIN_PRICE", 0.10)        # skip lottery tickets
MAX_PRICE = _f("MAX_PRICE", 0.85)        # skip pennies-in-front-of-steamroller
TAKE_PROFIT = _f("TAKE_PROFIT", 0.12)    # sell when bid is this far above entry
FEE_RATE = _f("FEE_RATE", 0.07)          # Kalshi taker fee multiplier
MIN_VOLUME = _i("MIN_VOLUME", 50)        # skip dead markets
# If our model disagrees with the market by more than this, assume WE are wrong and skip.
MAX_DISAGREE = _f("MAX_DISAGREE", 0.30)

# ---- Weather model ------------------------------------------------------
# Forecast error (std dev, deg F) by how far out the settlement day is.
SIGMA_NEXT_DAY = _f("SIGMA_NEXT_DAY", 2.6)
SIGMA_SAME_DAY = _f("SIGMA_SAME_DAY", 2.0)
WEATHER_CITIES = [c.strip() for c in os.getenv(
    "WEATHER_CITIES", "NYC,CHI,MIA,AUS,LAX,DEN,PHIL").split(",") if c.strip()]

# ---- Mentions model -----------------------------------------------------
# Optional fixed list of series tickers; blank = auto-discover "mention"/"say" series.
MENTION_SERIES = [s.strip() for s in os.getenv("MENTION_SERIES", "").split(",") if s.strip()]
# Anything financial is excluded (you trade no financial markets).
MENTION_EXCLUDE = [w.strip().upper() for w in os.getenv(
    "MENTION_EXCLUDE", "EARNINGS,EARN,FOMC,FED,POWELL,CEO,STOCK,CRYPTO,BITCOIN,BTC,ETH,DIVIDEND,SOLANA,INVEST,NASDAQ,DOW,SP500,TSLA,NVDA,AAPL,MSFT,AMZN,META,GOOG,TARIFFRATE").split(",") if w.strip()]
MENTION_MIN_HISTORY = _i("MENTION_MIN_HISTORY", 4)      # past events with this exact word
MENTION_PRIOR_WEIGHT = _f("MENTION_PRIOR_WEIGHT", 3)    # pulls thin history toward series average
MENTION_MIN_HOURS = _f("MENTION_MIN_HOURS", 2)          # don't enter close to / during the event
MENTION_MAX_MOVE = _f("MENTION_MAX_MOVE", 0.15)         # price jumped this much in 24h = event live, skip
MENTION_MAX_PER_TRADE = _f("MENTION_MAX_PER_TRADE", 5)  # half size: weakest data of the set
MENTION_MIN_EDGE = _f("MENTION_MIN_EDGE", 0.10)
MENTION_REFRESH_HOURS = _f("MENTION_REFRESH_HOURS", 6)  # re-find active series / history this often

ENABLED_STRATEGIES = [s.strip() for s in os.getenv(
    "ENABLED_STRATEGIES", "weather,mentions").split(",") if s.strip()]

# ---- Loop ---------------------------------------------------------------
LOOP_MINUTES = _i("LOOP_MINUTES", 10)

# ---- Live gate (approval tests before real money) ----------------------
GATE_MIN_DAYS = _i("GATE_MIN_DAYS", 21)
GATE_MIN_SETTLED = _i("GATE_MIN_SETTLED", 60)
GATE_MIN_ROI = _f("GATE_MIN_ROI", 0.05)
GATE_MAX_DRAWDOWN = _f("GATE_MAX_DRAWDOWN", 0.25)
LIVE_PIN = os.getenv("LIVE_PIN", "")

# ---- Logging / alerts ---------------------------------------------------
SHEETS_WEBHOOK_URL = os.getenv("SHEETS_WEBHOOK_URL", "")
SHEETS_WEBHOOK_SECRET = os.getenv("SHEETS_WEBHOOK_SECRET", "")
ALERT_GMAIL = os.getenv("ALERT_GMAIL", "")
ALERT_GMAIL_APP_PASSWORD = os.getenv("ALERT_GMAIL_APP_PASSWORD", "")
ALERT_SMS_ADDRESS = os.getenv("ALERT_SMS_ADDRESS", "")   # e.g. 4145551234@tmomail.net

# ---- Dashboard ----------------------------------------------------------
DASH_USER = os.getenv("DASH_USER", "burt")
DASH_PASSWORD = os.getenv("DASH_PASSWORD", "change-me")
DASH_PORT = _i("DASH_PORT", 8090)

DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(os.path.dirname(__file__)), "edge.db"))
USER_AGENT = os.getenv("USER_AGENT", "kalshi-edge-bot (personal research)")
