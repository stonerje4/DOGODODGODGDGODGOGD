# Prod (live.py) Audit — Leakage, API Waste, Bugs

_Reviewed 2026-04-02 against the feature_extractor changes we just shipped._

---

## 🚨 LEAKAGE / CORRECTNESS ISSUES IN PROD

### P1. `feats[-1]` grabs the LAST round's features — which is correct now
**Status:** ✅ OK after our fix.
Before the fix, `feats[-1]` was the latest round with look-ahead (it included
that round's own score/kills). Now `feats[-1]` is the state *entering* the
latest round — model sees everything up to round N-1 when predicting round N.
No change needed in live.py.

### P2. `extract_features_from_grid_state` is called up to 4x per tick — wasteful
**File:** `live.py` lines ~258-270
**Problem:** The main loop calls `extract_features_from_grid_state(state, game_idx=ai)`
once for the model prediction. Then `get_map_prob()` calls it AGAIN for each
active map (MAP1, MAP2, MAP3). Each call re-parses ALL segments, re-processes
ALL rounds, rebuilds all features from scratch. For a map at round 20, that's
~20 feature extractions × up to 4 calls = ~80 feature builds per tick.
**Fix:** Cache the extraction results per game_idx per tick. Extract once, reuse.

### P3. PM `get_market_by_slug` called per-market per-tick — up to 5 HTTP requests
**File:** `live.py` lines ~273
**Problem:** Every tick (every round change), the code calls `pm.get_market_by_slug()`
for WINNER, MAP1, MAP2, MAP3, and O/U. That's 5 separate HTTP GETs to Gamma API.
Most of these markets don't change between rounds (slug, outcomes, token IDs
are all static). Only the price changes, and for real-time prices you should
use the CLOB orderbook anyway (which is already fetched via `get_book`).
**Fix:** Fetch market metadata ONCE at startup (or when a new map starts),
cache the market objects, only refresh orderbooks per tick.

### P4. `get_book` makes 2 HTTP calls per market (one per outcome token)
**File:** `live.py` line ~70
**Problem:** Each market check = 1 `get_market_by_slug` + 2 `get_orderbook` = 3 HTTP calls.
With 5 markets checked per tick: **15 HTTP requests per round change**.
PM is generous but this is wasteful. Also, orderbooks for markets you don't
hold positions in and have no edge on are pure waste.
**Fix:** Only fetch orderbook for markets where the Gamma price suggests
possible edge. Skip orderbook fetch if `|model_prob - gamma_price| < threshold`.

### P5. BetSizer in live.py doesn't track positions — bankroll is always $1000
**File:** `live.py` → `sizer = BetSizer(bankroll=1000)`
**Problem:** `live.py` has its own `positions` dict for tracking, but the
`BetSizer` instance has its own separate `self.positions` list that never
gets updated. So `BetSizer.kelly_size()` always thinks `available = bankroll`
(full $1000). The `max_from_bankroll = available * 0.10` cap is never reduced
by existing positions. If you have 3 open positions worth $300, Kelly still
sizes as if you have $1000 free.
**Fix:** Either call `sizer.add_position()` / `sizer.remove_position()` when
live.py opens/closes positions, or just pass available capital directly.

