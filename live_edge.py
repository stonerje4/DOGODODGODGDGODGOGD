"""
Live edge detector.
Polls GRID live data + Polymarket prices simultaneously,
runs the model, and flags betting opportunities.

This is the main runtime loop.

Usage:
    python live_edge.py --series 2912641 --pm-slug cs2-ecstatic-minlate-2026-03-31
"""

import time
import argparse
from datetime import datetime, timezone
from typing import Dict, Optional

import config
from grid_client import GridClient
from polymarket_client import PolymarketClient
from feature_extractor import extract_features_from_grid_state
from model import MapWinModel, SeriesCalculator
from scrape_pinnacle import OddsScraper, PinnacleOdds
from bet_sizer import BetSizer, BetSignal


class LiveEdgeDetector:
    """
    Main runtime: continuously polls both data sources,
    compares model probabilities to market prices,
    and outputs bet signals.
    """

    def __init__(
        self,
        grid_series_id: str,
        pm_event_slug: str,
        pinnacle_odds: Optional[PinnacleOdds] = None,
        poll_interval: int = 15,
    ):
        self.grid = GridClient()
        self.pm = PolymarketClient()
        self.model = MapWinModel()
        self.model.load()
        self.series_calc = SeriesCalculator()
        self.bet_sizer = BetSizer()
        self.odds_scraper = OddsScraper()

        self.grid_series_id = grid_series_id
        self.pm_event_slug = pm_event_slug
        self.pinnacle_odds = pinnacle_odds
        self.poll_interval = poll_interval

        # State tracking
        self.last_round = 0
        self.last_game = 0
        self.signals_history = []

    def run(self):
        """Main loop."""
        print("=" * 70)
        print("LIVE EDGE DETECTOR")
        print(f"  GRID Series: {self.grid_series_id}")
        print(f"  Polymarket: {self.pm_event_slug}")
        print(f"  Poll interval: {self.poll_interval}s")
        print("=" * 70)

        while True:
            try:
                self._tick()
            except KeyboardInterrupt:
                print("\n\nStopped by user.")
                self._print_summary()
                break
            except Exception as e:
                print(f"\n  ERROR: {e}")
                import traceback
                traceback.print_exc()

            time.sleep(self.poll_interval)

    def _tick(self):
        """One polling cycle."""
        now = datetime.now(timezone.utc).strftime("%H:%M:%S")

        # ── Pull GRID state ──────────────────────────────────────────────
        grid_state = self.grid.get_series_state(self.grid_series_id)
        if not grid_state:
            print(f"  [{now}] GRID: no data")
            return

        if grid_state.get("finished"):
            print(f"\n  [{now}] Series FINISHED.")
            self._print_summary()
            raise KeyboardInterrupt

        games = grid_state.get("games", [])
        if not games:
            print(f"  [{now}] No games started yet")
            return

        # Find current active game
        current_game_idx = 0
        for i, g in enumerate(games):
            if g.get("started") and not g.get("finished"):
                current_game_idx = i
                break
            elif g.get("finished"):
                current_game_idx = i  # Will be overridden if next game started

        game = games[current_game_idx]
        segments = game.get("segments", [])
        current_round = len([s for s in segments if s.get("finished")])

        # Skip if nothing changed
        if (current_round == self.last_round and
                current_game_idx == self.last_game):
            return

        self.last_round = current_round
        self.last_game = current_game_idx

        # Series score
        series_teams = grid_state.get("teams", [])
        series_score_a = series_teams[0].get("score", 0) if series_teams else 0
        series_score_b = series_teams[1].get("score", 0) if len(series_teams) > 1 else 0

        # Map info
        map_name = (game.get("map") or {}).get("name", "?")
        game_teams = game.get("teams", [])
        team_a = game_teams[0]["name"] if game_teams else "?"
        team_b = game_teams[1]["name"] if len(game_teams) > 1 else "?"
        score_a = game_teams[0].get("score", 0) if game_teams else 0
        score_b = game_teams[1].get("score", 0) if len(game_teams) > 1 else 0

        # ── Run model ────────────────────────────────────────────────────
        features_list = extract_features_from_grid_state(
            grid_state, game_idx=current_game_idx)

        if not features_list:
            print(f"  [{now}] No features extracted")
            return

        latest_features = features_list[-1]
        p_map = self.model.predict(latest_features)

        # Future map estimates (from Pinnacle or default)
        if self.pinnacle_odds:
            p_map2_est = self.pinnacle_odds.team_a_ml
            p_map3_est = 0.5 + (self.pinnacle_odds.team_a_ml - 0.5) * 0.5
        else:
            p_map2_est = 0.50
            p_map3_est = 0.50

        # Series probabilities
        fmt = grid_state.get("format", "best-of-3")
        if "1" in fmt:  # Bo1
            series_probs = self.series_calc.bo1_probabilities(p_map)
        else:
            series_probs = self.series_calc.bo3_probabilities(
                p_current_map=p_map,
                p_map2=p_map2_est,
                p_map3=p_map3_est,
                series_score_a=series_score_a,
                series_score_b=series_score_b,
            )

        # ── Pull Polymarket prices ───────────────────────────────────────
        pm_event = self.pm.get_event_by_slug(self.pm_event_slug)
        if not pm_event:
            # Try market slug instead
            pm_market = self.pm.get_market_by_slug(self.pm_event_slug)
            if pm_market:
                pm_event = pm_market

        if not pm_event:
            print(f"  [{now}] Polymarket: no data for {self.pm_event_slug}")
            return

        pm_markets = self.pm.parse_match_markets(pm_event)

        # ── Compare model vs market ──────────────────────────────────────
        print(f"\n{'─' * 70}")
        print(f"  [{now}] Map {current_game_idx + 1} ({map_name}) "
              f"Round {current_round} | "
              f"{team_a} {score_a}-{score_b} {team_b} | "
              f"Series {series_score_a}-{series_score_b}")
        print(f"  Model: P({team_a} wins map) = {p_map:.1%}")
        print(f"  Model: P({team_a} wins series) = {series_probs['series_win_a']:.1%}")

        # Check each market for edge
        market_checks = {
            "match_winner": ("series_win_a", series_probs["series_win_a"]),
            f"map{current_game_idx + 1}": ("map_win", p_map),
            "over_under": ("goes_to_3", series_probs["goes_to_3"]),
        }

        for market_key, (prob_type, model_prob) in market_checks.items():
            pm_market = pm_markets.get(market_key)
            if not pm_market:
                continue

            prices = self.pm.extract_prices(pm_market)
            liquidity = self.pm.extract_liquidity(pm_market)

            if not prices:
                continue

            # Get the price for team A (first outcome)
            outcomes = list(prices.keys())
            pm_price_a = prices.get(outcomes[0], 0.5)

            # For over/under, team A = "Over"
            if market_key == "over_under":
                pm_price_a = prices.get("Over", prices.get(outcomes[0], 0.5))

            edge = model_prob - pm_price_a
            spread = liquidity.get("spread", 0)

            # Determine bet direction
            if edge > 0:
                bet_side = outcomes[0] if market_key != "over_under" else "Over"
                bet_prob = model_prob
                market_price = pm_price_a
            else:
                bet_side = outcomes[1] if len(outcomes) > 1 else outcomes[0]
                bet_prob = 1 - model_prob
                market_price = 1 - pm_price_a
                edge = -edge

            # Check if edge exceeds threshold
            effective_edge = edge - config.POLYMARKET_TAKER_FEE - (spread / 2)

            signal = BetSignal(
                market_key=market_key,
                bet_side=bet_side,
                model_prob=bet_prob,
                market_price=market_price,
                raw_edge=edge,
                effective_edge=effective_edge,
                liquidity=liquidity.get("liquidity", 0),
                spread=spread,
                recommended_size=0,
            )

            if effective_edge > config.MIN_EDGE_THRESHOLD:
                signal.recommended_size = self.bet_sizer.kelly_size(
                    prob=bet_prob,
                    price=market_price,
                    liquidity=liquidity.get("liquidity", 0),
                )
                self.signals_history.append(signal)
                print(f"\n  *** SIGNAL: {market_key} ***")
                print(f"      Bet: {bet_side}")
                print(f"      Model: {bet_prob:.1%} | Market: {market_price:.1%}")
                print(f"      Edge: {edge:.1%} (effective: {effective_edge:.1%})")
                print(f"      Liquidity: ${liquidity.get('liquidity', 0):,.0f} | "
                      f"Spread: {spread:.1%}")
                print(f"      Recommended size: ${signal.recommended_size:.0f}")
            else:
                status = "NO EDGE" if effective_edge <= 0 else "below threshold"
                print(f"  {market_key}: model={model_prob:.1%} "
                      f"market={pm_price_a:.1%} "
                      f"edge={edge:.1%} ({status})")

    def _print_summary(self):
        """Print session summary."""
        print(f"\n{'=' * 70}")
        print("SESSION SUMMARY")
        print(f"  Total signals: {len(self.signals_history)}")
        if self.signals_history:
            total_size = sum(s.recommended_size for s in self.signals_history)
            avg_edge = sum(s.effective_edge for s in self.signals_history) / len(self.signals_history)
            print(f"  Total recommended exposure: ${total_size:,.0f}")
            print(f"  Average effective edge: {avg_edge:.1%}")
            print(f"\n  Signals:")
            for s in self.signals_history:
                print(f"    {s.market_key}: {s.bet_side} @ ${s.market_price:.2f} "
                      f"(edge {s.effective_edge:.1%}, size ${s.recommended_size:.0f})")
        print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Live edge detector")
    parser.add_argument("--series", required=True, help="GRID series ID")
    parser.add_argument("--pm-slug", required=True,
                        help="Polymarket event slug")
    parser.add_argument("--team-a-prior", type=float, default=0.50,
                        help="Pre-match P(Team A) from Pinnacle")
    parser.add_argument("--interval", type=int, default=15,
                        help="Poll interval in seconds")
    args = parser.parse_args()

    # Set up pre-match priors
    pinnacle = None
    if args.team_a_prior != 0.50:
        odds = OddsScraper()
        pinnacle = odds.get_match_odds_manual("Team A", "Team B",
                                               args.team_a_prior)

    detector = LiveEdgeDetector(
        grid_series_id=args.series,
        pm_event_slug=args.pm_slug,
        pinnacle_odds=pinnacle,
        poll_interval=args.interval,
    )
    detector.run()


if __name__ == "__main__":
    main()
