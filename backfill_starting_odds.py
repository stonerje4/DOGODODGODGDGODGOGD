"""
Backfill starting odds from Polymarket price history.

For each CS2 match-winner market:
1. Fetch full market data from Gamma API (gets gameStartTime)
2. Fetch price history from CLOB API
3. Extract the pre-match starting odd (median price 60-10 min before start)
4. Save to data/starting_odds.json

Usage:
    python backfill_starting_odds.py                  # Process all
    python backfill_starting_odds.py --limit 10       # Test with 10
    python backfill_starting_odds.py --resume          # Skip already-fetched
"""

import json
import os
import sys
import time
import argparse
import statistics
import requests
from datetime import datetime, timezone
from typing import Optional, List, Dict

# ── Config ───────────────────────────────────────────────────────────────────

GAMMA_URL = "https://gamma-api.polymarket.com"
CLOB_URL = "https://clob.polymarket.com"
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
OUTPUT_FILE = os.path.join(DATA_DIR, "starting_odds.json")

# Rate limiting — be polite
GAMMA_DELAY = 0.3   # seconds between Gamma requests
CLOB_DELAY = 0.3    # seconds between CLOB requests


# ── Helpers ──────────────────────────────────────────────────────────────────

def parse_ts(s: str) -> Optional[float]:
    """Parse ISO timestamp or 'YYYY-MM-DD HH:MM:SS+00' to unix epoch."""
    if not s:
        return None
    s = s.strip()
    # Handle postgres-style: "2026-03-31 17:54:45+00"
    s = s.replace("+00", "+00:00").replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        return dt.timestamp()
    except Exception:
        return None


