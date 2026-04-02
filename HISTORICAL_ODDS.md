# How to Get Historical Starting Odds from Polymarket

This is the missing piece — getting the PM opening price for every match
so we can train the model WITH the prior as a feature.

---

## THE ENDPOINT

```
GET https://clob.polymarket.com/prices-history
    ?market={clobTokenId}
    &interval=max
    &fidelity=1
```

- **No auth required** (public endpoint)
- **Works on resolved/closed markets** (confirmed)
- Returns `{"history": [{"t": unix_timestamp, "p": price}, ...]}`
- `fidelity=1` = 1-minute granularity
- `interval=max` = full lifetime of the market

## HOW TO FIND THE STARTING ODD

The price timeseries follows a consistent pattern for CS2 markets:

```
[Market created] ──► [Price finds level] ──► [STABLE PRE-MATCH PRICE] ──► [Match starts → rapid movement]
    ~12-18h before         ~1-6h before           last ~2-6h                    ← THIS IS WHERE IT BREAKS
```

**The starting odd = the last stable price before rapid movement begins.**

### Algorithm to extract it:

1. Get full timeseries: `/prices-history?market={tokenId}&interval=max&fidelity=1`
2. Get the match start time from Gamma API (`gameStartTime` field)
3. Look at the last 2 hours before match start
4. Take the **median price in the 30-60 min window before start**
   - This filters out last-minute spikes/noise
   - Example: for Chinggis (start 13:00 UTC), the 12:00-12:30 window had 63-65c → starting odd = ~64c

### Alternative: direct window query
```
GET /prices-history?market={tokenId}&startTs={matchStart-3600}&endTs={matchStart-600}&fidelity=1
```
This gets prices from 60 min to 10 min before match start. Take the median.

## FULL PIPELINE TO GET STARTING ODDS

### Step 1: Get market metadata from Gamma
```
GET https://gamma-api.polymarket.com/markets?slug=cs2-{team1}-{team2}-{YYYY-MM-DD}
```
Extract: `clobTokenIds[0]`, `gameStartTime`, `conditionId`, `outcomes`

### Step 2: Get price history from CLOB
```
GET https://clob.polymarket.com/prices-history?market={clobTokenIds[0]}&interval=max&fidelity=1
```
Returns full timeseries.

### Step 3: Extract starting odd
- Parse `gameStartTime` to unix timestamp
- Filter history to window: `[matchStart - 3600, matchStart - 600]` (60 min to 10 min before)
- Starting odd = median of `p` values in that window
- If no data in window, fall back to last price before `matchStart`

### Step 4: For HISTORICAL matches (already resolved)
- Same endpoint works! `interval=max` returns data even for closed/resolved markets
- Slug format is predictable: `cs2-{team1abbr}-{team2abbr}-{YYYY-MM-DD}`
- The hard part: figuring out the slug abbreviations for old matches

## KNOWN ISSUES

1. **Slug abbreviations are not standardized** — PM uses short team codes (bst=BESTIA, cw=Chinggis Warriors) 
   that you learn by observation. No official mapping exists.

2. **Some very old resolved markets may return empty history** — GitHub issue #216 on py-clob-client. 
   Workaround: use `startTs`/`endTs` in ~15-day chunks instead of `interval`.

3. **`gameStartTime` can be null** — some markets don't have it set. 
   Fall back to looking for the inflection point in the price chart.

4. **Price history resolution** — fidelity=1 gives ~1 min resolution. 
   For markets that existed less than a few hours, you might only get 20-50 data points.

5. **Team A in token[0]** — `clobTokenIds[0]` corresponds to `outcomes[0]`. 
   Make sure you know which team is "team A" to interpret the price correctly.

## ALTERNATIVE: Trade Data (more granular)

```
GET https://data-api.polymarket.com/trades?market={conditionId}&limit=10000
```

Returns individual trades: `{price, size, timestamp, side, outcome}`. 
No auth needed. Gives you every single trade, not just 1-min snapshots.
To find opening price: paginate to find the first trades (offset near total count, since newest-first).

## GETTING ALL HISTORICAL CS2 SLUGS

To backfill starting odds for our training data:

1. Pull all closed CS2 markets:
   ```
   GET /markets?closed=true&limit=200&order=createdAt&ascending=false&offset=N
   ```
   Filter by slug prefix `cs2-`

2. For each, extract team names from `outcomes` and match start from `gameStartTime`

3. Cross-reference with GRID series by team names + date to link PM odds to GRID training data

4. Get starting odd via `/prices-history` for each

This gives us the PM prior for every historical match we have GRID round data on.
