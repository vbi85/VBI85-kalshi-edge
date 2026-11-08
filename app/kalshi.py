"""Thin Kalshi REST client. Public reads need no key; orders need a signed key."""
import base64
import time
import uuid
from urllib.parse import urlparse

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from . import config


class KalshiClient:
    def __init__(self, base_url=None, key_id=None, key_path=None):
        self.base_url = (base_url or config.BASE_URL).rstrip("/")
        self.prefix = urlparse(self.base_url).path          # /trade-api/v2
        self.key_id = key_id if key_id is not None else config.KALSHI_KEY_ID
        self._key = None
        path = key_path if key_path is not None else config.KALSHI_PRIVATE_KEY_PATH
        if path:
            with open(path, "rb") as fh:
                self._key = serialization.load_pem_private_key(fh.read(), password=None)
        self.s = requests.Session()

    # ---- signing --------------------------------------------------------
    @property
    def can_trade(self):
        return bool(self.key_id and self._key)

    def _headers(self, method, path):
        if not self.can_trade:
            return {}
        ts = str(int(time.time() * 1000))
        msg = (ts + method.upper() + self.prefix + path.split("?")[0]).encode()
        sig = self._key.sign(
            msg,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
        }

    def _req(self, method, path, params=None, body=None, signed=False):
        headers = {"Content-Type": "application/json"}
        if signed:
            headers.update(self._headers(method, path))
        for attempt in range(3):
            r = self.s.request(method, self.base_url + path, params=params, json=body,
                               headers=headers, timeout=20)
            if r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r.json() if r.content else {}
        r.raise_for_status()

    # ---- market data (public) ------------------------------------------
    def series_list(self, category=None):
        params = {"category": category} if category else None
        return self._req("GET", "/series", params=params).get("series", []) or []

    def markets(self, series_ticker=None, event_ticker=None, status="open", max_pages=20):
        out, cursor = [], None
        for _ in range(max_pages):
            params = {"limit": 200, "status": status}
            if series_ticker:
                params["series_ticker"] = series_ticker
            if event_ticker:
                params["event_ticker"] = event_ticker
            if cursor:
                params["cursor"] = cursor
            data = self._req("GET", "/markets", params=params)
            out.extend(data.get("markets", []))
            cursor = data.get("cursor")
            if not cursor:
                return out
        return out

    def market(self, ticker):
        return self._req("GET", f"/markets/{ticker}").get("market", {})

    # ---- trading (signed) ----------------------------------------------
    def balance(self):
        return self._req("GET", "/portfolio/balance", signed=True)

    def place_limit(self, ticker, side, action, count, price):
        """side: yes|no, action: buy|sell, price in dollars (0.01-0.99)."""
        cents = int(round(price * 100))
        body = {
            "ticker": ticker, "side": side, "action": action, "count": int(count),
            "type": "limit", "client_order_id": str(uuid.uuid4()),
            ("yes_price" if side == "yes" else "no_price"): cents,
        }
        return self._req("POST", "/portfolio/orders", body=body, signed=True).get("order", {})

    def get_order(self, order_id):
        return self._req("GET", f"/portfolio/orders/{order_id}", signed=True).get("order", {})

    def cancel(self, order_id):
        return self._req("DELETE", f"/portfolio/orders/{order_id}", signed=True)


# ---- helpers: Kalshi has shipped prices both as cents and as dollar strings ----
def px(market, field):
    """Return a price in dollars (float) or None. Handles yes_ask=42 and yes_ask_dollars='0.42'."""
    d = market.get(field + "_dollars")
    if d not in (None, ""):
        try:
            return float(d)
        except (TypeError, ValueError):
            pass
    v = market.get(field)
    if v in (None, ""):
        return None
    v = float(v)
    return v / 100.0 if v > 1 else v


def quotes(market):
    """yes_bid, yes_ask, no_bid, no_ask in dollars. Missing sides derived from the other."""
    yb, ya = px(market, "yes_bid"), px(market, "yes_ask")
    nb, na = px(market, "no_bid"), px(market, "no_ask")
    if na is None and yb is not None:
        na = round(1 - yb, 2)
    if nb is None and ya is not None:
        nb = round(1 - ya, 2)
    return yb, ya, nb, na
