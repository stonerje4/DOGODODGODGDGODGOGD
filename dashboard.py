"""
cd "C:\\Users\\Stoner2\\Desktop\\Please no"
python dashboard.py --series 2912641 --pm-slug cs2-ecs-minlat-2026-03-31
"""

import sys, io, os, time, argparse, json
from datetime import datetime, timezone

if sys.platform == "win32" and not isinstance(sys.stdout, io.TextIOWrapper):
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(__file__))
from grid_client import GridClient
from polymarket_client import PolymarketClient
from feature_extractor import extract_features_from_grid_state
from model import MapWinModel, SeriesCalculator
from bet_sizer import BetSizer
import config


def get_book(pm, mkt):
    """Get real orderbook for both sides."""
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
        best_ask = float(asks[0]["price"]) if asks else None
        best_bid = float(bids[0]["price"]) if bids else None
        depth = []
        c = 0
        for a in asks[:8]:
            p, s = float(a["price"]), float(a["size"])
            c += p * s
            depth.append({"price": p, "shares": s, "cumul": c})
        out[key] = {"ask": best_ask, "bid": best_bid, "depth": depth}
    return out


def fetch_prior(pm, slug):
    """Grab pre-match odds from Polymarket itself as our prior."""
    mkt = pm.get_market_by_slug(slug)
    if not mkt:
        return 0.50
    prices = pm.extract_prices(mkt)
    outcomes = list(prices.keys())
    if outcomes:
        return prices[outcomes[0]]
    return 0.50


