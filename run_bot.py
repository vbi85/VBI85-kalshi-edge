"""Main loop. Runs forever on the droplet (systemd keeps it alive)."""
import time
import traceback

from app import config, db, engine, gate, notify
from app.kalshi import KalshiClient
from app.strategies.mentions import MentionsStrategy
from app.strategies.sports import SportsStrategy
from app.strategies.weather import WeatherStrategy


def main():
    db.init()
    client = KalshiClient()
    strategies = [WeatherStrategy(), SportsStrategy(), MentionsStrategy()]
    db.log(f"Bot started. Requested mode={db.get_setting('mode', config.MODE)}, env={config.KALSHI_ENV}")
    while True:
        try:
            n = engine.run_once(client, strategies, gate.mode_for)
            db.set_setting("last_run", db.now())
            if n:
                db.log(f"{n} new position(s) this pass")
        except Exception as e:
            db.log(f"loop error: {e}\n{traceback.format_exc()[-800:]}", "error")
            notify.text(f"Kalshi Edge error: {e}")
        time.sleep(config.LOOP_MINUTES * 60)


if __name__ == "__main__":
    main()
