#!/usr/bin/env python3
"""
CS2 Live Dashboard — Web UI
Run: python3 dashboard_web.py
Access: http://<server-ip>:8050
"""

import os, re, glob, json, time
from datetime import datetime, timezone
from flask import Flask, render_template_string, jsonify

app = Flask(__name__)
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "live_logs")

def parse_log(lines):
    st = {
        "slug": "", "teams": ["?", "?"], "prior": None,
        "series_score": "0-0", "map": "?", "round": 0,
        "score": "0-0", "side": "?",
        "model_map": None, "model_series": None,
        "money_a": 0, "money_b": 0, "buy_a": "?", "buy_b": "?",
        "kills": "0-0", "fk": "0-0",
        "realized": 0.0, "unrealized": 0.0, "exposure": 0.0,
        "open_count": 0, "open_str": "",
        "signals": [], "finished": False, "has_data": False,
        "last_update": "",
    }
    for line in lines:
        line = line.strip()

        m = re.search(r"PM Slug:\s+(\S+)", line)
        if m: st["slug"] = m.group(1)

        m = re.search(r"Teams \(PM\):\s+(.+?)\s+vs\s+(.+)", line)
        if m: st["teams"] = [m.group(1).strip(), m.group(2).strip()]

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
            st["last_update"] = m.group(1)
            st["series_score"] = m.group(2)
            st["round"] = int(m.group(4))
            st["side"] = m.group(5)
            st["has_data"] = True
            ms = re.findall(r"M\d+\((\w+)\):(\d+-\d+)", m.group(3))
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

        # Buy signal with full context from the round line above it
        m = re.search(
            r">>> BUY (\w+): (.+?) \$([\d.]+) @ \$([\d.]+) \| edge:([+\-\d.]+%)"
            r" \| depth:\$([\d.]+) \| model:([\d.]+%)", line)
        if m:
            st["signals"].append({
                "type": "BUY", "market": m.group(1),
                "outcome": m.group(2), "bet": m.group(3),
                "price": m.group(4), "edge": m.group(5),
                "depth": m.group(6), "model": m.group(7),
                "time": st["last_update"],
                "round": st["round"], "map_score": st["score"],
                "series_score": st["series_score"],
            })

        m = re.search(
            r">>> (?:SELL|FORCE-EXIT) (\w+): (.+?) @ \$([\d.]+) \| bought \$([\d.]+)"
            r" \| P/L: \$([\d.+-]+)", line)
        if m:
            st["signals"].append({
                "type": "SELL", "market": m.group(1),
                "outcome": m.group(2), "exit": m.group(3),
                "entry": m.group(4), "pnl": float(m.group(5)),
                "time": st["last_update"],
                "round": st["round"], "map_score": st["score"],
                "series_score": st["series_score"],
            })

        if "FINISHED" in line:
            st["finished"] = True

    st["signals"] = st["signals"][-20:]
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
                try:
                    with open(log) as f:
                        lines = f.readlines()[-200:]
                    st = parse_log(lines)
                    st["slug"] = slug
                    matches.append(st)
                except:
                    pass
    return matches


@app.route("/api/matches")
def api_matches():
    matches = get_all_matches()
    return jsonify({
        "live": [m for m in matches if not m["finished"] and m["has_data"]],
        "waiting": [m for m in matches if not m["finished"] and not m["has_data"]],
        "finished": [m for m in matches if m["finished"]],
        "now": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
    })


@app.route("/")
@app.route("/cs2")
def index():
    return render_template_string(HTML)


