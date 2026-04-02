"""
Polymarket CS2 price history scraper.
Pulls historical odds curves for all CS2 markets from the CLOB API.

Usage:
    python scrape_pm_history.py
"""

import json
import os
import time
import sys
import requests
from datetime import datetime, timezone


DATA_DIR = os.path.join("data", "pm_history")
MARKETS_FILE = os.path.join("data", "polymarket_cs2_markets.json")
CLOB_URL = "https://clob.polymarket.com/prices-history"


def load_markets():
    with open(MARKETS_FILE) as f:
        return json.load(f)


def fetch_price_history(token_id: str) -> list:
    """Fetch full price history for a token ID."""
    try:
        resp = requests.get(
            CLOB_URL,
            params={"market": token_id, "interval": "1d", "fidelity": "1"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("history", [])
    except Exception as e:
        print(f"  Error fetching {token_id[:20]}...: {e}")
        return []


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    markets = load_markets()
    print(f"Loaded {len(markets)} CS2 markets")

    # Group by base_slug (match) for organized output
    saved = 0
    skipped = 0
    errors = 0
    total = len(markets)

    for i, m in enumerate(markets):
        slug = m.get("slug", "")
        tokens_raw = m.get("clobTokenIds", "")
        if not tokens_raw:
            skipped += 1
            continue

        tokens = json.loads(tokens_raw)
        if not tokens:
            skipped += 1
            continue

        # Output file per market
        outfile = os.path.join(DATA_DIR, f"{slug}.json")
        if os.path.exists(outfile):
            skipped += 1
            continue

        # Fetch history for both outcome tokens
        histories = {}
        for j, tid in enumerate(tokens):
            hist = fetch_price_history(tid)
            histories[f"outcome_{j}"] = {
                "token_id": tid,
                "history": hist,
            }
            time.sleep(0.15)  # ~6 req/s, gentle

        # Save
        record = {
            "slug": slug,
            "question": m.get("question", ""),
            "conditionId": m.get("conditionId", ""),
            "endDate": m.get("endDate", ""),
            "startDate": m.get("startDate", ""),
            "outcomes": m.get("outcomes", ""),
            "volume": m.get("volume", 0),
            "lastTradePrice": m.get("lastTradePrice", ""),
            "histories": histories,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        }

        total_pts = sum(len(h["history"]) for h in histories.values())

        with open(outfile, "w") as f:
            json.dump(record, f)

        saved += 1
        if saved % 25 == 0 or i < 5:
            print(
                f"[{i+1}/{total}] {slug[:50]:50s} | {total_pts:4d} pts | saved",
                flush=True,
            )

    print(f"\nDone: {saved} saved, {skipped} skipped, {errors} errors")


if __name__ == "__main__":
    main()
