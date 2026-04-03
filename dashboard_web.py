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
            r"\s*\|\s*(.+?)\s+map:([\d.]+)%\s+series:([\d.]+)%"
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
    # Fallback: derive team names from slug if still unknown
    if st["teams"] == ["?", "?"] and st["slug"]:
        # cs2-teamA-teamB-2026-04-03 -> teamA vs teamB
        parts = st["slug"].replace("cs2-", "").split("-")
        # Remove date parts (4-digit year, 2-digit month/day)
        name_parts = []
        for p in parts:
            if len(p) == 4 and p.isdigit():  # year
                break
            name_parts.append(p)
        if len(name_parts) >= 2:
            mid = len(name_parts) // 2
            st["teams"] = ["-".join(name_parts[:mid]).upper(),
                          "-".join(name_parts[mid:]).upper()]
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


def get_pending_matches():
    """Parse watcher log for PENDING matches."""
    pending = []
    try:
        with open("/var/log/cs2-watcher.log") as f:
            lines = f.readlines()[-80:]
        for line in lines:
            m = re.search(r"\[PENDING (\d+)min\]\s+(.+?)\s+→\s+(\S+)", line)
            if m:
                mins = int(m.group(1))
                teams_str = m.group(2).strip()
                slug = m.group(3)
                parts = teams_str.split(" vs ")
                if len(parts) == 2:
                    pending.append({
                        "teams": parts,
                        "slug": slug,
                        "mins_until": mins,
                    })
    except:
        pass
    # Deduplicate by slug, sort by time
    seen = set()
    deduped = []
    for p in pending:
        if p["slug"] not in seen:
            seen.add(p["slug"])
            deduped.append(p)
    return sorted(deduped, key=lambda x: x["mins_until"])


