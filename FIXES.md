# CS2-Edge: Remaining Fixes

_All critical leakage + model fixes done 2026-04-02. See git log for details._

## Remaining

| # | Issue | Impact |
|---|-------|--------|
| 1 | `live_edge.py` is dead code | Cleanup |
| 2 | `dashboard.py`, `live_logger*.py` — superseded by live.py | Cleanup |
| 3 | `scrape_pinnacle.py` — probably broken, unused | Cleanup |
| 4 | Correlated position exposure not capped (e.g. long A on WINNER + MAP1) | Low risk w/ quarter Kelly |
| 5 | OT economy: only handles MR3 format, not double OT edge cases | Very rare |

## Done (2026-04-02)
Look-ahead bias, round-start PM prices, first kills, GRID money→econ reconstruction,
rounds_remaining, OT economy, min edge threshold, Kelly real capital, team name by index,
PM market caching, selective orderbook, CLOB price history priors, Excel audit trail,
find_overlaps pagination fix, watcher.py, live PM price snapshot.
