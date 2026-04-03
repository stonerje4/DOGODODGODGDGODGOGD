# CS2 Live Paper Trading System — Complete Project Guide

**Last updated:** 2026-04-03 13:50 UTC
**Repo:** https://github.com/stonerje4/DOGODODGODGDGODGOGD
**Server:** Hetzner VPS — `ubuntu-4gb-hel1-1` (204.168.146.59)
**Dashboard:** https://dumpit.wtf/cs2

---

## What This Project Does

Automated paper trading system for CS2 esports matches on Polymarket. It:

1. **Discovers matches** that exist on both GRID.gg (live round data) and Polymarket (prediction markets)
2. **Monitors live matches** round-by-round via GRID's real-time API
3. **Runs an XGBoost model** (46 features) to predict P(Team A wins map) at every round
4. **Compares model probability vs Polymarket orderbook prices** to find edge
5. **Paper trades** when edge ≥ 8% using quarter-Kelly sizing
6. **Logs everything** to Excel files, GitHub, and a live web dashboard

**This is paper trading only — no real money is being wagered.**

---

## Architecture

```
watcher.py (systemd, runs 24/7)
    ├── Scans every 15 min: GRID API + Polymarket pagination
    ├── Matches teams by name normalization + date
    ├── For matches starting within 60min: launches live.py
    │
    └── live.py (one per match, detached process)
         ├── Grabs PM starting odds (CLOB price history, median 60-5min pre-match)
         ├── Polls GRID every 10s for round state changes
         ├── Extracts 46 features (score, economy, momentum, kills, tactical)
         ├── XGBoost predicts P(Team A wins map)
         ├── Derives series probabilities (Bo3 math)
         ├── Checks PM orderbook for edge (only fetches if rough edge > 2%)
         ├── Paper trades: BUY when edge ≥ 8%, SELL when market overvalues position
         ├── Logs to Excel every 5 min + git push to GitHub
         └── Writes to stdout log file for dashboard
```

---

## Key Files

