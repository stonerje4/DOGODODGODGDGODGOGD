"""
Live match dashboard — clean terminal view of all running matches.
Run: python3 watch.py

Tails all active stdout logs and renders a clean scoreboard every 5s.
"""

import os, sys, time, glob, re
from datetime import datetime, timezone

LOG_DIR = os.path.join(os.path.dirname(__file__), "data", "live_logs")

# ANSI colors
RED    = "\033[91m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

def clear():
    # Move cursor to top-left instead of clearing (prevents flicker)
    sys.stdout.write("\033[H\033[J")
    sys.stdout.flush()

def tail(path, n=120):
    try:
        with open(path) as f:
            lines = f.readlines()
        return lines[-n:]
    except:
        return []

def parse_log(lines):
    """Extract latest state from a stdout log."""
    state = {
        "slug": "",
        "teams": ("?", "?"),
        "prior": None,
        "series_score": "0-0",
        "map": "?",
        "round": 0,
        "score": "0-0",
        "side": "?",
        "model_map": None,
        "model_series": None,
        "money_a": 0,
        "money_b": 0,
        "buy_a": "?",
        "buy_b": "?",
        "kills": "0-0",
        "fk": "0-0",
        "pm_price": None,
        "pm_ask": None,
        "pm_bid": None,
        "edge_winner": None,
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "exposure": 0.0,
        "open_count": 0,
        "open_str": "",
        "last_signals": [],
        "finished": False,
        "has_data": False,
    }

    for line in lines:
        line = line.strip()

        # Slug
        m = re.search(r"PM Slug:\s+(\S+)", line)
        if m: state["slug"] = m.group(1)

        # Teams
        m = re.search(r"Teams \(PM\):\s+(.+?)\s+vs\s+(.+)", line)
        if m: state["teams"] = (m.group(1).strip(), m.group(2).strip())

        # Prior
        m = re.search(r"Prior \([^)]+\):\s+([\d.]+)%", line)
        if m: state["prior"] = float(m.group(1))

        # Round line: [HH:MM:SS] 1-0  M1(mirage):7-5< R12(CT) | ...
        m = re.match(
            r"\[(\d+:\d+:\d+)\]\s+(\d+-\d+)"
            r"(.*?)"
            r"R(\d+)\((\w+)\)"
            r"\s*\|\s*(\S+)\s+map:([\d.]+)%\s+series:([\d.]+)%"
            r"\s*\|\s*\$(\d+)k/\$(\d+)k\s+\((\w+)/(\w+)\)"
            r"\s+K:(\d+-\d+)\s+FK:(\d+-\d+)",
            line,
        )
        if m:
            state["series_score"] = m.group(2)
            maps_part = m.group(3)
            state["round"] = int(m.group(4))
            state["side"] = m.group(5)
            state["has_data"] = True
            # extract current map score from maps_part
            ms = re.findall(r"M\d+\((\w+)\):(\d+-\d+)<", maps_part)
            if ms:
                state["map"] = ms[-1][0]
                state["score"] = ms[-1][1]
            state["model_map"] = float(m.group(7))
            state["model_series"] = float(m.group(8))
            state["money_a"] = int(m.group(9))
            state["money_b"] = int(m.group(10))
            state["buy_a"] = m.group(11)
            state["buy_b"] = m.group(12)
            state["kills"] = m.group(13)
            state["fk"] = m.group(14)

        # P/L line
        m = re.search(r"\[P/L\] realized: \$([\d.+-]+)\s*\|\s*unrealized: \$([\d.+-]+)\s*\|\s*exposure: \$([\d.]+)\s*\|\s*open\((\d+)\):\s*(.*)", line)
        if m:
            state["realized_pnl"] = float(m.group(1))
            state["unrealized_pnl"] = float(m.group(2))
            state["exposure"] = float(m.group(3))
            state["open_count"] = int(m.group(4))
            state["open_str"] = m.group(5).strip()
            state["has_data"] = True
        # Fallback P/L without open details
        elif not m:
            m2 = re.search(r"\[P/L\] realized: \$([\d.+-]+)\s*\|\s*unrealized: \$([\d.+-]+)\s*\|\s*exposure: \$([\d.]+)", line)
            if m2:
                state["realized_pnl"] = float(m2.group(1))
                state["unrealized_pnl"] = float(m2.group(2))
                state["exposure"] = float(m2.group(3))
                state["has_data"] = True

        # Buy signals
        m = re.search(r">>> BUY (\w+): (.+?) \$([\d.]+) @ \$([\d.]+) \| edge:([+\-\d.]+%) \| depth:\$([\d.]+) \| model:([\d.]+%)", line)
        if m:
            state["last_signals"].append({
                "type": "BUY",
                "market": m.group(1),
                "outcome": m.group(2),
                "bet": float(m.group(3)),
                "price": float(m.group(4)),
                "edge": m.group(5),
                "depth": m.group(6),
                "model": m.group(7),
            })

        # Sell signals
        m = re.search(r">>> (?:SELL|FORCE-EXIT) (\w+): (.+?) @ \$([\d.]+) \| bought \$([\d.]+) \| P/L: \$([\d.+-]+)", line)
        if m:
            state["last_signals"].append({
                "type": "SELL",
                "market": m.group(1),
                "outcome": m.group(2),
                "exit_price": float(m.group(3)),
                "entry_price": float(m.group(4)),
                "pnl": float(m.group(5)),
            })

        if "FINISHED" in line:
            state["finished"] = True

    # Keep only last 5 signals
    state["last_signals"] = state["last_signals"][-5:]
    return state


