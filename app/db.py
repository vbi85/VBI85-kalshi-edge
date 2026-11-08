"""SQLite store for positions, decisions and settings. One file, no server."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mode TEXT, strategy TEXT, ticker TEXT, event_ticker TEXT, title TEXT,
  side TEXT, count INTEGER, entry_price REAL, entry_fee REAL,
  model_prob REAL, market_prob REAL, edge REAL,
  opened_at TEXT, status TEXT DEFAULT 'open',
  exit_price REAL, exit_fee REAL, exit_reason TEXT, closed_at TEXT, pnl REAL,
  outcome INTEGER, order_id TEXT, notes TEXT
);
CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, level TEXT, msg TEXT
);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def conn(path=None):
    c = sqlite3.connect(path or config.DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init(path=None):
    with conn(path) as c:
        c.executescript(SCHEMA)


def log(msg, level="info", path=None):
    print(f"[{level}] {msg}", flush=True)
    with conn(path) as c:
        c.execute("INSERT INTO events(at,level,msg) VALUES(?,?,?)", (now(), level, msg))


def get_setting(k, default=None, path=None):
    with conn(path) as c:
        r = c.execute("SELECT v FROM settings WHERE k=?", (k,)).fetchone()
    return json.loads(r["v"]) if r else default


def set_setting(k, v, path=None):
    with conn(path) as c:
        c.execute("INSERT OR REPLACE INTO settings(k,v) VALUES(?,?)", (k, json.dumps(v)))


def open_positions(mode=None, path=None):
    q, a = "SELECT * FROM positions WHERE status='open'", []
    if mode:
        q += " AND mode=?"
        a.append(mode)
    with conn(path) as c:
        return [dict(r) for r in c.execute(q, a)]


def all_positions(mode=None, path=None):
    q, a = "SELECT * FROM positions", []
    if mode:
        q += " WHERE mode=?"
        a.append(mode)
    q += " ORDER BY id DESC"
    with conn(path) as c:
        return [dict(r) for r in c.execute(q, a)]


def insert_position(p, path=None):
    cols = ",".join(p.keys())
    qs = ",".join("?" * len(p))
    with conn(path) as c:
        cur = c.execute(f"INSERT INTO positions({cols}) VALUES({qs})", list(p.values()))
        return cur.lastrowid


def update_position(pid, path=None, **fields):
    sets = ",".join(f"{k}=?" for k in fields)
    with conn(path) as c:
        c.execute(f"UPDATE positions SET {sets} WHERE id=?", list(fields.values()) + [pid])
