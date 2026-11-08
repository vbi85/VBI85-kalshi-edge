"""Paper and Live brokers share one interface so strategies never know the difference."""
import time

from .fees import taker_fee
from .kalshi import book_asks, book_bids


class Fill:
    def __init__(self, count, price, fee, order_id="", levels=None):
        self.count, self.price, self.fee, self.order_id = count, price, fee, order_id
        self.levels = levels or []          # [(price, qty), ...] actually taken


def walk(levels, want, limit, buying):
    """Take liquidity level by level. Buying: cheapest first, stop above `limit`.
    Selling: best first, stop below `limit`. Returns (count, avg_price, fee, levels_used)."""
    got, cash, fee, used = 0, 0.0, 0.0, []
    for price, qty in levels:
        if (buying and price > limit + 1e-9) or (not buying and price < limit - 1e-9):
            break
        take = int(min(qty, want - got))
        if take <= 0:
            continue
        got += take
        cash += take * price
        fee += taker_fee(price, take)
        used.append((price, take))
        if got >= want:
            break
    return got, (round(cash / got, 4) if got else 0.0), round(fee, 2), used


class PaperBroker:
    """Simulates real fills against Kalshi's live order book: it takes the best price first,
    then the next level, and so on, stopping where the trade would no longer be worth it.
    Pays the real taker fee on every level. No price improvement, no maker rebates."""
    mode = "paper"

    def __init__(self, client):
        self.c = client

    def buy(self, ticker, side, count, ask, max_price=None):
        try:
            book = self.c.orderbook(ticker)
        except Exception:
            return Fill(0, 0.0, 0.0, "paper")
        got, avg, fee, used = walk(book_asks(book, side), count, max_price or ask, buying=True)
        return Fill(got, avg, fee, "paper", used)

    def sell(self, ticker, side, count, bid, min_price=None):
        """All-or-nothing: if buyers can't take the whole position above min_price, hold."""
        try:
            book = self.c.orderbook(ticker)
        except Exception:
            return Fill(0, 0.0, 0.0, "paper")
        got, avg, fee, used = walk(book_bids(book, side), count, min_price if min_price is not None else bid,
                                   buying=False)
        if got < count:
            return Fill(0, 0.0, 0.0, "paper")
        return Fill(got, avg, fee, "paper", used)


class LiveBroker:
    """Real orders. A limit order at the worst acceptable price sweeps the book up to it;
    whatever is left after a short wait is cancelled."""
    mode = "live"

    def __init__(self, client, wait_seconds=20):
        if not client.can_trade:
            raise RuntimeError("Live mode needs KALSHI_KEY_ID and KALSHI_PRIVATE_KEY_PATH")
        self.c, self.wait = client, wait_seconds

    def _work(self, ticker, side, action, count, price):
        order = self.c.place_limit(ticker, side, action, count, price)
        oid = order.get("order_id", "")
        deadline = time.time() + self.wait
        while time.time() < deadline and order.get("status") not in ("executed", "canceled"):
            time.sleep(2)
            order = self.c.get_order(oid)
        if order.get("status") == "resting":
            try:
                self.c.cancel(oid)
            except Exception:
                pass
            order = self.c.get_order(oid)
        filled = order.get("fill_count_fp", order.get("fill_count"))
        if filled is None:
            filled = count - float(order.get("remaining_count_fp", order.get("remaining_count", count)))
        filled = int(float(filled))
        # average fill price if Kalshi reports cost, else the (worst-case) limit price
        cost = order.get("taker_fill_cost_dollars") or order.get("maker_fill_cost_dollars")
        avg = round(float(cost) / filled, 4) if cost and filled else price
        return Fill(filled, avg, taker_fee(avg, filled) if filled else 0.0, oid)

    def buy(self, ticker, side, count, ask, max_price=None):
        return self._work(ticker, side, "buy", count, max_price or ask)

    def sell(self, ticker, side, count, bid, min_price=None):
        return self._work(ticker, side, "sell", count, min_price if min_price is not None else bid)
