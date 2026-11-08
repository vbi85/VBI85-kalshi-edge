"""Outbound: Google Sheet trade log (via Apps Script webhook) and text alerts (Gmail -> T-Mobile)."""
import smtplib
from email.message import EmailMessage

import requests

from . import config


def sheet_upsert(position):
    if not config.SHEETS_WEBHOOK_URL:
        return
    try:
        requests.post(config.SHEETS_WEBHOOK_URL, timeout=15, json={
            "secret": config.SHEETS_WEBHOOK_SECRET, "row": {k: position.get(k) for k in (
                "id", "mode", "strategy", "ticker", "title", "side", "count", "entry_price",
                "entry_fee", "model_prob", "market_prob", "edge", "opened_at", "status",
                "exit_price", "exit_fee", "exit_reason", "closed_at", "pnl", "outcome", "notes")}})
    except Exception as e:
        print(f"[warn] sheet log failed: {e}")


def text(msg):
    if not (config.ALERT_GMAIL and config.ALERT_GMAIL_APP_PASSWORD and config.ALERT_SMS_ADDRESS):
        return
    try:
        em = EmailMessage()
        em["From"], em["To"], em["Subject"] = config.ALERT_GMAIL, config.ALERT_SMS_ADDRESS, "Kalshi Edge"
        em.set_content(msg[:300])
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as s:
            s.login(config.ALERT_GMAIL, config.ALERT_GMAIL_APP_PASSWORD)
            s.send_message(em)
    except Exception as e:
        print(f"[warn] text alert failed: {e}")
