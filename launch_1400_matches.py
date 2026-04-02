"""
Wait for the 14:00 UTC matches to start, then launch loggers.
Run this and walk away — it auto-starts when GRID shows 'started'.
"""
import time
import subprocess
import sys
from datetime import datetime, timezone
from grid_client import GridClient

MATCHES = [
    {
        "series": "2912551",
        "slug": "cs2-ast-meg1-2026-04-02",
        "prior": 0.61,
        "team_a": "ASTRAL",
    },
    {
        "series": "2912648",
        "slug": "cs2-ecs-ursa-2026-04-02",
        "prior": 0.605,
        "team_a": "ECSTATIC",
    },
]

grid = GridClient()
launched = set()

print("Waiting for 14:00 UTC matches to start...")
print(f"  ASTRAL vs megoshort (prior: ASTRAL 61c)")
print(f"  ECSTATIC vs Ursa (prior: ECSTATIC 60.5c)")
print()

while len(launched) < len(MATCHES):
    now = datetime.now(timezone.utc).strftime("%H:%M:%S")
    for m in MATCHES:
        if m["series"] in launched:
            continue
        state = grid.get_series_state(m["series"])
        if state and state.get("started") and not state.get("finished"):
            print(f"[{now}] {m['team_a']} match STARTED! Launching logger...")
            proc = subprocess.Popen(
                [sys.executable, "-u", "live_logger_v2.py",
                 "--series", m["series"],
                 "--pm-slug", m["slug"],
                 "--prior", str(m["prior"]),
                 "--team-a", m["team_a"],
                 "--interval", "12"],
                stdout=open(f"log_{m['slug']}_console.txt", "w"),
                stderr=subprocess.STDOUT,
            )
            print(f"  PID: {proc.pid}")
            launched.add(m["series"])

    if len(launched) < len(MATCHES):
        time.sleep(30)

print(f"\nAll {len(launched)} loggers launched. They'll run until matches finish.")
print("Check Excel files in this directory for results.")
