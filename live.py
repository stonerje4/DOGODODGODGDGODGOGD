"""
Live paper trading system.

1. Auto-grabs PM pre-match price as prior
2. Polls GRID once per round change (not every 3 sec)
3. Checks real orderbook, calculates real edge
4. Paper trades: logs every BUY/SELL with timestamp + P/L
5. Tracks cumulative P/L through the match

Usage:
    cd "C:\\Users\\Stoner2\\Desktop\\Please no"
    python live.py --series 2912641 --pm-slug cs2-ecs-minlat-2026-03-31
"""

import sys, io, os, time, json, argparse
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Dict, List, Optional

if sys.platform == "win32":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8',
                                       line_buffering=True)
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(__file__))
from grid_client import GridClient
from polymarket_client import PolymarketClient
from feature_extractor import extract_features_from_grid_state
from model import MapWinModel, SeriesCalculator
from bet_sizer import BetSizer
import config


# ── Data structures ──────────────────────────────────────────────────────────

@dataclass
class Trade:
    time: str
    market: str
    action: str       # BUY or SELL
    outcome: str
    shares: float
    price: float
    model_prob: float
    pnl: float = 0.0  # Only set on SELL or RESOLVE


@dataclass
class Position:
    market_slug: str
    market_label: str
    outcome: str
    shares: float
    avg_price: float
    entry_time: str
    entry_model_prob: float


# ── Orderbook helpers ────────────────────────────────────────────────────────

def get_book(pm, mkt):
    """Real orderbook for both sides."""
    tids = pm.get_clob_token_ids(mkt)
    if not tids or len(tids) < 2:
        return None
    out = {}
    for i, key in enumerate(["a", "b"]):
        book = pm.get_orderbook(tids[i])
        if not book:
            out[key] = {"ask": None, "bid": None, "depth": []}
            continue
        asks = sorted(book.get("asks", []), key=lambda x: float(x["price"]))
        bids = sorted(book.get("bids", []), key=lambda x: -float(x["price"]))
        depth = []
        c = 0
        for a in asks[:10]:
            p, s = float(a["price"]), float(a["size"])
            c += p * s
            depth.append({"price": p, "shares": s, "cumul": c})
        out[key] = {
            "ask": float(asks[0]["price"]) if asks else None,
            "bid": float(bids[0]["price"]) if bids else None,
            "depth": depth,
        }
    return out


def find_edge(model_prob, book_side, fee=config.POLYMARKET_TAKER_FEE):
    """Check if edge exists and how deep."""
    ask = book_side["ask"]
    if ask is None:
        return None, 0
    edge = model_prob - ask - fee
    max_bet = 0
    for lvl in book_side["depth"]:
        if model_prob - lvl["price"] - fee > 0:
            max_bet = lvl["cumul"]
        else:
            break
    return edge, max_bet


def should_sell(model_prob, book_side, fee=config.POLYMARKET_TAKER_FEE):
    """Should we exit? True if market overvalues our position."""
    bid = book_side["bid"]
    if bid is None:
        return False, None
    return model_prob < (bid - fee), bid


# ── Main ─────────────────────────────────────────────────────────────────────

