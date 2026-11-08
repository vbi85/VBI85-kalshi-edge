"""Offline tests: fake Kalshi + fake NWS, full paper cycle (enter -> settle -> gate)."""
import os
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ["DB_PATH"] = tempfile.mktemp(suffix=".db")
os.environ["WEATHER_CITIES"] = "CHI"

from app import config, db, engine, fees, gate, nws  # noqa: E402
from app.strategies import mentions, weather  # noqa: E402


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
    assert p["status"] == "closed" and p["exit_reason"] == "take_profit" and p["pnl"] > 0, p
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
