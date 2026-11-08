#!/usr/bin/env bash
# Asks for the dashboard password and Live PIN, saves them to .env, starts the dashboard.
set -e
cd /opt/kalshi-edge
while true; do
  read -r -s -p "New dashboard password (8+ characters): " PW; echo
  [ ${#PW} -ge 8 ] && break
  echo "Too short, try again."
done
while true; do
  read -r -s -p "Live PIN (4-6 digits): " PIN; echo
  [[ "$PIN" =~ ^[0-9]{4,6}$ ]] && break
  echo "Digits only, 4 to 6 of them."
done
python3 - "$PW" "$PIN" <<'EOF'
import sys, re
pw, pin = sys.argv[1], sys.argv[2]
s = open(".env").read()
for key, val in (("DASH_PASSWORD", pw), ("LIVE_PIN", pin)):
    if re.search(rf"^{key}=", s, re.M):
        s = re.sub(rf"^{key}=.*$", lambda m: f"{key}={val}", s, flags=re.M)
    else:
        s += f"\n{key}={val}\n"
open(".env", "w").write(s)
EOF
chmod 600 .env
systemctl enable --now kalshi-edge-dash >/dev/null 2>&1
systemctl restart kalshi-edge-dash
sleep 3
if systemctl is-active --quiet kalshi-edge-dash; then
  echo "Saved. Dashboard is running on port 8090. Log in as: $(grep ^DASH_USER= .env | cut -d= -f2)"
else
  echo "Saved, but the dashboard did not start:"; journalctl -u kalshi-edge-dash -n 5 --no-pager -o cat
fi