### P6. No cooldown after buy — can re-buy same market next tick
**File:** `live.py` BUY CHECK section
**Problem:** If you buy MAP1 and then the edge briefly dips below threshold
(triggering a SELL) and then rebounds, you'd buy again immediately. More
practically: if the GRID state hash changes (e.g. a timeout, pause) without
an actual round changing, you'd re-evaluate and potentially trade on stale
model output. The `state_hash` only includes scores, not round number.
**Fix:** Include the actual round number in `state_hash`. Add a per-market
cooldown timer (don't re-buy within N seconds of selling).

### P7. `state_hash` doesn't include round number — misses round changes at same score
**File:** `live.py` line ~`state_hash = f"{sa}{sb}{ai}{ms_a}{ms_b}"`
**Problem:** If team A wins round 5 then team B wins round 6 (score stays
3-3 → 4-3 → wait no, score does change). Actually the real issue is: halftime
resets sides but scores stay the same going into the half. Also OT rounds.
More importantly: the hash doesn't detect the GRID data refreshing with the
same score (e.g. player stats update, economy update mid-round). This means
mid-round updates from GRID won't trigger a re-check. For a live system
that's actually fine — you only want to act on round boundaries. But:
**Fix:** Add round count to hash: `f"{sa}{sb}{ai}{ms_a}{ms_b}{len(segments)}"`.

### P8. `map_prior = 0.5 + (match_prior - 0.5) * 0.67` — magic number
**File:** `live.py` line ~159
**Problem:** Already flagged in FIXES.md #9. This dampening factor is arbitrary.
For future maps (MAP2, MAP3), the prior should come from the actual PM map
market price, not a made-up linear scaling of the series prior.
**Fix:** When checking MAP2/MAP3 markets, grab the PM price from that specific
market as the prior. The PM map price IS the market's pre-match prior for
that individual map. The 0.67 factor is only needed for the initial
`bo3_probabilities` call where we need to estimate future map win rates —
use PM map market prices for those too.

---

## 🟡 API WASTE / PERFORMANCE

### P9. Total API calls per round change: up to 19
Breakdown:
- 1× GRID `get_series_state` (Live API)
- Up to 4× `extract_features_from_grid_state` (CPU, not API, but wasteful)
- 5× `pm.get_market_by_slug` (Gamma API)
- 10× `pm.get_orderbook` (CLOB API, 2 per market)
= **16 HTTP requests per round change**

At 10s poll interval with ~25 rounds per map and 2-3 maps per series:
~75 rounds × 16 = ~1,200 requests per match.

With caching (P3 fix) + selective orderbook (P4 fix):
- 1× GRID
- 0× Gamma (cached)
- 2-4× CLOB (only markets with possible edge)
= **3-5 requests per round change** = ~375 per match.

### P10. No request error handling / retry for PM API
**File:** `live.py`
**Problem:** If any PM request fails (timeout, 429, network blip), the whole
tick silently skips that market. No retry, no backoff, no logging of the
failure. The `polymarket_client.py` has basic `raise` on non-200, which would
bubble up as an exception and... get caught by the outer try/except in
`live_edge.py` but NOT in `live.py` (no try/except in the main loop).
A single PM API failure crashes the entire live session.
**Fix:** Wrap PM calls in try/except with retry. At minimum, don't crash on
a transient PM failure.

### P11. GRID poll interval vs rate limit mismatch
**File:** `live.py` default `--poll 10`
**Problem:** GRID Live API allows 6 req/min per series = 1 every 10 seconds.
The poll interval is 10s. BUT: after a state change, the code immediately
makes up to 4 more `extract_features` calls (CPU only, no API hit) and
then 15+ PM API calls, which take time. The next GRID poll could fire
before 10s have elapsed from the last one if PM calls are fast.
**Fix:** Actually the GridClient already has `_throttle_live()` with 0.4s min
gap. But the per-series 6/min limit isn't tracked — only the global 180/min.
For a single series this is fine (10s >> 0.4s), but if running multiple
series concurrently it could be an issue.

---

## 🟢 QUICK FIXES

### P12. Team A/B name resolution happens too late
**File:** `live.py` lines ~133-148
**Problem:** `team_a_name` and `team_b_name` are set from PM outcomes during
prior grab. But later, the GRID data has its own team names that may differ.
The code uses GRID names (`ta`, `tb`) for display but PM names for positions.
If names mismatch (GRID: "Natus Vincere", PM: "NaVi"), position resolution
at match end may fail — `pos.outcome == winner` could be False even when
the team actually won.
**Fix:** Normalize team names or resolve positions by outcome index, not name.

### P13. Duplicate `Position` dataclass
**File:** `live.py` and `bet_sizer.py` both define their own `Position` class
**Problem:** Two different Position classes with slightly different fields.
`live.py` uses its own, `bet_sizer.py` has its own. Confusing and will cause
bugs if someone tries to pass one to the other.
**Fix:** Use one Position class, probably from bet_sizer.

---

## Summary: What to Fix Now

**Critical (affects correctness of live trading):**
1. P5 — BetSizer doesn't track real positions (sizing is wrong)
2. P10 — No error handling, PM failure crashes everything
3. P12 — Team name mismatch can break position resolution

**High value (API waste reduction):**
4. P3 — Cache PM market metadata (saves 5 calls/tick)
5. P4 — Selective orderbook fetch (saves ~8 calls/tick)
6. P2 — Cache feature extraction per game_idx (CPU savings)

**Nice to have:**
7. P6/P7 — Better state_hash with round number + cooldown
8. P8 — Use PM map prices as priors instead of magic 0.67
9. P13 — Deduplicate Position class