| File | Purpose |
|------|---------|
| `watcher.py` | Main scanner — finds GRID/PM overlaps, launches live.py |
| `live.py` | Per-match trader — model, orderbook, paper trades, Excel logging |
| `feature_extractor.py` | 46 features from GRID data → model input |
| `economy_engine.py` | Deterministic CS2 economy reconstruction (GRID doesn't provide money) |
| `model.py` | XGBoost model wrapper + Bo3 series probability calculator |
| `bet_sizer.py` | Quarter-Kelly sizing with liquidity cap |
| `find_overlaps.py` | GRID + PM market matching by team names |
| `grid_client.py` | GRID.gg GraphQL client (Central + Live APIs) |
| `polymarket_client.py` | Polymarket Gamma + CLOB API client |
| `config.py` | API keys, CS2 economy constants, model parameters |
| `train_model.py` | Training pipeline: load data → extract features → train XGBoost |
| `dashboard_web.py` | Flask web UI on port 8050 (proxied through nginx at /cs2) |
| `watch.py` | Terminal dashboard (alternative to web UI) |
| `models/map_win_xgb.pkl` | Trained model (46 features, 244k samples, AUC 0.799) |
| `data/series/*.json` | 5,236 historical GRID series (training data) |
| `data/live_logs/` | Excel audit trails + per-match stdout logs |
| `data/watcher_state.json` | Persisted watcher state (survives restarts) |

---

## Running Services (systemd)

| Service | Status | Purpose |
|---------|--------|---------|
| `cs2-watcher.service` | **enabled, always-on** | Scans for matches, launches live.py |
| `cs2-dashboard.service` | **enabled, always-on** | Web dashboard on port 8050 |

```bash
systemctl status cs2-watcher.service    # Check watcher
systemctl status cs2-dashboard.service  # Check dashboard
journalctl -u cs2-watcher -f            # Tail watcher logs
cat /var/log/cs2-watcher.log            # Watcher log file
```

### Watcher behavior:
- Scans every 15 minutes
- Launches live.py 60 minutes before scheduled match start
- Monitors up to 4 hours after scheduled start (covers late starts + Bo3)
- Persists state to `data/watcher_state.json` (12h expiry)
- On restart: detects already-running live.py via `ps aux`, avoids double-launches
- `KillMode=process` — live.py children survive watcher restarts
- Skips markets with < $30 liquidity

---

## The Model

**Type:** XGBoost binary classifier
**Target:** P(Team A wins current map)
**Training data:** 244,146 round-state snapshots from 5,236 historical GRID series
**Validation AUC:** 0.799

### Features (46 total)

**Core (12):** score_diff, score_a, score_b, rounds_played, rounds_remaining, team_a_is_ct, is_second_half, rounds_until_switch, map_ct_rate, map one-hot (8 maps)

**Economy (11):** money_a, money_b, money_diff, buy_full/force/eco for both teams, loss_tier_a/b, consec_losses_a/b

**Momentum (3):** last_3_wins_a, last_5_wins_a, streak_a

**Combat (2):** kill_diff, first_kill_diff

**Pistol (4):** pistol_1_a/b, pistol_2_a/b

**NEW Tactical (7, added 2026-04-03):**
- `h1_score_diff` — halftime score differential (#5 feature by importance)
- `plant_rate_diff` — T-side bomb plant rate differential (#6 by importance)
- `plant_win_rate_diff` — post-plant clutch/defuse-prevention rate
- `ct_outperformance_a` — team's CT win rate vs map historical average
- `defuse_attempt_rate_diff` — defuse kit investment signal
- `post_pistol_r2_a/b` — anti-eco conversion after pistol round

### Economy Note
GRID does NOT provide per-round money data (confirmed: 0% of 2,740 segments have money fields). Both training and production use the deterministic economy reconstruction engine (`economy_engine.py`). This is consistent — no train/serve skew.

### What GRID Provides Per Round (segment)
- kills, deaths per team ✅ (94% coverage)
- firstKill per team ✅ (50% coverage)
- weaponKills breakdown ✅ (94% coverage)
- side (CT/T) ✅ (100%)
- won ✅ (100%)
- objectives (plantBomb, defuseBomb, beginDefuseWithKit/WithoutKit) ✅ (100%)
- startedAt + duration per round ✅ (100%) — NOT used, confirmed no signal
- money/loadoutValue/netWorth per round ❌ (0% — never provided by GRID on segments)

---

## Trading Logic

### Entry
1. Model predicts P(Team A wins map) from current round state
2. Bo3 math derives P(Team A wins series), P(goes to map 3), etc.
3. For each market (WINNER, MAP1, MAP2, MAP3, O/U 2.5):
   - Quick check: `|model_prob - gamma_mid_price| - fee > 2%`? If not, skip orderbook fetch
   - If potential edge: fetch real CLOB orderbook (2 API calls)
   - Calculate edge: `model_prob - best_ask - 3% fee`
   - If edge ≥ 8%: size with quarter-Kelly, capped by book depth and 10% of bankroll

### Exit
- Sell when `model_prob < best_bid - fee` (market overvalues our position)
- Force-exit at map point when underwater (one team at 12 rounds)
- Auto-resolve on match finish

### Sizing
- Quarter-Kelly: `f* = kelly_fraction * (p*b - q) / b`
- Capped at: 10% of available bankroll, 30% of market liquidity
- Minimum bet: $5
- Starting bankroll: $1,000 per match (paper)

---

## API Rate Limits

| API | Limit | Our throttle |
|-----|-------|-------------|
| GRID Central | 20 req/min | 1 per 3.1s |
| GRID Live (series state) | 180 req/min | 1 per 0.4s |
| GRID Live (per series) | 6 req/min | Implicit via poll interval |
| Polymarket Gamma | No hard limit | Cached per-map, not per-round |
| Polymarket CLOB | No hard limit | Only fetch when rough edge > 2% |

---

## Recent Updates (April 2-3, 2026)

### April 2
- Built the entire system from scratch: watcher, live.py, model, feature extractor
- Fixed data leakage: look-ahead bias, backtest round-start prices, economy reconstruction
- Trained model: 244k samples, 39 features, AUC 0.798
- First live run: caught some matches but watcher kept dying

### April 3
- **Watcher reliability**: Replaced nohup/disown with systemd service (auto-restart, state persistence, process recovery)
- **New features**: Added 7 tactical features (h1_score_diff, plant_rate_diff, plant_win_rate_diff, ct_outperformance, defuse_attempt_rate_diff, post_pistol_r2). Retrained to 46 features, AUC 0.799
- **Deep GRID audit**: Confirmed exactly what GRID provides vs doesn't. Economy reconstruction is the only option (consistent train/serve).
- **Web dashboard**: Flask app at https://dumpit.wtf/cs2 — Live/Waiting/Finished tabs, auto-refresh
- **Cleanup**: Removed stale Docker containers (Airbyte, FlareSolverr), freed 13GB disk

---

## Known Issues / TODO

### Dashboard (https://dumpit.wtf/cs2)
1. **Full page refresh resets view** — every 10s reload resets tab state, scroll position, and which matches are expanded. Need to switch to AJAX/fetch updates instead of full page reload so user view is preserved.
2. **Some matches show blank data** — dashboard log parser fails on certain round line formats (e.g., when map transitions happen). Chinggis and QUBE showed blank in the UI despite having active data in stdout logs.
3. **Waiting tab should show countdown** — currently just lists team names. Should show scheduled start time and time until launch.
4. **Signals need round/score/time context** — currently just shows the trade params. Need: round number, map score at time of trade, timestamp.
5. **Open positions need entry context** — need: when position was opened, at what round/score, not just current state.
6. **Match ordering flickers** — matches jump around on refresh because they're sorted by log file mtime.

### Model / Trading (ALL FIXED April 3)
1. **~~Map transition edge exploit~~** FIXED: live.py skips trading entirely between maps (active_map_live flag)
2. **~~Garbage lottery tickets~~** FIXED: MIN_PROB_THRESHOLD=20%, MAX_PROB_THRESHOLD=95%
3. **~~MAP positions never resolve~~** FIXED: auto-resolve on map finish
4. **~~Force-exit kills winning positions~~** FIXED: only exits when OPPONENT at map point, not our team
5. **~~Force-exit fails on empty orderbook~~** FIXED: falls back to $0.001 bid + force-exit at model<5%
6. **90s map transition cooldown** added to prevent stale orderbook trades

### Previously logged (keeping for reference)
1. **~~Map transition edge exploit~~** — model instantly recalculates series prob when a map ends (e.g., 99% at 1-0 in Bo3) while PM orderbook is slow to update. This creates apparent "edge" that is really just latency arbitrage against thin books ($150 depth). In prod with real money this would be eaten instantly. May need a cooldown period after map transitions.

### System
1. **Economy display sometimes looks wrong** — reconstruction is approximate (especially around force-buys). The model handles this fine but the display can show misleading values.
2. **Old model vs new model transition** — live.py processes load the model once at startup. Currently-running processes use whichever model was on disk when they started. Only new launches pick up model changes.
3. **No real money trading** — system is paper-only. To go live would need Polymarket CLOB authentication + order placement.
4. **Git push conflicts** — multiple live.py processes push to the same repo simultaneously. Usually works but can occasionally fail silently.
5. **GRID data latency** — sometimes takes 5-20 minutes after a match starts for GRID to provide round data.
6. **Starting odds fallback** — if live.py launches mid-match, starting odds come from "first available price" which may be extreme. --prior flag can override.
7. **Dashboard is public** — https://dumpit.wtf/cs2 has no authentication. Anyone with the URL can see it.

---

## How to Check Everything is Working

```bash
# Services alive?
systemctl status cs2-watcher.service
systemctl status cs2-dashboard.service

# Live processes?
ps aux | grep live.py | grep -v grep

# Latest watcher activity?
tail -30 /var/log/cs2-watcher.log

# Watcher state?
cat /root/.openclaw/workspace/cs2-edge/data/watcher_state.json | python3 -m json.tool

# Latest match logs?
ls -lt /root/.openclaw/workspace/cs2-edge/data/live_logs/stdout_*.log | head -10

# Specific match output?
tail -50 /root/.openclaw/workspace/cs2-edge/data/live_logs/stdout_cs2-SLUG-HERE_*.log

# Excel files?
ls -lt /root/.openclaw/workspace/cs2-edge/data/live_logs/*.xlsx | head -10

# Web dashboard?
curl -s https://dumpit.wtf/cs2 | grep "<title>"
```

---

## Config Quick Reference

```python
# config.py key values
GRID_API_KEY = "kQv2gBFtB5hPqSiTxgYuR2oKKvEZzqB4ZFZEAf4N"
MIN_EDGE_THRESHOLD = 0.08       # 8% minimum edge
KELLY_FRACTION = 0.25           # Quarter-Kelly
DEFAULT_BANKROLL = 1000         # $1k paper per match
POLYMARKET_TAKER_FEE = 0.03     # 3% taker fee
```

**GitHub:** stonerje4/DOGODODGODGDGODGOGD (credentials in git remote URL)
