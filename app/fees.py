"""Kalshi fee math. Fees are why quick scalps lose, so every decision runs through here."""
import math
from . import config


def taker_fee(price, count, rate=None):
    """Total fee in dollars: round_up(rate * count * P * (1-P)) to the next cent."""
    rate = config.FEE_RATE if rate is None else rate
    raw = rate * count * price * (1 - price)
    return math.ceil(round(raw * 100, 6)) / 100.0


def all_in_cost_per_contract(price, count):
    return price + taker_fee(price, count) / max(count, 1)


def round_trip_breakeven(entry, count):
    """Exit price needed just to break even after paying fees in and out."""
    p = entry
    for _ in range(50):
        need = entry + (taker_fee(entry, count) + taker_fee(p, count)) / count
        if abs(need - p) < 1e-6:
            break
        p = need
    return math.ceil(p * 100) / 100.0
