#!/usr/bin/env bash
# Connects the bot to your Google Sheet's Apps Script: trade log + email alerts.
set -e
cd /opt/kalshi-edge
echo "Paste the Web app URL from Apps Script (starts with https://script.google.com/), then Enter:"
read -r URL
case "$URL" in https://script.google.com/*) ;; *) echo "That doesn't look like an Apps Script URL. Run this again."; exit 1;; esac
echo "Paste the SECRET shown in the Apps Script log, then Enter:"
read -r SECRET
[ ${#SECRET} -ge 16 ] || { echo "Secret looks too short. Run this again."; exit 1; }
venv/bin/python - "$URL" "$SECRET" <<'EOF'
import sys, re
url, sec = sys.argv[1].strip(), sys.argv[2].strip()
s = open(".env").read()
for key, val in (("SHEETS_WEBHOOK_URL", url), ("SHEETS_WEBHOOK_SECRET", sec)):
    if re.search(rf"^{key}=", s, re.M):
        s = re.sub(rf"^{key}=.*$", lambda m: f"{key}={val}", s, flags=re.M)
    else:
        s += f"\n{key}={val}\n"
open(".env", "w").write(s)
EOF
chmod 600 .env
echo "Sending a test email..."
if venv/bin/python -c "
from app import notify, db; db.init()
ok = notify.email('test alert', 'If you can read this, alerts work. You will get a summary every morning at 7am.')
notify.heartbeat()
raise SystemExit(0 if ok else 1)"; then
  systemctl restart kalshi-edge-bot
  echo "Done. Check your Gmail for 'Kalshi Edge: test alert'."
else
  echo "The Apps Script did not accept it. Check that you deployed as 'Anyone' and copied the right URL and SECRET."
fi
