"""
Find matches that exist on BOTH GRID.gg and Polymarket.
This is the match discovery tool — run it to see what's tradeable right now.

Usage:
    python find_overlaps.py
    python find_overlaps.py --date 2026-04-01
"""

import argparse
from datetime import datetime, timezone
from typing import List, Dict, Tuple

from grid_client import GridClient
from polymarket_client import PolymarketClient


def normalize_team_name(name: str) -> str:
    """Normalize team names for fuzzy matching."""
    name = name.lower().strip()
    # Common substitutions
    replacements = {
        "team liquid": "liquid",
        "team vitality": "vitality",
        "natus vincere": "navi",
        "g2 esports": "g2",
        "faze clan": "faze",
        "cloud9": "c9",
        "100 thieves": "100t",
        "heroic academy": "heroic academy",
        "heroic acad": "heroic academy",
    }
    for full, short in replacements.items():
        if full in name:
            return short
    return name


def find_overlapping_matches(
    grid_series: List[dict],
    pm_markets: List[dict],
) -> List[Dict]:
    """
    Match GRID series to Polymarket markets by team names.
    Returns list of matched pairs with both data sources.
    """
    overlaps = []

    # Build lookup from GRID: normalize team names → series data
    grid_by_teams = {}
    for s in grid_series:
        teams = [t.get("baseInfo", {}).get("name", "")
                 for t in s.get("teams", [])]
        if len(teams) >= 2:
            key = tuple(sorted(normalize_team_name(t) for t in teams))
            grid_by_teams[key] = {
                "series_id": s["id"],
                "teams": teams,
                "tournament": s.get("tournament", {}).get("name", ""),
                "scheduled": s.get("startTimeScheduled", ""),
            }

    # Try to match each Polymarket market
    for pm in pm_markets:
        # Extract team names from Polymarket
        outcomes = pm.get("outcomes", [])
        if not outcomes or len(outcomes) < 2:
            continue

        if isinstance(outcomes[0], str):
            pm_teams = outcomes[:2]
        else:
            pm_teams = [o.get("name", "") for o in outcomes[:2]]

        pm_key = tuple(sorted(normalize_team_name(t) for t in pm_teams))

        # Direct match
        if pm_key in grid_by_teams:
            grid_data = grid_by_teams[pm_key]
            overlaps.append({
                "grid": grid_data,
                "polymarket": {
                    "slug": pm.get("slug", ""),
                    "teams": pm_teams,
                    "prices": pm.get("outcomePrices", ""),
                    "volume": pm.get("volume", 0),
                    "liquidity": pm.get("liquidity", 0),
                    "market_id": pm.get("id", ""),
                },
            })
            continue

        # Fuzzy match: check if any GRID team name is substring of PM team
        for grid_key, grid_data in grid_by_teams.items():
            grid_names = set(grid_key)
            pm_names = set(pm_key)
            # Check overlap
            if grid_names & pm_names:
                overlaps.append({
                    "grid": grid_data,
                    "polymarket": {
                        "slug": pm.get("slug", ""),
                        "teams": pm_teams,
                        "prices": pm.get("outcomePrices", ""),
                        "volume": pm.get("volume", 0),
                        "liquidity": pm.get("liquidity", 0),
                        "market_id": pm.get("id", ""),
                    },
                    "fuzzy_match": True,
                })
                break

    return overlaps


def main():
    parser = argparse.ArgumentParser(description="Find GRID + Polymarket overlaps")
    parser.add_argument("--date", type=str, default=None,
                        help="Date to check (YYYY-MM-DD), default=today")
    args = parser.parse_args()

    if args.date:
        date = args.date
    else:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    print(f"Finding overlapping matches for {date}...\n")

    # ── Pull GRID ────────────────────────────────────────────────────────
    print("[1/3] Pulling GRID.gg series...")
    grid = GridClient()
    grid_series = grid.get_series_by_date_range(
        f"{date}T00:00:00Z", f"{date}T23:59:59Z")
    print(f"  Found {len(grid_series)} GRID series")

    # ── Pull Polymarket ──────────────────────────────────────────────────
    print("\n[2/3] Pulling Polymarket CS2 markets...")
    pm = PolymarketClient()

    # Search for CS2 matches
    pm_markets = pm.search_markets("Counter-Strike", closed=False, limit=100)
    # Also try specific team searches for better coverage
    for team in ["ECSTATIC", "Nemesis", "3DMAX", "Fire Flux", "TYLOO"]:
        extra = pm.search_markets(team, closed=False, limit=20)
        slugs_seen = {m.get("slug") for m in pm_markets}
        for m in extra:
            if m.get("slug") not in slugs_seen:
                pm_markets.append(m)

    print(f"  Found {len(pm_markets)} Polymarket markets")

    # ── Find overlaps ────────────────────────────────────────────────────
    print("\n[3/3] Matching teams...")
    overlaps = find_overlapping_matches(grid_series, pm_markets)

    if not overlaps:
        print("\n  No overlapping matches found!")
        print("  This could mean:")
        print("  - Different tournaments covered by each platform")
        print("  - Team name mismatches (try adding to normalize_team_name)")
        print("  - No CS2 matches today on Polymarket")
        return

    # ── Display results ──────────────────────────────────────────────────
    print(f"\n{'=' * 70}")
    print(f"OVERLAPPING MATCHES: {len(overlaps)}")
    print(f"{'=' * 70}")

    for i, match in enumerate(overlaps):
        g = match["grid"]
        p = match["polymarket"]
        fuzzy = " (fuzzy)" if match.get("fuzzy_match") else ""

        print(f"\n  Match {i+1}{fuzzy}:")
        print(f"    Teams: {g['teams'][0]} vs {g['teams'][1]}")
        print(f"    Tournament: {g['tournament']}")
        print(f"    Scheduled: {g['scheduled']}")
        print(f"    GRID Series ID: {g['series_id']}")
        print(f"    PM Slug: {p['slug']}")
        print(f"    PM Volume: ${float(p.get('volume', 0)):,.0f}")
        print(f"    PM Liquidity: ${float(p.get('liquidity', 0)):,.0f}")
        print(f"    PM Prices: {p['prices']}")

        print(f"\n    To run live edge detector:")
        print(f"    python live_edge.py --series {g['series_id']} "
              f"--pm-slug {p['slug']}")

    print(f"\n{'=' * 70}")


if __name__ == "__main__":
    main()
