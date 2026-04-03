"""
CS2 Match Watcher — continuous overlap scanner + auto-launcher.

Runs forever. Every 15 minutes:
  1. Pull all GRID CS2 series for the next 24h
  2. Pull all open PM CS2 markets (full pagination)
  3. Match them up
  4. For any new matches found: start live.py

Persists launched series IDs to disk so restarts don't double-launch.
Detects already-running live.py processes on startup.

Usage:
    python watcher.py --auto-launch              # scan + launch
    python watcher.py --auto-launch --interval 900  # custom interval
    python watcher.py --auto-launch --log-dir /path  # custom log dir
"""

import argparse
import subprocess
import sys
import time
import os
import json
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from find_overlaps import run as find_overlaps
import config

# How long before a match starts to begin monitoring (seconds)
MONITOR_AHEAD_SECS = 60 * 60   # 60 min before scheduled start
# How long after scheduled start to still try (matches run late)
MONITOR_LATE_SECS = 4 * 60 * 60  # 4 hours after scheduled start

STATE_FILE = os.path.join(os.path.dirname(__file__), "data", "watcher_state.json")


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
        return True
    now = datetime.now(timezone.utc)
    delta = (sched - now).total_seconds()
    return -MONITOR_LATE_SECS <= delta <= MONITOR_AHEAD_SECS


def load_state():
    """Load persisted state (launched series IDs + timestamps)."""
    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
        # Expire entries older than 12 hours
        cutoff = time.time() - 12 * 3600
        launched = {
            k: v for k, v in data.get("launched", {}).items()
            if v.get("ts", 0) > cutoff
        }
        return launched
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(launched):
    """Persist launched series IDs to disk."""
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump({"launched": launched, "updated": time.time()}, f)


def find_running_live_pids():
    """Find already-running live.py processes and their series IDs."""
    running = {}
    try:
        import re
        result = subprocess.run(
            ["ps", "aux"], capture_output=True, text=True, timeout=5)
        for line in result.stdout.splitlines():
            if "live.py" in line and "--series" in line:
                # Extract PID and series ID
                parts = line.split()
                pid = int(parts[1])
                m = re.search(r"--series\s+(\d+)", line)
                if m:
                    sid = m.group(1)
                    running[sid] = pid
    except Exception as e:
        print(f"  [WARN] Could not scan for running live.py: {e}")
    return running


