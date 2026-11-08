"""Plain-English morning summary, emailed once a day."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config, db, gate, risk


def local_now():
    return datetime.now(ZoneInfo(config.ALERT_TZ))


def build():
    now = local_now()
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    rows = db.all_positions()
    closed = [r for r in rows if r["status"] != "open" and (r["closed_at"] or "") >= since]
    open_ = [r for r in rows if r["status"] == "open"]
    day_pnl = sum(r["pnl"] or 0 for r in closed)
    wins = sum(1 for r in closed if (r["pnl"] or 0) > 0)
    lines = [
        f"Good morning. Kalshi Edge report for {now:%A %b %d}.",
        "",
        f"Last 24h: {len(closed)} trades finished, {wins} won, P&L ${day_pnl:+.2f}",
        f"Paper bankroll: ${risk.bankroll('paper'):.2f} (started ${config.PAPER_BANKROLL:.0f})",
        f"Open now: {len(open_)} positions, ${risk.open_exposure('paper'):.2f} at risk",
        "",
    ]
    for name in config.ENABLED_STRATEGIES:
        ok, tests = gate.check(name)
        passed = sum(1 for t in tests if t[1])
        s = gate.stats("paper", name)
        lines.append(f"{name.title()}: {s['trades']} trades, P&L ${s['pnl']:+.2f}, "
                     f"go-live tests {passed}/5 {'PASSED' if ok else ''}".rstrip())
        for t in tests:
            lines.append(f"   {'OK ' if t[1] else ' - '} {t[0]}: {t[2]}")
    for r in closed[:10]:
        lines.append(f"  {'+' if (r['pnl'] or 0) > 0 else '-'} ${r['pnl']:+.2f} {r['strategy']} "
                     f"{r['side'].upper()} {r['title'][:50]} ({r['exit_reason']})")
    with db.conn() as c:
        errs = c.execute("SELECT COUNT(*) FROM events WHERE level IN ('error','warn') AND at >= ?",
                         (since,)).fetchone()[0]
    lines += ["", f"Warnings/errors in last 24h: {errs}", "",
              f"Dashboard: {config.DASH_URL}"]
    subject = f"{'+' if day_pnl >= 0 else '-'}${abs(day_pnl):.2f} yesterday, {len(open_)} open"
    return subject, "\n".join(lines)


def maybe_send(send):
    """Send once per day, at or after SUMMARY_HOUR local time."""
    now = local_now()
    today = now.date().isoformat()
    if now.hour < config.SUMMARY_HOUR or db.get_setting("summary_sent") == today:
        return False
    subject, body = build()
    if send(subject, body):
        db.set_setting("summary_sent", today)
        return True
    return False