def run(series_id, pm_slug, loop_sec=3, prior_override=None):
    grid = GridClient()
    grid.min_delay = 0.5  # Faster for live
    pm = PolymarketClient()
    model = MapWinModel()
    model.load()
    calc = SeriesCalculator()
    sizer = BetSizer(bankroll=1000)
    fee = config.POLYMARKET_TAKER_FEE

    # Auto-fetch prior from PM pre-match price
    if prior_override:
        match_prior = prior_override
    else:
        match_prior = fetch_prior(pm, pm_slug)
    map_prior = 0.5 + (match_prior - 0.5) * 0.67

    # Paper trading state
    trades = []       # [{time, market, side, shares, price, model_prob}]
    positions = {}    # slug -> {outcome, shares, avg_price, entry_time}
    realized_pnl = 0.0

    tick = 0
    last_state_hash = None

    while True:
        tick += 1
        now = datetime.now(timezone.utc).strftime("%H:%M:%S")

        # ── GRID ─────────────────────────────────────────────
        state = grid.get_series_state(series_id)
        if not state:
            sys.stdout.write(f"\r  [{now}] Waiting for GRID data...          ")
            sys.stdout.flush()
            time.sleep(loop_sec)
            continue

        t = state["teams"]
        ta, tb = t[0]["name"], t[1]["name"]
        sa, sb = t[0].get("score", 0), t[1].get("score", 0)
        games = state.get("games", [])
        is_bo3 = "3" in state.get("format", "")

        if state.get("finished"):
            os.system('cls' if os.name == 'nt' else 'clear')
            w = ta if t[0].get("won") else tb
            print(f"\n  FINISHED: {w} wins {sa}-{sb}")
            print_trades_summary(trades, realized_pnl, positions)
            return

        # Active map
        ai = 0
        for i, g in enumerate(games):
            if g.get("started") and not g.get("finished"): ai = i; break
            elif g.get("finished"): ai = i

        g = games[ai]
        gt = g.get("teams", [])
        mn = (g.get("map") or {}).get("name", "?")
        ms_a = gt[0].get("score", 0) if gt else 0
        ms_b = gt[1].get("score", 0) if len(gt) > 1 else 0

        # Check if state changed
        state_hash = f"{sa}{sb}{ai}{ms_a}{ms_b}"
        if state_hash == last_state_hash and tick > 1:
            time.sleep(loop_sec)
            continue
        last_state_hash = state_hash

        # ── Model ────────────────────────────────────────────
        feats = extract_features_from_grid_state(state, game_idx=ai)
        if not feats:
            sys.stdout.write(f"\r  [{now}] No rounds yet...                  ")
            sys.stdout.flush()
            time.sleep(loop_sec)
            continue

        f = feats[-1]
        p_map = model.predict(f)
        sp = calc.bo3_probabilities(p_map, map_prior, 0.50, sa, sb) if is_bo3 else calc.bo1_probabilities(p_map)

        # ── Render ───────────────────────────────────────────
        if loop_sec > 0:
            os.system('cls' if os.name == 'nt' else 'clear')

        # Line 1: Match state
        print(f"  {ta} vs {tb}  |  {now} UTC")
        map_scores = []
        for i, gm in enumerate(games):
            gmt = gm.get("teams", [])
            gmn = (gm.get("map") or {}).get("name", "?")
            if gmt and gm.get("started"):
                live = " <" if i == ai and not gm.get("finished") else ""
                map_scores.append(f"M{i+1}({gmn}):{gmt[0].get('score',0)}-{gmt[1].get('score',0)}{live}")
        print(f"  Series {sa}-{sb}  |  {' | '.join(map_scores)}")

        # Line 2: Economy snapshot
        side_a = f.team_a_side.upper()
        side_b = "CT" if side_a == "T" else "T"
        print(f"  R{f.round_num}: {ta}({side_a}) ${f.money_a:,} {f.buy_type_a}  vs  {tb}({side_b}) ${f.money_b:,} {f.buy_type_b}  |  K:{f.total_kills_a}-{f.total_kills_b}  streak:{f.current_streak_a:+d}")

        # Line 3: Model
        print(f"  Model: {ta} map={p_map:.0%} series={sp['series_win_a']:.0%}  |  prior={match_prior:.0%}")
        print(f"  ───────────────────────────────────────────────────────")

        # ── Markets ──────────────────────────────────────────
        def map_prob(idx):
            if idx < len(games) and games[idx].get("started"):
                ff = extract_features_from_grid_state(state, game_idx=idx)
                return model.predict(ff[-1]) if ff else None
            return None

        mkts = [
            ("WINNER", "", sp["series_win_a"]),
            ("MAP1", "-game1", map_prob(0)),
            ("MAP2", "-game2", map_prob(1)),
            ("MAP3", "-game3", map_prob(2)),
            ("O/U",  "-total-games-2pt5", sp["goes_to_3"]),
        ]

        for label, suffix, prob_a in mkts:
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

            # ── If we hold a position, check sell ────────────
            if slug_key in positions:
                pos = positions[slug_key]
                held_side = "a" if pos["outcome"] == outcomes[0] else "b"
                held_prob = prob_a if held_side == "a" else (1 - prob_a)
                held_bid = book[held_side]["bid"]

                if held_bid:
                    pnl = (held_bid - pos["avg_price"]) * pos["shares"]
                    pnl_pct = (held_bid / pos["avg_price"] - 1) * 100

                    if held_prob < (held_bid - fee):
                        # SELL
                        realized_pnl += pnl
                        trades.append({"time": now, "market": label,
                                       "action": "SELL", "outcome": pos["outcome"],
                                       "shares": pos["shares"], "price": held_bid,
                                       "pnl": pnl})
                        print(f"  {label}: SOLD {pos['outcome']} @ ${held_bid:.3f}  "
                              f"P/L: ${pnl:+.2f} ({pnl_pct:+.0f}%)  "
                              f"[model {held_prob:.0%} < bid {held_bid:.0%}]")
                        del positions[slug_key]
                    else:
                        print(f"  {label}: HOLD {pos['outcome']} "
                              f"@ ${pos['avg_price']:.3f} -> bid ${held_bid:.3f}  "
                              f"P/L: ${pnl:+.2f}  model:{held_prob:.0%}")
                else:
                    print(f"  {label}: HOLD {pos['outcome']} (no bid)")
                continue

            # ── Check buy ────────────────────────────────────
            best = None
            for sk, oi in [("a", 0), ("b", 1)]:
                prob = prob_a if oi == 0 else (1 - prob_a)
                ask = book[sk]["ask"]
                if ask is None:
                    continue
                edge = prob - ask - fee
                mx = 0
                for lvl in book[sk]["depth"]:
                    if prob - lvl["price"] - fee > 0:
                        mx = lvl["cumul"]
                    else:
                        break
                if best is None or edge > best["edge"]:
                    best = {"outcome": outcomes[oi], "prob": prob,
                            "ask": ask, "edge": edge, "max_bet": mx,
                            "depth": book[sk]["depth"]}

            if best["edge"] > 0:
                kelly = sizer.kelly_size(best["prob"], best["ask"],
                                         float(pm.extract_liquidity(mkt).get("liquidity", 0)))
                rec = min(kelly, best["max_bet"])
                if rec >= 5:  # Minimum $5 bet
                    # Paper trade: buy
                    shares = rec / best["ask"]
                    positions[slug_key] = {
                        "outcome": best["outcome"],
                        "shares": shares,
                        "avg_price": best["ask"],
                        "entry_time": now,
                    }
                    trades.append({"time": now, "market": label,
                                   "action": "BUY", "outcome": best["outcome"],
                                   "shares": shares, "price": best["ask"],
                                   "pnl": 0})
                    print(f"  {label}: ** BUY {best['outcome']} ** "
                          f"${rec:.0f} @ ${best['ask']:.3f}  "
                          f"edge:{best['edge']:+.1%}  "
                          f"depth:${best['max_bet']:.0f}")
                else:
                    print(f"  {label}: edge {best['edge']:+.1%} but too small (${rec:.0f})")
            else:
                # One-liner skip
                print(f"  {label}: {outcomes[0]} {prob_a:.0%}/{1-prob_a:.0%}  "
                      f"ask=${best['ask']:.3f}  {best['edge']:+.1%}")

        # ── P/L footer ───────────────────────────────────────
        unrealized = 0
        for sk, pos in positions.items():
            # Rough mark-to-market using displayed price
            unrealized += pos["shares"] * (pos["avg_price"] * 0.1)  # placeholder

        print(f"  ───────────────────────────────────────────────────────")
        open_str = ", ".join(f"{p['outcome']}@${p['avg_price']:.2f}"
                            for p in positions.values()) if positions else "none"
        print(f"  Open: {open_str}  |  Realized P/L: ${realized_pnl:+.2f}  "
              f"| Trades: {len(trades)}")

        time.sleep(loop_sec)


def print_trades_summary(trades, realized_pnl, positions):
    """End-of-match summary."""
    if not trades:
        print("\n  No trades taken.")
        return

    print(f"\n  TRADE LOG:")
    for t in trades:
        pnl_str = f"  P/L: ${t['pnl']:+.2f}" if t["action"] == "SELL" else ""
        print(f"    [{t['time']}] {t['action']} {t['outcome']} "
              f"x{t['shares']:.0f} @ ${t['price']:.3f}{pnl_str}")

    # Unsold positions = resolved at $1 or $0
    for slug, pos in positions.items():
        print(f"    [END] HELD {pos['outcome']} "
              f"x{pos['shares']:.0f} @ ${pos['avg_price']:.3f}  "
              f"=> resolves to $1.00 or $0.00")

    print(f"\n  Realized P/L: ${realized_pnl:+.2f}")
    print(f"  Total trades: {len(trades)}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--series", required=True)
    p.add_argument("--pm-slug", required=True)
    p.add_argument("--loop", type=int, default=3)
    p.add_argument("--prior", type=float, default=None,
                   help="Override P(Team A) prior. Auto-fetches from PM if not set.")
    a = p.parse_args()
    run(a.series, a.pm_slug, a.loop, a.prior)
