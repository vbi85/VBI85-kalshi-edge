"""Main loop. Runs forever on the droplet (systemd keeps it alive)."""
import time
import traceback

from app import config, db, engine, gate, notify, summary
from app.kalshi import KalshiClient
from app.strategies.mentions import MentionsStrategy
from app.strategies.sports import SportsStrategy
from app.strategies.weather import WeatherStrategy


def main():
    db.init()
    client = KalshiClient()
    strategies = [WeatherStrategy(), SportsStrategy(), MentionsStrategy()]
    db.log(f"Bot started. Requested mode={db.get_setting('mode', config.MODE)}, env={config.KALSHI_ENV}")
    # a restart is worth knowing about (crash, reboot or update), but not more than hourly
    notify.alert("bot (re)started", "The bot just started. Normal after an update or reboot; "
                 "if you didn't do either, it crashed and recovered on its own.",
                 key="restart", every_minutes=60)
    while True:
        try:
            n = engine.run_once(client, strategies, gate.mode_for)
            db.set_setting("last_run", db.now())
            notify.heartbeat()
            if n:
                db.log(f"{n} new position(s) this pass")
            summary.maybe_send(notify.email)
        except Exception as e:
            db.log(f"loop error: {e}\n{traceback.format_exc()[-800:]}", "error")
            notify.alert("error in the bot loop", f"{e}\n\n{traceback.format_exc()[-1500:]}",
                         key="loop_error", every_minutes=120)
        time.sleep(config.LOOP_MINUTES * 60)


if __name__ == "__main__":
    main()
