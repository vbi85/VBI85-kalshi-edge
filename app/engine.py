"""One pass of the bot: manage open positions, then look for new edges."""
from . import config, db, notify, risk
from .broker import LiveBroker, PaperBroker
from .fees import taker_fee
from .kalshi import bid_size, quotes


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


def manage(client, broker, strategies, mode, path=None):
    by_name = {s.name: s for s in strategies}
    for p in db.open_positions(mode, path):
        try:
            m = client.market(p["ticker"])
        except Exception as e:
            db.log(f'manage {p["ticker"]}: {e}', "warn", path)
            continue
        res = _settled_result(m)
        if res:
            won = 1 if res == p["side"] else 0
            _close(p, 1.0 if won else 0.0, 0.0, "settled", outcome=won, path=path)
            continue
        yb, ya, nb, na = quotes(m)
        bid = yb if p["side"] == "yes" else nb
        if not bid:
            continue
        strat = by_name.get(p["strategy"])
        fv = strat.fair_value(p, m) if strat else None
        net = bid - taker_fee(bid, p["count"]) / p["count"]
        cost_each = p["entry_price"] + (p["entry_fee"] or 0) / p["count"]
        reason = None
        if bid >= p["entry_price"] + config.TAKE_PROFIT and net > cost_each:
            reason = "take_profit"
        elif fv is not None and net >= fv + 0.02:
            reason = "market_above_model"   # someone pays more than it's worth -> sell
        if reason and broker.mode == "paper":
            bsz = bid_size(m, p["side"])
            if bsz is not None and bsz < p["count"]:
                reason = None                     # not enough buyers at this price: hold instead
        if reason:
            fill = broker.sell(p["ticker"], p["side"], p["count"], bid)
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
            if c.ask_size is not None:
                n = min(n, int(c.ask_size))      # can't buy more than is for sale at this price
            if n < 1:
                continue
            edge = c.model_prob - (c.ask + taker_fee(c.ask, n) / n)
            if edge < (c.min_edge if c.min_edge is not None else config.MIN_EDGE):
                continue
            fill = broker.buy(c.ticker, c.side, n, c.ask)
            if fill.count < 1:
                continue
            p = dict(mode=mode, strategy=c.strategy, ticker=c.ticker, event_ticker=c.event_ticker,
                     title=c.title, side=c.side, count=fill.count, entry_price=fill.price,
                     entry_fee=fill.fee, model_prob=c.model_prob, market_prob=c.market_prob,
                     edge=round(edge, 4), opened_at=db.now(), status="open",
                     order_id=fill.order_id, notes=c.notes)
            p["id"] = db.insert_position(p, path)
            notify.sheet_upsert(p)
            msg = (f'{mode.upper()} {c.strategy} BUY {c.ticker} {c.side} x{fill.count} @ {fill.price:.2f} '
                   f'model {c.model_prob:.0%} edge {edge*100:.0f}c')
            db.log(msg, path=path)
            if mode == "live":
                notify.text(msg)
            opened += 1
    return opened


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
    brokers = {"paper": PaperBroker(), "live": _LazyLive(client)}
    cands = scan_all(client, strategies, path)
    for mode in ("paper", "live"):
        manage(client, brokers[mode], strategies, mode, path)
    backfill_outcomes(client, path)
    return enter(cands, brokers, mode_for, path)
