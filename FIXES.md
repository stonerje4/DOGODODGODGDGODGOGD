# CS2-Edge: Master Fix Tracker

_Full audit 2026-04-02. All issues from feature_extractor, backtest, live.py, and prod infrastructure._

---

## ✅ COMPLETED (2026-04-02)

### Data Leakage
- **Look-ahead bias** — Features for round N now only use state from rounds 1..N-1. Cumulative stats (score, kills, momentum, first kills) update AFTER the feature snapshot, not before. (`feature_extractor.py`)
- **Round-start PM price** — Backtest now uses `startedAt` timestamp (before round plays) instead of `startedAt + duration` (after round resolves). (`backtest.py`)

### Model Quality
- **First kills wired up** — `first_kill_team` parsed from GRID `firstKill` boolean on segment teams/players. `first_kill_diff` feature is no longer always 0. (`feature_extractor.py`, `economy_engine.py`)
- **GRID actual money** — Uses GRID's real `money`/`loadoutValue` from segment data instead of reconstructed economy. Falls back to carry-forward if missing. (`feature_extractor.py`, `economy_engine.py`)
- **rounds_remaining fixed** — Now `max(0, 25 - rounds_played)` instead of `ROUNDS_TO_WIN - max(score_a, score_b)`. (`feature_extractor.py`)

### Prod (live.py) Rewrite
- **PM market caching** — Market metadata fetched once per map change, not per round. Saves ~5 Gamma API calls per tick.
- **Selective orderbook** — Only fetches orderbook if rough edge > 2% or holding a position. Saves ~8 CLOB calls per tick. Total: ~16 requests/tick → ~3-5.
- **Feature extraction caching** — `extract_features_from_grid_state` called once per active game, results reused for all market checks. Was being called up to 4× per tick.
- **BetSizer tracks real exposure** — Kelly sizing now uses `bankroll - open_exposure` for available capital. Previously always assumed full $1000.
- **Team name resolution by index** — Positions resolved by `outcome_idx` (0 or 1) not string comparison. Immune to GRID "Natus Vincere" vs PM "NaVi" mismatches.
- **Error handling** — GRID and PM calls wrapped in try/except. A single API timeout no longer crashes the session.
- **State hash includes round count** — `n_segments` added to hash. Detects round changes even when map score briefly looks the same.
- **Map priors from PM markets** — For Bo3, future map priors now come from PM's actual map1/map2/map3 market prices when available, instead of the `0.67` magic number. Falls back to scaled series prior only if PM map market doesn't exist.
- **Full Excel audit trail** — Every round logged with: model prob, PM price, orderbook ask/bid, liquidity, edge, economy, kills, first kills, positions, P&L. Written every 5 min + git push.

---

## 🟡 REMAINING — Model / Training

| # | Issue | File | Impact |
|---|-------|------|--------|
| 1 | Label field `team_a_won_map` on every row (not leaking via `features_to_dict` but fragile) | feature_extractor.py | Low — monitor |
| 2 | No overtime economy (MR12 OT = $10k start, no loss bonus) | feature_extractor.py, economy_engine.py | Medium — rare but corrupts data |
| 3 | Economy reconstruction engine still exists but unused in live | economy_engine.py | Cleanup — can simplify |

## 🟡 REMAINING — Infrastructure

| # | Issue | File | Impact |
|---|-------|------|--------|
| 4 | `find_overlaps.py` uses PM search (known broken) | find_overlaps.py | High — discovery misses matches |
| 5 | `find_overlaps.py` no titleId filter (pulls all esports) | find_overlaps.py | Medium — wastes API, false matches |
| 6 | `grid_client.py` comment says titleId "7" (should be "28") | grid_client.py | Low — misleading |
| 7 | `live_edge.py` is dead code (superseded by live.py) | live_edge.py | Cleanup |
| 8 | `scrape_pinnacle.py` probably doesn't work (needs headless browser) | scrape_pinnacle.py | Low — unused now |
| 9 | No backtest PM fee deduction | backtest.py | Low — backtest may be deleted |

---

## Timing / Information Flow (Reference)

```
Round N plays out (~1-2 min)
  ├─ PM traders watch streams (~30s delay), price moves mid-round
  ├─ Round N ends in-game
  │   ├─ GRID updates (~1-5s)
  │   └─ PM has ALREADY priced round N result
  │
  ├─ WE poll GRID (up to 10s from poll interval)
  │   ├─ Features = state entering round N+1 (post round N)
  │   ├─ Model predicts P(win)
  │   └─ Compare to PM orderbook
  │
  Round N+1 starts ← information parity point
```

**Our edge is accuracy, not speed.** We see the same info as PM traders at round boundaries. The model's value is better probability estimation from round state + economy + momentum, not from being faster.

**Mid-round:** PM moves on kills/clutches before we see anything. We should only trade at round boundaries (which the code enforces via state_hash change detection).
