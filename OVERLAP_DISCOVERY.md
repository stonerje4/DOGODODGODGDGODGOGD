# Finding ALL Live/Upcoming CS2 Overlaps: GRID.gg + Polymarket

Complete instructions for discovering every CS2 match that exists on BOTH platforms
so we can scan for betting edge.

---

## TL;DR — The Scanning Loop

```
1. GRID: Pull upcoming/live CS2 series (titleId: "28") for next 24h
2. Polymarket: Paginate /markets?closed=false, filter slugs starting with "cs2-"
3. Match by team names + date
4. For each overlap: feed to live_edge.py
```

---

## PART 1: GRID.gg — Finding All CS2 Matches

### API Info
| Item | Value |
|---|---|
| Central Data URL | `https://api-op.grid.gg/central-data/graphql` |
| Live Data URL | `https://api-op.grid.gg/live-data-feed/series-state/graphql` |
| Auth | `x-api-key` header (key in config.py) |
| Rate limit (Central) | **20 req/min** (1 every 3 sec) |
| Rate limit (Live) | **180 req/min** overall, **6/min per series** |
| CS2 title ID | **`"28"`** (NOT "7" — we had this wrong!) |

### CRITICAL FIX: CS2 titleId is "28"

Title ID mapping:
- Dota 2 = `"2"`
- League of Legends = `"3"`
- Valorant = `"6"`
- Rainbow Six = `"25"`
- **Counter-Strike 2 = `"28"`**
- Standoff 2 = `"32"`

### Query: Get ALL Upcoming CS2 Series (next 24h)

```graphql
query UpcomingCS2 {
    allSeries(
        filter: {
            startTimeScheduled: {
                gte: "2026-04-02T00:00:00Z"
                lte: "2026-04-03T00:00:00Z"
            }
            titleId: "28"
            type: ESPORTS
        }
        orderBy: StartTimeScheduled
        orderDirection: ASC
        first: 50
    ) {
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
```

Paginate with `after: <endCursor>` when `hasNextPage` is true. Max page size = 50.

### Query: Find Currently LIVE Series

There is NO direct "give me live matches" endpoint. The method:

1. Pull series scheduled within a **-4h to +1h window** (matches can start late):
```graphql
filter: {
    startTimeScheduled: {
        gte: "2026-04-02T10:00:00Z"   # 4 hours ago
        lte: "2026-04-02T15:00:00Z"   # 1 hour from now
    }
    titleId: "28"
    type: ESPORTS
}
```

2. For each candidate, poll **Series State API** and check:
```python
state = grid.get_series_state(series_id)
is_live = state["started"] == True and state["finished"] == False
```

3. A series is:
   - **Upcoming**: `started == False`
   - **Live**: `started == True` and `finished == False`
   - **Finished**: `finished == True`

### ALL SeriesFilter Fields (from schema introspection)

| Field | Type | Use |
|---|---|---|
| `startTimeScheduled` | `DateTimeFilter {gte, lte}` | Date range |
| `titleId` | `ID` | Single title: `"28"` for CS2 |
| `titleIds` | `IdFilter` | Multiple titles |
| `type` | `SeriesType` enum | `ESPORTS` |
| `types` | `[SeriesType]` | Multiple types |
| `tournamentId` | `ID` | Filter by specific tournament |
| `tournamentIds` | `IdFilter` | Multiple tournaments |
| `tournament` | `SeriesTournamentFilter` | Nested tournament filter |
| `teamId` | `ID` | Filter by specific team |
| `teamIds` | `IdFilter` | Multiple teams |
| `live` | `SeriesLiveFilter` | Filter by live game/map state |
| `livePlayerIds` | `IdFilter` | Filter by active player |
| `private` | `BooleanFilter` | Public/private series |
| `players` | `SeriesPlayerFilter` | Filter by players |
| `updatedAt` | `DateTimeFilter` | Last updated time |
| `productServiceLevels` | `ProductServiceLevelFilter` | Service tier |

Order by: `StartTimeScheduled`, `UpdatedAt`, or `ID`.

### Other Useful GRID Queries

**List all tournaments (find which ones overlap with Polymarket):**
```graphql
query CS2Tournaments {
    tournaments(
        filter: { titleId: "28" }
        first: 50
    ) {
        edges {
            node { id name nameShortened startDate endDate }
        }
    }
}
```

**Look up a team by name:**
```graphql
query FindTeam {
    teams(
        filter: { name: { contains: "NaVi" } }
        first: 10
    ) {
        edges {
            node { id name nameShortened }
        }
    }
}
```

### GRID Tournament Coverage

GRID has exclusive data deals for:
- **ESL/FACEIT**: IEM Cologne, Katowice, ESL Pro League, ESL One
- **BLAST**: BLAST Premier, Bounty, Open, Rivals
- **PGL**: PGL Majors, PGL events
- **WePlay, IMG, Perfect World** and 70+ other TOs
- **Esports World Cup**: Official partner

