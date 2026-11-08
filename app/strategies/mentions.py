"""Mentions edge: "Will <speaker> say <word> at <event>?"

Our probability comes from Kalshi's OWN settled history: how often this exact word
resolved YES in past events of the same series (weekly briefings, rallies, broadcasts).
Thin history is pulled toward the series' overall hit rate. Free, no transcripts needed.

Guards:
- Financial series (earnings calls, Fed/FOMC, CEOs, crypto) are excluded.
- No entries within MENTION_MIN_HOURS of close, or after a 24h price jump (event is live:
  the history-based number is stale once people are actually talking).
- Half the normal size and a higher edge bar. Weakest data of all strategies."""
import re
import time
from datetime import datetime, timezone

from .. import config, db
from ..fees import taker_fee
from ..kalshi import ask_size, px, quotes, volume
from .base import Candidate, Strategy


def norm_word(m):
    """The term being traded, normalized. Prefer explicit strike fields over titles."""
    raw = m.get("yes_sub_title") or ""
    cs = m.get("custom_strike")
    if not raw and isinstance(cs, dict):
        # skip hidden id fields (e.g. structured_*_id) so words match across events
        raw = " ".join(str(v) for k, v in cs.items() if "id" not in k.lower())
    if not raw:
        raw = m.get("subtitle") or ""
    if not raw:
        q = re.search(r'["“](.+?)["”]', m.get("title") or "")
        raw = q.group(1) if q else ""
    return re.sub(r"[^a-z0-9 ]+", "", raw.lower()).strip()


def _hours_to(ts):
    if not ts:
        return None
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (t - datetime.now(timezone.utc)).total_seconds() / 3600


def is_financial(*texts):
    blob = " ".join(t or "" for t in texts).upper()
    return any(w in blob for w in config.MENTION_EXCLUDE)


class History:
    """Hit rates per word for one series, from settled markets."""

    def __init__(self, settled):
        self.words = {}
        hits = n = 0
        for m in settled:
            res = (m.get("result") or "").lower()
            if res not in ("yes", "no"):
                continue                       # void / scalar -> ignore
            w = norm_word(m)
            if not w:
                continue
            h, c = self.words.get(w, (0, 0))
            self.words[w] = (h + (res == "yes"), c + 1)
            hits += res == "yes"
            n += 1
        self.base = (hits + 1) / (n + 2) if n else None

    def prob(self, word):
        """Shrunk hit rate, or None if not enough history for this word."""
        if word not in self.words or self.base is None:
            return None
        h, c = self.words[word]
        if c < config.MENTION_MIN_HISTORY:
            return None
        k = config.MENTION_PRIOR_WEIGHT
        return (h + k * self.base) / (c + k)

    def count(self, word):
        return self.words.get(word, (0, 0))


class MentionsStrategy(Strategy):
    name = "mentions"

    def __init__(self):
        self._hist = {}           # series_ticker -> (History, fetched_at)
        self._active = None       # (list of series with open markets, fetched_at)

    # ---- discovery ------------------------------------------------------
    def series(self, client):
        if config.MENTION_SERIES:
            return [s for s in config.MENTION_SERIES if not is_financial(s)]
        out = []
        try:
            for s in client.series_list():
                t, title = s.get("ticker", ""), s.get("title", "")
                looks_mention = ("MENTION" in t.upper() or re.search(r"\b(say|said|mention)", title, re.I))
                if looks_mention and not is_financial(t, title, s.get("category")):
                    out.append(t)
        except Exception as e:
            db.log(f"mentions: series discovery failed: {e}", "warn")
        return out

    def _fresh(self, ts):
        return time.time() - ts < config.MENTION_REFRESH_HOURS * 3600

    def history(self, client, series_ticker):
        h = self._hist.get(series_ticker)
        if not h or not self._fresh(h[1]):
            settled = client.markets(series_ticker=series_ticker, status="settled", max_pages=5)
            self._hist[series_ticker] = (History(settled), time.time())
        return self._hist[series_ticker][0]

    def active_series(self, client):
        """Series that currently have open markets. Full sweep only every few hours."""
        if self._active and self._fresh(self._active[1]):
            return self._active[0]
        act = []
        for st in self.series(client):
            try:
                if client.markets(series_ticker=st, status="open", max_pages=1):
                    act.append(st)
            except Exception as e:
                db.log(f"mentions {st}: {e}", "warn")
        db.log(f"mentions: {len(act)} active series (rechecked every {config.MENTION_REFRESH_HOURS:g}h)")
        self._active = (act, time.time())
        return act

    # ---- scanning -------------------------------------------------------
    def scan(self, client):
        out = []
        for st in self.active_series(client):
            try:
                live = client.markets(series_ticker=st, status="open")
                if not live:
                    continue
                hist = self.history(client, st)
            except Exception as e:
                db.log(f"mentions {st}: fetch failed: {e}", "warn")
                continue
            by_event = {}
            for m in live:
                if is_financial(m.get("title"), m.get("event_ticker"), m.get("yes_sub_title")):
                    continue
                by_event.setdefault(m.get("event_ticker"), []).append(m)
            for ev, ms in by_event.items():
                best = self._best_in_event(st, ev, ms, hist)
                if best:
                    out.append(best)
        return out

    def _too_late(self, m):
        hrs = _hours_to(m.get("close_time"))
        if hrs is not None and hrs < config.MENTION_MIN_HOURS:
            return True
        prev = px(m, "previous_price")
        last = px(m, "last_price")
        return prev is not None and last is not None and abs(last - prev) > config.MENTION_MAX_MOVE

    def _best_in_event(self, st, ev, markets, hist):
        best = None
        for m in markets:
            if m.get("result") or (m.get("status") or "active") not in ("active", "open"):
                continue
            if self._too_late(m):
                continue
            if volume(m) < config.MIN_VOLUME:
                continue
            word = norm_word(m)
            p = hist.prob(word)
            if p is None:
                continue
            yb, ya, nb, na = quotes(m)
            for side, prob, ask, bid in (("yes", p, ya, yb), ("no", 1 - p, na, nb)):
                if ask is None or not (config.MIN_PRICE <= ask <= config.MAX_PRICE):
                    continue
                edge = prob - (ask + taker_fee(ask, 10) / 10)
                if edge < config.MENTION_MIN_EDGE:
                    continue
                h, c = hist.count(word)
                mid = (ask + bid) / 2 if bid is not None else ask
                if abs(prob - mid) > config.MAX_DISAGREE:
                    continue      # huge disagreement = our model is probably wrong
                cand = Candidate(self.name, m["ticker"], ev, m.get("title") or word, side,
                                 round(prob, 4), ask, round(mid, 4),
                                 notes=f'"{word}" said {h}/{c} past events',
                                 max_stake=config.MENTION_MAX_PER_TRADE,
                                 min_edge=config.MENTION_MIN_EDGE,
                                 ask_size=ask_size(m, side))
                if best is None or edge > best[0]:
                    best = (edge, cand)
        return best[1] if best else None

    # ---- exits ----------------------------------------------------------
    def fair_value(self, position, market):
        """Pre-event history only. Once the event is live, return None: hold to settle
        (or take profit) instead of trusting a stale number."""
        if self._too_late(market):
            return None
        st = position["ticker"].split("-")[0]
        h = self._hist.get(st)
        if h is None:
            return None
        hist = h[0]
        p = hist.prob(norm_word(market))
        if p is None:
            return None
        return p if position["side"] == "yes" else 1 - p