@app.route("/api/matches")
@app.route("/cs2/api/matches")
def api_matches():
    matches = get_all_matches()
    pending = get_pending_matches()
    # "waiting" = launched but no GRID data yet + pending from watcher
    launched_waiting = [m for m in matches if not m["finished"] and not m["has_data"]]
    return jsonify({
        "live": [m for m in matches if not m["finished"] and m["has_data"]],
        "waiting": launched_waiting,
        "pending": pending,
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

.trade-table { width: 100%; border-collapse: collapse; font-size: 11px; margin-top: 4px; }
.trade-table th { text-align: left; padding: 6px 8px; color: #8b949e; border-bottom: 1px solid #30363d; font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px; position: sticky; top: 0; background: #161b22; }
.trade-table td { padding: 5px 8px; border-bottom: 1px solid #21262d; }
.trade-table tr:hover { background: #1c2128; }
.row-buy { border-left: 2px solid #3fb950; }
.row-sell { border-left: 2px solid #d29922; }

.pos-cards { display: flex; flex-direction: column; gap: 6px; }
.pcard { background: #0d1117; border: 1px solid #21262d; border-radius: 6px; overflow: hidden; }
.pcard-open { border-left: 3px solid #58a6ff; }
.pcard-win { border-left: 3px solid #3fb950; }
.pcard-loss { border-left: 3px solid #f85149; }
.pcard-head { display: flex; align-items: center; gap: 8px; padding: 8px 12px; font-size: 12px; background: #161b22; }
.pcard-id { color: #484f58; font-size: 11px; }
.pcard-mkt { color: #8b949e; }
.pcard-body { padding: 6px 12px; }
.pcard-row { font-size: 12px; padding: 4px 0; border-bottom: 1px solid #21262d; display: flex; gap: 8px; flex-wrap: wrap; }
.pcard-row:last-child { border-bottom: none; }
.badge-win { background: #0d2818; color: #3fb950; padding: 2px 8px; border-radius: 4px; font-size: 10px; font-weight: bold; }
.badge-loss { background: #2d0000; color: #f85149; padding: 2px 8px; border-radius: 4px; font-size: 10px; font-weight: bold; }
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
    <div class="tab" data-tab="waiting"><span class="dot dot-wait"></span>Upcoming <span class="count" id="cnt-waiting">0</span></div>
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

        // Build positions: match BUYs to their SELLs
        if (m.signals && m.signals.length > 0) {
            let positions = [];
            m.signals.forEach(s => {
                if (s.type === 'BUY') {
                    positions.push({ id: positions.length + 1, buy: s, sell: null, status: 'OPEN' });
                } else {
                    const pos = [...positions].reverse().find(p =>
                        p.status === 'OPEN' && p.buy && p.buy.market === s.market && p.buy.outcome === s.outcome
                    );
                    if (pos) { pos.sell = s; pos.status = s.pnl >= 0 ? 'WIN' : 'LOSS'; }
                    else { positions.push({ id: positions.length + 1, buy: null, sell: s, status: s.pnl >= 0 ? 'WIN' : 'LOSS' }); }
                }
            });

            html += `<div class="signals"><h3>Positions (${positions.length})</h3>`;
            html += `<div class="pos-cards">`;
            [...positions].reverse().forEach(p => {
                const b = p.buy, s = p.sell;
                const sc = p.status === 'OPEN' ? 'pcard-open' : (p.status === 'WIN' ? 'pcard-win' : 'pcard-loss');
                html += `<div class="pcard ${sc}">`;
                html += `<div class="pcard-head">`;
                html += `<span class="pcard-id">#${p.id}</span>`;
                if (b) html += ` <span class="pcard-mkt">${b.market}</span> <b>${b.outcome}</b>`;
                if (p.status === 'OPEN') html += `<span class="badge badge-live" style="margin-left:auto">OPEN</span>`;
                else if (p.status === 'WIN') html += `<span class="badge badge-win" style="margin-left:auto">WIN</span>`;
                else html += `<span class="badge badge-loss" style="margin-left:auto">LOSS</span>`;
                html += `</div>`;
                html += `<div class="pcard-body">`;
                if (b) {
                    html += `<div class="pcard-row"><span class="pos">BUY</span> <b>$${b.price}</b> x $${b.bet} `;
                    html += `<span class="dim">@ ${b.time}</span> `;
                    html += `Series ${b.series_score} Map ${b.map_score} R${b.round} `;
                    html += `| Model: ${b.model} Edge: <span class="pos">${b.edge}</span></div>`;
                }
                if (s) {
                    html += `<div class="pcard-row"><span class="neutral">SELL</span> <b>$${s.exit}</b> `;
                    html += `<span class="dim">@ ${s.time}</span> `;
                    html += `Series ${s.series_score} Map ${s.map_score} R${s.round} `;
                    html += `| P&L: <span class="${s.pnl>=0?'pos':'neg'}"><b>$${s.pnl.toFixed(2)}</b></span></div>`;
                }
                html += `</div></div>`;
            });
            html += `</div></div>`;
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
    fetch('/cs2/api/matches')
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
            // Waiting = launched but no data + pending from watcher
            let allWaiting = [...(data.waiting || []), ...(data.pending || []).map(p => ({
                teams: p.teams, slug: p.slug, mins_until: p.mins_until, isPending: true
            }))];
            let waitHtmlFinal = '';
            if (allWaiting.length) {
                waitHtmlFinal = allWaiting.map(m => {
                    if (m.isPending) {
                        const hrs = Math.floor(m.mins_until / 60);
                        const mins = m.mins_until % 60;
                        const timeStr = hrs > 0 ? `${hrs}h ${mins}m` : `${mins}m`;
                        return `<div class="match"><div class="match-header"><span class="match-teams">${m.teams[0]} vs ${m.teams[1]}</span><div class="match-meta"><span class="dim">${m.slug}</span><span>${timeStr}</span><span class="badge badge-wait">PENDING</span></div></div></div>`;
                    } else {
                        return renderMatch(m, 'waiting');
                    }
                }).join('');
            } else {
                waitHtmlFinal = '<div class="no-data">No upcoming matches</div>';
            }
            document.getElementById('sec-waiting').innerHTML = waitHtmlFinal;
            document.getElementById('cnt-waiting').textContent = allWaiting.length;
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