def launch_live(series_id, slug, log_dir):
    """Launch live.py as a fully detached process with its own log file."""
    cmd = [
        sys.executable, "-u", "live.py",
        "--series", str(series_id),
        "--pm-slug", slug,
    ]
    if log_dir:
        cmd += ["--log-dir", log_dir]

    # Each live.py gets its own stdout log file
    stdout_log = os.path.join(
        log_dir or "data/live_logs",
        f"stdout_{slug}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.log"
    )
    os.makedirs(os.path.dirname(stdout_log), exist_ok=True)

    print(f"    → Launching: {' '.join(cmd)}")
    print(f"    → Log: {stdout_log}")
    try:
        log_fh = open(stdout_log, "w")
        proc = subprocess.Popen(
            cmd,
            cwd=os.path.dirname(os.path.abspath(__file__)),
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            # start_new_session=True makes the child survive parent death
            start_new_session=True,
        )
        print(f"    → PID {proc.pid}")
        return proc.pid, stdout_log
    except Exception as e:
        print(f"    → FAILED to launch: {e}")
        return None, None


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

    # Load persisted state
    launched = load_state()
    print(f"Loaded {len(launched)} previously launched series from state file")

    # Detect already-running live.py processes
    running_pids = find_running_live_pids()
    if running_pids:
        print(f"Found {len(running_pids)} live.py processes already running:")
        for sid, pid in running_pids.items():
            print(f"  Series {sid} → PID {pid}")
            if sid not in launched:
                launched[sid] = {"ts": time.time(), "pid": pid, "recovered": True}
        save_state(launched)

    print("=" * 70)
    print("CS2 MATCH WATCHER")
    print(f"  Scan interval:  {args.interval}s ({args.interval//60} min)")
    print(f"  Auto-launch:    {args.auto_launch}")
    print(f"  Monitor window: {MONITOR_AHEAD_SECS//60} min before → "
          f"{MONITOR_LATE_SECS//3600}h after start")
    print(f"  State file:     {STATE_FILE}")
    print(f"  Tracked:        {len(launched)} series")
    print("=" * 70)
    print()

    scan_count = 0
    while True:
        now = datetime.now(timezone.utc).strftime("%H:%M:%S")
        scan_count += 1
        print(f"\n[{now}] ── Scan #{scan_count} ──────────────────────────────")

        # ── Check which launched processes are still alive ────────
        running_pids = find_running_live_pids()
        running_series = set(running_pids.keys())
        done_series = [
            sid for sid in launched
            if sid not in running_series and launched[sid].get("pid")
        ]
        for sid in done_series:
            if not launched[sid].get("done"):
                launched[sid]["done"] = True
                print(f"  [DONE] Series {sid} — live.py exited")
        save_state(launched)

        # ── Find overlaps ────────────────────────────────────────
        try:
            overlaps = find_overlaps(hours=args.hours)
        except Exception as e:
            print(f"  [ERROR] Overlap scan failed: {e}")
            import traceback; traceback.print_exc()
            time.sleep(args.interval)
            continue

        if not overlaps:
            print(f"  No overlaps found. Running: {len(running_series)}")
            time.sleep(args.interval)
            continue

        # ── Process each overlap ─────────────────────────────────
        new_count = 0
        for match in overlaps:
            g = match["grid"]
            p = match["polymarket"]
            sid = str(g["series_id"])
            slug = p["slug"]
            sched = g["scheduled"]
            teams = f"{g['teams'][0]} vs {g['teams'][1]}"
            liq = p["liquidity"]

            in_window = should_monitor_now(sched)

            # Skip already launched
            if sid in launched:
                is_running = sid in running_series
                status = "RUNNING" if is_running else "DONE"
                print(f"  [{status}] {teams} (series {sid})")
                continue

            # Skip if not in monitoring window yet
            if not in_window:
                sched_dt = parse_iso(sched)
                mins = int((sched_dt - datetime.now(timezone.utc)
                            ).total_seconds() / 60) if sched_dt else 0
                print(f"  [PENDING {mins}min] {teams} → {slug}")
                continue

            # Skip low liquidity markets
            if liq < config.MIN_LIQUIDITY:
                print(f"  [SKIP low liq ${liq:,.0f} < ${config.MIN_LIQUIDITY:,.0f}] {teams} → {slug}")
                launched[sid] = {"ts": time.time(), "skipped": True}
                save_state(launched)
                continue

            # New match in window!
            new_count += 1
            print(f"\n  ★ NEW MATCH: {teams}")
            print(f"    Series: {sid}  |  Slug: {slug}")
            print(f"    Start: {format_dt(parse_iso(sched))}  "
                  f"|  Liquidity: ${liq:,.0f}")
            print(f"    Tournament: {g['tournament']}")

            if args.auto_launch:
                pid, log_path = launch_live(sid, slug, args.log_dir)
                launched[sid] = {
                    "ts": time.time(),
                    "pid": pid,
                    "slug": slug,
                    "teams": teams,
                    "log": log_path,
                }
                save_state(launched)
            else:
                print(f"    → Run: python live.py "
                      f"--series {sid} --pm-slug {slug}")

            launched[sid] = launched.get(sid, {"ts": time.time()})
            if "slug" not in launched[sid]:
                launched[sid]["slug"] = slug
            save_state(launched)

        if new_count == 0:
            print(f"  No new matches. Running: {len(running_series)} | "
                  f"Tracked: {len(launched)}")

        # ── Show brief status of running processes ───────────────
        if running_series:
            print(f"\n  ── Running live.py processes: {len(running_series)} ──")
            for sid, pid in running_pids.items():
                info = launched.get(sid, {})
                slug = info.get("slug", "?")
                teams = info.get("teams", "?")
                print(f"    PID {pid}: {teams} ({slug})")

        print(f"\n  Next scan in {args.interval}s "
              f"({args.interval//60}min)... Ctrl+C to stop")
        sys.stdout.flush()
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nWatcher stopped.")
