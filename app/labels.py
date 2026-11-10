"""Human-readable names for positions: city, day, bracket, and what the bet means."""
import math
import re

from . import nws
from .strategies.weather import ticker_date

CITY_NAMES = {
    "NYC": "New York", "CHI": "Chicago", "MIA": "Miami", "AUS": "Austin", "LAX": "Los Angeles",
    "DEN": "Denver", "PHIL": "Philadelphia", "PHX": "Phoenix", "HOU": "Houston",
    "MIN": "Minneapolis", "OKC": "Oklahoma City", "SFO": "San Francisco", "DC": "Washington DC",
    "BOS": "Boston", "DAL": "Dallas", "SEA": "Seattle", "LV": "Las Vegas", "ATL": "Atlanta",
    "NOLA": "New Orleans", "SAN": "San Diego", "SDF": "Louisville", "EWR": "Newark",
}


def city_for(ticker):
    for code, v in nws.CITIES.items():
        if (ticker or "").startswith(v[0] + "-"):
            return CITY_NAMES.get(code, code)
    return None


def bracket_text(market=None, ticker=""):
    """'61–62°F', '62°F or higher', '54°F or lower' from strike fields (or the ticker)."""
    m = market or {}
    st, fl, cp = (m.get("strike_type") or "").lower(), m.get("floor_strike"), m.get("cap_strike")
    if st == "between" and fl is not None and cp is not None:
        return f"{math.ceil(fl)}–{math.floor(cp)}°F"
    if st == "greater" and fl is not None:
        return f"{math.floor(fl) + 1}°F or higher"
    if st == "less" and cp is not None:
        return f"{math.ceil(cp) - 1}°F or lower"
    b = re.search(r"-B(\d+(?:\.\d+)?)$", ticker or "")
    if b:
        x = float(b.group(1))
        return f"{math.floor(x)}–{math.ceil(x)}°F"
    t = re.search(r"-T(\d+(?:\.\d+)?)$", ticker or "")
    return f"{t.group(1)}°F line" if t else ""


def weather_title(market=None, ticker=""):
    ticker = ticker or (market or {}).get("ticker", "")
    city = city_for(ticker) or "?"
    d = ticker_date(ticker)
    day = d.strftime("%a %b %-d") if d else ""
    return f"{city} · {day} · high {bracket_text(market, ticker)}"


def card(p):
    """Everything the dashboard shows for one position, in plain words."""
    notes = p.get("notes") or ""
    cost = p["entry_price"] * p["count"] + (p["entry_fee"] or 0)
    c = {
        "strategy": p["strategy"], "pnl": p.get("pnl"),
        "test": "TEST" if "PRE-CAP" in notes else ("OLD MODEL" if "MODEL-V1" in notes else ""),
        "status": p["status"], "exit_reason": p.get("exit_reason"), "closed_at": p.get("closed_at"),
        "money": f'{p["count"]} × {p["entry_price"] * 100:.0f}¢ = ${cost:.2f}',
        "odds": f'our odds {p["model_prob"] * 100:.0f}% vs market {(p["market_prob"] or 0) * 100:.0f}%',
        "side": p["side"].upper(),
    }
    if p["strategy"] == "weather":
        t = p["title"] if "·" in (p["title"] or "") else weather_title(None, p["ticker"])
        parts = t.split(" · ")
        c["head"] = " · ".join(parts[:2])
        c["what"] = parts[2].replace("high ", "High ") if len(parts) > 2 else ""
        c["bet"] = "it WILL land here" if p["side"] == "yes" else "it will NOT land here"
        mu = re.search(r"mu=([\d.]+)", notes)
        obs = re.search(r"obs=(\d+)", notes)
        extra = []
        bias = re.search(r"bias=([+-][\d.]+)", notes)
        if mu:
            b = float(bias.group(1)) if bias else 0.0
            extra.append(f"forecast {float(mu.group(1)):.0f}°F" + (f" (incl. {b:+.0f}°F city correction)" if abs(b) >= 0.5 else ""))
        if obs:
            extra.append(f"already hit {obs.group(1)}°F today")
        c["extra"] = " · ".join(extra)
    else:
        word = re.search(r'"(.+?)" said (\d+)/(\d+)', notes)
        c["head"] = p["title"] or p["ticker"]
        c["what"] = f'Word: "{word.group(1)}"' if word else ""
        c["bet"] = "they WILL say it" if p["side"] == "yes" else "they will NOT say it"
        c["extra"] = f"said in {word.group(2)} of the last {word.group(3)} events" if word else ""
    return c
