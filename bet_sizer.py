"""
Bet sizing and position management.

Entry rule:  model_prob - best_ask - fee > 0  →  BET
Exit rule:   model_prob < best_bid - fee       →  SELL
Size rule:   min(kelly, book_depth_with_edge)
"""

from dataclasses import dataclass, field
from typing import List, Optional
import config


@dataclass
class Position:
    """A position we're holding."""
    market_slug: str
    outcome: str         # Which outcome we bought
    shares: float
    avg_price: float     # Average cost per share
    entry_model_prob: float
    entry_time: str


class BetSizer:
    """Kelly criterion sizing with liquidity cap."""

    def __init__(self, bankroll: float = None, kelly_fraction: float = None):
        self.bankroll = bankroll or config.DEFAULT_BANKROLL
        self.kelly_fraction = kelly_fraction or config.KELLY_FRACTION
        self.positions: List[Position] = []

    def kelly_size(self, prob, ask_price, liquidity,
                   max_pct=0.10) -> float:
        """
        How much to bet.
        prob: our estimated probability of winning
        ask_price: actual best ask (what we'd pay per share)
        liquidity: total market liquidity (used as a sanity cap)
        """
        if ask_price <= 0 or ask_price >= 1 or prob <= 0 or prob >= 1:
            return 0.0

        effective_price = ask_price + config.POLYMARKET_TAKER_FEE
        if effective_price >= 1:
            return 0.0

        # Kelly: f* = (p*b - q) / b where b = (1-price)/price
        b = (1 - effective_price) / effective_price
        q = 1 - prob
        kelly_full = (prob * b - q) / b

        if kelly_full <= 0:
            return 0.0

        kelly_adj = kelly_full * self.kelly_fraction

        available = self.bankroll - sum(p.shares * p.avg_price for p in self.positions)
        max_from_bankroll = available * max_pct
        max_from_liquidity = liquidity * 0.3  # Don't take more than 30% of the book

        size = min(kelly_adj * self.bankroll, max_from_bankroll, max_from_liquidity)
        return max(0, round(size, 2))

    def should_sell(self, position: Position, model_prob: float,
                    best_bid: float) -> bool:
        """
        Should we exit this position?
        Sell when the market overvalues what we're holding.
        """
        if best_bid is None:
            return False
        proceeds_after_fee = best_bid - config.POLYMARKET_TAKER_FEE
        return model_prob < proceeds_after_fee

    def add_position(self, slug, outcome, shares, price, model_prob, time_str):
        self.positions.append(Position(
            market_slug=slug, outcome=outcome, shares=shares,
            avg_price=price, entry_model_prob=model_prob, entry_time=time_str,
        ))

    def remove_position(self, slug):
        self.positions = [p for p in self.positions if p.market_slug != slug]

    @property
    def total_exposure(self):
        return sum(p.shares * p.avg_price for p in self.positions)

    @property
    def available(self):
        return self.bankroll - self.total_exposure
