"""Every strategy (weather, sports, mentions) plugs into the engine through this shape."""
from dataclasses import dataclass
from typing import Optional


@dataclass
class Candidate:
    strategy: str
    ticker: str
    event_ticker: str
    title: str
    side: str            # yes | no
    model_prob: float    # our probability that this SIDE wins
    ask: float           # price to buy this side, dollars
    market_prob: float   # market's implied probability for this side (mid)
    notes: str = ""
    max_stake: Optional[float] = None   # per-strategy cap (dollars); None = global
    min_edge: Optional[float] = None    # per-strategy edge floor; None = global


class Strategy:
    name = "base"
    enabled = True

    def scan(self, client):
        """Return a list of Candidate trades (already filtered for edge)."""
        return []

    def fair_value(self, position, market):
        """Our current probability that the position's side wins, or None if unknown."""
        return None
