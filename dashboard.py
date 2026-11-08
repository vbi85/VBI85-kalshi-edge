"""Phone dashboard: black background, big numbers, Paper/Live switch behind the approval tests."""
import hmac
from functools import wraps

from flask import Flask, Response, redirect, render_template_string, request

from app import config, db, gate, labels, risk

app = Flask(__name__)


def auth(f):
    @wraps(f)
    def w(*a, **k):
        a_ = request.authorization
        if not a_ or not (hmac.compare_digest(a_.username or "", config.DASH_USER)
                          and hmac.compare_digest(a_.password or "", config.DASH_PASSWORD)):
            return Response("Login required", 401, {"WWW-Authenticate": 'Basic realm="Kalshi Edge"'})
        return f(*a, **k)
    return w


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="60"><title>Kalshi Edge</title>
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" type="image/png" href="/static/favicon.png">
<link rel="apple-touch-icon" href="/static/apple-touch-icon.png">
<meta name="theme-color" content="#000000">
<style>
:root{--bg:#000;--card:#111;--line:#222;--txt:#eee;--dim:#888;--up:#2ecc71;--dn:#ff5c5c;--warn:#f5b041}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);font:15px/1.4 -apple-system,Segoe UI,Roboto,sans-serif;padding:16px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);margin:22px 0 8px}
.pill{display:inline-block;padding:3px 10px;border-radius:99px;font-weight:700;font-size:12px}
.paper{background:#1d3557;color:#a8dadc}.live{background:#5c0f0f;color:#ffb3b3}
.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}
.tile b{display:block;font-size:22px}.tile span{color:var(--dim);font-size:12px}
.row{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin-bottom:6px}
.row small{color:var(--dim);display:block}.up{color:var(--up)}.dn{color:var(--dn)}
.ok{color:var(--up)}.no{color:var(--dn)}
input,button{font:inherit;padding:10px;border-radius:8px;border:1px solid var(--line);background:#1a1a1a;color:var(--txt)}
button{font-weight:700;cursor:pointer}button:disabled{opacity:.35}
.head{font-weight:700;font-size:16px}
.tag{font-size:10px;padding:1px 6px;border-radius:99px;background:#333;color:#bbb;vertical-align:middle}
.bet{display:inline-block;margin-left:6px;font-size:12px;font-weight:700;padding:1px 8px;border-radius:6px}
.bet.yes{background:#123d24;color:var(--up)}.bet.no{background:#3d1212;color:var(--dn)}
.log{font-family:ui-monospace,monospace;font-size:12px;color:var(--dim);white-space:pre-wrap}
</style></head><body>
<h1>Kalshi Edge <span class="pill {{mode}}">{{mode|upper}}</span></h1>
<small style="color:var(--dim)">Weather + Mentions · Sports next · last run {{last_run or "never"}}</small>

<h2>Paper scoreboard</h2>
<div class="grid">
 <div class="tile"><span>Bankroll</span><b>${{"%.2f"|format(bank)}}</b></div>
 <div class="tile"><span>Total P&amp;L</span><b class="{{'up' if s.pnl>=0 else 'dn'}}">${{"%+.2f"|format(s.pnl)}}</b></div>
 <div class="tile"><span>Win rate</span><b>{{ "%.0f%%"|format(s.win_rate*100) if s.win_rate is not none else "—" }}</b></div>
 <div class="tile"><span>Trades (open)</span><b>{{s.trades}} ({{s.open}})</b></div>
</div>

<h2>Go-live tests (each strategy earns Live on its own)</h2>
{% for g in gates %}
<div class="row"><b>{{g.name|title}}</b> <span class="pill {{'live' if g.live else 'paper'}}">{{'LIVE' if g.live else 'PAPER'}}</span>
<small>{{g.s.trades}} trades · P&amp;L ${{"%+.2f"|format(g.s.pnl)}} · win {{ "%.0f%%"|format(g.s.win_rate*100) if g.s.win_rate is not none else "—" }}</small>
{% for name, ok, detail in g.tests %}<small><span class="{{'ok' if ok else 'no'}}">{{'✓' if ok else '✗'}}</span> {{name}}: {{detail}}</small>{% endfor %}
</div>
{% endfor %}
<small style="color:var(--dim)">Go Live arms real money only for strategies with all ✓. The rest keep paper trading.</small>
<form method="post" action="/mode" style="display:flex;gap:8px;margin-top:8px">
 <input name="pin" type="password" placeholder="PIN" inputmode="numeric" style="flex:1">
 {% if mode == 'live' %}<button name="to" value="paper">Back to Paper</button>
 {% else %}<button name="to" value="live" {{'' if passed else 'disabled'}}>Go Live</button>{% endif %}
</form>
{% if msg %}<p class="no">{{msg}}</p>{% endif %}

{% macro pos(c) %}
<div class="row">
 <div class="head">{{c.head}} {% if c.test %}<span class="tag">TEST</span>{% endif %}</div>
 <div>{{c.what}} <span class="bet {{'yes' if c.side=='YES' else 'no'}}">Bet {{c.side}}: {{c.bet}}</span></div>
 <small>{{c.money}} · {{c.odds}}{% if c.extra %} · {{c.extra}}{% endif %}</small>
 {% if c.status != 'open' %}<small><span class="{{'up' if (c.pnl or 0)>=0 else 'dn'}}">${{"%+.2f"|format(c.pnl or 0)}}</span> · {{c.exit_reason|replace('_',' ')}} · {{(c.closed_at or '')[5:16]|replace('T',' ')}}</small>{% endif %}
</div>
{% endmacro %}

{% for name, label in [('weather','🌡 Weather'),('mentions','🎤 Mentions')] %}
<h2>{{label}} — open ({{open_by[name]|length}})</h2>
{% for c in open_by[name] %}{{ pos(c) }}{% else %}<div class="row"><small>None</small></div>{% endfor %}
{% endfor %}

<h2>Recent closed</h2>
{% for c in closed %}{{ pos(c) }}{% else %}<div class="row"><small>None yet</small></div>{% endfor %}

<h2>Activity</h2>
<div class="row log">{% for e in events %}{{e.at[5:16]}} {{e.msg}}
{% endfor %}</div>
</body></html>"""


@app.route("/manifest.webmanifest")
def manifest():
    """Public on purpose: Android fetches this without the login. It only names the icon."""
    from flask import jsonify
    r = jsonify({
        "name": "Kalshi Edge", "short_name": "Kalshi Edge", "start_url": "/",
        "display": "standalone", "background_color": "#000000", "theme_color": "#000000",
        "icons": [
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ]})
    r.headers["Content-Type"] = "application/manifest+json"
    return r


@app.route("/")
@auth
def home():
    db.init()
    mode = db.get_setting("mode", config.MODE)
    s = gate.stats("paper")
    armed = mode == "live"
    gates = []
    for name in config.ENABLED_STRATEGIES:
        ok, tests = gate.check(name)
        gates.append({"name": name, "tests": tests, "s": gate.stats("paper", name), "live": armed and ok})
    passed = any(g_["tests"] and all(t[1] for t in g_["tests"]) for g_ in gates)
    rows = db.all_positions()
    with db.conn() as c:
        events = [dict(r) for r in c.execute("SELECT * FROM events ORDER BY id DESC LIMIT 25")]
    return render_template_string(
        PAGE, mode=mode, s=s, bank=risk.bankroll("paper"), gates=gates, passed=passed,
        open_by={n: [labels.card(r) for r in rows if r["status"] == "open" and r["strategy"] == n]
                 for n in ("weather", "mentions")},
        closed=[labels.card(r) for r in rows if r["status"] != "open"][:15],
        events=events, last_run=(db.get_setting("last_run") or "")[:16].replace("T", " "),
        msg=request.args.get("msg"))


@app.route("/mode", methods=["POST"])
@auth
def set_mode():
    to = request.form.get("to")
    if not config.LIVE_PIN or not hmac.compare_digest(request.form.get("pin", ""), config.LIVE_PIN):
        return redirect("/?msg=Wrong PIN")
    if to == "live":
        if not gate.any_passes(config.ENABLED_STRATEGIES):
            return redirect("/?msg=Approval tests not passed yet")
        db.set_setting("mode", "live")
        for name in config.ENABLED_STRATEGIES:
            db.set_setting(f"warned_{name}", False)
        db.log("Owner armed LIVE (only strategies passing all tests trade real money)", "alert")
    else:
        db.set_setting("mode", "paper")
        db.log("Owner switched to PAPER", "alert")
    return redirect("/")


if __name__ == "__main__":
    if len(config.DASH_PASSWORD) < 8 or config.DASH_PASSWORD == "change-me":
        raise SystemExit("Set DASH_PASSWORD in .env (8+ characters) before starting the dashboard.")
    app.run(host="0.0.0.0", port=config.DASH_PORT)
