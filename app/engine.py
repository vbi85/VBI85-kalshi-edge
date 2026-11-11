"""One pass of the bot: manage open positions, then look for new edges."""
from . import calibrate, config, db, notify, risk
from .broker import LiveBroker, PaperBroker
from .fees import taker_fee
from .kalshi import quotes


def _settled_result(m):
    r = (m.get("result") or "").lower()
    return r if r in ("yes", "no") else None


def _close(p, price, fee, reason, outcome=None, path=None):
    cost = p["entry_price"] * p["count"] + (p["entry_fee"] or 0)
    pnl = round(price * p["count"] - fee - cost, 2)
    fields = dict(status="closed", exit_price=price, exit_fee=fee, exit_reason=reason,
                  closed_at=db.now(), pnl=pnl)
    if outcome is not None:
        fields["outcome"] = outcome
    db.update_position(p["id"], path=path, **fields)
    p.update(fields)
    notify.sheet_upsert(p)
    msg = f'{p["mode"].upper()} CLOSE {reason}: {p["ticker"]} {p["side"]} x{p["count"]} P&L ${pnl:+.2f}'
    db.log(msg, path=path)
    if p["mode"] == "live":
        notify.text(msg)


def decide_exit(p, bid, fv):
    """Returns (reason, lowest acceptable sell price) or (None, None) to keep holding.
    Order matters: protect against big losses first, then let winners run to fair value."""
    entry, n = p["entry_price"], p["count"]
    net = bid - taker_fee(bid, n) / n                 # what selling one contract nets after fee
    cost_each = entry + (p["entry_fee"] or 0) / n
    edge_left = None if fv is None else fv - net      # how much better holding is than selling

    # 1. Optional blind hard stop (off by default), never on cheap longshots.
    if config.HARD_STOP > 0 and entry >= config.CHEAP_PRICE and bid <= entry * (1 - config.HARD_STOP):
        return "stop_hard", max(0.01, round(bid - 0.05, 2))
    # 2. Down 15c: hold only if the (freshly updated) model still sees a big edge.
    if bid <= entry - config.STOP_LOSS and (edge_left is None or edge_left < config.STOP_KEEP_EDGE):
        return "stop_edge_gone", max(0.01, round(bid - 0.05, 2))
    if fv is not None:
        # 3. Winner: market has caught up to our forecast -> the edge is captured, take it.
        if net >= fv - 0.01 and net > cost_each:
            return "caught_up", round(max(cost_each + 0.01, fv - 0.03), 2)
        # 4. Market pays more than it's worth to us (even at a loss) -> selling beats holding.
        if net >= fv + 0.02:
            return "market_above_model", round(fv + 0.01, 2)
    # 5. Backup take-profit: up 12c and our remaining edge is small (or unknown).
    if bid >= entry + config.TAKE_PROFIT and net > cost_each and (edge_left is None or edge_left < 0.05):
        return "take_profit", round(cost_each + 0.02, 2)
    return None, None


def manage(client, broker, strategies, mode, path=None):
    for p in db.open_positions(mode, path):
        try:
            _manage_one(client, broker, {s.name: s for s in strategies}, p, path)
        except Exception as e:                     # one bad position never stops the rest
            db.log_once(f"manage:{p['ticker']}", f'manage {p["ticker"]} failed: {e}', "warn", path)


def _manage_one(client, broker, by_name, p, path=None):
    try:
        m = client.market(p["ticker"])
    except Exception as e:
        db.log(f'manage {p["ticker"]}: {e}', "warn", path)
        return
    if p["strategy"] == "weather" and "·" not in (p["title"] or ""):
        from .labels import weather_title          # upgrade older, vague titles in place
        p["title"] = weather_title(m, p["ticker"])
        db.update_position(p["id"], path=path, title=p["title"])
    res = _settled_result(m)
    if res:
        won = 1 if res == p["side"] else 0
        _close(p, 1.0 if won else 0.0, 0.0, "settled", outcome=won, path=path)
        return
    yb, ya, nb, na = quotes(m)
    bid = yb if p["side"] == "yes" else nb
    if not bid:
        return
    strat = by_name.get(p["strategy"])
    try:
        fv = strat.fair_value(p, m) if strat else None
    except Exception as e:                     # e.g. weather service down for this city
        db.log_once(f"fv:{p['ticker']}", f'fair value unavailable for {p["ticker"]}: {e}', "warn", path)
        fv = None
    reason, floor = decide_exit(p, bid, fv)
    if reason:
        fill = broker.sell(p["ticker"], p["side"], p["count"], bid, min_price=floor)
        if fill.count == p["count"]:
            _close(p, fill.price, fill.fee, reason, path=path)
        elif fill.count:
            db.log(f'partial exit {p["ticker"]} {fill.count}/{p["count"]} — check Kalshi', "alert", path)


