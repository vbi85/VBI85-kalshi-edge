"""The approval tests. Each strategy must earn Live on its OWN paper record.
Weather making money never puts Mentions on real money."""
from datetime import datetime, timezone

from . import config, db


def brier(rows, key):
    rows = [r for r in rows if r.get(key) is not None and r.get("outcome") is not None]
    if not rows:
        return None
    return sum((r[key] - r["outcome"]) ** 2 for r in rows) / len(rows)


def stats(mode="paper", strategy=None, path=None):
    rows = [r for r in db.all_positions(mode, path) if "PRE-CAP" not in (r["notes"] or "")]
    if strategy:
        rows = [r for r in rows if r["strategy"] == strategy]
    closed = [r for r in rows if r["status"] != "open"]
    resolved = [r for r in rows if r["outcome"] is not None]
    pnl = sum(r["pnl"] or 0 for r in closed)
    first = min((r["opened_at"] for r in rows), default=None)
    days = (datetime.now(timezone.utc) - datetime.fromisoformat(first)).days if first else 0
    eq = peak = config.PAPER_BANKROLL
    dd = 0.0
    for r in sorted(closed, key=lambda r: r["closed_at"] or ""):
        eq += r["pnl"] or 0
        peak = max(peak, eq)
        dd = max(dd, (peak - eq) / peak if peak else 0)
    wins = [r for r in closed if (r["pnl"] or 0) > 0]
    return {
        "days": days, "trades": len(rows), "open": len(rows) - len(closed),
        "closed": len(closed), "resolved": len(resolved),
        "win_rate": len(wins) / len(closed) if closed else None,
        "pnl": round(pnl, 2), "roi": pnl / config.PAPER_BANKROLL,
        "max_drawdown": dd,
        "brier_model": brier(resolved, "model_prob"),
        "brier_market": brier(resolved, "market_prob"),
    }


def check(strategy, path=None):
    """Returns (passed, list of (test, ok, detail)) for one strategy."""
    s = stats("paper", strategy, path)
    bm, bk = s["brier_model"], s["brier_market"]
    tests = [
        ("Paper days", s["days"] >= config.GATE_MIN_DAYS, f'{s["days"]} / {config.GATE_MIN_DAYS}'),
        ("Settled trades", s["resolved"] >= config.GATE_MIN_SETTLED, f'{s["resolved"]} / {config.GATE_MIN_SETTLED}'),
        ("Return", s["roi"] >= config.GATE_MIN_ROI, f'{s["roi"]:.1%} (need {config.GATE_MIN_ROI:.0%})'),
        ("Max drawdown", s["max_drawdown"] <= config.GATE_MAX_DRAWDOWN,
         f'{s["max_drawdown"]:.1%} (limit {config.GATE_MAX_DRAWDOWN:.0%})'),
        ("Beats market odds", bm is not None and bk is not None and bm < bk,
         f'model {bm:.3f} vs market {bk:.3f}' if bm is not None and bk is not None else "no data yet"),
    ]
    return all(t[1] for t in tests), tests


def armed(path=None):
    return db.get_setting("mode", config.MODE, path) == "live"


def mode_for(strategy, path=None):
    """Live only if the owner armed Live AND this strategy passes every test right now."""
    if not armed(path):
        return "paper"
    passed, _ = check(strategy, path)
    if passed:
        return "live"
    key = f"warned_{strategy}"
    if not db.get_setting(key, False, path):
        db.log(f"{strategy}: stays on PAPER, approval tests not passed.", "alert", path)
        db.set_setting(key, True, path)
    return "paper"


def any_passes(strategies, path=None):
    return any(check(s, path)[0] for s in strategies)
