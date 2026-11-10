"""Self-correction for the weather model.

Every evening the bot writes down its forecast for tomorrow in each city. Each morning,
Kalshi publishes which bracket actually won. The difference (actual - forecast) is that
city's miss. The bot shifts future forecasts by the city's typical miss, cautiously:

  correction = sum(misses) / (number of days + PRIOR_DAYS)     capped at +/- MAX_SHIFT

With 1 day of data a 3F miss moves the forecast only 0.75F. After 2 weeks of consistent
misses it moves nearly the full amount. Random noise averages out; real bias does not."""
from datetime import timedelta

from . import db, nws

PRIOR_DAYS = 3        # how many "zero-miss" days we pretend to have before any data
MAX_SHIFT = 3.0       # never shift a city by more than this many degrees
KEEP = 30             # remember the last 30 misses per city


def _day_of(event_ticker):
    from .strategies.weather import ticker_date
    return ticker_date(event_ticker)


def record_forecast(event_ticker, code, raw_mu, path=None):
    """Remember tomorrow's raw forecast (overwritten each scan, so the last one before
    the day starts is what gets graded)."""
    db.set_setting(f"fc:{event_ticker}", {"code": code, "mu": round(raw_mu, 1)}, path)


def bias(code, path=None):
    hist = db.get_setting(f"bias:{code}", [], path) or []
    if not hist:
        return 0.0
    shift = sum(h["miss"] for h in hist) / (len(hist) + PRIOR_DAYS)
    return max(-MAX_SHIFT, min(MAX_SHIFT, round(shift, 2)))


def _actual_from_winner(m):
    st = (m.get("strike_type") or "").lower()
    fl, cp = m.get("floor_strike"), m.get("cap_strike")
    if st == "between" and fl is not None and cp is not None:
        return (fl + cp) / 2
    if st == "greater" and fl is not None:
        return fl + 1.5
    if st == "less" and cp is not None:
        return cp - 1.5
    return None


def learn(client, path=None):
    """Grade yesterday's (and older) forecasts against Kalshi's winning bracket."""
    with db.conn(path) as c:
        keys = [r[0] for r in c.execute("SELECT k FROM settings WHERE k LIKE 'fc:%'")]
    graded = 0
    for k in keys:
        ev = k[3:]
        day = _day_of(ev)
        rec = db.get_setting(k, None, path)
        if not day or not rec or rec.get("code") not in nws.CITIES:
            db.set_setting(k, None, path)
            continue
        today = nws.lst_now(nws.CITIES[rec["code"]][4]).date()
        if day >= today:
            continue                                   # not over yet
        try:
            ms = client.event_markets(ev)
        except Exception:
            continue
        win = [m for m in ms if (m.get("result") or "").lower() == "yes"]
        if not win:
            if (today - day) > timedelta(days=4):      # never settled / delisted: give up
                _drop(k, path)
            continue
        actual = _actual_from_winner(win[0])
        if actual is None:
            _drop(k, path)
            continue
        miss = round(actual - rec["mu"], 1)
        hist = (db.get_setting(f"bias:{rec['code']}", [], path) or [])
        hist = [h for h in hist if h.get("day") != day.isoformat()] + [
            {"day": day.isoformat(), "fc": rec["mu"], "actual": actual, "miss": miss}]
        db.set_setting(f"bias:{rec['code']}", hist[-KEEP:], path)
        _drop(k, path)
        graded += 1
        db.log(f"calibrate {rec['code']} {day}: forecast {rec['mu']}F, actual ~{actual}F, "
               f"miss {miss:+.1f}F -> city correction now {bias(rec['code'], path):+.1f}F", path=path)
    return graded


def _drop(k, path=None):
    with db.conn(path) as c:
        c.execute("DELETE FROM settings WHERE k=?", (k,))


def table(path=None):
    """[(city code, days graded, avg miss, current correction)] for dashboards/emails."""
    out = []
    for code in nws.CITIES:
        hist = db.get_setting(f"bias:{code}", [], path) or []
        if hist:
            out.append((code, len(hist), round(sum(h["miss"] for h in hist) / len(hist), 1), bias(code, path)))
    return out
