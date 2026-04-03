#!/usr/bin/env python3
"""
CS2 Live Dashboard — Web UI
Run: python3 dashboard_web.py
Access: http://<server-ip>:8050
"""

import os, re, glob, json, time
from datetime import datetime, timezone
from flask import Flask, render_template_string

app = Flask(__name__)
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "live_logs")

HTML = """
<!DOCTYPE html>
<html>
<head>
<title>CS2 Live Dashboard</title>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #0d1117; color: #c9d1d9; font-family: 'JetBrains Mono', 'Fira Code', monospace; font-size: 13px; }

.header { background: #161b22; border-bottom: 1px solid #30363d; padding: 12px 20px; display: flex; justify-content: space-between; align-items: center; position: sticky; top: 0; z-index: 100; }
.header h1 { font-size: 16px; color: #58a6ff; }
.header .time { color: #8b949e; font-size: 12px; }
.header .total { font-size: 13px; }
.header .total .pos { color: #3fb950; }
.header .total .neg { color: #f85149; }

.tabs { display: flex; gap: 2px; background: #161b22; border-bottom: 1px solid #30363d; padding: 0 16px; position: sticky; top: 45px; z-index: 99; }
.tab { padding: 10px 16px; cursor: pointer; color: #8b949e; border-bottom: 2px solid transparent; font-size: 12px; transition: all 0.15s; }
.tab:hover { color: #c9d1d9; }
.tab.active { color: #58a6ff; border-bottom-color: #58a6ff; }
.tab .count { background: #30363d; border-radius: 10px; padding: 1px 6px; margin-left: 4px; font-size: 10px; }
.tab.active .count { background: #1f6feb; color: #fff; }
.tab .dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; margin-right: 6px; }
.dot-live { background: #3fb950; animation: pulse 2s infinite; }
.dot-wait { background: #d29922; }
.dot-done { background: #8b949e; }
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }

.content { padding: 16px; }
.section { display: none; }
.section.active { display: block; }

.match-list { display: flex; flex-direction: column; gap: 8px; }

.match { background: #161b22; border: 1px solid #30363d; border-radius: 8px; overflow: hidden; }
.match.has-position { border-color: #1f6feb; }
.match-header { padding: 12px 16px; display: flex; justify-content: space-between; align-items: center; cursor: pointer; }
.match-header:hover { background: #1c2128; }
.match-teams { font-size: 15px; font-weight: bold; color: #e6edf3; }
.match-meta { display: flex; gap: 12px; align-items: center; font-size: 11px; color: #8b949e; }
.match-status { padding: 2px 8px; border-radius: 4px; font-size: 10px; font-weight: bold; text-transform: uppercase; }
.status-live { background: #0d2818; color: #3fb950; }
.status-waiting { background: #2d1b00; color: #d29922; }
.status-done { background: #1c1c1c; color: #8b949e; }

.match-body { display: none; padding: 0 16px 16px; }
.match.expanded .match-body { display: block; }

.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 8px; margin: 8px 0; }
.stat { background: #0d1117; border: 1px solid #21262d; border-radius: 6px; padding: 10px 12px; }
.stat-label { font-size: 10px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }
.stat-value { font-size: 18px; font-weight: bold; }
.stat-sub { font-size: 11px; color: #8b949e; margin-top: 2px; }

.pos { color: #3fb950; }
.neg { color: #f85149; }
.neutral { color: #d29922; }
.blue { color: #58a6ff; }

.signal-log { margin-top: 10px; }
.signal-log h3 { font-size: 11px; color: #8b949e; text-transform: uppercase; margin-bottom: 6px; }
.signal { display: flex; gap: 8px; padding: 6px 10px; border-left: 3px solid; margin-bottom: 2px; font-size: 12px; background: #0d1117; border-radius: 0 4px 4px 0; }
.signal-buy { border-color: #3fb950; }
.signal-sell { border-color: #d29922; }
.signal-type { font-weight: bold; width: 40px; }
.signal-buy .signal-type { color: #3fb950; }
.signal-sell .signal-type { color: #d29922; }

.round-log { margin-top: 10px; max-height: 300px; overflow-y: auto; }
.round-log h3 { font-size: 11px; color: #8b949e; text-transform: uppercase; margin-bottom: 6px; }
.round-row { display: grid; grid-template-columns: 50px 60px 80px 80px 100px 120px 1fr; gap: 4px; padding: 4px 8px; font-size: 11px; border-bottom: 1px solid #21262d; }
.round-row:nth-child(even) { background: #0d1117; }
.round-row.header { color: #8b949e; font-weight: bold; position: sticky; top: 0; background: #161b22; }

.no-matches { text-align: center; padding: 40px; color: #8b949e; }
</style>
</head>
<body>

<div class="header">
    <h1>CS2 Live Paper Trader</h1>
    <div class="total">
        Total: Realized=<span class="{{ 'pos' if totals.realized >= 0 else 'neg' }}">${{ "%.2f"|format(totals.realized) }}</span>
        Unreal=<span class="{{ 'pos' if totals.unrealized >= 0 else 'neg' }}">${{ "%.2f"|format(totals.unrealized) }}</span>
        Exposure=${{ "%.0f"|format(totals.exposure) }}
    </div>
    <div class="time" id="clock">{{ now }}</div>
</div>

<div class="tabs">
    <div class="tab active" onclick="showSection('live', this)">
        <span class="dot dot-live"></span>Live<span class="count">{{ live|length }}</span>
    </div>
    <div class="tab" onclick="showSection('waiting', this)">
        <span class="dot dot-wait"></span>Waiting<span class="count">{{ waiting|length }}</span>
    </div>
    <div class="tab" onclick="showSection('finished', this)">
        <span class="dot dot-done"></span>Finished<span class="count">{{ finished|length }}</span>
    </div>
</div>

<div class="content">
    <!-- LIVE -->
    <div id="sec-live" class="section active">
        <div class="match-list">
        {% for m in live %}
        <div class="match {{ 'has-position' if m.exposure > 0 }} expanded">
            <div class="match-header" onclick="this.parentElement.classList.toggle('expanded')">
                <div>
                    <span class="match-teams">{{ m.teams.0 }} vs {{ m.teams.1 }}</span>
                </div>
                <div class="match-meta">
                    <span>{{ m.map }} R{{ m.round }}</span>
                    <span>{{ m.score }}</span>
                    {% if m.exposure > 0 %}<span class="blue">${{ "%.0f"|format(m.exposure) }} exposed</span>{% endif %}
                    <span class="match-status status-live">LIVE</span>
                </div>
            </div>
            <div class="match-body">
                <div class="grid">
                    <div class="stat">
                        <div class="stat-label">Series Score</div>
                        <div class="stat-value">{{ m.series_score }}</div>
                    </div>
                    <div class="stat">
                        <div class="stat-label">Map ({{ m.map }})</div>
                        <div class="stat-value">{{ m.score }}</div>
                        <div class="stat-sub">Round {{ m.round }} ({{ m.side }})</div>
                    </div>
                    <div class="stat">
                        <div class="stat-label">Model Map %</div>
                        <div class="stat-value {{ 'pos' if m.model_map and m.model_map > 60 else ('neg' if m.model_map and m.model_map < 40 else 'neutral') }}">
                            {{ "%.0f"|format(m.model_map or 0) }}%
                        </div>
                        <div class="stat-sub">{{ m.teams.0 }}</div>
                    </div>
                    <div class="stat">
                        <div class="stat-label">Model Series %</div>
                        <div class="stat-value {{ 'pos' if m.model_series and m.model_series > 60 else ('neg' if m.model_series and m.model_series < 40 else 'neutral') }}">
                            {{ "%.0f"|format(m.model_series or 0) }}%
                        </div>
                        <div class="stat-sub">PM pre: {{ "%.0f"|format(m.prior or 0) }}%</div>
                    </div>
                    <div class="stat">
                        <div class="stat-label">Economy</div>
                        <div class="stat-value">${{ m.money_a }}k / ${{ m.money_b }}k</div>
                        <div class="stat-sub">{{ m.buy_a }} / {{ m.buy_b }}</div>
                    </div>
                    <div class="stat">
                        <div class="stat-label">Combat</div>
                        <div class="stat-value">K:{{ m.kills }}  FK:{{ m.fk }}</div>
                    </div>
                    <div class="stat">
                        <div class="stat-label">Realized P&L</div>
                        <div class="stat-value {{ 'pos' if m.realized >= 0 else 'neg' }}">${{ "%.2f"|format(m.realized) }}</div>
                    </div>
                    <div class="stat">
                        <div class="stat-label">Unrealized</div>
                        <div class="stat-value {{ 'pos' if m.unrealized >= 0 else 'neg' }}">${{ "%.2f"|format(m.unrealized) }}</div>
                        <div class="stat-sub">Exposure: ${{ "%.0f"|format(m.exposure) }}</div>
                    </div>
                </div>
                {% if m.open_str and m.open_str != 'none' %}
                <div class="stat" style="margin-top: 8px;">
                    <div class="stat-label">Open Positions</div>
                    <div class="stat-value" style="font-size: 13px;">{{ m.open_str }}</div>
                </div>
                {% endif %}
                {% if m.signals %}
                <div class="signal-log">
                    <h3>Recent Signals</h3>
                    {% for sig in m.signals|reverse %}
                    <div class="signal {{ 'signal-buy' if sig.type == 'BUY' else 'signal-sell' }}">
                        <span class="signal-type">{{ sig.type }}</span>
                        <span>{{ sig.market }}</span>
                        <span>{{ sig.outcome }}</span>
                        {% if sig.type == 'BUY' %}
                        <span>${{ sig.bet }} @ ${{ sig.price }}</span>
                        <span class="pos">edge={{ sig.edge }}</span>
                        <span>depth=${{ sig.depth }}</span>
                        <span>model={{ sig.model }}</span>
                        {% else %}
                        <span>${{ sig.entry }} -> ${{ sig.exit }}</span>
                        <span class="{{ 'pos' if sig.pnl >= 0 else 'neg' }}">P&L=${{ "%.2f"|format(sig.pnl) }}</span>
                        {% endif %}
                    </div>
                    {% endfor %}
                </div>
                {% endif %}
            </div>
        </div>
        {% endfor %}
        {% if not live %}<div class="no-matches">No live matches right now</div>{% endif %}
        </div>
    </div>

    <!-- WAITING -->
    <div id="sec-waiting" class="section">
        <div class="match-list">
        {% for m in waiting %}
        <div class="match">
            <div class="match-header">
                <span class="match-teams">{{ m.teams.0 }} vs {{ m.teams.1 }}</span>
                <div class="match-meta">
                    <span>{{ m.slug }}</span>
                    <span class="match-status status-waiting">WAITING</span>
                </div>
            </div>
        </div>
        {% endfor %}
        {% if not waiting %}<div class="no-matches">No matches waiting</div>{% endif %}
        </div>
    </div>

    <!-- FINISHED -->
    <div id="sec-finished" class="section">
        <div class="match-list">
        {% for m in finished %}
        <div class="match">
            <div class="match-header" onclick="this.parentElement.classList.toggle('expanded')">
                <span class="match-teams">{{ m.teams.0 }} vs {{ m.teams.1 }}</span>
                <div class="match-meta">
                    <span class="{{ 'pos' if m.realized >= 0 else 'neg' }}">P&L ${{ "%.2f"|format(m.realized) }}</span>
                    <span class="match-status status-done">DONE</span>
                </div>
            </div>
            <div class="match-body">
                {% if m.signals %}
                <div class="signal-log">
                    <h3>Trade Log</h3>
                    {% for sig in m.signals %}
                    <div class="signal {{ 'signal-buy' if sig.type == 'BUY' else 'signal-sell' }}">
                        <span class="signal-type">{{ sig.type }}</span>
                        <span>{{ sig.market }}</span>
                        <span>{{ sig.outcome }}</span>
                        {% if sig.type == 'BUY' %}
                        <span>${{ sig.bet }} @ ${{ sig.price }}  edge={{ sig.edge }}</span>
                        {% else %}
                        <span class="{{ 'pos' if sig.pnl >= 0 else 'neg' }}">P&L=${{ "%.2f"|format(sig.pnl) }}</span>
                        {% endif %}
                    </div>
                    {% endfor %}
                </div>
                {% endif %}
            </div>
        </div>
        {% endfor %}
        {% if not finished %}<div class="no-matches">No finished matches yet</div>{% endif %}
        </div>
    </div>
</div>

<script>
function showSection(name, el) {
    document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.getElementById('sec-' + name).classList.add('active');
    el.classList.add('active');
}
// Auto-refresh every 10s
setTimeout(() => location.reload(), 10000);
</script>
</body>
</html>
"""

