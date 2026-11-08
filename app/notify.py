"""Everything outbound goes through your Google Sheet's Apps Script web app:
trade log rows, a heartbeat each loop, and emails sent from your own Gmail.
(DigitalOcean blocks direct email from droplets, so this is the reliable route.)"""
import time

import requests

from . import config, db


def _post(payload):
    if not (config.SHEETS_WEBHOOK_URL and config.SHEETS_WEBHOOK_SECRET):
        return False
    try:
        r = requests.post(config.SHEETS_WEBHOOK_URL, timeout=20,
                          json={"secret": config.SHEETS_WEBHOOK_SECRET, **payload})
        return r.ok and r.text.strip() == "ok"
    except Exception as e:
        print(f"[warn] webhook failed: {e}")
        return False


def sheet_upsert(position):
    _post({"type": "trade", "row": {k: position.get(k) for k in (
        "id", "mode", "strategy", "ticker", "title", "side", "count", "entry_price",
        "entry_fee", "model_prob", "market_prob", "edge", "opened_at", "status",
        "exit_price", "exit_fee", "exit_reason", "closed_at", "pnl", "outcome", "notes")}})


def heartbeat():
    _post({"type": "heartbeat"})


def email(subject, body):
    return _post({"type": "email", "subject": f"Kalshi Edge: {subject}", "body": body})


def alert(subject, body="", key=None, every_minutes=60):
    """Email, but the same `key` at most once per `every_minutes` (no inbox flooding)."""
    if key:
        last = db.get_setting(f"alert_{key}", 0) or 0
        if time.time() - last < every_minutes * 60:
            return False
        db.set_setting(f"alert_{key}", time.time())
    return email(subject, body or subject)


def text(msg):
    """Back-compat name used for trade notices: one email per live trade."""
    return email(msg[:80], msg)
