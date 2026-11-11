"""Offline tests: fake Kalshi + fake NWS, full paper cycle (enter -> settle -> gate)."""
import os
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ["DB_PATH"] = tempfile.mktemp(suffix=".db")
os.environ["WEATHER_CITIES"] = "CHI"
os.environ["MAX_DISAGREE"] = "1.0"   # fixtures use big mispricings; cap tested separately

from app import config, db, engine, fees, gate, nws  # noqa: E402
from app.strategies import mentions, weather  # noqa: E402

nws.daily_high = lambda code, day: None      # tests use the hourly path unless they set this


def test_fees():
    assert fees.taker_fee(0.50, 10) == 0.18          # 0.07*10*.25 = 0.175 -> 0.18
    assert fees.taker_fee(0.10, 1) == 0.01
    be = fees.round_trip_breakeven(0.40, 10)
    assert be > 0.42, be                              # a 2-cent scalp loses money


def test_ticker_date():
    assert weather.ticker_date("KXHIGHCHI-26NOV09") == date(2026, 11, 9)


def test_dist_sums_to_one():
    d = weather.HighTempDist(55.0, 2.6)
    ms = [{"strike_type": "less", "cap_strike": 50}]
    ms += [{"strike_type": "between", "floor_strike": f, "cap_strike": f + 1} for f in range(50, 60, 2)]
    ms += [{"strike_type": "greater", "floor_strike": 59}]
    total = sum(d.prob_market(m) for m in ms)
    assert abs(total - 1) < 1e-9, total


def test_observed_floor():
    d = weather.HighTempDist(55.0, 1.0, floor=58)
    assert d.prob_market({"strike_type": "less", "cap_strike": 58}) == 0.0


def fake_book(m):
    """Order book implied by a fake market's quotes (bids only, Kalshi style)."""
    if "book" in m:
        return m["book"]
    sz = lambda k: float(m.get(k) or 1000)
    yes = [(m["yes_bid"] / 100, sz("yes_bid_size_fp"))] if m.get("yes_bid") else []
    no = [(m["no_bid"] / 100, sz("yes_ask_size_fp"))] if m.get("no_bid") else []
    return {"yes": yes, "no": no}


class FakeClient:
    can_trade = False

    def __init__(self, day):
        ev = "KXHIGHCHI-" + day.strftime("%y%b%d").upper()
        self.ev = ev
        self.ms = {}
        # Market thinks 52-53 is likely; our forecast says 56. Mispriced.
        layout = [("less", None, 50, 5), ("between", 50, 51, 20), ("between", 52, 53, 45),
                  ("between", 54, 55, 20), ("between", 56, 57, 8), ("greater", 57, None, 4)]
        for i, (st, fl, cp, ask) in enumerate(layout):
            t = f"{ev}-X{i}"
            self.ms[t] = {"ticker": t, "event_ticker": ev, "title": f"bracket {i}", "strike_type": st,
                          "floor_strike": fl, "cap_strike": cp, "yes_ask": ask, "yes_bid": ask - 2,
                          "no_ask": 100 - ask + 2, "no_bid": 100 - ask, "volume": 500, "status": "active"}

    def markets(self, series_ticker=None, **k):
        return list(self.ms.values())

    def market(self, t):
        return self.ms[t]

    def orderbook(self, t, depth=30):
        return fake_book(self.ms[t])

    def event_markets(self, ev):
        return [m for m in self.ms.values() if m["event_ticker"] == ev]