def parse_log(lines):
    st = {
        "slug": "", "teams": ("?", "?"), "prior": None,
        "series_score": "0-0", "map": "?", "round": 0,
        "score": "0-0", "side": "?",
        "model_map": None, "model_series": None,
        "money_a": 0, "money_b": 0, "buy_a": "?", "buy_b": "?",
        "kills": "0-0", "fk": "0-0",
        "realized": 0.0, "unrealized": 0.0, "exposure": 0.0,
        "open_count": 0, "open_str": "",
        "signals": [], "finished": False, "has_data": False,
    }
    for line in lines:
        line = line.strip()

        m = re.search(r"PM Slug:\s+(\S+)", line)
        if m: st["slug"] = m.group(1)

        m = re.search(r"Teams \(PM\):\s+(.+?)\s+vs\s+(.+)", line)
        if m: st["teams"] = (m.group(1).strip(), m.group(2).strip())

        m = re.search(r"Prior \([^)]+\):\s+([\d.]+)%", line)
        if m: st["prior"] = float(m.group(1))

        m = re.match(
            r"\[(\d+:\d+:\d+)\]\s+(\d+-\d+)"
            r"(.*?)"
            r"R(\d+)\((\w+)\)"
            r"\s*\|\s*(\S+)\s+map:([\d.]+)%\s+series:([\d.]+)%"
            r"\s*\|\s*\$(\d+)k/\$(\d+)k\s+\((\w+)/(\w+)\)"
            r"\s+K:(\d+-\d+)\s+FK:(\d+-\d+)", line)
        if m:
            st["series_score"] = m.group(2)
            st["round"] = int(m.group(4))
            st["side"] = m.group(5)
            st["has_data"] = True
            ms = re.findall(r"M\d+\((\w+)\):(\d+-\d+)<", m.group(3))
            if ms:
                st["map"] = ms[-1][0]
                st["score"] = ms[-1][1]
            st["model_map"] = float(m.group(7))
            st["model_series"] = float(m.group(8))
            st["money_a"] = int(m.group(9))
            st["money_b"] = int(m.group(10))
            st["buy_a"] = m.group(11)
            st["buy_b"] = m.group(12)
            st["kills"] = m.group(13)
            st["fk"] = m.group(14)

        m = re.search(
            r"\[P/L\] realized: \$([\d.+-]+)\s*\|\s*unrealized: \$([\d.+-]+)"
            r"\s*\|\s*exposure: \$([\d.]+)\s*\|\s*open\((\d+)\):\s*(.*)", line)
        if m:
            st["realized"] = float(m.group(1))
            st["unrealized"] = float(m.group(2))
            st["exposure"] = float(m.group(3))
            st["open_count"] = int(m.group(4))
            st["open_str"] = m.group(5).strip()
            st["has_data"] = True

        m = re.search(
            r">>> BUY (\w+): (.+?) \$([\d.]+) @ \$([\d.]+) \| edge:([+\-\d.]+%)"
            r" \| depth:\$([\d.]+) \| model:([\d.]+%)", line)
        if m:
            st["signals"].append({
                "type": "BUY", "market": m.group(1),
                "outcome": m.group(2), "bet": m.group(3),
                "price": m.group(4), "edge": m.group(5),
                "depth": m.group(6), "model": m.group(7),
            })

        m = re.search(
            r">>> (?:SELL|FORCE-EXIT) (\w+): (.+?) @ \$([\d.]+) \| bought \$([\d.]+)"
            r" \| P/L: \$([\d.+-]+)", line)
        if m:
            st["signals"].append({
                "type": "SELL", "market": m.group(1),
                "outcome": m.group(2), "exit": m.group(3),
                "entry": m.group(4), "pnl": float(m.group(5)),
            })

        if "FINISHED" in line:
            st["finished"] = True

    st["signals"] = st["signals"][-10:]
    return st