def run(series_id, pm_slug, poll_interval=10, prior_override=None):
    grid = GridClient()
    pm = PolymarketClient()
    model = MapWinModel()
    model.load()
    calc = SeriesCalculator()
    sizer = BetSizer(bankroll=1000)
    fee = config.POLYMARKET_TAKER_FEE

    # State
    positions: Dict[str, Position] = {}
    trades: List[Trade] = []
    realized_pnl = 0.0
    last_hash = None

    # ── Step 0: Get pre-match prior ──────────────────────────────────
    # Option A: user passed --prior 0.78
    # Option B: grab from PM now (only valid if match hasn't started!)
    # Option C: wait for match to appear, grab prior, then wait for start

    team_a_name = "Team A"
    team_b_name = "Team B"

    if prior_override:
        match_prior = prior_override
        print(f"Using manual prior: {match_prior:.0%}", flush=True)
    else:
        print("Grabbing pre-match prior from Polymarket...", flush=True)
        mkt = pm.get_market_by_slug(pm_slug)
        if mkt:
            prices = pm.extract_prices(mkt)
            outcomes = list(prices.keys())
            match_prior = prices[outcomes[0]] if outcomes else 0.50
            team_a_name = outcomes[0] if outcomes else "Team A"
            team_b_name = outcomes[1] if len(outcomes) > 1 else "Team B"
        else:
            match_prior = 0.50
        print(f"Prior from PM: {team_a_name} {match_prior:.0%}", flush=True)

    # WARN if prior looks like mid-match (>90% or <10%)
    if match_prior > 0.90 or match_prior < 0.10:
        print(f"WARNING: Prior {match_prior:.0%} looks mid-match, not pre-match!", flush=True)
        print(f"  Pass --prior 0.XX to set it manually", flush=True)

    map_prior = 0.5 + (match_prior - 0.5) * 0.67
    print(f"Map prior: {map_prior:.0%} (for future maps)", flush=True)

    # Wait for match to start
    print(f"Watching series {series_id}...", flush=True)
    print(flush=True)

    # ── Main loop ────────────────────────────────────────────────────
    while True:
        now = datetime.now(timezone.utc).strftime("%H:%M:%S")

        # Pull GRID
        state = grid.get_series_state(series_id)
        if not state:
            time.sleep(poll_interval)
            continue

        t = state["teams"]
        ta, tb = t[0]["name"], t[1]["name"]
        sa, sb = t[0].get("score", 0), t[1].get("score", 0)
        games = state.get("games", [])
        is_bo3 = "3" in state.get("format", "")

        # Find active map
        ai = 0
        for i, g in enumerate(games):
            if g.get("started") and not g.get("finished"):
                ai = i
                break
            elif g.get("finished"):
                ai = i

        g = games[ai] if games else None
        gt = g.get("teams", []) if g else []
        mn = (g.get("map") or {}).get("name", "?") if g else "?"
        ms_a = gt[0].get("score", 0) if gt else 0
        ms_b = gt[1].get("score", 0) if len(gt) > 1 else 0

        # Check state change
        state_hash = f"{sa}{sb}{ai}{ms_a}{ms_b}"
        if state_hash == last_hash:
            time.sleep(poll_interval)
            continue
        last_hash = state_hash

        # ── Finished? ────────────────────────────────────────────
        if state.get("finished"):
            winner = ta if t[0].get("won") else tb
            print(f"\n[{now}] FINISHED: {winner} wins {sa}-{sb}", flush=True)
            # Resolve open positions
            for slug_key, pos in list(positions.items()):
                won = (pos.outcome == winner) or (
                    pos.outcome == ta and t[0].get("won")) or (
                    pos.outcome == tb and t[1].get("won"))
                payout = 1.0 if won else 0.0
                pnl = (payout - pos.avg_price) * pos.shares
                realized_pnl += pnl
                trades.append(Trade(now, pos.market_label, "RESOLVE",
                                    pos.outcome, pos.shares, payout,
                                    0, pnl))
                result = "WIN" if won else "LOSS"
                print(f"  {pos.market_label}: {pos.outcome} {result} "
                      f"| bought @ ${pos.avg_price:.3f} "
                      f"| P/L: ${pnl:+.2f}", flush=True)
            print_summary(trades, realized_pnl)
            return

        # ── Model ────────────────────────────────────────────────
        feats = extract_features_from_grid_state(state, game_idx=ai)
        if not feats:
            time.sleep(poll_interval)
            continue

        f = feats[-1]
        p_map = model.predict(f)
        sp = (calc.bo3_probabilities(p_map, map_prior, 0.50, sa, sb)
              if is_bo3 else calc.bo1_probabilities(p_map))

        # ── Print state ──────────────────────────────────────────
        maps_str = ""
        for i, gm in enumerate(games):
            gmt = gm.get("teams", [])
            gmn = (gm.get("map") or {}).get("name", "?")
            if gmt and gm.get("started"):
                s1, s2 = gmt[0].get("score", 0), gmt[1].get("score", 0)
                live = "<" if i == ai and not gm.get("finished") else ""
                maps_str += f"  M{i+1}({gmn}):{s1}-{s2}{live}"

        side = f.team_a_side.upper()
        print(f"[{now}] {sa}-{sb}{maps_str} R{f.round_num}({side}) "
              f"| {ta} map:{p_map:.0%} series:{sp['series_win_a']:.0%} "
              f"| econ: {f.buy_type_a}/{f.buy_type_b} "
              f"K:{f.total_kills_a}-{f.total_kills_b}", flush=True)

        # ── Check markets ────────────────────────────────────────
        def get_map_prob(idx):
            if idx < len(games) and games[idx].get("started"):
                ff = extract_features_from_grid_state(state, game_idx=idx)
                return model.predict(ff[-1]) if ff else None
            return None

        market_defs = [
            ("WINNER", "", sp["series_win_a"]),
            ("MAP1", "-game1", get_map_prob(0)),
            ("MAP2", "-game2", get_map_prob(1)),
            ("MAP3", "-game3", get_map_prob(2)),
            ("O/U", "-total-games-2pt5", sp["goes_to_3"]),
        ]

        for label, suffix, prob_a in market_defs:
            if prob_a is None:
                continue

            slug_key = pm_slug + suffix
            mkt = pm.get_market_by_slug(slug_key)
            if not mkt:
                continue

            prices = pm.extract_prices(mkt)
            outcomes = list(prices.keys())
            if len(outcomes) < 2:
                continue

            book = get_book(pm, mkt)
            if not book:
                continue

            # ── SELL CHECK ────────────────────────────────────
            if slug_key in positions:
                pos = positions[slug_key]
                held_side = "a" if pos.outcome == outcomes[0] else "b"
                held_prob = prob_a if held_side == "a" else (1 - prob_a)
                sell, bid = should_sell(held_prob, book[held_side])

                if sell and bid:
                    pnl = (bid - fee - pos.avg_price) * pos.shares
                    realized_pnl += pnl
                    trades.append(Trade(now, label, "SELL", pos.outcome,
                                        pos.shares, bid, held_prob, pnl))
                    print(f"  >>> SELL {label}: {pos.outcome} "
                          f"@ ${bid:.3f} | bought ${pos.avg_price:.3f} "
                          f"| P/L: ${pnl:+.2f} "
                          f"| model:{held_prob:.0%} < bid:{bid:.0%}", flush=True)
                    del positions[slug_key]
                else:
                    # Mark to market
                    if book[held_side]["bid"]:
                        mtm = (book[held_side]["bid"] - pos.avg_price) * pos.shares
                        print(f"  {label}: HOLD {pos.outcome} "
                              f"@ ${pos.avg_price:.3f} "
                              f"| mtm: ${mtm:+.2f} "
                              f"| model:{held_prob:.0%}", flush=True)
                continue

            # ── BUY CHECK ─────────────────────────────────────
            best_edge = None
            best_side = None
            for sk, oi in [("a", 0), ("b", 1)]:
                prob = prob_a if oi == 0 else (1 - prob_a)
                edge, max_bet = find_edge(prob, book[sk])
                if edge is not None and (best_edge is None or edge > best_edge):
                    best_edge = edge
                    best_side = {
                        "key": sk, "oi": oi, "outcome": outcomes[oi],
                        "prob": prob, "ask": book[sk]["ask"],
                        "edge": edge, "max_bet": max_bet,
                    }

            if best_side and best_side["edge"] > 0:
                s = best_side
                liq = float(pm.extract_liquidity(mkt).get("liquidity", 0))
                kelly = sizer.kelly_size(s["prob"], s["ask"], liq)
                rec = min(kelly, s["max_bet"])

                if rec >= 5:  # Min $5 bet
                    shares = rec / s["ask"]
                    positions[slug_key] = Position(
                        slug_key, label, s["outcome"], shares,
                        s["ask"], now, s["prob"])
                    trades.append(Trade(now, label, "BUY", s["outcome"],
                                        shares, s["ask"], s["prob"]))
                    print(f"  >>> BUY {label}: {s['outcome']} "
                          f"${rec:.0f} @ ${s['ask']:.3f} "
                          f"| edge:{s['edge']:+.1%} "
                          f"| depth:${s['max_bet']:.0f} "
                          f"| model:{s['prob']:.0%}", flush=True)

        # ── P/L line ─────────────────────────────────────────
        open_count = len(positions)
        total_trades = len(trades)
        if open_count > 0 or total_trades > 0:
            pos_str = " | ".join(f"{p.outcome}@${p.avg_price:.2f}"
                                  for p in positions.values())
            print(f"  [P/L] realized: ${realized_pnl:+.2f} "
                  f"| open({open_count}): {pos_str or 'none'} "
                  f"| trades: {total_trades}", flush=True)

        time.sleep(poll_interval)


