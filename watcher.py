"""
CS2 Match Watcher — continuous overlap scanner + auto-launcher.

Runs forever. Every 15 minutes:
  1. Pull all GRID CS2 series for the next 24h
  2. Pull all open PM CS2 markets (full pagination)
  3. Match them up
  4. For any new matches found: log them + optionally start live.py

Tracks which series are already being monitored so it doesn't double-launch.

Usage:
    python watcher.py                  # scan only, print overlaps
    python watcher.py --auto-launch    # also spawn live.py for each match
    python watcher.py --interval 900   # custom scan interval (seconds)
    python watcher.py --log-dir /path  # custom log dir for live.py
"""

import argparse
import subprocess
import sys
import time
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from find_overlaps import run as find_overlaps
import config

# How long before a match starts to begin monitoring (seconds)
MONITOR_AHEAD_SECS = 60 * 60   # 60 min before scheduled start
# How long after scheduled start to still try (matches run late)
MONITOR_LATE_SECS = 4 * 60 * 60  # 4 hours after scheduled start

def parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def format_dt(dt):
    if not dt:
        return "?"
    return dt.strftime("%H:%M UTC")


def should_monitor_now(scheduled_str):
    """Is this match in the window we want to monitor?"""
    sched = parse_iso(scheduled_str)
    if not sched:
        return True  # Unknown time — monitor anyway
    now = datetime.now(timezone.utc)
    delta = (sched - now).total_seconds()
    return -MONITOR_LATE_SECS <= delta <= MONITOR_AHEAD_SECS


def main():
    parser = argparse.ArgumentParser(description="CS2 match watcher")
    parser.add_argument("--auto-launch", action="store_true",
                        help="Spawn live.py for each new overlap found")
    parser.add_argument("--interval", type=int, default=900,
                        help="Scan interval in seconds (default 900 = 15 min)")
    parser.add_argument("--log-dir", type=str, default=None,
                        help="Log dir passed to live.py")
    parser.add_argument("--hours", type=int, default=24,
                        help="Hours ahead to scan GRID (default 24)")
    args = parser.parse_args()

    # Series IDs we've already launched or decided to skip
    launched: set = set()
    procs: dict = {}  # series_id → subprocess.Popen

    print("=" * 70)
    print("CS2 MATCH WATCHER")
    print(f"  Scan interval:  {args.interval}s ({args.interval//60} min)")
    print(f"  Auto-launch:    {args.auto_launch}")
    print(f"  Monitor window: {MONITOR_AHEAD_SECS//60} min before → "
          f"{MONITOR_LATE_SECS//3600}h after start")
    print("=" * 70)
    print()

    scan_count = 0
    while True:
        now = datetime.now(timezone.utc).strftime("%H:%M:%S")
        scan_count += 1
        print(f"\n[{now}] ── Scan #{scan_count} ──────────────────────────────")

        # ── Reap finished processes ──────────────────────────────────
        finished = [sid for sid, p in procs.items()
                    if p.poll() is not None]
        for sid in finished:
            ret = procs.pop(sid).returncode
            print(f"  [watcher] live.py for series {sid} finished "
                  f"(exit {ret})")

        # ── Find overlaps ────────────────────────────────────────────
        try:
            overlaps = find_overlaps(hours=args.hours)
        except Exception as e:
            print(f"  [ERROR] Overlap scan failed: {e}")
            import traceback; traceback.print_exc()
            time.sleep(args.interval)
            continue

        if not overlaps:
            print(f"  No overlaps found. Currently running: "
                  f"{len(procs)} processes.")
            time.sleep(args.interval)
            continue

        # ── Process each overlap ────────────────────────────────────
        new_count = 0
        for match in overlaps:
            g = match["grid"]
            p = match["polymarket"]
            sid = g["series_id"]
            slug = p["slug"]
            sched = g["scheduled"]
            teams = f"{g['teams'][0]} vs {g['teams'][1]}"
            liq = p["liquidity"]

            in_window = should_monitor_now(sched)

            # Skip already launched
            if sid in launched:
                status = "RUNNING" if sid in procs else "DONE"
                print(f"  [{status}] {teams} (series {sid})")
                continue

            # Skip if not in monitoring window yet
            if not in_window:
                sched_dt = parse_iso(sched)
                mins = int((sched_dt - datetime.now(timezone.utc)
                            ).total_seconds() / 60) if sched_dt else 0
                print(f"  [PENDING {mins}min] {teams} → {slug}")
                continue

            # Skip very low liquidity markets (under $30 - basically no market)
            if liq < 30:
                print(f"  [SKIP low liq ${liq:.0f}] {teams} → {slug}")
                launched.add(sid)
                continue

            # New match in window!
            new_count += 1
            print(f"\n  ★ NEW MATCH: {teams}")
            print(f"    Series: {sid}  |  Slug: {slug}")
            print(f"    Start: {format_dt(parse_iso(sched))}  "
                  f"|  Liquidity: ${liq:,.0f}")
            print(f"    Tournament: {g['tournament']}")

            if args.auto_launch:
                cmd = [
                    sys.executable, "live.py",
                    "--series", str(sid),
                    "--pm-slug", slug,
                ]
                if args.log_dir:
                    cmd += ["--log-dir", args.log_dir]

                print(f"    → Launching: {' '.join(cmd)}")
                try:
                    proc = subprocess.Popen(
                        cmd,
                        cwd=os.path.dirname(__file__),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                    )
                    procs[sid] = proc
                    print(f"    → PID {proc.pid}")
                except Exception as e:
                    print(f"    → FAILED to launch: {e}")
            else:
                print(f"    → Run: python live.py "
                      f"--series {sid} --pm-slug {slug}")

            launched.add(sid)

        if new_count == 0:
            print(f"  No new matches. Running: {len(procs)} | "
                  f"Tracked: {len(launched)}")

        # Print live process output (non-blocking)
        if procs:
            print(f"\n  ── Live processes output ──")
            for sid, proc in list(procs.items()):
                lines = []
                if proc.stdout:
                    import select
                    try:
                        while select.select([proc.stdout], [], [], 0)[0]:
                            line = proc.stdout.readline()
                            if line:
                                lines.append(line.rstrip())
                    except Exception:
                        pass
                if lines:
                    series_teams = next(
                        (f"{m['grid']['teams'][0]} vs {m['grid']['teams'][1]}"
                         for m in overlaps if m["grid"]["series_id"] == sid),
                        sid
                    )
                    print(f"\n  [{series_teams}]")
                    for line in lines[-10:]:  # Last 10 lines
                        print(f"    {line}")

        print(f"\n  Next scan in {args.interval}s "
              f"({args.interval//60}min)... Ctrl+C to stop")
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nWatcher stopped.")