def fetch_market_detail(slug: str) -> Optional[dict]:
    """Fetch full market data from Gamma API (includes gameStartTime)."""
    try:
        resp = requests.get(
            f"{GAMMA_URL}/markets",
            params={"slug": slug},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if data and len(data) > 0:
            return data[0]
    except Exception as e:
        print(f"  [WARN] Gamma fetch failed for {slug}: {e}")
    return None


def fetch_price_history(clob_token_id: str) -> Optional[List[dict]]:
    """Fetch price history from CLOB API. Returns [{t, p}, ...]."""
    try:
        resp = requests.get(
            f"{CLOB_URL}/prices-history",
            params={
                "market": clob_token_id,
                "interval": "max",
                "fidelity": "1",
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("history", [])
    except Exception as e:
        print(f"  [WARN] CLOB fetch failed: {e}")
    return None


def extract_starting_odd(
    history: List[dict],
    match_start_ts: float,
    window_start_sec: int = 3600,  # 60 min before
    window_end_sec: int = 600,     # 10 min before
) -> Optional[dict]:
    """
    Extract pre-match starting odd from price history.
    
    Takes median price in the window [start - 60min, start - 10min].
    Falls back to wider windows if needed.
    """
    if not history or not match_start_ts:
        return None

    # Primary window: 60 to 10 min before match
    w_start = match_start_ts - window_start_sec
    w_end = match_start_ts - window_end_sec

    prices_in_window = [
        h["p"] for h in history
        if w_start <= h["t"] <= w_end
    ]

    if len(prices_in_window) >= 2:
        return {
            "starting_odd": round(statistics.median(prices_in_window), 4),
            "window_prices": len(prices_in_window),
            "method": "median_60_10",
        }

    # Fallback 1: wider window, 3h to 5min before
    w_start = match_start_ts - 10800
    w_end = match_start_ts - 300
    prices_in_window = [
        h["p"] for h in history
        if w_start <= h["t"] <= w_end
    ]

    if len(prices_in_window) >= 2:
        return {
            "starting_odd": round(statistics.median(prices_in_window), 4),
            "window_prices": len(prices_in_window),
            "method": "median_3h_5m",
        }

    # Fallback 2: last price before match start
    pre_match = [h for h in history if h["t"] < match_start_ts]
    if pre_match:
        last = pre_match[-1]
        return {
            "starting_odd": round(last["p"], 4),
            "window_prices": 1,
            "method": "last_before_start",
        }

    # Fallback 3: first price in history (market just opened)
    if history:
        return {
            "starting_odd": round(history[0]["p"], 4),
            "window_prices": 1,
            "method": "first_price",
        }

    return None


def get_match_start_from_history(
    history: List[dict], 
    end_date_ts: Optional[float]
) -> Optional[float]:
    """
    Estimate match start from price history inflection.
    
    Look for first big price movement (>10% swing in short time)
    after prices were stable. This is when the match started.
    """
    if not history or len(history) < 5:
        return None

    # Look for rapid movement: price changes > 5% between consecutive points
    for i in range(2, len(history)):
        prev_prices = [history[j]["p"] for j in range(max(0, i-3), i)]
        if not prev_prices:
            continue
        avg_prev = sum(prev_prices) / len(prev_prices)
        if avg_prev == 0:
            continue
        
        curr = history[i]["p"]
        change = abs(curr - avg_prev) / avg_prev
        
        # Big swing after stability = match started
        if change > 0.08:
            # Match started roughly at this point
            return history[i]["t"]
    
    # If no inflection found, use endDate - typical match duration (~2h)
    if end_date_ts:
        return end_date_ts - 7200  # 2 hours before end
    
    return None


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="Max markets to process (0=all)")
    parser.add_argument("--resume", action="store_true", help="Skip already-processed slugs")
    parser.add_argument("--dry-run", action="store_true", help="Don't fetch, just show what would run")
    args = parser.parse_args()

    # Load markets
    markets_file = os.path.join(DATA_DIR, "polymarket_cs2_markets.json")
    with open(markets_file) as f:
        all_markets = json.load(f)

    # Filter to match-winner markets only (no -game, -total, -handicap, -kill, -odd-even)
    match_winners = []
    for m in all_markets:
        slug = m.get("slug", "")
        if not slug.startswith("cs2-"):
            continue
        # Skip sub-markets
        if any(x in slug for x in ["-game", "-total", "-handicap", "-kill", "-odd-even", "-round"]):
            continue
        # Skip zero volume
        vol = float(m.get("volume", 0) or 0)
        if vol < 10:
            continue
        match_winners.append(m)

    print(f"Found {len(match_winners)} match-winner markets with volume >= $10")

    # Load existing results if resuming
    existing = {}
    if args.resume and os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE) as f:
            existing_list = json.load(f)
        existing = {r["slug"]: r for r in existing_list}
        print(f"Loaded {len(existing)} existing results")

    if args.limit > 0:
        match_winners = match_winners[:args.limit]
        print(f"Limited to {args.limit} markets")

    if args.dry_run:
        for m in match_winners[:20]:
            skip = "SKIP" if m["slug"] in existing else "FETCH"
            print(f"  [{skip}] {m['slug']} vol=${float(m.get('volume',0)):.0f}")
        return

    # Process each market
    results = list(existing.values())  # Keep existing results
    processed = 0
    errors = 0
    skipped = 0

    for i, m in enumerate(match_winners):
        slug = m["slug"]

        if slug in existing:
            skipped += 1
            continue

        if i > 0 and i % 50 == 0:
            # Save progress
            with open(OUTPUT_FILE, "w") as f:
                json.dump(results, f, indent=2)
            print(f"  [SAVED] {len(results)} results so far")

        print(f"[{i+1}/{len(match_winners)}] {slug}...", end=" ", flush=True)

        # Step 1: Get full market data from Gamma (for gameStartTime)
        time.sleep(GAMMA_DELAY)
        detail = fetch_market_detail(slug)
        
        game_start_ts = None
        if detail:
            gst = detail.get("gameStartTime")
            if gst:
                game_start_ts = parse_ts(gst)

        # Step 2: Get clobTokenIds
        clob_ids_raw = m.get("clobTokenIds", "")
        if isinstance(clob_ids_raw, str):
            try:
                clob_ids = json.loads(clob_ids_raw)
            except:
                clob_ids = []
        else:
            clob_ids = clob_ids_raw

        if not clob_ids:
            print("NO CLOB IDS")
            errors += 1
            continue

        # Step 3: Fetch price history for outcome 0 (team A)
        time.sleep(CLOB_DELAY)
        history = fetch_price_history(clob_ids[0])

        if not history or len(history) < 3:
            print(f"NO HISTORY ({len(history) if history else 0} pts)")
            errors += 1
            continue

        # Step 4: Determine match start time
        if not game_start_ts:
            # Try to estimate from endDate or closedTime
            end_ts = parse_ts(m.get("endDate", "")) or parse_ts(m.get("closedTime", ""))
            game_start_ts = get_match_start_from_history(history, end_ts)
            
            if not game_start_ts:
                print("NO START TIME")
                errors += 1
                continue

        # Step 5: Extract starting odd
        result = extract_starting_odd(history, game_start_ts)
        
        if not result:
            print("NO STARTING ODD")
            errors += 1
            continue

        # Parse outcomes
        outcomes_raw = m.get("outcomes", "[]")
        if isinstance(outcomes_raw, str):
            try:
                outcomes = json.loads(outcomes_raw)
            except:
                outcomes = []
        else:
            outcomes = outcomes_raw

        record = {
            "slug": slug,
            "team_a": outcomes[0] if outcomes else "?",
            "team_b": outcomes[1] if len(outcomes) > 1 else "?",
            "starting_odd_a": result["starting_odd"],
            "starting_odd_b": round(1 - result["starting_odd"], 4),
            "method": result["method"],
            "data_points": result["window_prices"],
            "volume": float(m.get("volume", 0) or 0),
            "game_start_ts": game_start_ts,
            "game_start_utc": datetime.fromtimestamp(game_start_ts, tz=timezone.utc).isoformat() if game_start_ts else None,
            "history_points": len(history),
        }

        results.append(record)
        existing[slug] = record
        processed += 1
        print(f"{outcomes[0] if outcomes else '?'} {result['starting_odd']:.1%} | {result['method']} ({result['window_prices']} pts)")

    # Save final results
    with open(OUTPUT_FILE, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n{'='*60}")
    print(f"DONE")
    print(f"  Processed: {processed}")
    print(f"  Skipped (existing): {skipped}")
    print(f"  Errors: {errors}")
    print(f"  Total results: {len(results)}")
    print(f"  Saved to: {OUTPUT_FILE}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