HTML = r"""
<!DOCTYPE html>
<html>
<head>
<title>CS2 Live Dashboard</title>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #0d1117; color: #c9d1d9; font-family: 'JetBrains Mono', 'Fira Code', 'SF Mono', monospace; font-size: 13px; }

.header { background: #161b22; border-bottom: 1px solid #30363d; padding: 12px 20px; display: flex; justify-content: space-between; align-items: center; position: sticky; top: 0; z-index: 100; }
.header h1 { font-size: 16px; color: #58a6ff; }
.header .total { font-size: 13px; }

.tabs { display: flex; gap: 2px; background: #161b22; border-bottom: 1px solid #30363d; padding: 0 16px; position: sticky; top: 45px; z-index: 99; }
.tab { padding: 10px 16px; cursor: pointer; color: #8b949e; border-bottom: 2px solid transparent; font-size: 12px; transition: all 0.15s; user-select: none; }
.tab:hover { color: #c9d1d9; }
.tab.active { color: #58a6ff; border-bottom-color: #58a6ff; }
.tab .count { background: #30363d; border-radius: 10px; padding: 1px 6px; margin-left: 4px; font-size: 10px; }
.tab.active .count { background: #1f6feb; color: #fff; }
.dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; margin-right: 6px; }
.dot-live { background: #3fb950; animation: pulse 2s infinite; }
.dot-wait { background: #d29922; }
.dot-done { background: #8b949e; }
@keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:0.4; } }

.content { padding: 16px; }
.section { display: none; }
.section.active { display: block; }

.match { background: #161b22; border: 1px solid #30363d; border-radius: 8px; overflow: hidden; margin-bottom: 8px; }
.match.has-pos { border-left: 3px solid #1f6feb; }
.match-header { padding: 12px 16px; display: flex; justify-content: space-between; align-items: center; cursor: pointer; }
.match-header:hover { background: #1c2128; }
.match-teams { font-size: 15px; font-weight: bold; color: #e6edf3; }
.match-meta { display: flex; gap: 12px; align-items: center; font-size: 11px; color: #8b949e; }
.badge { padding: 2px 8px; border-radius: 4px; font-size: 10px; font-weight: bold; text-transform: uppercase; }
.badge-live { background: #0d2818; color: #3fb950; }
.badge-wait { background: #2d1b00; color: #d29922; }
.badge-done { background: #1c1c1c; color: #8b949e; }

.match-body { display: none; padding: 0 16px 16px; }
.match.expanded .match-body { display: block; }

.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 8px; margin: 8px 0; }
.stat { background: #0d1117; border: 1px solid #21262d; border-radius: 6px; padding: 10px 12px; }
.stat-label { font-size: 10px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }
.stat-value { font-size: 18px; font-weight: bold; }
.stat-sub { font-size: 11px; color: #8b949e; margin-top: 2px; }

.pos { color: #3fb950; }
.neg { color: #f85149; }
.neutral { color: #d29922; }
.blue { color: #58a6ff; }
.dim { color: #484f58; }

.signals { margin-top: 10px; }
.signals h3 { font-size: 11px; color: #8b949e; text-transform: uppercase; margin-bottom: 6px; }
.sig { display: grid; grid-template-columns: 55px 40px 60px 60px auto; gap: 6px; padding: 6px 10px; border-left: 3px solid; margin-bottom: 2px; font-size: 12px; background: #0d1117; border-radius: 0 4px 4px 0; align-items: center; }
.sig-buy { border-color: #3fb950; }
.sig-sell { border-color: #d29922; }
.sig-type { font-weight: bold; }

.positions { margin-top: 10px; }
.positions h3 { font-size: 11px; color: #8b949e; text-transform: uppercase; margin-bottom: 6px; }
.pos-row { padding: 8px 10px; background: #0d1117; border: 1px solid #21262d; border-radius: 4px; margin-bottom: 4px; font-size: 12px; }

.no-data { text-align: center; padding: 40px; color: #484f58; }
#update-time { color: #8b949e; font-size: 12px; }
</style>
</head>
<body>

<div class="header">
    <h1>CS2 Live Paper Trader</h1>
    <div class="total" id="totals"></div>
    <div id="update-time"></div>
</div>

<div class="tabs">
    <div class="tab active" data-tab="live"><span class="dot dot-live"></span>Live <span class="count" id="cnt-live">0</span></div>
    <div class="tab" data-tab="waiting"><span class="dot dot-wait"></span>Waiting <span class="count" id="cnt-waiting">0</span></div>
    <div class="tab" data-tab="finished"><span class="dot dot-done"></span>Finished <span class="count" id="cnt-finished">0</span></div>
</div>

<div class="content">
    <div id="sec-live" class="section active"></div>
    <div id="sec-waiting" class="section"></div>
    <div id="sec-finished" class="section"></div>
</div>

<script>
let expandedSlugs = new Set();

document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
        tab.classList.add('active');
        document.getElementById('sec-' + tab.dataset.tab).classList.add('active');
    });
});

function valClass(v) { return v > 0 ? 'pos' : v < 0 ? 'neg' : 'dim'; }
function probClass(v) { return v > 60 ? 'pos' : v < 40 ? 'neg' : 'neutral'; }

function renderMatch(m, status) {
    const isExpanded = expandedSlugs.has(m.slug);
    const hasPos = m.exposure > 0;
    let html = `<div class="match ${isExpanded ? 'expanded' : ''} ${hasPos ? 'has-pos' : ''}" data-slug="${m.slug}">`;
    html += `<div class="match-header" onclick="toggleMatch('${m.slug}')">`;
    html += `<div><span class="match-teams">${m.teams[0]} vs ${m.teams[1]}</span></div>`;
    html += `<div class="match-meta">`;

    if (status === 'live') {
        html += `<span>${m.map} R${m.round} (${m.side})</span>`;
        html += `<span><b>${m.score}</b></span>`;
        if (hasPos) html += `<span class="blue">$${m.exposure.toFixed(0)} exposed</span>`;
        if (m.realized !== 0) html += `<span class="${valClass(m.realized)}">P&L $${m.realized.toFixed(2)}</span>`;
        html += `<span class="badge badge-live">LIVE</span>`;
    } else if (status === 'finished') {
        html += `<span class="${valClass(m.realized)}">P&L $${m.realized.toFixed(2)}</span>`;
        html += `<span class="badge badge-done">DONE</span>`;
    } else {
        html += `<span class="dim">${m.slug}</span>`;
        html += `<span class="badge badge-wait">WAITING</span>`;
    }
    html += `</div></div>`;

    // Body
    html += `<div class="match-body">`;

    if (status !== 'waiting') {
        html += `<div class="grid">`;
        html += `<div class="stat"><div class="stat-label">Series</div><div class="stat-value">${m.series_score}</div></div>`;
        html += `<div class="stat"><div class="stat-label">Map (${m.map})</div><div class="stat-value">${m.score}</div><div class="stat-sub">Round ${m.round} (${m.side})</div></div>`;
        html += `<div class="stat"><div class="stat-label">Model Map%</div><div class="stat-value ${probClass(m.model_map)}">${(m.model_map||0).toFixed(0)}%</div><div class="stat-sub">${m.teams[0]}</div></div>`;
        html += `<div class="stat"><div class="stat-label">Model Series%</div><div class="stat-value ${probClass(m.model_series)}">${(m.model_series||0).toFixed(0)}%</div><div class="stat-sub">PM pre: ${(m.prior||0).toFixed(0)}%</div></div>`;
        html += `<div class="stat"><div class="stat-label">Economy</div><div class="stat-value">$${m.money_a}k / $${m.money_b}k</div><div class="stat-sub">${m.buy_a} / ${m.buy_b}</div></div>`;
        html += `<div class="stat"><div class="stat-label">Combat</div><div class="stat-value">K:${m.kills}</div><div class="stat-sub">FK:${m.fk}</div></div>`;
        html += `<div class="stat"><div class="stat-label">Realized P&L</div><div class="stat-value ${valClass(m.realized)}">$${m.realized.toFixed(2)}</div></div>`;
        html += `<div class="stat"><div class="stat-label">Unrealized</div><div class="stat-value ${valClass(m.unrealized)}">$${m.unrealized.toFixed(2)}</div><div class="stat-sub">Exposure: $${m.exposure.toFixed(0)}</div></div>`;
        html += `</div>`;

        // Open positions
        if (m.open_str && m.open_str !== 'none') {
            html += `<div class="positions"><h3>Open Positions</h3>`;
            m.open_str.split(' | ').forEach(p => {
                html += `<div class="pos-row">${p}</div>`;
            });
            html += `</div>`;
        }

        // Signals with context
        if (m.signals && m.signals.length > 0) {
            html += `<div class="signals"><h3>Trade Log</h3>`;
            // Reverse so newest on top
            [...m.signals].reverse().forEach(s => {
                if (s.type === 'BUY') {
                    html += `<div class="sig sig-buy">`;
                    html += `<span class="sig-type pos">BUY</span>`;
                    html += `<span>${s.market}</span>`;
                    html += `<span>${s.series_score} ${s.map_score} R${s.round}</span>`;
                    html += `<span class="dim">${s.time}</span>`;
                    html += `<span>${s.outcome} $${s.bet} @ $${s.price} edge=${s.edge} depth=$${s.depth} model=${s.model}</span>`;
                    html += `</div>`;
                } else {
                    const pc = s.pnl >= 0 ? 'pos' : 'neg';
                    html += `<div class="sig sig-sell">`;
                    html += `<span class="sig-type neutral">SELL</span>`;
                    html += `<span>${s.market}</span>`;
                    html += `<span>${s.series_score} ${s.map_score} R${s.round}</span>`;
                    html += `<span class="dim">${s.time}</span>`;
                    html += `<span>${s.outcome} $${s.entry}→$${s.exit} <span class="${pc}">P&L $${s.pnl.toFixed(2)}</span></span>`;
                    html += `</div>`;
                }
            });
            html += `</div>`;
        }
    }

    html += `</div></div>`;
    return html;
}

function toggleMatch(slug) {
    if (expandedSlugs.has(slug)) expandedSlugs.delete(slug);
    else expandedSlugs.add(slug);
    // Re-render just that match
    const el = document.querySelector(`[data-slug="${slug}"]`);
    if (el) el.classList.toggle('expanded');
}

function update() {
    fetch('/api/matches')
        .then(r => r.json())
        .then(data => {
            document.getElementById('update-time').textContent = data.now;
            document.getElementById('cnt-live').textContent = data.live.length;
            document.getElementById('cnt-waiting').textContent = data.waiting.length;
            document.getElementById('cnt-finished').textContent = data.finished.length;

            const tr = [...data.live, ...data.waiting, ...data.finished].reduce((a,m) => a + m.realized, 0);
            const tu = [...data.live, ...data.waiting, ...data.finished].reduce((a,m) => a + m.unrealized, 0);
            const te = [...data.live, ...data.waiting, ...data.finished].reduce((a,m) => a + m.exposure, 0);
            document.getElementById('totals').innerHTML =
                `Real=<span class="${valClass(tr)}">$${tr.toFixed(2)}</span> ` +
                `Unreal=<span class="${valClass(tu)}">$${tu.toFixed(2)}</span> ` +
                `Exp=$${te.toFixed(0)}`;

            // Render sections preserving expanded state
            let liveHtml = data.live.length ? data.live.map(m => renderMatch(m, 'live')).join('') : '<div class="no-data">No live matches</div>';
            let waitHtml = data.waiting.length ? data.waiting.map(m => renderMatch(m, 'waiting')).join('') : '<div class="no-data">No matches waiting</div>';
            let doneHtml = data.finished.length ? data.finished.map(m => renderMatch(m, 'finished')).join('') : '<div class="no-data">No finished matches</div>';

            document.getElementById('sec-live').innerHTML = liveHtml;
            document.getElementById('sec-waiting').innerHTML = waitHtml;
            document.getElementById('sec-finished').innerHTML = doneHtml;

            // Restore expanded state
            expandedSlugs.forEach(slug => {
                const el = document.querySelector(`[data-slug="${slug}"]`);
                if (el) el.classList.add('expanded');
            });
        })
        .catch(err => console.error('Update failed:', err));
}

// Initial load
update();
// Refresh every 8 seconds via fetch (no page reload)
setInterval(update, 8000);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    print("CS2 Dashboard: http://0.0.0.0:8050")
    app.run(host="0.0.0.0", port=8050, debug=False)
