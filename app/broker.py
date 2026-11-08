"""Paper and Live brokers share one interface so strategies never know the difference."""
import time

from .fees import taker_fee


class Fill:
    def __init__(self, count, price, fee, order_id=""):
        self.count, self.price, self.fee, self.order_id = count, price, fee, order_id


class PaperBroker:
    """Fills instantly at the quoted ask (buys) or bid (sells), and pays the real taker fee.
    Deliberately pessimistic: no price improvement, no maker rebates."""
    mode = "paper"

    def buy(self, ticker, side, count, ask):
        return Fill(count, ask, taker_fee(ask, count), "paper")

    def sell(self, ticker, side, count, bid):
        return Fill(count, bid, taker_fee(bid, count), "paper")


class LiveBroker:
    """Real orders. Limit order at the quoted price, wait briefly, cancel any remainder."""
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
        filled = order.get("fill_count")
        if filled is None:
            filled = count - int(order.get("remaining_count", count))
        filled = int(filled)
        return Fill(filled, price, taker_fee(price, filled) if filled else 0.0, oid)

    def buy(self, ticker, side, count, ask):
        return self._work(ticker, side, "buy", count, ask)

    def sell(self, ticker, side, count, bid):
        return self._work(ticker, side, "sell", count, bid)
