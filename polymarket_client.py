"""
Polymarket API client.
Pulls odds, orderbook depth, and market metadata for CS2 matches.
"""

import requests
from typing import List, Optional, Dict, Any
import config


class PolymarketClient:
    """Client for Polymarket Gamma + CLOB APIs."""

    def __init__(self):
        self.gamma_url = config.POLYMARKET_GAMMA_URL
        self.clob_url = config.POLYMARKET_CLOB_URL

    # ── Gamma API: Market Discovery ──────────────────────────────────────

    def get_event_by_slug(self, slug: str) -> Optional[dict]:
        """
        Get a Polymarket event by its URL slug.
        e.g. slug = "cs2-havu-tl1-2026-03-31"
        Returns full event with all child markets.
        """
        resp = requests.get(
            f"{self.gamma_url}/events",
            params={"slug": slug},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if data and len(data) > 0:
            return data[0]
        return None

    def get_market_by_slug(self, slug: str) -> Optional[dict]:
        """Get a single market by slug."""
        resp = requests.get(
            f"{self.gamma_url}/markets",
            params={"slug": slug},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if data and len(data) > 0:
            return data[0]
        return None

    def search_markets(self, query: str, closed: bool = False,
                       limit: int = 50) -> List[dict]:
        """Search markets by text query."""
        resp = requests.get(
            f"{self.gamma_url}/markets",
            params={"search": query, "closed": str(closed).lower(),
                    "limit": limit},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def get_esports_events(self, limit: int = 100) -> List[dict]:
        """
        Get open esports events.
        Note: Gamma tag system doesn't always work for esports.
        Fallback to search-based discovery.
        """
        # Try tag first
        resp = requests.get(
            f"{self.gamma_url}/events",
            params={"closed": "false", "tag": "esports", "limit": limit},
            timeout=15,
        )
        resp.raise_for_status()
        events = resp.json()

        # If empty, try searching for CS2 matches
        if not events:
            events = self._search_cs2_events(limit)
        return events

    def _search_cs2_events(self, limit: int = 100) -> List[dict]:
        """Fallback: search for CS2 markets by common team names."""
        markets = self.search_markets("Counter-Strike", limit=limit)
        # Group by event
        events_by_id = {}
        for m in markets:
            eid = m.get("eventId") or m.get("id")
            if eid not in events_by_id:
                events_by_id[eid] = m
        return list(events_by_id.values())

    # ── CLOB API: Orderbook Depth ────────────────────────────────────────

    def get_orderbook(self, token_id: str) -> Optional[dict]:
        """
        Get orderbook for a specific outcome token.
        token_id is the CLOB token ID from market data.
        Returns bids and asks with price/size.
        """
        try:
            resp = requests.get(
                f"{self.clob_url}/book",
                params={"token_id": token_id},
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return None

    def get_market_price(self, condition_id: str) -> Optional[dict]:
        """Get current market price from CLOB."""
        try:
            resp = requests.get(
                f"{self.clob_url}/markets/{condition_id}",
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            pass
            return None

    # ── Parsing Helpers ──────────────────────────────────────────────────

    @staticmethod
    def parse_match_markets(event: dict) -> Dict[str, dict]:
        """
        Parse a Polymarket event into structured market data.
        Returns dict keyed by market type:
          "match_winner", "map1", "map2", "map3",
          "over_under", "handicap_home", "handicap_away"
        """
        markets_raw = event.get("markets", [])
        if not markets_raw:
            # Sometimes market data is at top level
            return {"match_winner": event}

        parsed = {}
        for m in markets_raw:
            slug = m.get("slug", "") or m.get("groupItemTitle", "")
            market_type = m.get("marketType", "")

            if market_type == "moneyline" or (
                "game1" not in slug and "game2" not in slug
                and "game3" not in slug and "total" not in slug
                and "handicap" not in slug and "odd-even" not in slug
            ):
                parsed["match_winner"] = m
            elif "game1" in slug:
                parsed["map1"] = m
            elif "game2" in slug:
                parsed["map2"] = m
            elif "game3" in slug:
                parsed["map3"] = m
            elif "total" in slug:
                parsed["over_under"] = m
            elif "handicap" in slug and "home" in slug:
                parsed["handicap_home"] = m
            elif "handicap" in slug and "away" in slug:
                parsed["handicap_away"] = m

        return parsed

    @staticmethod
    def extract_prices(market: dict) -> Dict[str, float]:
        """
        Extract current prices from a market.
        Returns {"team_a": 0.55, "team_b": 0.45} or similar.
        """
        outcomes_raw = market.get("outcomes", [])
        prices_raw = market.get("outcomePrices", "")

        # Both fields can be JSON strings like '["Team A","Team B"]'
        import json
        if isinstance(outcomes_raw, str):
            try:
                outcomes = json.loads(outcomes_raw)
            except (json.JSONDecodeError, TypeError):
                outcomes = []
        else:
            outcomes = outcomes_raw

        if isinstance(prices_raw, str):
            try:
                prices_list = json.loads(prices_raw)
            except (json.JSONDecodeError, TypeError):
                prices_list = []
        elif isinstance(prices_raw, list):
            prices_list = prices_raw
        else:
            prices_list = []

        result = {}
        for i, outcome in enumerate(outcomes):
            name = outcome if isinstance(outcome, str) else outcome.get("name", f"outcome_{i}")
            price = float(prices_list[i]) if i < len(prices_list) else 0.5
            result[name] = price

        return result

    @staticmethod
    def extract_liquidity(market: dict) -> dict:
        """Extract liquidity and volume info from a market."""
        return {
            "volume": float(market.get("volume", 0) or 0),
            "liquidity": float(market.get("liquidity", 0) or 0),
            "spread": float(market.get("spread", 0) or 0),
            "best_bid": float(market.get("bestBid", 0) or 0),
            "best_ask": float(market.get("bestAsk", 0) or 0),
        }

    @staticmethod
    def get_clob_token_ids(market: dict) -> List[str]:
        """Extract CLOB token IDs for orderbook queries."""
        tokens = market.get("clobTokenIds", "")
        if isinstance(tokens, str):
            try:
                import json
                return json.loads(tokens)
            except (json.JSONDecodeError, TypeError):
                return []
        elif isinstance(tokens, list):
            return tokens
        return []
