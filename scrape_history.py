"""
Historical data scraper.
Pulls completed CS2 matches from GRID and saves round-by-round data locally.
This is the training data pipeline for the win probability model.

Usage:
    python scrape_history.py --months 3
    python scrape_history.py --start 2026-01-01 --end 2026-03-30
"""

import os
import json
import time
import argparse
from datetime import datetime, timedelta, timezone
from grid_client import GridClient
import config


def scrape_series_ids(client: GridClient, start: str, end: str) -> list:
    """Get all series IDs in a date range from central data."""
    print(f"Fetching series list from {start} to {end}...")
    series = client.get_series_by_date_range(start, end)
    print(f"  Found {len(series)} series")
    return series


def scrape_series_states(client: GridClient, series_list: list,
                         output_dir: str, skip_existing: bool = True):
    """
    Pull full round-by-round state for each series and save to JSON.
    One file per series: data/{series_id}.json
    """
    os.makedirs(output_dir, exist_ok=True)

    total = len(series_list)
    saved = 0
    skipped = 0
    errors = 0

    for i, series in enumerate(series_list):
        sid = series["id"]
        filepath = os.path.join(output_dir, f"{sid}.json")

        if skip_existing and os.path.exists(filepath):
            skipped += 1
            continue

        print(f"  [{i+1}/{total}] Fetching series {sid}...", end=" ")

        state = client.get_series_state(sid)
        if state is None:
            print("ERROR")
            errors += 1
            continue

        # Skip unfinished or forfeited matches
        if not state.get("finished"):
            print("not finished, skipping")
            skipped += 1
            continue

        if state.get("forfeited"):
            print("forfeited, skipping")
            skipped += 1
            continue

        # Check it has actual round data
        games = state.get("games", [])
        has_rounds = any(
            len(g.get("segments", [])) > 0 for g in games
        )
        if not has_rounds:
            print("no round data, skipping")
            skipped += 1
            continue

        # Attach central data metadata
        state["_meta"] = {
            "tournament": series.get("tournament", {}).get("name", ""),
            "scheduled": series.get("startTimeScheduled", ""),
            "title": (series.get("title") or {}).get("nameShortened", ""),
            "teams_central": [
                t.get("baseInfo", {}).get("name", "")
                for t in series.get("teams", [])
            ],
        }

        with open(filepath, "w") as f:
            json.dump(state, f)

        saved += 1
        print(f"OK ({len(games)} maps, "
              f"{sum(len(g.get('segments',[])) for g in games)} rounds)")

    print(f"\nDone: {saved} saved, {skipped} skipped, {errors} errors")
    print(f"Total requests: {client.request_count}")


def main():
    parser = argparse.ArgumentParser(description="Scrape GRID historical data")
    parser.add_argument("--months", type=int, default=1,
                        help="How many months back to scrape")
    parser.add_argument("--start", type=str, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, help="End date (YYYY-MM-DD)")
    parser.add_argument("--output", type=str, default=None,
                        help="Output directory")
    args = parser.parse_args()

    if args.start and args.end:
        start_dt = datetime.strptime(args.start, "%Y-%m-%d")
        end_dt = datetime.strptime(args.end, "%Y-%m-%d")
    else:
        end_dt = datetime.now(timezone.utc).replace(tzinfo=None)
        start_dt = end_dt - timedelta(days=30 * args.months)

    output_dir = args.output or os.path.join(config.DATA_DIR, "series")
    client = GridClient()
    client.min_delay = 1.5  # Be gentle with rate limits

    # Scrape in weekly chunks to avoid massive single queries
    chunk_start = start_dt
    all_series = []
    while chunk_start < end_dt:
        chunk_end = min(chunk_start + timedelta(days=7), end_dt)
        s = chunk_start.strftime("%Y-%m-%dT00:00:00Z")
        e = chunk_end.strftime("%Y-%m-%dT23:59:59Z")
        series = scrape_series_ids(client, s, e)
        all_series.extend(series)
        chunk_start = chunk_end + timedelta(days=1)

    print(f"\nTotal series found: {len(all_series)}")

    # Step 2: Pull round data for each
    scrape_series_states(client, all_series, output_dir)


if __name__ == "__main__":
    main()