def test_full_paper_cycle(monkeypatch=None):
    db.init()
    off = nws.CITIES["CHI"][4]
    tomorrow = (datetime.now(timezone.utc) + timedelta(hours=off)).date() + timedelta(days=1)
    start, _ = nws.climate_day_bounds_utc(tomorrow, off)
    hourly = [(start + timedelta(hours=h), 40 + (16 if h == 15 else h % 10)) for h in range(24)]
    nws.hourly_forecast = lambda code: hourly          # forecast high = 56
    nws.observed_max_f = lambda code, day: None
    fc = FakeClient(tomorrow)
    strats = [weather.WeatherStrategy()]
    n = engine.run_once(fc, strats)
    assert n == 1, n
    pos = db.open_positions("paper")[0]
    print("opened:", pos["ticker"], pos["side"], pos["count"], pos["entry_price"], pos["edge"])
    assert pos["model_prob"] > pos["entry_price"] + config.MIN_EDGE
    # second pass: no duplicate on same event
    assert engine.run_once(fc, strats) == 0
    # settle in our favor
    won_side_yes = pos["side"] == "yes"
    fc.ms[pos["ticker"]]["result"] = "yes" if won_side_yes else "no"
    engine.run_once(fc, strats)
    closed = db.all_positions("paper")[0]
    assert closed["status"] == "closed" and closed["outcome"] == 1 and closed["pnl"] > 0, closed
    print("settled P&L:", closed["pnl"])
    passed, tests = gate.check("weather")
    assert not passed                                   # 1 trade must never unlock live money
    for t in tests:
        print(" ", "PASS" if t[1] else "FAIL", t[0], t[2])


def test_take_profit():
    off = nws.CITIES["CHI"][4]
    day = (datetime.now(timezone.utc) + timedelta(hours=off)).date() + timedelta(days=2)
    start, _ = nws.climate_day_bounds_utc(day, off)
    nws.hourly_forecast = lambda code: [(start + timedelta(hours=h), 40 + (16 if h == 15 else h % 10)) for h in range(24)]
    fc = FakeClient(day)
    strats = [weather.WeatherStrategy()]
    assert engine.run_once(fc, strats) == 1
    pos = db.open_positions("paper")[0]
    m = fc.ms[pos["ticker"]]
    jump = int((pos["entry_price"] + config.TAKE_PROFIT + 0.02) * 100)
    if pos["side"] == "no":
        m["no_bid"], m["no_ask"] = jump, jump + 2
    else:
        m["yes_bid"], m["yes_ask"] = jump, jump + 2
    engine.run_once(fc, strats)
    p = [r for r in db.all_positions("paper") if r["id"] == pos["id"]][0]
    assert p["status"] == "closed" and p["exit_reason"] in ("take_profit", "caught_up") and p["pnl"] > 0, p
    print("take-profit P&L:", p["pnl"])


class FakeMentionClient:
    """One political-briefing series + one earnings series (must be ignored)."""
    can_trade = False

    def __init__(self):
        self.settled = []
        # "tariff" said in 9 of 10 past briefings, "bitcoin" never; "ukraine" 1 of 10.
        for i in range(10):
            ev = f"KXBRIEFMENTION-26OCT{i+10:02d}"
            for w, hit in (("Tariff", True), ("Ukraine", i == 0), ("Weather", i % 2 == 0)):
                self.settled.append({"ticker": f"{ev}-{w.upper()}", "event_ticker": ev,
                                     "yes_sub_title": w, "result": "yes" if hit else "no"})
        ev = "KXBRIEFMENTION-26NOV12"
        self.ev = ev
        far = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
        self.open = {}
        # Market underprices "Tariff" (40c) and overprices "Ukraine" (55c).
        for w, ask in (("Tariff", 40), ("Ukraine", 55), ("Weather", 50)):
            t = f"{ev}-{w.upper()}"
            self.open[t] = {"ticker": t, "event_ticker": ev, "title": f"Will they say {w}?",
                            "yes_sub_title": w, "yes_ask": ask, "yes_bid": ask - 2,
                            "no_ask": 102 - ask, "no_bid": 100 - ask, "volume": 900,
                            "status": "active", "close_time": far,
                            "last_price": ask - 1, "previous_price": ask}
        self.earn = {"ticker": "KXEARNINGSMENTIONNVDA-26NOV19-AI", "event_ticker": "KXEARNINGSMENTIONNVDA-26NOV19",
                     "title": "Will NVIDIA say AI?", "yes_sub_title": "AI", "yes_ask": 20, "yes_bid": 18,
                     "no_ask": 82, "no_bid": 80, "volume": 9000, "status": "active", "close_time": far}

    def series_list(self, category=None):
        return [{"ticker": "KXBRIEFMENTION", "title": "What will be said at the briefing?"},
                {"ticker": "KXEARNINGSMENTIONNVDA", "title": "What will NVIDIA say on earnings call?"},
                {"ticker": "KXHIGHNY", "title": "Highest temperature in NYC"}]

    def markets(self, series_ticker=None, status="open", **k):
        if series_ticker == "KXEARNINGSMENTIONNVDA":
            raise AssertionError("financial series must never be fetched")
        if series_ticker != "KXBRIEFMENTION":
            return []
        return self.settled if status == "settled" else list(self.open.values())

    def market(self, t):
        return self.open[t]

    def orderbook(self, t, depth=30):
        return fake_book(self.open[t])

    def event_markets(self, ev):
        return [m for m in self.open.values() if m["event_ticker"] == ev]