**Known gap**: DraculaN tournament is NOT on GRID but IS on Polymarket.

---

## PART 2: Polymarket — Finding All CS2 Markets

### API Info
| Item | Value |
|---|---|
| Gamma API | `https://gamma-api.polymarket.com` |
| CLOB API | `https://clob.polymarket.com` |
| Auth | None needed (public) |
| Rate limit | Undocumented, generous |

### THE ONLY RELIABLE METHOD: Paginate All Open Markets

Polymarket's search, tags, and category params are **broken/useless for esports**.
The ONLY way to reliably find all CS2 markets:

```
GET /markets?closed=false&limit=200&order=createdAt&ascending=false&offset=0
GET /markets?closed=false&limit=200&order=createdAt&ascending=false&offset=200
GET /markets?closed=false&limit=200&order=createdAt&ascending=false&offset=400
...keep going until no more results
```

Then **client-side filter** by slug prefix:
- `cs2-` = Counter-Strike 2
- `lol-` = League of Legends
- `dota2-` = Dota 2
- `val-` = Valorant

### Polymarket Slug Format (this is gold)

Slugs follow a predictable pattern:
```
cs2-{team1abbr}-{team2abbr}-{YYYY-MM-DD}
```

Examples:
- `cs2-bst-3dmax-2026-04-02` (moneyline / match winner)
- `cs2-bst-3dmax-2026-04-02-game1` (map 1 winner)
- `cs2-bst-3dmax-2026-04-02-game2` (map 2 winner)
- `cs2-bst-3dmax-2026-04-02-game3` (map 3 winner)
- `cs2-bst-3dmax-2026-04-02-map-handicap-home-1pt5` (handicap)
- `cs2-bst-3dmax-2026-04-02-total-games-2pt5` (over/under)

This means: **if you know the date and team abbreviations, you can construct the slug directly** without searching.

### Key Fields from Polymarket Market Object

| Field | Use |
|---|---|
| `slug` | Identifies game type, teams, date |
| `question` | Full match description with tournament name |
| `outcomes` | `["Team A", "Team B"]` — team names |
| `outcomePrices` | `["0.795", "0.205"]` — implied probability |
| `gameStartTime` | `"2026-04-02 11:00:00+00"` — match start |
| `clobTokenIds` | Token IDs for orderbook queries |
| `conditionId` | CLOB market ID |
| `volume` | Total volume traded |
| `liquidity` | Current liquidity |
| `acceptingOrders` | Can you trade? |
| `closed` | Market resolved? |
| `endDate` | When market closes |

### Backup: Search Queries (unreliable but sometimes works)

```
GET /markets?search=Counter-Strike&closed=false&limit=100
```
This is **extremely unreliable** — often returns unrelated results. Only use as supplement, never as primary discovery.

### What's NOT Filterable on Polymarket API

