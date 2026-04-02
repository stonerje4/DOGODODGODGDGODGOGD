# CS2-Edge: Fixes & Leakage Audit

_Reviewed 2026-04-02. Full code read of every .py + docs._

---

## 🚨 DATA LEAKAGE (will inflate backtest, blow up live)

### 1. Training label leaks into every round's features
**File:** `feature_extractor.py` line ~`team_a_won_map=team_a_won`  
**Problem:** Every `RoundFeatures` row for a given map has `team_a_won_map` set from the start — the model sees the final outcome as a field on the dataclass. Right now it's only used as the *label* (`y`), and `features_to_dict()` doesn't include it in `X`, so it's not leaking **yet**. But it's one careless refactor away from catastrophe. More importantly:

### 2. Features are computed AFTER the round resolves — then used to "predict" that same round
**File:** `feature_extractor.py`, the main loop  
**Problem:** The loop processes round `i`, updates `score_a/score_b`, kills, streak, etc. **including the result of round i**, then stores those features tagged as `round_num = i`. In `backtest.py`, the model predicts using features that already contain round i's outcome (score, kills, momentum). This is **textbook look-ahead bias** — the model "predicts" the map winner using information from a round whose result it already knows.  
**Fix:** Features at round N should only use state **before** round N resolves (i.e., state after round N-1). Shift the feature snapshot one round earlier, or build features *before* processing the round result.

### 3. Economy state is post-round, not pre-round
**File:** `feature_extractor.py` → `econ.get_economy_at_round(round_num)`  
**Problem:** The economy engine records economy at the *start* of each round (good), but the feature extractor grabs economy for `round_num` after it already processed that round's result. The comment says "economy state for NEXT round" but it indexes by `round_num`, not `round_num + 1`. The economy values themselves are pre-round (recorded before resolution in `process_round`), so this is *partially* OK, but the pairing with post-round score/kills makes the whole row inconsistent.  
**Fix:** Be explicit — features for "predicting from the state entering round N+1" should all use post-round-N data consistently.

### 4. Backtest uses round-end PM price, not round-start
**File:** `backtest.py` → `find_pm_price_at(pm_hist_sorted, round_end_ts)`  
**Problem:** The PM price at round *end* already reflects the round's outcome (PM traders react in real-time). The model's "edge" is measured against a price that already moved. This makes edges look smaller than they are for correct predictions, and larger for incorrect ones — net effect is noisy and biased.  
**Fix:** Use PM price at round *start* timestamp (`startedAt`), not round end (`startedAt + duration`).

---

## 🟡 MODEL / BETTING LOGIC BUGS

### 5. `first_kills_a` / `first_kills_b` are always 0
**File:** `feature_extractor.py`  
**Problem:** `fk_a` and `fk_b` are initialized to 0 and never incremented. The `first_kill_diff` feature is always 0 — dead weight in the model. GRID does provide `firstKill` boolean per team per segment.  
**Fix:** Parse `firstKill` from segment team data and increment `fk_a`/`fk_b`.