def test_mentions():
    os.environ.pop("MENTION_SERIES", None)
    strat = mentions.MentionsStrategy()
    fc = FakeMentionClient()
    assert strat.series(fc) == ["KXBRIEFMENTION"]          # earnings + weather excluded
    n = engine.run_once(fc, [strat])
    assert n == 1, n                                        # one per event
    pos = [p for p in db.open_positions("paper") if p["strategy"] == "mentions"][0]
    print("mentions opened:", pos["ticker"], pos["side"], pos["count"], pos["entry_price"], pos["notes"])
    assert pos["ticker"].endswith("TARIFF") and pos["side"] == "yes"
    assert pos["count"] * pos["entry_price"] <= config.MENTION_MAX_PER_TRADE
    # event goes live: price jumps -> no new entries, fair value goes quiet (hold)
    m = fc.open[pos["ticker"]]
    m["last_price"], m["previous_price"] = 70, 40
    assert strat.fair_value(pos, m) is None
    # word is said -> settles YES
    m["result"] = "yes"
    engine.run_once(fc, [strat])
    p = [r for r in db.all_positions("paper") if r["id"] == pos["id"]][0]
    assert p["status"] == "closed" and p["pnl"] > 0, p
    print("mentions settled P&L:", p["pnl"])
    ok, tests = gate.check("mentions")
    assert not ok


def test_disagreement_cap_blocks_wild_edges():
    off = nws.CITIES["CHI"][4]
    day = (datetime.now(timezone.utc) + timedelta(hours=off)).date() + timedelta(days=3)
    start, _ = nws.climate_day_bounds_utc(day, off)
    nws.hourly_forecast = lambda code: [(start + timedelta(hours=h), 40 + (16 if h == 15 else h % 10)) for h in range(24)]
    fc = FakeClient(day)
    old = config.MAX_DISAGREE
    config.MAX_DISAGREE = 0.30
    try:
        cands = weather.WeatherStrategy().scan(fc)
        assert all(abs(c.model_prob - c.market_prob) <= 0.30 for c in cands), cands
        print("candidates with cap:", [(c.ticker[-2:], c.side, c.model_prob, c.market_prob) for c in cands])
    finally:
        config.MAX_DISAGREE = old


