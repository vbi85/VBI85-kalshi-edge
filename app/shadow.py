"""Shadow mode: a strategy keeps finding bets and we grade them against real results,
but no money (paper or real) is put on them. Used to test a strategy that's losing to the
crowd without letting it keep losing. One shadow bet per event, priced at the ask we saw."""
from . import db
from .fees import taker_fee

SCHEMA = """
CREATE TABLE IF NOT EXISTS shadow (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  strategy TEXT, ticker TEXT, event_ticker TEXT, title TEXT, side TEXT,
  model_prob REAL, market_prob REAL, ask REAL, noted_at TEXT, notes TEXT,
  outcome INTEGER
);
"""


def init(path=None):
    with db.conn(path) as c:
        c.executescript(SCHEMA)


def record(c, path=None):
    """Log a would-be bet (first one per event only). Returns True if newly recorded."""
    init(path)
    with db.conn(path) as con:
        if con.execute("SELECT 1 FROM shadow WHERE event_ticker=? AND strategy=?",
                       (c.event_ticker, c.strategy)).fetchone():
            return False
        con.execute("INSERT INTO shadow(strategy,ticker,event_ticker,title,side,model_prob,market_prob,"
                    "ask,noted_at,notes) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (c.strategy, c.ticker, c.event_ticker, c.title, c.side, c.model_prob,
                     c.market_prob, c.ask, db.now(), c.notes))
    db.log(f"SHADOW {c.strategy} would buy {c.ticker} {c.side} @ {c.ask:.2f} "
           f"(model {c.model_prob:.0%} vs market {c.market_prob:.0%}) — $0 at risk", path=path)
    return True


def grade(client, path=None):
    """Fill in outcomes for shadow bets whose market has settled."""
    init(path)
    with db.conn(path) as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM shadow WHERE outcome IS NULL LIMIT 40")]
    for r in rows:
        try:
            res = (client.market(r["ticker"]).get("result") or "").lower()
        except Exception:
            continue
        if res in ("yes", "no"):
            with db.conn(path) as con:
                con.execute("UPDATE shadow SET outcome=? WHERE id=?", (1 if res == r["side"] else 0, r["id"]))


def stats(strategy, path=None):
    """Would-be results if each shadow bet had been 1 contract held to the result."""
    init(path)
    with db.conn(path) as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM shadow WHERE strategy=?", (strategy,))]
    done = [r for r in rows if r["outcome"] is not None]
    pnl = sum(r["outcome"] - r["ask"] - taker_fee(r["ask"], 1) for r in done)
    staked = sum(r["ask"] for r in done)
    bm = sum((r["model_prob"] - r["outcome"]) ** 2 for r in done) / len(done) if done else None
    bk = sum((r["market_prob"] - r["outcome"]) ** 2 for r in done) / len(done) if done else None
    return {"logged": len(rows), "graded": len(done), "wins": sum(r["outcome"] for r in done),
            "pnl_per_contract": round(pnl, 2), "roi": (pnl / staked) if staked else None,
            "brier_model": bm, "brier_market": bk}