These params are **silently ignored** (API returns default results):
- `tag=`, `tag_slug=`, `category=`, `category_slug=`
- `sports_market_type=`, `fee_type=`
- `slug_contains=` (would be so useful but doesn't exist)

### Sub-Markets per Match

Each CS2 match on Polymarket typically has:
1. **Moneyline** (BO3 match winner) — the one we care about most
2. **Map 1/2/3 winner** (game1, game2, game3)
3. **Map handicap** (home/away -1.5)
4. **Total maps** (over/under 2.5)
5. **Exotic props** (odd/even kills, pentakills, etc.) — less common

---

## PART 3: The Overlap Matching Process

### Step-by-Step: Finding ALL Overlaps

```
STEP 1 — Pull GRID CS2 series for today + tomorrow
         filter: titleId "28", type ESPORTS, date range now → +24h
         Extract: series_id, team names, scheduled time, tournament

STEP 2 — Pull ALL open Polymarket markets
         Paginate: /markets?closed=false&limit=200&offset=N
         Client-side filter: slug starts with "cs2-"
         Extract: slug, outcomes (team names), gameStartTime, prices

STEP 3 — Match by team names
         Normalize both sides (lowercase, strip "Team", abbreviations)
         Primary: exact match on normalized team name pair
         Fallback: fuzzy match (at least 1 team name overlaps)

STEP 4 — For each overlap, you now have:
         - GRID series_id (for live round data)
         - PM slug + token IDs (for orderbook/pricing)
         - Scheduled time (for when to start monitoring)
         → Feed to: python live_edge.py --series <id> --pm-slug <slug>
```

### Team Name Normalization Map

This is critical. GRID uses full names, Polymarket uses abbreviations in slugs but full names in outcomes:

| GRID Name | PM Outcome Name | PM Slug Abbreviation |
|---|---|---|
| Natus Vincere | NaVi | navi |
| Team Liquid | Liquid / Team Liquid | liquid / tl |
| Team Vitality | Vitality | vitality |
| G2 Esports | G2 | g2 |
| FaZe Clan | FaZe | faze |
| Cloud9 | Cloud9 | c9 |
| MOUZ | MOUZ | mouz |
| BESTIA | BESTIA | bst |
| 3DMAX | 3DMAX | 3dmax |
| Fire Flux | Fire Flux | fire-flux |

**Add to this map as you discover new mismatches.**

### Date/Time Matching

Both platforms provide start times:
- GRID: `startTimeScheduled` in ISO 8601 (e.g. `"2026-04-02T11:00:00Z"`)
- PM: `gameStartTime` (e.g. `"2026-04-02 11:00:00+00"`)

Use this as secondary confirmation when team name matching is ambiguous.

---

## PART 4: Continuous Scanning Strategy

### Polling Schedule

| Task | Frequency | API | Notes |
|---|---|---|---|
| Discover new markets | Every **15 min** | PM Gamma | Paginate all open, filter cs2- |
| Discover new series | Every **30 min** | GRID Central | 24h window, titleId 28 |
| Check if match is live | Every **2 min** | GRID Live | Only for matched overlaps within -4h to +1h |
| Grab pre-match price | **2 min before start** | PM Gamma | This becomes the prior for the model |
| Live edge detection | Every **round end** (~1-2 min) | Both | This is live_edge.py |

### Rate Limit Budget

GRID Central: 20 req/min total
- Discovery scan (every 30 min): ~2 req (paginated)
- Live status checks: each costs 1 req on Live API (180/min budget)
- **Don't run scrape_history.py at the same time as live scanning**

PM Gamma: generous but don't abuse
- Full market pagination: ~5-10 requests per scan (1000-2000 open markets)
- Individual market lookups: unlimited practically

### What Tournaments Overlap?

Based on research, these tournaments appear on BOTH platforms:
- **CCT** (Champion of Champions Tour)
- **Parken Challenger Championship**
- **ESL Challenger**
- **PGL events** (including Astana qualifiers)
- **BLAST events** (Premier, Bounty, etc.)

**NOT on GRID but on Polymarket:**
- DraculaN tournament

**NOT on Polymarket but on GRID:**
- Many tier-3 events, online qualifiers, etc.

---

## PART 5: Quick Reference Commands

```bash
# Find overlaps for today
python find_overlaps.py

# Find overlaps for a specific date
python find_overlaps.py --date 2026-04-03

# Run live edge detection for a matched pair
python live_edge.py --series <GRID_SERIES_ID> --pm-slug <PM_SLUG>

# Check dashboard
python dashboard.py
```

### Raw API Test Commands

```bash
# GRID: Get today's CS2 series
curl -s -X POST "https://api-op.grid.gg/central-data/graphql" \
  -H "Content-Type: application/json" \
  -H "x-api-key: YOUR_KEY" \
  -d '{"query":"{ allSeries(filter: { startTimeScheduled: { gte: \"2026-04-02T00:00:00Z\", lte: \"2026-04-02T23:59:59Z\" }, titleId: \"28\", type: ESPORTS }, orderBy: StartTimeScheduled, orderDirection: ASC, first: 50) { totalCount edges { node { id startTimeScheduled teams { baseInfo { name } } tournament { name } } } } }"}'

# Polymarket: Get newest open markets (page 1)
curl -s "https://gamma-api.polymarket.com/markets?closed=false&limit=200&order=createdAt&ascending=false&offset=0"

# Polymarket: Get specific CS2 market by slug
curl -s "https://gamma-api.polymarket.com/markets?slug=cs2-bst-3dmax-2026-04-02"
```

---

## Known Issues / Gotchas

1. **titleId was wrong** — We were NOT filtering by CS2. Fix: use `"28"` not `"7"`
2. **PM search is broken** — `search=Counter-Strike` returns GTA VI results. Don't rely on it.
3. **PM tags are broken** — `tag=esports` is silently ignored. Don't use it.
4. **GRID rate limits are aggressive** — Never run scrape + live simultaneously
5. **Team name mismatches** — The #1 source of missed overlaps. Keep expanding the normalization map.
6. **PM slug abbreviations** — No official mapping. You learn these by observation (bst = BESTIA, etc.)
7. **Matches can start late** — Use -4h/+1h window, not exact scheduled time
8. **DraculaN gap** — Biggest PM esports markets but GRID doesn't cover this tournament
9. **PM `gameStartTime` can be null** — Some markets don't have it set, fall back to `endDate`
10. **GRID Open Access** — No access to LoL/Valorant data. CS2 and Dota 2 only.
