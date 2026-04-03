
"""
Configuration for CS2 betting edge system.
API keys, endpoints, constants.
"""

# ── GRID.gg ──────────────────────────────────────────────────────────────────
GRID_API_KEY = "kQv2gBFtB5hPqSiTxgYuR2oKKvEZzqB4ZFZEAf4N"
GRID_CENTRAL_URL = "https://api-op.grid.gg/central-data/graphql"
GRID_LIVE_URL = "https://api-op.grid.gg/live-data-feed/series-state/graphql"

# ── Polymarket ───────────────────────────────────────────────────────────────
POLYMARKET_GAMMA_URL = "https://gamma-api.polymarket.com"
POLYMARKET_CLOB_URL = "https://clob.polymarket.com"

# ── Pinnacle (for pre-match priors) ──────────────────────────────────────────
PINNACLE_ODDS_URL = "https://www.pinnacle.com/en/esports/counter-strike-2/matchups"

# ── CS2 Economy Constants (2026 meta) ────────────────────────────────────────
STARTING_MONEY = 800
MAX_MONEY = 16000
ROUNDS_PER_HALF = 12  # MR12 format
ROUNDS_TO_WIN = 13

# Overtime (MR3): starts at round 25, each half = 3 rounds, $10k start
OT_START_ROUND = 25        # Round where OT begins
OT_HALF_ROUNDS = 3         # Rounds per OT half
OT_START_MONEY = 10000     # Per player at OT start
OT_ROUNDS_TO_WIN = 4       # First to 4 rounds wins OT half (16 maps total)

# Round win/loss rewards
ROUND_WIN_BONUS = 3250
BOMB_EXPLODE_WIN_BONUS = 3500
DEFUSE_WIN_BONUS = 3500
BOMB_PLANT_TEAM_BONUS = 800
BOMB_PLANT_PLANTER_BONUS = 300

# Loss bonus tiers (index = consecutive losses - 1)
LOSS_BONUS_TIERS = [1400, 1900, 2400, 2900, 3400]
PISTOL_LOSS_FIRST_BONUS = 1900  # First loss after pistol = $1900 not $1400

# Kill rewards by weapon category
KILL_REWARDS = {
    # Pistols: $300
    "glock": 300, "usp_silencer": 300, "p2000": 300, "p250": 300,
    "fiveseven": 300, "tec9": 300, "deagle": 300, "revolver": 300,
    "cz75auto": 300, "elite": 300, "hkp2000": 300,
    # Rifles: $300
    "ak47": 300, "m4a1": 300, "m4a1_silencer": 300, "sg556": 300,
    "aug": 300, "famas": 300, "galilar": 300, "scar20": 300, "g3sg1": 300,
    # SMGs: $600 (except P90)
    "mac10": 600, "mp9": 600, "mp7": 600, "mp5sd": 600, "ump45": 600,
    "bizon": 600,
    "p90": 300,  # P90 is only $300
    # Shotguns
    "xm1014": 600, "mag7": 900, "nova": 900, "sawedoff": 900,
    # LMGs: $300
    "negev": 300, "m249": 300,
    # Special
    "awp": 100, "ssg08": 300, "taser": 0, "knife": 1500,
    "knife_t": 1500, "bayonet": 1500,
    # Grenades: $300
    "hegrenade": 300, "inferno": 300, "molotov": 300,
    "incgrenade": 300, "flashbang": 300, "smokegrenade": 300, "decoy": 300,
    # Fall damage / world
    "world": 0, "planted_c4": 0,
}

# Approximate weapon costs (for buy type inference)
WEAPON_COSTS = {
    # Pistols
    "glock": 0, "usp_silencer": 0, "p2000": 0,  # default pistols = free
    "p250": 300, "fiveseven": 500, "tec9": 500, "cz75auto": 500,
    "deagle": 700, "revolver": 600, "elite": 400,
    # SMGs
    "mac10": 1050, "mp9": 1250, "mp7": 1500, "mp5sd": 1500,
    "ump45": 1200, "bizon": 1400, "p90": 2350,
    # Rifles
    "famas": 2050, "galilar": 1800, "ak47": 2700, "m4a1": 3100,
    "m4a1_silencer": 3100, "aug": 3300, "sg556": 3000,
    "ssg08": 1700, "awp": 4750, "scar20": 5000, "g3sg1": 5000,
    # Shotguns
    "nova": 1050, "xm1014": 2000, "mag7": 1300, "sawedoff": 1100,
    # LMGs
    "negev": 1700, "m249": 5200,
    # Gear
    "kevlar": 650, "kevlar_helmet": 1000, "defuser": 400,
}

# CT/T side win rates by map (pro level, 2025-2026 meta)
MAP_CT_WIN_RATE = {
    "nuke":     0.57,
    "overpass": 0.55,
    "mirage":   0.53,
    "inferno":  0.51,
    "ancient":  0.50,
    "dust2":    0.50,
    "vertigo":  0.48,
    "anubis":   0.43,
}

# ── Model / Betting Config ───────────────────────────────────────────────────
MIN_EDGE_THRESHOLD = 0.08       # 8% minimum edge to consider a bet
MIN_PROB_THRESHOLD = 0.20       # Don't bet if model probability < 20%
MAX_PROB_THRESHOLD = 0.95       # Don't bet if model probability > 95% (likely stale/transition)
KELLY_FRACTION = 0.25           # Quarter-Kelly for safety
DEFAULT_BANKROLL = 1000         # Starting bankroll in dollars
POLYMARKET_TAKER_FEE = 0.03     # 3% taker fee
POLYMARKET_ORDER_DELAY_SEC = 3  # Built-in order delay

# ── Data paths ───────────────────────────────────────────────────────────────
import os
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
MODEL_DIR = os.path.join(os.path.dirname(__file__), "models")