def test_fill_capped_to_book_size():
    off = nws.CITIES["CHI"][4]
    day = (datetime.now(timezone.utc) + timedelta(hours=off)).date() + timedelta(days=4)
    start, _ = nws.climate_day_bounds_utc(day, off)
    nws.hourly_forecast = lambda code: [(start + timedelta(hours=h), 40 + (16 if h == 15 else h % 10)) for h in range(24)]
    fc = FakeClient(day)
    for m in fc.ms.values():
        m["yes_ask_size_fp"] = "3.00"      # only 3 YES for sale
        m["yes_bid_size_fp"] = "4.00"      # => only 4 NO for sale
    assert engine.run_once(fc, [weather.WeatherStrategy()]) == 1
    p = db.open_positions("paper")[0]
    assert p["count"] <= 4, p["count"]
    print("capped fill:", p["side"], p["count"])
    # paper exit blocked when buyers can't absorb the position
    m = fc.ms[p["ticker"]]
    jump = int((p["entry_price"] + config.TAKE_PROFIT + 0.05) * 100)
    m["no_bid"], m["no_ask"], m["yes_bid"], m["yes_ask"] = jump, jump + 2, 100 - jump - 2, 100 - jump
    m["yes_ask_size_fp"] = "1.00"          # only 1 NO buyer
    m["yes_bid_size_fp"] = "1.00"
    engine.run_once(fc, [weather.WeatherStrategy()])
    assert db.open_positions("paper")[0]["status"] == "open"


def test_book_walk_multi_level():
    from app.broker import walk
    from app.kalshi import book_asks, parse_orderbook
    book = parse_orderbook({"orderbook_fp": {
        "yes_dollars": [["0.30", "5.00"], ["0.40", "5.00"]],
        "no_dollars": [["0.50", "10.00"], ["0.55", "4.00"], ["0.58", "3.00"]]}})
    asks = book_asks(book, "yes")              # 1 - NO bids, cheapest first
    assert asks[0] == (0.42, 3.0) and asks[1] == (0.45, 4.0), asks
    got, avg, fee, used = walk(asks, 12, limit=0.46, buying=True)
    assert got == 7 and used == [(0.42, 3), (0.45, 4)], (got, used)   # stops before 0.50
    assert abs(avg - (0.42 * 3 + 0.45 * 4) / 7) < 1e-4
    got, avg, fee, used = walk(asks, 12, limit=0.50, buying=True)
    assert got == 12 and used[-1] == (0.50, 5)
    print("walk: 12 contracts over 3 levels, avg", avg, "fee", fee)


