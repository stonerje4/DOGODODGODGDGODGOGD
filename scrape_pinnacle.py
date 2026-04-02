"""
Pinnacle odds scraper for pre-match priors.

Pinnacle is the sharpest sportsbook — their lines are the closest thing
to "true odds" in the market. We use these as pre-match priors for maps
that haven't started yet.

This scrapes their public odds page. No API key needed.
Pinnacle doesn't have a public API, so we scrape or use third-party
odds APIs that aggregate Pinnacle lines.

Fallback sources:
- odds-api.com (aggregates multiple books including Pinnacle)
- hltv.org match pages (sometimes show betting odds)
"""

import requests
from typing import Dict, Optional, List
from dataclasses import dataclass
from datetime import datetime


@dataclass
class PinnacleOdds:
    """Pre-match odds from Pinnacle or equivalent sharp book."""
    team_a: str
    team_b: str
    team_a_ml: float      # Moneyline implied probability for team A
    team_b_ml: float      # Moneyline implied probability for team B
    map_odds: Dict[str, Dict[str, float]]  # {map_name: {team_a: prob, team_b: prob}}
    over_under_maps: Optional[Dict[str, float]] = None  # {"over": prob, "under": prob}
    source: str = "pinnacle"
    timestamp: str = ""


class OddsScraper:
    """
    Scrape pre-match odds from available sources.
    Priority: Pinnacle > odds-api.com > manual input.
    """

    # The-Odds-API is a free aggregator (500 requests/month free tier)
    ODDS_API_BASE = "https://api.the-odds-api.com/v4"

    def __init__(self, odds_api_key: str = None):
        self.odds_api_key = odds_api_key

    def get_match_odds_oddsapi(self, team_a: str, team_b: str,
                                sport: str = "esports_csgo") -> Optional[PinnacleOdds]:
        """
        Try to get odds from the-odds-api.com.
        Free tier: 500 requests/month, includes Pinnacle.
        sport key for CS2: "esports_csgo" (they haven't renamed it)
        """
        if not self.odds_api_key:
            return None

        try:
            resp = requests.get(
                f"{self.ODDS_API_BASE}/sports/{sport}/odds",
                params={
                    "apiKey": self.odds_api_key,
                    "regions": "eu",          # Pinnacle is EU
                    "markets": "h2h",          # Head-to-head / moneyline
                    "bookmakers": "pinnacle",
                    "oddsFormat": "decimal",
                },
                timeout=15,
            )
            resp.raise_for_status()
            events = resp.json()
        except Exception as e:
            print(f"  Odds API error: {e}")
            return None

        # Find matching event
        team_a_lower = team_a.lower()
        team_b_lower = team_b.lower()

        for event in events:
            home = event.get("home_team", "").lower()
            away = event.get("away_team", "").lower()

            if ((team_a_lower in home or team_a_lower in away) and
                (team_b_lower in home or team_b_lower in away)):

                # Find Pinnacle's odds
                for bookmaker in event.get("bookmakers", []):
                    if bookmaker.get("key") == "pinnacle":
                        markets = bookmaker.get("markets", [])
                        for market in markets:
                            if market.get("key") == "h2h":
                                outcomes = market.get("outcomes", [])
                                odds = {}
                                for o in outcomes:
                                    odds[o["name"]] = o["price"]

                                # Convert decimal odds to implied probability
                                total_implied = sum(1/v for v in odds.values())
                                probs = {k: (1/v) / total_implied
                                         for k, v in odds.items()}

                                # Match names back to team_a/team_b
                                prob_a = None
                                prob_b = None
                                for name, prob in probs.items():
                                    if team_a_lower in name.lower():
                                        prob_a = prob
                                    elif team_b_lower in name.lower():
                                        prob_b = prob

                                if prob_a is not None and prob_b is not None:
                                    return PinnacleOdds(
                                        team_a=team_a,
                                        team_b=team_b,
                                        team_a_ml=prob_a,
                                        team_b_ml=prob_b,
                                        map_odds={},
                                        source="pinnacle_via_oddsapi",
                                        timestamp=datetime.utcnow().isoformat(),
                                    )
        return None

    def get_match_odds_manual(self, team_a: str, team_b: str,
                               team_a_prob: float) -> PinnacleOdds:
        """
        Manual fallback: input odds directly.
        Use this when scrapers fail or for backtesting.
        """
        return PinnacleOdds(
            team_a=team_a,
            team_b=team_b,
            team_a_ml=team_a_prob,
            team_b_ml=1.0 - team_a_prob,
            map_odds={},
            source="manual",
            timestamp=datetime.utcnow().isoformat(),
        )

    def estimate_map_odds(self, match_odds: PinnacleOdds,
                           map_name: str,
                           picker: str = None) -> Dict[str, float]:
        """
        Estimate per-map odds from match-level odds.

        Logic:
        - If team A is favored at 60% in Bo3, each map is closer to 55%
          (Bo3 amplifies small edges)
        - Map pick advantage: the picker wins their map ~55% of the time
        - Map-specific CT/T balance affects which side benefits

        This is approximate. With historical data we can calibrate this properly.
        """
        import config

        p_match = match_odds.team_a_ml

        # Convert Bo3 match prob → single map prob
        # Approximate inverse: if Bo3 is p, single map ≈ solve for q where
        # p = q^2 * (3 - 2q)  (simplified Bo3 with equal map probs)
        # For small deviations from 0.5, q ≈ 0.5 + (p - 0.5) * 0.67
        q = 0.5 + (p_match - 0.5) * 0.67

        # Map pick adjustment
        if picker == match_odds.team_a:
            q += 0.03  # Picker has ~3% advantage on their map
        elif picker == match_odds.team_b:
            q -= 0.03

        # Clamp
        q = max(0.05, min(0.95, q))

        return {
            match_odds.team_a: q,
            match_odds.team_b: 1.0 - q,
        }