def get_all_matches():
    logs = sorted(glob.glob(os.path.join(LOG_DIR, "stdout_*.log")),
                  key=os.path.getmtime, reverse=True)
    seen = set()
    matches = []
    for log in logs:
        m = re.search(r"stdout_(cs2-[^_]+)_", os.path.basename(log))
        if m:
            slug = m.group(1)
            if slug not in seen:
                seen.add(slug)
                with open(log) as f:
                    lines = f.readlines()[-150:]
                st = parse_log(lines)
                st["slug"] = slug
                matches.append(st)
    return matches


@app.route("/")
def index():
    matches = get_all_matches()
    live = [m for m in matches if not m["finished"] and m["has_data"]]
    waiting = [m for m in matches if not m["finished"] and not m["has_data"]]
    finished = [m for m in matches if m["finished"]]

    totals = {
        "realized": sum(m["realized"] for m in matches),
        "unrealized": sum(m["unrealized"] for m in matches),
        "exposure": sum(m["exposure"] for m in matches),
    }

    now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    return render_template_string(HTML,
        live=live, waiting=waiting, finished=finished,
        totals=totals, now=now)


if __name__ == "__main__":
    print("CS2 Dashboard: http://0.0.0.0:8050")
    app.run(host="0.0.0.0", port=8050, debug=False)