### 6. `rounds_remaining` calculation is wrong
**File:** `feature_extractor.py`  
**Problem:** `rounds_remaining = max(0, ROUNDS_TO_WIN - max(score_a, score_b))` — this gives rounds until the *leading* team wins, not total possible rounds remaining. At 6-6, this gives 7, but there could be up to 13 rounds left (all the way to 13-12 in OT... though OT isn't handled at all).  
**Fix:** Should be `max(0, 2 * ROUNDS_TO_WIN - 1 - rounds_played)` for regulation max, or think about what this feature actually means for the model.

### 7. Winner economy gets kill reward applied to TEAM total, not per-player
**File:** `economy_engine.py` → `process_round`  
**Problem:** `win_bonus * 5` is correct (each of 5 players gets the bonus). But `winner_kill_money` from `_kill_reward_total` sums weapon kills × reward — this is already the total across all players who got kills. Then it's added to team money directly. This is correct IF `weapon_kills[winner]` is already the full team's kills. ✅ Actually OK on re-read, but the buy-cost deduction at the end is rough: it deducts a fixed estimate for all 5 players based on buy type, which can drift significantly over a half.

### 8. Economy buy-cost deduction is very approximate
**File:** `economy_engine.py` → end of `process_round`  
**Problem:** After computing income, the code deducts an estimated buy cost for the *next* round based on current money. But this happens *before* the next round's economy is recorded. Over 12 rounds, cumulative error can be $5-10k per team, which pollutes `money_diff` — one of the model's features.  
**Fix:** Consider anchoring to GRID's actual `money` / `netWorth` / `loadoutValue` fields when available (the GraphQL query already fetches them) instead of pure reconstruction.

### 9. `map_prior` derivation is a magic number
**File:** `live.py` → `map_prior = 0.5 + (match_prior - 0.5) * 0.67`  
**Problem:** The 0.67 dampening factor to go from series prior to map prior is arbitrary. For a Bo3, if team A has 70% series win probability, their individual map win prob depends on their edge per map, not a linear scaling. The `SeriesCalculator` already handles Bo3 math correctly — the prior for future maps should come from inverting the Bo3 formula, not a magic constant.  
**Fix:** Either solve for p_map given P(series) and Bo3 math, or just use the PM map-level market price directly as the map prior (PM has map1/map2/map3 markets).

### 10. `live.py` and `live_edge.py` are two separate systems doing the same thing
**File:** `live.py`, `live_edge.py`  
**Problem:** `live.py` has paper trading with position tracking, P&L, orderbook checks, buy/sell logic. `live_edge.py` has signal detection with Pinnacle prior support. They share no code and have divergent logic. Which one is the "real" one?  
**Fix:** Pick one. `live.py` is more complete. Kill or merge `live_edge.py`.

---

## 🟢 QUICK WINS

### 11. `find_overlaps.py` still uses PM search (known broken)
**File:** `find_overlaps.py`  
**Problem:** Uses `pm.search_markets("Counter-Strike")` which the docs say returns garbage (GTA VI results). The `OVERLAP_DISCOVERY.md` explicitly says to paginate all open markets and filter client-side by `cs2-` slug prefix.  
**Fix:** Switch to paginate-all approach as documented.

### 12. GRID `titleId` not filtered in `find_overlaps.py`
**File:** `find_overlaps.py` → `get_series_by_date_range` call  
**Problem:** No `title_id="28"` passed — pulls ALL esports series (LoL, Valorant, Dota, etc.) and tries to match them to PM CS2 markets. Wastes API calls, risks false matches.  
**Fix:** Add `title_id="28"` to the call.

### 13. `grid_client.py` comment says titleId "7" for CS2
**File:** `grid_client.py` line ~`title_id: "7" for CS2`  
**Problem:** The docs in `OVERLAP_DISCOVERY.md` say CS2 is `"28"`, not `"7"`. The comment is wrong and will mislead.  
**Fix:** Update comment to `"28"`.

### 14. No PM taker fee in backtest P&L
**File:** `backtest.py` → `summarize()`  
**Problem:** P&L simulation does `profit = BET_SIZE * (1-p)/p` on win — no fee deducted. The 3% taker fee is significant. A 5% edge with 3% fee is really 2% edge.  
**Fix:** Deduct `POLYMARKET_TAKER_FEE` from profit calculation.

### 15. `scrape_pinnacle.py` imported but probably doesn't work
**File:** `live_edge.py` → `from scrape_pinnacle import OddsScraper, PinnacleOdds`  
**Problem:** Pinnacle requires scraping a JS-rendered page. Without a headless browser this almost certainly fails silently and returns `None`, meaning `live_edge.py` always falls back to 50/50 priors for future maps.  
**Fix:** Verify it works. If not, use PM map prices as priors instead.

### 16. No overtime handling
**File:** `feature_extractor.py`, `economy_engine.py`  
**Problem:** MR12 overtime (starting at round 25) has different economy rules ($10k start money, no loss bonus). The economy engine has no OT logic. If a map goes to OT, the economy reconstruction goes off the rails.  
**Fix:** Detect OT rounds (>24) and apply OT economy rules.

---

## Priority Order

**Do first (leakage — invalidates all backtest results):**
1. ✅ Fix #2 — shift features one round earlier (look-ahead bias) — DONE 2026-04-02
2. ✅ Fix #4 — use round-start PM price, not round-end — DONE 2026-04-02
3. ~~Fix #14 — add fees to backtest P&L~~ — SKIPPED (backtest may be deleted)

**Then (model quality):**
4. ✅ Fix #5 — wire up first_kills from GRID firstKill field — DONE 2026-04-02
5. ✅ Fix #8 — use GRID's actual money/loadout fields, fall back gracefully — DONE 2026-04-02
6. Fix #9 — proper map prior from Bo3 math or PM map markets
7. ✅ Fix #6 — rounds_remaining = max_total - rounds_played — DONE 2026-04-02
8. Fix #16 — overtime economy

**Cleanup:**
9. Fix #10 — merge live.py and live_edge.py
10. Fix #11 + #12 — fix find_overlaps.py discovery
11. Fix #13 — titleId comment
12. Fix #15 — verify Pinnacle scraper or remove
