"""PHASE 2 — Sports (not trading yet).

Plan: the edge is NOT our own opinion on games (sharp money beats that). The edge is
price lag: compare each Kalshi game-winner price to the vig-free consensus line from
sportsbooks (The Odds API, free tier) and buy only when Kalshi is 6+ cents off consensus
after fees. Exit when Kalshi catches up to consensus, or hold to the final whistle.

Turn on after Weather has 2+ weeks of paper results."""
from .base import Strategy


class SportsStrategy(Strategy):
    name = "sports"
    enabled = False
