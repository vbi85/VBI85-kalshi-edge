"""Position sizing and hard risk caps. Every trade has to pass here first."""
import math
from datetime import datetime, timezone

from . import config, db
from .fees import taker_fee


def bankroll(mode, path=None):
    """Paper bankroll = starting stake + realized P&L."""
    realized = sum((p["pnl"] or 0) for p in db.all_positions(mode, path) if p["status"] != "open")
    return config.PAPER_BANKROLL + realized


def open_exposure(mode, path=None, strategy=None):
    return sum(p["entry_price"] * p["count"] + (p["entry_fee"] or 0)
               for p in db.open_positions(mode, path) if strategy is None or p["strategy"] == strategy)


def room(mode, path=None, strategy=None):
    """Dollars still available to `strategy`: its own budget left, and the overall cap left.
    Other strategies count against the overall cap only up to THEIR budget, so one strategy
    sitting over its budget (e.g. after the budget is lowered) can't block the others."""
    if strategy not in config.STRATEGY_BUDGET:
        return config.MAX_OPEN_EXPOSURE - open_exposure(mode, path)
    mine = open_exposure(mode, path, strategy)
    others = sum(min(open_exposure(mode, path, s), b)
                 for s, b in config.STRATEGY_BUDGET.items() if s != strategy)
    return min(config.STRATEGY_BUDGET[strategy] - mine, config.MAX_OPEN_EXPOSURE - others - mine)


def realized_today(mode, path=None):
    today = datetime.now(timezone.utc).date().isoformat()
    return sum((p["pnl"] or 0) for p in db.all_positions(mode, path)
               if p["status"] != "open" and (p["closed_at"] or "").startswith(today))


def size(model_prob, price, mode, path=None, cap=None, strategy=None):
    """Quarter-Kelly, then capped. Returns contract count (0 = skip)."""
    if not (0 < price < 1):
        return 0
    kelly = (model_prob - price) / (1 - price)
    if kelly <= 0:
        return 0
    cap = cap if cap is not None else config.MAX_PER_TRADE
    if price < config.CHEAP_PRICE:
        cap = min(cap, config.CHEAP_MAX_STAKE)      # longshots: small stake, held to the result
    stake = min(cap,
                kelly * config.KELLY_FRACTION * bankroll(mode, path),
                room(mode, path, strategy))
    if stake <= 0:
        return 0
    count = math.floor(stake / price)
    while count > 0 and count * price + taker_fee(price, count) > stake:
        count -= 1
    return max(count, 0)


def can_open(event_ticker, mode, path=None, strategy=None):
    """Returns (ok, reason)."""
    if realized_today(mode, path) <= -config.DAILY_LOSS_LIMIT:
        return False, "daily loss limit hit"
    if room(mode, path, strategy) < 1:
        return False, f"{strategy or 'overall'} budget full"
    if any(p["event_ticker"] == event_ticker for p in db.all_positions(mode, path)):
        return False, "already traded this event (no re-entry)"
    return True, ""
