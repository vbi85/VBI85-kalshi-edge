"""Weather edge: NWS forecast + today's observed high  ->  probability for every Kalshi
daily-high bracket  ->  trade only where the market price is clearly wrong after fees.

Model: final high T = max(observed_so_far, Y),  Y ~ Normal(forecast_remaining_max, sigma).
Sigma shrinks as the afternoon passes. Temps settle as whole degrees F, so we use
continuity correction. Paper mode exists to prove (or kill) this calibration."""
import math
import re
from datetime import date, timedelta, timezone

from .. import config, db, nws
from ..kalshi import ask_size, quotes, volume
from ..fees import taker_fee
from .base import Candidate, Strategy

MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


def ticker_date(event_ticker):
    """KXHIGHNY-26NOV09 -> date(2026,11,9). Use the ticker date, never close_time."""
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})", event_ticker or "")
    if not m:
        return None
    return date(2000 + int(m.group(1)), MONTHS[m.group(2)], int(m.group(3)))


def phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


class HighTempDist:
    """P(final high <= k) for integer k."""

    def __init__(self, mu, sigma, floor=None):
        self.mu, self.sigma, self.floor = mu, max(sigma, 0.5), floor

    def cdf(self, k):
        if self.floor is not None and k < self.floor:
            return 0.0
        return phi((k + 0.5 - self.mu) / self.sigma)

    def prob_market(self, m):
        """Probability YES for a Kalshi bracket using its strike fields."""
        st = (m.get("strike_type") or "").lower()
        fl, cp = m.get("floor_strike"), m.get("cap_strike")
        if st == "between" and fl is not None and cp is not None:
            return self.cdf(math.floor(cp)) - self.cdf(math.ceil(fl) - 1)
        if st == "greater" and fl is not None:          # YES if high > floor
            return 1 - self.cdf(math.floor(fl))
        if st == "less" and cp is not None:             # YES if high < cap
            return self.cdf(math.ceil(cp) - 1)
        return None


def build_dist(code, day, hourly, obs_max, now_lst):
    """Forecast distribution for the climate day `day` (local standard time)."""
    off = nws.CITIES[code][4]
    start, end = nws.climate_day_bounds_utc(day, off)
    now_utc = (now_lst - timedelta(hours=off)).replace(tzinfo=timezone.utc)
    remaining = [t for (ts, t) in hourly if start <= ts < end and ts >= now_utc]
    same_day = now_lst.date() == day
    if not remaining and obs_max is None:
        return None
    if same_day:
        heat_hours_left = max(0.0, 16 - (now_lst.hour + now_lst.minute / 60))
        if heat_hours_left <= 0:
            # past the afternoon peak: the observed high is very nearly final
            sigma = 0.4
        else:
            sigma = max(0.8, config.SIGMA_SAME_DAY * min(1.0, heat_hours_left / 8))
        mu = max(remaining) if remaining else obs_max - 3
        return HighTempDist(mu, sigma, floor=obs_max)
    if not remaining:
        return None
    return HighTempDist(max(remaining), config.SIGMA_NEXT_DAY)


def market_center(markets):
    """The temperature the market expects, from bracket mid-prices."""
    tot = w = 0.0
    for m in markets:
        yb, ya, _, _ = quotes(m)
        if ya is None:
            continue
        mid = (ya + (yb or 0)) / 2
        st, fl, cp = (m.get("strike_type") or "").lower(), m.get("floor_strike"), m.get("cap_strike")
        if st == "between" and fl is not None and cp is not None:
            t = (fl + cp) / 2
        elif st == "greater" and fl is not None:
            t = fl + 2
        elif st == "less" and cp is not None:
            t = cp - 2
        else:
            continue
        tot += t * mid
        w += mid
    return tot / w if w > 0.2 else None