def print_summary(trades, realized_pnl):
    """End of match summary."""
    print(f"\n{'='*60}", flush=True)
    print(f"TRADE LOG:", flush=True)
    for t in trades:
        pnl_str = f"  P/L: ${t.pnl:+.2f}" if t.action in ("SELL", "RESOLVE") else ""
        print(f"  [{t.time}] {t.action:>7} {t.market:<8} {t.outcome} "
              f"x{t.shares:.0f} @ ${t.price:.3f} "
              f"(model:{t.model_prob:.0%}){pnl_str}", flush=True)

    buys = [t for t in trades if t.action == "BUY"]
    sells = [t for t in trades if t.action in ("SELL", "RESOLVE")]
    wins = [t for t in sells if t.pnl > 0]

    print(f"\nSUMMARY:", flush=True)
    print(f"  Total trades: {len(trades)}", flush=True)
    print(f"  Buys: {len(buys)}  |  Exits: {len(sells)}", flush=True)
    if sells:
        print(f"  Win rate: {len(wins)}/{len(sells)} "
              f"({len(wins)/len(sells)*100:.0f}%)", flush=True)
    print(f"  Realized P/L: ${realized_pnl:+.2f}", flush=True)
    print(f"{'='*60}", flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--series", required=True)
    p.add_argument("--pm-slug", required=True)
    p.add_argument("--poll", type=int, default=10,
                   help="Poll interval in seconds (default 10, min for 6/min/series limit)")
    p.add_argument("--prior", type=float, default=None,
                   help="Pre-match P(Team A wins) e.g. 0.78. Grabs from PM if not set.")
    a = p.parse_args()
    run(a.series, a.pm_slug, a.poll, a.prior)
