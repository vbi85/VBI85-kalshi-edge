"""Health check: does the bot see live markets, and what does the model think of them?
Run: venv/bin/python tools/diagnose.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config, nws  # noqa: E402
from app.kalshi import KalshiClient, quotes  # noqa: E402
from app.strategies.mentions import MentionsStrategy  # noqa: E402
from app.strategies.weather import WeatherStrategy  # noqa: E402

c = KalshiClient()
w = WeatherStrategy()
print("== WEATHER ==")
for code in (list(nws.CITIES) if config.WEATHER_CITIES in (["ALL"], ["all"]) else config.WEATHER_CITIES):
    series = nws.CITIES[code][0]
    try:
        ms = c.markets(series_ticker=series, status="open")
    except Exception as e:
        print(f"{code} {series}: ERROR {e}")
        continue
    evs = sorted({m["event_ticker"] for m in ms})
    print(f"{code} {series}: {len(ms)} open markets, events {evs}")
    if not evs:
        continue
    ev = evs[0]
    try:
        d = w._dist_for_event(code, ev)
    except Exception as e:
        print(f"   forecast ERROR {e}")
        continue
    if not d:
        print("   no forecast for this day")
        continue
    tot = 0
    for m in sorted([m for m in ms if m["event_ticker"] == ev], key=lambda m: m.get("floor_strike") or -999):
        p = d.prob_market(m)
        yb, ya, nb, na = quotes(m)
        tot += p or 0
        print(f"   {m.get('strike_type','?'):8} fl={m.get('floor_strike')} cap={m.get('cap_strike')} "
              f"model={p if p is None else round(p, 2)} yes_ask={ya} no_ask={na} vol={m.get('volume')}")
    print(f"   forecast high {d.mu:.1f}F sd {d.sigma:.1f} observed {d.floor}  | model sum {tot:.2f}")

print("\n== MENTIONS ==")
s = MentionsStrategy()
series = s.series(c)
print(f"{len(series)} mention series found (financial excluded)")
for st in series[:15]:
    try:
        live = c.markets(series_ticker=st, status="open")
        h = s.history(c, st) if live else None
    except Exception as e:
        print(f"  {st}: ERROR {e}")
        continue
    usable = sum(1 for m in live if h and h.prob(__import__('app.strategies.mentions', fromlist=['x']).norm_word(m)) is not None)
    print(f"  {st}: {len(live)} open, {len(h.words) if h else 0} words with history, {usable} tradeable")
print("\nCandidates right now:", [(x.ticker, x.side, x.model_prob, x.ask) for x in s.scan(c)][:10])
print("Weather candidates:", [(x.ticker, x.side, x.model_prob, x.ask) for x in w.scan(c)][:10])
