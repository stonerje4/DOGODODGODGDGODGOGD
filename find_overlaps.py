"""
Find CS2 matches that exist on BOTH GRID.gg and Polymarket.
Uses correct discovery methods for both platforms.

Usage:
    python find_overlaps.py                    # today
    python find_overlaps.py --date 2026-04-03  # specific date
    python find_overlaps.py --hours 48         # next 48 hours
"""

import argparse
import json
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Tuple

from grid_client import GridClient
from polymarket_client import PolymarketClient
import config


# ── Team name normalization ──────────────────────────────────────────────────

KNOWN_ALIASES = {
    "natus vincere": "navi",
    "team liquid": "liquid",
    "team vitality": "vitality",
    "g2 esports": "g2",
    "faze clan": "faze",
    "cloud9": "c9",
    "100 thieves": "100t",
    "heroic academy": "heroic academy",
}


def normalize(name: str) -> str:
    """Normalize a team name for comparison."""
    n = name.lower().strip()
    # Strip common suffixes
    for sfx in [" esports", " esport", " gaming", " academy"]:
        if n.endswith(sfx):
            n = n[:-len(sfx)].strip()
    for pfx in ["team "]:
        if n.startswith(pfx):
            n = n[len(pfx):].strip()
    return KNOWN_ALIASES.get(n, n)


def _name_similar(a: str, b: str) -> bool:
    """Check if two normalized team names plausibly refer to the same team."""
    if not a or not b:
        return False
    if a == b:
        return True
    # Substring match only if the shorter string is ≥4 chars
    # (avoids false positives like 'esc' matching random things)
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) >= 4 and shorter in longer:
        return True
    return False


def teams_match(names_a: List[str], names_b: List[str]) -> bool:
    """Check if two team name pairs refer to the same matchup.
    
    Requires BOTH teams to match (bipartite), not just one.
    """
    norm_a = [normalize(n) for n in names_a]
    norm_b = [normalize(n) for n in names_b]
    if set(norm_a) == set(norm_b):
        return True
    if len(norm_a) < 2 or len(norm_b) < 2:
        return False
    # Try both orderings: a0↔b0,a1↔b1  or  a0↔b1,a1↔b0
    straight = _name_similar(norm_a[0], norm_b[0]) and _name_similar(norm_a[1], norm_b[1])
    crossed  = _name_similar(norm_a[0], norm_b[1]) and _name_similar(norm_a[1], norm_b[0])
    return straight or crossed


# ── PM discovery: paginate all open markets, filter cs2- slugs ──────────────

def get_all_pm_cs2_markets(pm: PolymarketClient) -> List[dict]:
    """
    Paginate ALL open Polymarket markets and filter to CS2.
    This is the ONLY reliable method — PM search and tag filters are broken.
    Returns only match-winner markets (no -game1, -handicap etc.)
    """
    cs2_markets = []
    offset = 0
    limit = 200
    seen_slugs = set()

    while True:
        try:
            resp = __import__("requests").get(
                f"{pm.gamma_url}/markets",
                params={
                    "closed": "false",
                    "limit": limit,
                    "order": "createdAt",
                    "ascending": "false",
                    "offset": offset,
                },
                timeout=20,
            )
            resp.raise_for_status()
            page = resp.json()
        except Exception as e:
            print(f"  [WARN] PM pagination error at offset {offset}: {e}")
            break

        if not page:
            break

        for mkt in page:
            slug = mkt.get("slug", "")
            if not slug.startswith("cs2-"):
                continue
            # Only keep match-winner markets (no map/handicap/ou variants)
            if any(x in slug for x in [
                "-game1", "-game2", "-game3",
                "-total-games", "-handicap", "-odd-even",
                "-map-", "-first-", "-pistol-",
            ]):
                continue
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)
            cs2_markets.append(mkt)

        # If page was smaller than limit, we've hit the end
        if len(page) < limit:
            break
        offset += limit

    return cs2_markets


# ── GRID discovery: CS2 only, titleId=28 ────────────────────────────────────

def get_grid_cs2_series(
    grid: GridClient, start: str, end: str
) -> List[dict]:
    """Get CS2 series from GRID using titleId=28 (correct CS2 title ID)."""
    return grid.get_series_by_date_range(start, end, title_id="28")


# ── Matching ────────────────────────────────────────────────────────────────

