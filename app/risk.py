"""Position sizing and hard risk caps. Every trade has to pass here first."""
import math
from datetime import datetime, timezone

from . import config, db
from .fees import taker_fee


def bankroll(mode, path=None):
    """Paper bankroll = starting stake + realized P&L."""
    realized = sum((p["pnl"] or 0) for p in db.all_positions(mode, path) if p["status"] != "open")
    return config.PAPER_BANKROLL + realized


def open_exposure(mode, path=None):
    return sum(p["entry_price"] * p["count"] + (p["entry_fee"] or 0)
               for p in db.open_positions(mode, path))


def realized_today(mode, path=None):
    today = datetime.now(timezone.utc).date().isoformat()
    return sum((p["pnl"] or 0) for p in db.all_positions(mode, path)
               if p["status"] != "open" and (p["closed_at"] or "").startswith(today))


def size(model_prob, price, mode, path=None, cap=None):
    """Quarter-Kelly, then capped. Returns contract count (0 = skip)."""
    if not (0 < price < 1):
        return 0
    kelly = (model_prob - price) / (1 - price)
    if kelly <= 0:
        return 0
    stake = min(cap if cap is not None else config.MAX_PER_TRADE,
                kelly * config.KELLY_FRACTION * bankroll(mode, path),
                config.MAX_OPEN_EXPOSURE - open_exposure(mode, path))
    if stake <= 0:
        return 0
    count = math.floor(stake / price)
    while count > 0 and count * price + taker_fee(price, count) > stake:
        count -= 1
    return max(count, 0)


def can_open(event_ticker, mode, path=None):
    """Returns (ok, reason)."""
    if realized_today(mode, path) <= -config.DAILY_LOSS_LIMIT:
        return False, "daily loss limit hit"
    if open_exposure(mode, path) >= config.MAX_OPEN_EXPOSURE:
        return False, "max open exposure"
    if any(p["event_ticker"] == event_ticker for p in db.all_positions(mode, path)):
        return False, "already traded this event (no re-entry)"
    return True, ""