def backfill_outcomes(client, path=None):
    """Positions we sold early still need the final result to grade the model."""
    with db.conn(path) as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM positions WHERE status='closed' AND outcome IS NULL LIMIT 25")]
    for p in rows:
        try:
            res = _settled_result(client.market(p["ticker"]))
        except Exception:
            continue
        if res:
            db.update_position(p["id"], path=path, outcome=1 if res == p["side"] else 0)


def scan_all(client, strategies, path=None):
    """Scan first so each strategy's fair-value data is fresh before we manage exits."""
    out = {}
    for strat in strategies:
        if not strat.enabled or strat.name not in config.ENABLED_STRATEGIES:
            continue
        try:
            out[strat.name] = strat.scan(client)
        except Exception as e:
            db.log(f"{strat.name} scan failed: {e}", "warn", path)
    return out


def price_limit(model_prob, count, min_edge):
    """Highest price (whole cents) that still leaves `min_edge` after the taker fee."""
    best = None
    for cents in range(1, 100):
        p = cents / 100
        if model_prob - (p + taker_fee(p, count) / count) >= min_edge:
            best = p
    return best


def enter(cands_by_strategy, brokers, mode_for, path=None):
    opened = 0
    for name, cands in cands_by_strategy.items():
        mode = mode_for(name)
        broker = brokers[mode]
        for c in sorted(cands, key=lambda c: c.model_prob - c.ask, reverse=True):
            ok, why = risk.can_open(c.event_ticker, mode, path)
            if not ok:
                continue
            n = risk.size(c.model_prob, c.ask, mode, path, cap=c.max_stake)
            if n < 1:
                continue
            min_edge = c.min_edge if c.min_edge is not None else config.MIN_EDGE
            max_price = price_limit(c.model_prob, n, min_edge)
            if max_price is not None:
                max_price = min(max_price, config.MAX_PRICE)
            if max_price is None or max_price < c.ask:
                continue
            # walks the book: best price first, then the next level, never above max_price
            fill = broker.buy(c.ticker, c.side, n, c.ask, max_price=max_price)
            if fill.count < 1:
                continue
            edge = c.model_prob - (fill.price + fill.fee / fill.count)
            p = dict(mode=mode, strategy=c.strategy, ticker=c.ticker, event_ticker=c.event_ticker,
                     title=c.title, side=c.side, count=fill.count, entry_price=fill.price,
                     entry_fee=fill.fee, model_prob=c.model_prob, market_prob=c.market_prob,
                     edge=round(edge, 4), opened_at=db.now(), status="open",
                     order_id=fill.order_id,
                     notes=c.notes + (f" | filled {len(fill.levels)} price levels" if len(fill.levels) > 1 else ""))
            p["id"] = db.insert_position(p, path)
            notify.sheet_upsert(p)
            msg = (f'{mode.upper()} {c.strategy} BUY {c.ticker} {c.side} x{fill.count} @ {fill.price:.2f} '
                   f'model {c.model_prob:.0%} edge {edge*100:.0f}c')
            db.log(msg, path=path)
            if mode == "live":
                notify.text(msg)
            opened += 1
    return opened


MODEL_VERSION = 2


def tag_old_model(path=None):
    """Model v2 (daily forecast + city correction + new exits) restarts the go-live clock:
    trades from the old model stay visible but no longer count toward the tests."""
    if (db.get_setting("model_version", 1, path) or 1) >= MODEL_VERSION:
        return
    with db.conn(path) as c:
        n = c.execute("UPDATE positions SET notes = COALESCE(notes,'') || ' | MODEL-V1' "
                      "WHERE COALESCE(notes,'') NOT LIKE '%MODEL-V1%' AND COALESCE(notes,'') NOT LIKE '%PRE-CAP%'").rowcount
    db.set_setting("model_version", MODEL_VERSION, path)
    db.log(f"Model v2 active: {n} earlier trades tagged OLD MODEL; go-live clock restarts now.", "alert", path)


class _LazyLive:
    """Only builds the live broker (and needs API keys) if something actually goes live."""
    def __init__(self, client):
        self.client, self._b = client, None

    def __getattr__(self, name):
        if self._b is None:
            self._b = LiveBroker(self.client)
        return getattr(self._b, name)


def run_once(client, strategies, mode_for=None, path=None):
    mode_for = mode_for or (lambda name: "paper")
    brokers = {"paper": PaperBroker(client), "live": _LazyLive(client)}
    tag_old_model(path)
    cands = scan_all(client, strategies, path)
    for mode in ("paper", "live"):
        manage(client, brokers[mode], strategies, mode, path)
    backfill_outcomes(client, path)
    try:
        calibrate.learn(client, path)
    except Exception as e:
        db.log_once("calibrate", f"calibration skipped: {e}", "warn", path)
    db.prune(path)
    return enter(cands, brokers, mode_for, path)
