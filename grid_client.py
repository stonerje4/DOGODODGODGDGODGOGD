"""
GRID.gg API client.
Handles both Central Data (metadata, series lookup) and
Live Data Feed (round-by-round match state).
"""

import time
import json
import requests
from typing import List, Optional, Dict, Any
import config


class GridClient:
    """Client for GRID.gg GraphQL APIs."""

    def __init__(self, api_key: str = None):
        self.api_key = api_key or config.GRID_API_KEY
        self.headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
        }
        self.request_count = 0
        # Separate rate limits per endpoint:
        # Central Data: 20 req/min = 1 every 3s
        # Series State: 180 req/min = 1 every 0.35s (6/min per series)
        self._last_central = 0
        self._last_live = 0

    def _throttle_central(self):
        """Rate limit for Central Data: 20 req/min."""
        elapsed = time.time() - self._last_central
        if elapsed < 3.1:
            time.sleep(3.1 - elapsed)
        self._last_central = time.time()
        self.request_count += 1

    def _throttle_live(self):
        """Rate limit for Series State: 180 req/min overall."""
        elapsed = time.time() - self._last_live
        if elapsed < 0.4:
            time.sleep(0.4 - elapsed)
        self._last_live = time.time()
        self.request_count += 1

    def _query(self, url: str, query: str, variables: dict = None,
               is_central: bool = False) -> dict:
        """Execute a GraphQL query with retry on rate limit."""
        max_retries = 5
        for attempt in range(max_retries):
            if is_central:
                self._throttle_central()
            else:
                self._throttle_live()
            payload = {"query": query}
            if variables:
                payload["variables"] = variables

            resp = requests.post(url, headers=self.headers, json=payload, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            if "errors" in data:
                err_msg = str(data["errors"])
                if "rate limit" in err_msg.lower() or "ENHANCE_YOUR_CALM" in err_msg:
                    wait = 2 ** attempt * 5  # 5, 10, 20, 40, 80 seconds
                    print(f"  Rate limited, waiting {wait}s (attempt {attempt+1}/{max_retries})")
                    time.sleep(wait)
                    continue
                raise Exception(f"GraphQL errors: {data['errors']}")
            return data.get("data", {})

        raise Exception("Rate limit exceeded after max retries")

    def central_query(self, query: str, variables: dict = None) -> dict:
        return self._query(config.GRID_CENTRAL_URL, query, variables,
                           is_central=True)

    def live_query(self, query: str, variables: dict = None) -> dict:
        return self._query(config.GRID_LIVE_URL, query, variables,
                           is_central=False)

    # ── Central Data: Series Lookup ──────────────────────────────────────

    SERIES_LIST_QUERY = """
    query($first: Int!, $after: String, $filter: SeriesFilter,
          $orderBy: SeriesOrderBy, $orderDir: OrderDirection) {
        allSeries(first: $first, after: $after, filter: $filter,
                  orderBy: $orderBy, orderDirection: $orderDir) {
            totalCount
            pageInfo { hasNextPage endCursor }
            edges {
                node {
                    id
                    startTimeScheduled
                    title { name nameShortened }
                    tournament { id name }
                    teams { baseInfo { id name nameShortened } }
                }
            }
        }
    }
    """

    def get_series_by_date_range(
        self, start: str, end: str, title_id: str = None, page_size: int = 50
    ) -> List[dict]:
        """
        Get all series in a date range. Paginates automatically.
        Dates should be ISO format: "2026-03-01T00:00:00Z"
        title_id: "28" for CS2
        """
        all_series = []
        cursor = None

        while True:
            filt: Dict[str, Any] = {
                "startTimeScheduled": {"gte": start, "lte": end},
                "type": "ESPORTS",
            }
            if title_id:
                filt["titleId"] = title_id

            variables = {
                "first": page_size,
                "after": cursor,
                "filter": filt,
                "orderBy": "StartTimeScheduled",
                "orderDir": "ASC",
            }

            data = self.central_query(self.SERIES_LIST_QUERY, variables)
            result = data.get("allSeries", {})
            edges = result.get("edges", [])

            for edge in edges:
                all_series.append(edge["node"])

            page_info = result.get("pageInfo", {})
            if page_info.get("hasNextPage") and edges:
                cursor = page_info["endCursor"]
            else:
                break

        return all_series

    def get_series_ids_for_month(self, year: int, month: int) -> List[str]:
        """Get all series IDs for a given month."""
        start = f"{year}-{month:02d}-01T00:00:00Z"
        if month == 12:
            end = f"{year + 1}-01-01T00:00:00Z"
        else:
            end = f"{year}-{month + 1:02d}-01T00:00:00Z"

        series = self.get_series_by_date_range(start, end)
        return [s["id"] for s in series]

    # ── Live Data Feed: Round-by-Round State ─────────────────────────────

    SERIES_STATE_QUERY = """
    query($id: ID!) {
        seriesState(id: $id) {
            id
            format
            started
            finished
            valid
            startedAt
            duration
            updatedAt
            title { nameShortened }
            teams {
                name
                score
                won
            }
            games {
                sequenceNumber
                started
                finished
                map { name }
                teams {
                    name
                    score
                    won
                    side
                    money
                    loadoutValue
                    netWorth
                    kills
                    deaths
                    firstKill
                    players {
                        name
                        kills
                        deaths
                        killAssistsReceived
                        money
                        loadoutValue
                        netWorth
                    }
                }
                segments {
                    sequenceNumber
                    started
                    finished
                    startedAt
                    duration
                    teams {
                        name
                        side
                        won
                        kills
                        deaths
                        firstKill
                        weaponKills { weaponName count }
                        objectives { type completionCount }
                        players {
                            name
                            kills
                            deaths
                            killAssistsReceived
                            firstKill
                            weaponKills { weaponName count }
                            objectives { type completionCount }
                        }
                    }
                }
            }
        }
    }
    """

    def get_series_state(self, series_id: str) -> Optional[dict]:
        """Get full round-by-round state for a series."""
        try:
            data = self.live_query(self.SERIES_STATE_QUERY, {"id": series_id})
            return data.get("seriesState")
        except Exception as e:
            if not getattr(self, '_logged_error', False):
                print(f"\n  [DEBUG] First state error for {series_id}: {e}")
                self._logged_error = True
            return None

    # ── Convenience: Today's Matches ─────────────────────────────────────

    def get_todays_series(self) -> List[dict]:
        """Get all series scheduled for today."""
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.get_series_by_date_range(
            f"{today}T00:00:00Z", f"{today}T23:59:59Z"
        )

    def get_live_series(self) -> List[dict]:
        """Get today's series and filter to ones currently live."""
        all_today = self.get_todays_series()
        live = []
        for s in all_today:
            state = self.get_series_state(s["id"])
            if state and state.get("started") and not state.get("finished"):
                state["_central"] = s  # Attach central data
                live.append(state)
        return live
