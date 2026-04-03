#!/usr/bin/env python3
"""
Live match dashboard - clean terminal view of all running matches.
Run: python3 watch.py
"""

import os, sys, time, glob, re
from datetime import datetime, timezone

LOG_DIR = os.path.join(os.path.dirname(__file__), "data", "live_logs")

R = "\033[0m"
RD = "\033[91m"
GR = "\033[92m"
YL = "\033[93m"
CY = "\033[96m"
B = "\033[1m"
D = "\033[2m"

def tail(path, n=120):
    try:
        with open(path) as f:
            return f.readlines()[-n:]
    except:
        return []

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
            st["signals"].append(
                f"{GR}BUY{R}  {m.group(1):<8} {m.group(2)[:18]:<18} "
                f"${m.group(3)} @ ${m.group(4)}  edge={GR}{m.group(5)}{R}  "
                f"depth=${m.group(6)}  model={m.group(7)}")

        m = re.search(
            r">>> (?:SELL|FORCE-EXIT) (\w+): (.+?) @ \$([\d.]+) \| bought \$([\d.]+)"
            r" \| P/L: \$([\d.+-]+)", line)
        if m:
            pnl = float(m.group(5))
            c = GR if pnl >= 0 else RD
            st["signals"].append(
                f"{YL}SELL{R} {m.group(1):<8} {m.group(2)[:18]:<18} "
                f"${m.group(4)} -> ${m.group(3)}  P/L={c}${pnl:+.2f}{R}")

        if "FINISHED" in line:
            st["finished"] = True

    st["signals"] = st["signals"][-5:]
    return st


def render(states):
    now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    lines = []
    w = lines.append

    w(f"{B}{'='*72}{R}")
    w(f"{B}  CS2 LIVE DASHBOARD  {D}{now}{R}")
    w(f"{B}{'='*72}{R}")

    active = [(s, st) for s, st in states if not st["finished"] and st["has_data"]]
    waiting = [(s, st) for s, st in states if not st["finished"] and not st["has_data"]]
    done = [(s, st) for s, st in states if st["finished"]]

    for slug, st in active:
        ta, tb = st["teams"]
        ss = st["series_score"]
        rc = GR if st["realized"] >= 0 else RD
        uc = GR if st["unrealized"] >= 0 else RD

        w("")
        w(f"  {B}{CY}{ta} vs {tb}{R}  {D}[{slug}]{R}")
        w(f"  Series: {B}{ss}{R}  |  Map: {st['map']}  |  "
          f"Round: {B}R{st['round']}{R} ({st['side']})  |  "
          f"Score: {B}{st['score']}{R}")

        if st["model_map"] is not None:
            mp = st["model_map"]
            sp = st["model_series"]
            mc = GR if mp > 60 else (RD if mp < 40 else YL)
            sc = GR if sp > 60 else (RD if sp < 40 else YL)
            prior = f"  |  PM pre: {st['prior']:.0f}%" if st["prior"] else ""
            w(f"  Model:  Map {ta}={mc}{mp:.0f}%{R}  "
              f"Series={sc}{sp:.0f}%{R}{prior}")
            w(f"  Econ:   ${st['money_a']}k vs ${st['money_b']}k  "
              f"({st['buy_a']}/{st['buy_b']})  |  "
              f"K:{st['kills']}  FK:{st['fk']}")

        if st["exposure"] > 0 or st["realized"] != 0:
            w(f"  P&L:    Real={rc}${st['realized']:+.2f}{R}  "
              f"Unreal={uc}${st['unrealized']:+.2f}{R}  "
              f"Exposure=${st['exposure']:.0f}")
            if st["open_str"] and st["open_str"] != "none":
                w(f"  Open:   {st['open_str']}")
        else:
            w(f"  {D}No positions{R}")

        if st["signals"]:
            for sig in st["signals"][-3:]:
                w(f"    {sig}")

        w(f"  {D}{'-'*68}{R}")

    if waiting:
        w(f"\n  {YL}Waiting for GRID data:{R}")
        for slug, st in waiting:
            ta, tb = st["teams"]
            w(f"    {D}* {ta} vs {tb}  [{slug}]{R}")

    if done:
        w(f"\n  {D}Finished:{R}")
        for slug, st in done:
            ta, tb = st["teams"]
            c = GR if st["realized"] >= 0 else RD
            w(f"    + {ta} vs {tb}  "
              f"P&L={c}${st['realized']:+.2f}{R}  {D}[{slug}]{R}")

    tr = sum(st["realized"] for _, st in states)
    tu = sum(st["unrealized"] for _, st in states)
    te = sum(st["exposure"] for _, st in states)
    c = GR if tr >= 0 else RD
    w(f"\n  {B}TOTAL:{R}  "
      f"Realized={c}${tr:+.2f}{R}  "
      f"Unreal=${tu:+.2f}  "
      f"Exposure=${te:.0f}  |  "
      f"{len(active)} live  {len(waiting)} waiting  {len(done)} done")
    w(f"{B}{'='*72}{R}")
    w(f"{D}  Refreshing every 5s | Ctrl+C to quit{R}")

    # Pad with blank lines to prevent leftover text from previous render
    for _ in range(15):
        w("")

    return "\n".join(lines)


def main():
    try:
        while True:
            logs = sorted(glob.glob(os.path.join(LOG_DIR, "stdout_*.log")),
                         key=os.path.getmtime, reverse=True)

            seen = set()
            deduped = []
            for log in logs:
                m = re.search(r"stdout_(cs2-[^_]+)_", os.path.basename(log))
                if m:
                    slug = m.group(1)
                    if slug not in seen:
                        seen.add(slug)
                        deduped.append((slug, log))

            states = []
            for slug, log in deduped:
                st = parse_log(tail(log))
                st["slug"] = slug
                states.append((slug, st))

            output = render(states)
            # Move cursor home and print (no flicker)
            sys.stdout.write("\033[H" + output)
            sys.stdout.flush()
            time.sleep(5)

    except KeyboardInterrupt:
        print("\nDashboard closed.")


if __name__ == "__main__":
    # Clear screen once at start
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()
    main()