class WeatherStrategy(Strategy):
    name = "weather"

    def __init__(self):
        self._dist_cache = {}   # event_ticker -> dist (per scan)

    def _dist_for_event(self, code, event_ticker):
        if event_ticker in self._dist_cache:
            return self._dist_cache[event_ticker]
        day = ticker_date(event_ticker)
        off = nws.CITIES[code][4]
        now_lst = nws.lst_now(off).replace(tzinfo=None)
        dist = None
        if day and day >= now_lst.date():
            hourly = nws.hourly_forecast(code)
            obs = nws.observed_max_f(code, day) if day == now_lst.date() else None
            dist = build_dist(code, day, hourly, obs, now_lst)
        self._dist_cache[event_ticker] = dist
        return dist

    def scan(self, client):
        self._dist_cache = {}
        out = []
        cities = list(nws.CITIES) if config.WEATHER_CITIES in (["ALL"], ["all"]) else config.WEATHER_CITIES
        for code in cities:
            if code not in nws.CITIES:
                continue
            series = nws.CITIES[code][0]
            try:
                markets = client.markets(series_ticker=series, status="open")
            except Exception as e:
                db.log(f"weather {code}: market fetch failed: {e}", "warn")
                continue
            by_event = {}
            for m in markets:
                by_event.setdefault(m.get("event_ticker"), []).append(m)
            for ev, ms in by_event.items():
                try:
                    dist = self._dist_for_event(code, ev)
                except Exception as e:
                    db.log(f"weather {ev}: forecast failed: {e}", "warn")
                    continue
                if not dist:
                    continue
                out.extend(self._evaluate_event(ev, ms, dist))
        return out

    def _evaluate_event(self, ev, markets, dist):
        probs = {m["ticker"]: dist.prob_market(m) for m in markets}
        known = [p for p in probs.values() if p is not None]
        # Sanity check: brackets of one event must add to ~100%. If not, our reading of the
        # strike fields is wrong for this event -> refuse to trade it.
        if len(known) == len(markets) and len(markets) >= 3 and abs(sum(known) - 1) > 0.10:
            db.log(f"weather {ev}: bracket probs sum to {sum(known):.2f}, skipping", "warn")
            return []
        implied = market_center(markets)
        center = max(dist.mu, dist.floor) if dist.floor is not None else dist.mu
        if implied is not None and abs(implied - center) > config.MAX_MEAN_GAP:
            db.log(f"weather {ev}: market centers {implied:.0f}F, we say {center:.0f}F, skipping", "warn")
            return []
        best = None
        for m in markets:
            p = probs[m["ticker"]]
            if p is None or m.get("result") or (m.get("status") or "active") not in ("active", "open"):
                continue
            if volume(m) < config.MIN_VOLUME:
                continue
            yb, ya, nb, na = quotes(m)
            for side, prob, ask, bid in (("yes", p, ya, yb), ("no", 1 - p, na, nb)):
                if ask is None or not (config.MIN_PRICE <= ask <= config.MAX_PRICE):
                    continue
                edge = prob - (ask + taker_fee(ask, 10) / 10)
                if edge < config.MIN_EDGE:
                    continue
                mid = (ask + bid) / 2 if bid is not None else ask
                if abs(prob - mid) > config.MAX_DISAGREE:
                    continue      # huge disagreement = our model is probably wrong
                c = Candidate(self.name, m["ticker"], ev, m.get("title") or m.get("subtitle") or m["ticker"],
                              side, round(prob, 4), ask, round(mid, 4),
                              notes=f"mu={dist.mu:.1f} sd={dist.sigma:.1f} obs={dist.floor}",
                              ask_size=ask_size(m, side))
                if best is None or edge > best[0]:
                    best = (edge, c)
        return [best[1]] if best else []

    def fair_value(self, position, market):
        code = next((c for c, v in nws.CITIES.items() if position["ticker"].startswith(v[0] + "-")), None)
        if not code:
            return None
        dist = self._dist_for_event(code, position["event_ticker"])
        if not dist:
            return None
        p = dist.prob_market(market)
        if p is None:
            return None
        return p if position["side"] == "yes" else 1 - p
