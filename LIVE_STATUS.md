# CS2 Live Paper Trader — 🟡 WATCHING

**Last update:** 2026-04-02 17:57 UTC  
**Watcher:** Running (screen session `cs2watcher`)  
**Active trades:** 0 — waiting for matches to start

## Upcoming Games (auto-launch when in window)

| Match | Start (UTC) | Liquidity | Launches at |
|-------|-------------|-----------|-------------|
| RED Canids Academy vs LyP | 19:00 | $41 | ~18:00 |
| Vivo Keyd vs Bestia Academy | 21:00 | $2,921 | ~20:00 |
| ShindeN vs Charrados | 21:00 | $569 | ~20:00 |
| BLITZKRIEG vs UNO MILLE | 21:00 | $86 | ~20:00 |
| paiN Academy vs FOLHA AMARELA | 21:00 | $447 | ~20:00 |
| Game Hunters vs R2 | 21:00 | $3,736 | ~20:00 |
| MAGICOS vs LP | 21:00 | $30,217 | ~20:00 |
| desempleHADAS vs Crashers | 21:00 | $288 | ~20:00 |
| + NA games at 01:00 UTC | 01:00 | various | ~00:00 |

## How It Works

1. `watcher.py` scans GRID + Polymarket every 15 min
2. When a match is <60 min from start → launches `live.py`
3. `live.py` grabs starting odds from CLOB price history
4. Model runs every round, compares to PM orderbook
5. Paper trades when edge ≥ 8% after fees
6. Excel audit log + this file push to GitHub every 5 min

## Recent Changes
- Fixed look-ahead bias in model features
- Model retrained on 244k samples (AUC 0.798)
- Starting odds from CLOB price history (not stale JSON)
- Kelly sizing uses real available capital
- Team name resolution by index (not string matching)

---
*Auto-updates every 5 min once a game is live*