def render(states):
    now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    print(f"{BOLD}{'='*72}{RESET}")
    print(f"{BOLD}  CS2 LIVE DASHBOARD  {DIM}{now}{RESET}")
    print(f"{BOLD}{'='*72}{RESET}")

    active = [(s, st) for s, st in states if not st["finished"] and st["round"] > 0]
    waiting = [(s, st) for s, st in states if not st["finished"] and st["round"] == 0]
    finished = [(s, st) for s, st in states if st["finished"]]

    if not active and not waiting:
        print(f"\n  {DIM}No active matches yet — waiting for GRID data...{RESET}\n")

    for slug, st in active:
        ta, tb = st["teams"]
        ss = st["series_score"]
        pnl_color = GREEN if st["realized_pnl"] >= 0 else RED
        unr_color = GREEN if st["unrealized_pnl"] >= 0 else RED

        # Header
        print(f"\n  {BOLD}{CYAN}{ta} vs {tb}{RESET}  {DIM}[{slug}]{RESET}")
        print(f"  Series: {BOLD}{ss}{RESET}  │  Map: {st['map']}  │  "
              f"Round: {BOLD}R{st['round']}{RESET} ({st['side']})  │  "
              f"Score: {BOLD}{st['score']}{RESET}")

        # Model probs
        if st["model_map"] is not None:
            mp = st["model_map"]
            sp = st["model_series"]
            mp_color = GREEN if mp > 60 else (RED if mp < 40 else YELLOW)
            sp_color = GREEN if sp > 60 else (RED if sp < 40 else YELLOW)
            print(f"  Model:  Map={mp_color}{mp:.0f}%{RESET}  "
                  f"Series={sp_color}{sp:.0f}%{RESET}  │  "
                  f"Econ: ${st['money_a']}k/{st['money_b']}k  "
                  f"({st['buy_a']}/{st['buy_b']})  │  "
                  f"K:{st['kills']}  FK:{st['fk']}")

        # Prior
        if st["prior"]:
            print(f"  Prior:  {ta} {st['prior']:.0f}% pre-match")

        # P&L
        print(f"  P&L:    Realized={pnl_color}${st['realized_pnl']:+.2f}{RESET}  "
              f"Unreal={unr_color}${st['unrealized_pnl']:+.2f}{RESET}  "
              f"Exposure=${st['exposure']:.0f}")

        # Recent signals
        if st["last_signals"]:
            print(f"  {BOLD}Signals:{RESET}")
            for sig in reversed(st["last_signals"][-3:]):
                if sig["type"] == "BUY":
                    print(f"    {GREEN}▲ BUY{RESET}  {sig['market']:<8} "
                          f"{sig['outcome'][:20]:<20}  "
                          f"${sig['bet']:.0f} @ ${sig['price']:.3f}  "
                          f"edge={GREEN}{sig['edge']}{RESET}  "
                          f"depth=${sig['depth']}  model={sig['model']}")
                else:
                    pnl = sig.get("pnl", 0)
                    c = GREEN if pnl >= 0 else RED
                    print(f"    {YELLOW}▼ SELL{RESET} {sig['market']:<8} "
                          f"{sig['outcome'][:20]:<20}  "
                          f"entry=${sig['entry_price']:.3f} → ${sig['exit_price']:.3f}  "
                          f"P/L={c}${pnl:+.2f}{RESET}")
        else:
            print(f"  {DIM}No signals yet — monitoring...{RESET}")

        print(f"  {DIM}{'─'*68}{RESET}")

    if waiting:
        print(f"\n  {YELLOW}Waiting for GRID data:{RESET}")
        for slug, st in waiting:
            ta, tb = st["teams"]
            print(f"    {DIM}◷ {ta} vs {tb}  [{slug}]{RESET}")

    if finished:
        print(f"\n  {DIM}Finished:{RESET}")
        for slug, st in finished:
            ta, tb = st["teams"]
            pnl_color = GREEN if st["realized_pnl"] >= 0 else RED
            print(f"    ✓ {ta} vs {tb}  "
                  f"P&L={pnl_color}${st['realized_pnl']:+.2f}{RESET}  {DIM}[{slug}]{RESET}")

    # Summary
    total_realized = sum(st["realized_pnl"] for _, st in states)
    total_unreal = sum(st["unrealized_pnl"] for _, st in states)
    total_exposure = sum(st["exposure"] for _, st in states)
    c = GREEN if total_realized >= 0 else RED
    print(f"\n  {BOLD}TOTAL:{RESET}  "
          f"Realized={c}${total_realized:+.2f}{RESET}  "
          f"Unreal=${total_unreal:+.2f}  "
          f"Exposure=${total_exposure:.0f}  │  "
          f"{len(active)} live  {len(waiting)} waiting  {len(finished)} done")
    print(f"{BOLD}{'='*72}{RESET}")
    print(f"{DIM}  Refreshing every 5s — Ctrl+C to quit{RESET}")


def main():
    interval = 5
    try:
        while True:
            # Find all active stdout logs
            logs = sorted(glob.glob(os.path.join(LOG_DIR, "stdout_*.log")),
                         key=os.path.getmtime, reverse=True)

            # Deduplicate by slug (keep newest per slug)
            seen_slugs = set()
            deduped = []
            for log in logs:
                m = re.search(r"stdout_(cs2-[^_]+)_", os.path.basename(log))
                if m:
                    slug = m.group(1)
                    if slug not in seen_slugs:
                        seen_slugs.add(slug)
                        deduped.append((slug, log))

            states = []
            for slug, log in deduped:
                lines = tail(log)
                st = parse_log(lines)
                st["slug"] = slug
                states.append((slug, st))

            clear()
            render(states)
            time.sleep(interval)

    except KeyboardInterrupt:
        print("\nDashboard closed.")


if __name__ == "__main__":
    main()