def match_grid_to_pm(
    grid_series: List[dict],
    pm_markets: List[dict],
) -> List[Dict]:
    """
    Match GRID series to PM markets by team names + date proximity.
    Returns list of overlap dicts with both sources and the base PM slug.
    """
    overlaps = []
    seen_series_ids = set()

    # Index GRID by (date, norm_team_set)
    grid_index: Dict[Tuple, dict] = {}
    for s in grid_series:
        teams = [t.get("baseInfo", {}).get("name", "")
                 for t in s.get("teams", [])]
        if len(teams) < 2:
            continue
        sched = s.get("startTimeScheduled", "")
        date = sched[:10] if sched else ""
        key = (date, frozenset(normalize(t) for t in teams))
        grid_index[key] = {
            "series_id": s["id"],
            "teams": teams,
            "tournament": (s.get("tournament") or {}).get("name", ""),
            "scheduled": sched,
            "format": s.get("format", ""),
        }

    for pm in pm_markets:
        outcomes_raw = pm.get("outcomes", "[]")
        import json as _json
        outcomes = _json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
        if len(outcomes) < 2:
            continue

        pm_teams = outcomes[:2]
        slug = pm.get("slug", "")

        # Extract date from slug (cs2-team1-team2-YYYY-MM-DD)
        parts = slug.split("-")
        date_str = ""
        for i, p in enumerate(parts):
            if len(p) == 4 and p.isdigit() and i + 2 < len(parts):
                date_str = f"{p}-{parts[i+1]}-{parts[i+2]}"
                break

        # Try matching on exact date, then ±1 day
        matched = None
        for delta in [0, -1, 1]:
            try:
                d = datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=delta)
                ds = d.strftime("%Y-%m-%d")
            except Exception:
                ds = date_str
            key = (ds, frozenset(normalize(t) for t in pm_teams))
            if key in grid_index:
                matched = grid_index[key]
                break

        # Fallback: check all GRID series for this date range by team name alone
        if not matched and date_str:
            for gkey, gdata in grid_index.items():
                gdate, gteams_frozen = gkey
                if abs((datetime.strptime(gdate, "%Y-%m-%d") if gdate else
                        datetime.min) -
                       (datetime.strptime(date_str, "%Y-%m-%d")
                        if date_str else datetime.min)).days <= 1:
                    if teams_match(pm_teams, list(gteams_frozen)):
                        matched = gdata
                        break

        if not matched:
            continue

        sid = matched["series_id"]
        if sid in seen_series_ids:
            continue
        seen_series_ids.add(sid)

        # Determine PM base slug (strip map suffix if any)
        base_slug = slug

        overlaps.append({
            "grid": matched,
            "polymarket": {
                "slug": base_slug,
                "teams": pm_teams,
                "volume": float(pm.get("volume", 0) or 0),
                "liquidity": float(pm.get("liquidity", 0) or 0),
                "prices": pm.get("outcomePrices", ""),
                "game_start_time": pm.get("gameStartTime", ""),
            },
        })

    return overlaps


# ── Main ─────────────────────────────────────────────────────────────────────

def run(date: str = None, hours: int = 24) -> List[Dict]:
    """Run overlap discovery. Returns list of matched pairs."""
    now_utc = datetime.now(timezone.utc)
    if date:
        start_dt = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    else:
        start_dt = now_utc

    end_dt = start_dt + timedelta(hours=hours)
    start_str = start_dt.strftime("%Y-%m-%dT00:00:00Z") if date else \
        now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_str = end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    grid = GridClient()
    pm = PolymarketClient()

    # ── GRID ────────────────────────────────────────────────────────
    print(f"[1/3] Pulling GRID CS2 series ({start_str} → {end_str})...")
    grid_series = get_grid_cs2_series(grid, start_str, end_str)
    print(f"  Found {len(grid_series)} CS2 series")
    if grid_series:
        tournaments = sorted({
            (s.get("tournament") or {}).get("name", "?")
            for s in grid_series
        })
        print(f"  Tournaments: {', '.join(tournaments[:8])}"
              f"{'...' if len(tournaments) > 8 else ''}")

    # ── Polymarket ──────────────────────────────────────────────────
    print("\n[2/3] Pulling ALL open PM markets (paginating, cs2- filter)...")
    pm_markets = get_all_pm_cs2_markets(pm)
    print(f"  Found {len(pm_markets)} CS2 match-winner markets")

    # ── Match ───────────────────────────────────────────────────────
    print("\n[3/3] Matching by team names + date...")
    overlaps = match_grid_to_pm(grid_series, pm_markets)

    # ── Output ──────────────────────────────────────────────────────
    print(f"\n{'=' * 70}")
    print(f"OVERLAPPING MATCHES: {len(overlaps)}")
    print(f"{'=' * 70}")

    if not overlaps:
        print("\n  Nothing found. Possible reasons:")
        print("  - No CS2 matches on BOTH platforms right now")
        print("  - Team name mismatch (add to KNOWN_ALIASES in find_overlaps.py)")
        print("  - PM market not yet created for upcoming match")
        print("\n  GRID series found (no PM match):")
        for s in grid_series[:10]:
            teams = [t.get("baseInfo", {}).get("name", "?")
                     for t in s.get("teams", [])]
            print(f"    [{s.get('startTimeScheduled','?')[:16]}] "
                  f"{' vs '.join(teams)} — "
                  f"{(s.get('tournament') or {}).get('name','?')}")
        return []

    for i, match in enumerate(overlaps):
        g = match["grid"]
        p = match["polymarket"]
        prices_raw = p["prices"]
        import json as _j
        try:
            prices = _j.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
            price_str = (f"{p['teams'][0]} {float(prices[0]):.0%} / "
                         f"{p['teams'][1]} {float(prices[1]):.0%}"
                         if prices else "?")
        except Exception:
            price_str = str(prices_raw)

        start_fmt = g["scheduled"][:16].replace("T", " ") if g["scheduled"] else "?"
        print(f"\n  [{i+1}] {g['teams'][0]} vs {g['teams'][1]}")
        print(f"       Tournament: {g['tournament']}")
        print(f"       Start (UTC): {start_fmt}  |  Format: {g.get('format','?')}")
        print(f"       GRID ID: {g['series_id']}")
        print(f"       PM Slug: {p['slug']}")
        print(f"       PM Prices: {price_str}")
        print(f"       Volume: ${p['volume']:,.0f}  |  Liquidity: ${p['liquidity']:,.0f}")
        print(f"\n       → python live.py --series {g['series_id']} --pm-slug {p['slug']}")

    print(f"\n{'=' * 70}")
    return overlaps


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Find GRID + PM CS2 overlaps")
    parser.add_argument("--date", type=str, default=None,
                        help="Start date YYYY-MM-DD (default: now)")
    parser.add_argument("--hours", type=int, default=24,
                        help="Hours ahead to scan (default: 24)")
    args = parser.parse_args()
    run(args.date, args.hours)