def test_wrong_station_guard():
    ms = [{"ticker": "KXHIGHTEST-26NOV20-L", "strike_type": "less", "cap_strike": 70, "yes_ask": 5, "yes_bid": 3}]
    ms += [{"ticker": f"KXHIGHTEST-26NOV20-B{f}", "strike_type": "between", "floor_strike": f, "cap_strike": f + 1,
            "yes_ask": a, "yes_bid": a - 2} for f, a in ((70, 20), (72, 50), (74, 20))]
    ms += [{"ticker": "KXHIGHTEST-26NOV20-G", "strike_type": "greater", "floor_strike": 75, "yes_ask": 5, "yes_bid": 3}]
    c = weather.market_center(ms)
    assert 71 < c < 74, c
    d = weather.HighTempDist(60.0, 2.0)          # we think ~60F, market thinks ~72F
    db.log("guard-test-marker")
    assert weather.WeatherStrategy()._evaluate_event("KXHIGHTEST-26NOV20", ms, d) == []
    with db.conn() as conn:
        last = conn.execute("SELECT msg FROM events ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert "market centers" in last, last


def test_precap_excluded_and_summary():
    db.insert_position(dict(mode="paper", strategy="weather", ticker="T-1", event_ticker="E-PRE",
                            title="t", side="no", count=1, entry_price=0.5, entry_fee=0.01,
                            model_prob=0.9, market_prob=0.5, edge=0.3, opened_at=db.now(),
                            status="closed", pnl=-100.0, closed_at=db.now(), outcome=0,
                            notes="x | PRE-CAP TEST"))
    s = gate.stats("paper", "weather")
    assert s["pnl"] > -50, s                # the -$100 test trade is not counted
    from app import summary
    subj, body = summary.build()
    assert "Weather" in body and "go-live" in body
    print("summary subject:", subj)


def test_exit_rules():
    from app.engine import decide_exit
    pos = {"entry_price": 0.40, "count": 10, "entry_fee": 0.17}
    # winner, model still sees big edge -> HOLD (let it run), even though up 15c
    assert decide_exit(pos, 0.55, 0.85) == (None, None)
    # market caught up to our fair value -> take the win
    assert decide_exit(pos, 0.86, 0.85)[0] == "caught_up"
    # up 12c+ but our edge is nearly gone -> backup take-profit
    assert decide_exit(pos, 0.53, 0.56)[0] in ("take_profit", "caught_up")
    # down 15c and model no longer sees a big edge -> stop
    assert decide_exit(pos, 0.25, 0.30)[0] == "stop_edge_gone"
    # down 15c but model (freshly updated) still sees 10c+ edge -> hold
    assert decide_exit(pos, 0.25, 0.45) == (None, None)
    # lost half its value but model still sees a big edge -> HOLD (blind hard stop is off)
    assert decide_exit(pos, 0.19, 0.90) == (None, None)
    # ...and the optional hard stop never fires on a cheap longshot even if switched on
    old = config.HARD_STOP
    config.HARD_STOP = 0.5
    try:
        assert decide_exit(pos, 0.19, 0.90)[0] == "stop_hard"
        cheap = {"entry_price": 0.15, "count": 20, "entry_fee": 0.05}
        assert decide_exit(cheap, 0.06, 0.30) == (None, None)
    finally:
        config.HARD_STOP = old
    # unknown fair value (mentions during the event), down 15c -> stop
    assert decide_exit(pos, 0.24, None)[0] == "stop_edge_gone"
    print("exit rules ok")


def test_no_fade_favorite():
    ev = "KXHIGHTEST-26NOV21"
    mk = lambda t, st, fl, cp, a: {"ticker": f"{ev}-{t}", "event_ticker": ev, "strike_type": st,
                                   "floor_strike": fl, "cap_strike": cp, "yes_ask": a, "yes_bid": a - 2,
                                   "no_ask": 102 - a, "no_bid": 100 - a, "volume": 500, "status": "active"}
    ms = [mk("L", "less", None, 60, 5), mk("B60", "between", 60, 61, 20), mk("B62", "between", 62, 63, 45),
          mk("B64", "between", 64, 65, 40), mk("G", "greater", 65, None, 25)]
    d = weather.HighTempDist(65.0, 0.8)        # model says the crowd favorite (62-63) is very unlikely
    old = config.MAX_DISAGREE, config.MAX_MEAN_GAP
    config.MAX_DISAGREE, config.MAX_MEAN_GAP = 1.0, 99
    try:
        config.NO_FADE_FAVORITE = False
        unguarded = weather.WeatherStrategy()._evaluate_event(ev, ms, d)
        config.NO_FADE_FAVORITE = True
        guarded = weather.WeatherStrategy()._evaluate_event(ev, ms, d)
    finally:
        config.MAX_DISAGREE, config.MAX_MEAN_GAP = old
    print("without rule:", [(x.ticker[-3:], x.side) for x in unguarded],
          "with rule:", [(x.ticker[-3:], x.side) for x in guarded])
    assert any(x.side == "no" and x.ticker.endswith("B62") for x in unguarded)   # the bet it used to make
    assert not any(x.side == "no" and x.ticker.endswith("B62") for x in guarded)


def test_daily_forecast_and_calibration():
    from app import calibrate
    off = nws.CITIES["CHI"][4]
    day = (datetime.now(timezone.utc) + timedelta(hours=off)).date() + timedelta(days=1)
    start, _ = nws.climate_day_bounds_utc(day, off)
    hourly = [(start + timedelta(hours=h), 50.0 + (8 if h == 15 else 0)) for h in range(24)]   # hourly max 58
    now_lst = nws.lst_now(off).replace(tzinfo=None)
    d = weather.build_dist("CHI", day, hourly, None, now_lst, daily=61.0, bias=0.0)
    assert d.mu == 61.0 and d.source == "daily", (d.mu, d.source)          # daily beats hourly max
    d2 = weather.build_dist("CHI", day, hourly, None, now_lst, daily=None, bias=0.0)
    assert d2.mu == 58.0 and d2.source == "hourly"
    # calibration: record a forecast for a past day, settle it 3F hotter, learn
    past = (now_lst.date() - timedelta(days=1))
    ev = "KXHIGHCHI-" + past.strftime("%y%b%d").upper()
    calibrate.record_forecast(ev, "CHI", 58.0)

    class C:
        def event_markets(self, e):
            return [{"strike_type": "between", "floor_strike": 61, "cap_strike": 62, "result": "yes"},
                    {"strike_type": "between", "floor_strike": 59, "cap_strike": 60, "result": "no"}]
    assert calibrate.learn(C()) == 1
    b = calibrate.bias("CHI")
    assert 0.5 < b < 1.5, b                       # 3.5F miss, 1 day of data -> cautious ~0.9F shift
    d3 = weather.build_dist("CHI", day, hourly, None, now_lst, daily=61.0, bias=b)
    assert d3.mu > 61.0 and d3.raw_mu == 61.0
    print("calibration after 1 day:", b, "table:", calibrate.table())


def test_old_model_tagging_restarts_clock():
    from app import engine
    db.set_setting("model_version", 1)
    before = gate.stats("paper", "weather")["trades"]
    engine.tag_old_model()
    after = gate.stats("paper", "weather")["trades"]
    assert before > 0 and after == 0, (before, after)
    assert db.get_setting("model_version") == 2
    print(f"old-model tagging: {before} counted -> {after}")


def test_cheap_bets_capped():
    from app import risk
    n = risk.size(0.30, 0.13, "paper")              # big edge on a 13c longshot
    assert n * 0.13 <= config.CHEAP_MAX_STAKE + 0.01, n
    m = risk.size(0.80, 0.45, "paper")              # normal bet keeps the normal cap
    assert m * 0.45 > config.CHEAP_MAX_STAKE, m
    print(f"longshot: {n} contracts (${n*0.13:.2f}); normal: {m} contracts (${m*0.45:.2f})")


def test_strategy_budgets():
    from app import risk
    real_today = risk.realized_today
    risk.realized_today = lambda mode, path=None: 0.0        # isolate from earlier tests' fake losses
    db.insert_position(dict(mode="paper", strategy="weather", ticker="W-FULL", event_ticker="E-WFULL",
                            title="t", side="yes", count=100, entry_price=0.40, entry_fee=0.5,
                            model_prob=0.6, market_prob=0.4, edge=0.1, opened_at=db.now(), status="open",
                            notes=""))                       # $40.50 of weather -> weather budget full
    assert risk.can_open("E-NEW", "paper", strategy="weather")[0] is False
    ok, why = risk.can_open("E-NEW2", "paper", strategy="mentions")
    n = risk.size(0.80, 0.60, "paper", strategy="mentions")
    print("weather full; mentions can_open:", ok, why, "| contracts:", n)
    assert ok and n > 0                                      # mentions still gets its own money
    # weather far OVER its budget (legacy positions) must not block mentions either
    db.insert_position(dict(mode="paper", strategy="weather", ticker="W-OVER", event_ticker="E-WOVER",
                            title="t", side="yes", count=50, entry_price=0.40, entry_fee=0.3,
                            model_prob=0.6, market_prob=0.4, edge=0.1, opened_at=db.now(), status="open",
                            notes=""))                       # weather now ~$60.8 open
    r = risk.room("paper", strategy="mentions")
    print("weather over budget; mentions room:", round(r, 2))
    assert r >= config.STRATEGY_BUDGET["mentions"] - 0.01, r
    with db.conn() as c:
        c.execute("DELETE FROM positions WHERE ticker='W-OVER'")
    with db.conn() as c:
        c.execute("DELETE FROM positions WHERE ticker='W-FULL'")
    risk.realized_today = real_today


def test_live_gate_is_per_strategy():
    db.set_setting("mode", "live")
    assert gate.mode_for("weather") == "paper"              # armed, but no strategy has earned it
    assert gate.mode_for("mentions") == "paper"
    db.set_setting("mode", "paper")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
