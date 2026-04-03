"""
Feature extractor: turns GRID round-by-round data + reconstructed economy
into feature vectors for the win probability model.
"""

from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from economy_engine import EconomyReconstructor, RoundResult, RoundEconomy, classify_buy_type
import config

# Source labels for debug/logging
MONEY_SOURCE_GRID = "grid"
MONEY_SOURCE_ECON = "econ"


@dataclass
class RoundFeatures:
    """Feature vector for one round state → map win prediction."""
    # ── Identifiers (not model features) ─────────────────────────────────
    series_id: str
    game_seq: int          # Map number in series (1, 2, 3)
    round_num: int
    map_name: str
    team_a: str
    team_b: str

    # ── Core features ────────────────────────────────────────────────────
    score_a: int           # Team A rounds won so far
    score_b: int           # Team B rounds won so far
    score_diff: int        # score_a - score_b
    rounds_played: int     # Total rounds completed
    rounds_remaining: int  # Max rounds left (25 - rounds_played in MR12)

    # Side info
    team_a_side: str       # "ct" or "t"
    is_second_half: bool   # After halftime?
    rounds_until_switch: int  # Rounds until side switch (or 0 if 2nd half)

    # ── Economy features ─────────────────────────────────────────────────
    money_a: int           # Team A estimated bank
    money_b: int           # Team B estimated bank
    money_diff: int        # money_a - money_b
    buy_type_a: str        # "full", "force", "eco", "pistol"
    buy_type_b: str
    loss_tier_a: int       # 0-4
    loss_tier_b: int
    consec_losses_a: int
    consec_losses_b: int

    # ── Momentum features ────────────────────────────────────────────────
    last_3_wins_a: int     # How many of last 3 rounds team A won
    last_5_wins_a: int     # How many of last 5 rounds team A won
    current_streak_a: int  # Positive = A winning streak, negative = B streak

    # ── Skill signals ────────────────────────────────────────────────────
    total_kills_a: int     # Total kills so far this map
    total_kills_b: int
    total_deaths_a: int
    total_deaths_b: int
    first_kills_a: int     # First kills won this map
    first_kills_b: int

    # ── Map-specific priors ──────────────────────────────────────────────
    map_ct_rate: float     # Historical CT win rate for this map (from config)

    # ── Pistol round results ─────────────────────────────────────────────
    pistol_1_winner: str   # "a", "b", or "none" (if not yet played)
    pistol_2_winner: str   # "a", "b", or "none"

    # ── Target (for training) ────────────────────────────────────────────
    team_a_won_map: Optional[bool] = None  # Did team A win this map?


def extract_features_from_grid_state(
    series_state: dict,
    game_idx: int = 0,
    up_to_round: int = None,
) -> List[RoundFeatures]:
    """
    Extract feature vectors from a GRID seriesState response.

    IMPORTANT: Features for "round N" represent the state ENTERING round N,
    i.e. everything known AFTER round N-1 resolved but BEFORE round N plays.
    This avoids look-ahead bias — the model never sees the current round's
    outcome in its features.

    Args:
        series_state: Full seriesState dict from GRID live feed
        game_idx: Which game (map) to extract from (0-indexed)
        up_to_round: If set, only extract up to this round number.
                     If None, extract for every round (training mode).

    Returns:
        List of RoundFeatures, one per round.
    """
    games = series_state.get("games", [])
    if game_idx >= len(games):
        return []

    game = games[game_idx]
    segments = game.get("segments", [])
    map_name = (game.get("map") or {}).get("name", "unknown")
    game_teams = game.get("teams", [])

    if len(game_teams) < 2:
        return []

    team_a = game_teams[0]["name"]
    team_b = game_teams[1]["name"]

    # Did team A win this map? (for training labels)
    team_a_won = game_teams[0].get("won")

    # Parse all rounds into RoundResults
    round_results = _parse_rounds(segments, team_a, team_b, game_teams)

    # Build economy reconstruction as fallback for when GRID doesn't
    # provide per-round money (historical data never has it; live may).
    econ = EconomyReconstructor(team_a, team_b)
    for rr in round_results:
        econ.process_round(rr)

    # Build feature vectors
    # We emit a feature row for the state ENTERING each round.
    # Round 1: no prior data, emit baseline features.
    # Round N (N>1): features reflect cumulative state after rounds 1..N-1.
    features = []
    max_round = up_to_round or len(round_results)

    # Cumulative trackers — updated AFTER each round resolves
    kills_a, kills_b = 0, 0
    deaths_a, deaths_b = 0, 0
    fk_a, fk_b = 0, 0
    score_a, score_b = 0, 0
    round_winners = []  # List of "a" or "b"
    pistol_1 = "none"
    pistol_2 = "none"
    last_side_a = None  # Track side from previous round

    # Map CT rate
    ct_rate = config.MAP_CT_WIN_RATE.get(map_name, 0.50)

    for i, rr in enumerate(round_results):
        if i >= max_round:
            break

        round_num = rr.round_num

        # ── SNAPSHOT STATE ENTERING THIS ROUND (before it resolves) ──
        # score_a, score_b, kills_a, etc. reflect rounds 1..(round_num-1)

        # Side info for THIS round (from round result metadata)
        winner_is_a = rr.winner == team_a
        side_a = rr.winner_side if winner_is_a else rr.loser_side
        is_ct_a = side_a == "counter-terrorists"
        is_second_half = round_num > config.ROUNDS_PER_HALF

        # OT side switches every OT_HALF_ROUNDS rounds
        ot_start = config.OT_START_ROUND
        ot_half = config.OT_HALF_ROUNDS
        in_ot = round_num >= ot_start
        if in_ot:
            ot_offset = (round_num - ot_start) % (ot_half * 2)
            rounds_until_switch = max(0, ot_half - ot_offset)
        elif not is_second_half:
            rounds_until_switch = config.ROUNDS_PER_HALF - round_num + 1
        else:
            rounds_until_switch = 0

        # Momentum (from completed rounds only)
        recent = round_winners[-5:]
        last_5_a = sum(1 for w in recent if w == "a")
        last_3_a = sum(1 for w in round_winners[-3:] if w == "a")

        streak = 0
        for w in reversed(round_winners):
            if w == "a":
                if streak >= 0:
                    streak += 1
                else:
                    break
            else:
                if streak <= 0:
                    streak -= 1
                else:
                    break

        rounds_played = score_a + score_b
        # Regulation: max 25 rounds. OT: effectively unbounded but cap at 6 per period.
        # Use rounds until one team reaches 13 (or 16 in OT) as signal.
        max_total = 2 * config.ROUNDS_TO_WIN - 1  # 25 in regulation
        if round_num >= config.OT_START_ROUND:
            # In OT - count rounds until current OT period ends
            ot_offset = (round_num - config.OT_START_ROUND) % (config.OT_HALF_ROUNDS * 2)
            rounds_remaining = max(0, config.OT_HALF_ROUNDS * 2 - ot_offset)
        else:
            rounds_remaining = max(0, max_total - rounds_played)

        # ── ECONOMY: prefer GRID actual money, fall back to reconstruction ──
        # GRID live API may return per-segment money; historical data never has it.
        # Economy reconstruction is accurate and used as fallback for training.
        grid_money_a = rr.team_money.get(team_a)
        grid_money_b = rr.team_money.get(team_b)

        if grid_money_a is not None and grid_money_b is not None:
            # Live mode: use real GRID money
            money_a = grid_money_a
            money_b = grid_money_b
        else:
            # Training mode / historical: use economy reconstruction
            econ_state = econ.get_economy_at_round(round_num)
            econ_a = econ_state.get(team_a)
            econ_b = econ_state.get(team_b)
            if econ_a and econ_b:
                money_a = econ_a.money_estimate
                money_b = econ_b.money_estimate
            elif round_num == 1 or round_num == config.ROUNDS_PER_HALF + 1:
                money_a = config.STARTING_MONEY * 5
                money_b = config.STARTING_MONEY * 5
            elif features:
                money_a = features[-1].money_a
                money_b = features[-1].money_b
            else:
                money_a = config.STARTING_MONEY * 5
                money_b = config.STARTING_MONEY * 5

        # Classify buy type from actual money
        is_pistol = round_num in (1, config.ROUNDS_PER_HALF + 1)
        if is_pistol:
            buy_a = "pistol"
            buy_b = "pistol"
        else:
            side_a_str = "counter-terrorists" if is_ct_a else "terrorists"
            side_b_str = "terrorists" if is_ct_a else "counter-terrorists"
            buy_a = classify_buy_type(money_a // 5, side_a_str)
            buy_b = classify_buy_type(money_b // 5, side_b_str)

        # Loss tier / consecutive losses — track from round winners
        consec_losses_a = 0
        for w in reversed(round_winners):
            if w == "b":
                consec_losses_a += 1
            else:
                break
        consec_losses_b = 0
        for w in reversed(round_winners):
            if w == "a":
                consec_losses_b += 1
            else:
                break

        loss_tier_a = min(consec_losses_a, len(config.LOSS_BONUS_TIERS) - 1)
        loss_tier_b = min(consec_losses_b, len(config.LOSS_BONUS_TIERS) - 1)

        # Reset loss tracking at half and OT resets
        ot_start = config.OT_START_ROUND
        ot_half_len = config.OT_HALF_ROUNDS * 2
        is_ot_reset = (round_num >= ot_start and
                       (round_num - ot_start) % ot_half_len == 0)
        if round_num == config.ROUNDS_PER_HALF + 1 or is_ot_reset:
            consec_losses_a = 0
            consec_losses_b = 0
            loss_tier_a = 0
            loss_tier_b = 0

        features.append(RoundFeatures(
            series_id=series_state.get("id", ""),
            game_seq=game_idx + 1,
            round_num=round_num,
            map_name=map_name,
            team_a=team_a,
            team_b=team_b,
            score_a=score_a,
            score_b=score_b,
            score_diff=score_a - score_b,
            rounds_played=rounds_played,
            rounds_remaining=rounds_remaining,
            team_a_side="ct" if is_ct_a else "t",
            is_second_half=is_second_half,
            rounds_until_switch=max(0, rounds_until_switch),
            money_a=money_a,
            money_b=money_b,
            money_diff=money_a - money_b,
            buy_type_a=buy_a,
            buy_type_b=buy_b,
            loss_tier_a=loss_tier_a,
            loss_tier_b=loss_tier_b,
            consec_losses_a=consec_losses_a,
            consec_losses_b=consec_losses_b,
            last_3_wins_a=last_3_a,
            last_5_wins_a=last_5_a,
            current_streak_a=streak,
            total_kills_a=kills_a,
            total_kills_b=kills_b,
            total_deaths_a=deaths_a,
            total_deaths_b=deaths_b,
            first_kills_a=fk_a,
            first_kills_b=fk_b,
            map_ct_rate=ct_rate,
            pistol_1_winner=pistol_1,
            pistol_2_winner=pistol_2,
            team_a_won_map=team_a_won,
        ))

        # ── NOW update cumulative state with THIS round's result ─────
        round_winners.append("a" if winner_is_a else "b")

        tk_a = rr.team_kills.get(team_a, 0)
        tk_b = rr.team_kills.get(team_b, 0)
        kills_a += tk_a
        kills_b += tk_b
        deaths_a += tk_b  # A's deaths ≈ B's kills
        deaths_b += tk_a

        # First kills
        if rr.first_kill_team == team_a:
            fk_a += 1
        elif rr.first_kill_team == team_b:
            fk_b += 1

        if winner_is_a:
            score_a += 1
        else:
            score_b += 1

        if round_num == 1:
            pistol_1 = "a" if winner_is_a else "b"
        elif round_num == config.ROUNDS_PER_HALF + 1:
            pistol_2 = "a" if winner_is_a else "b"

    return features


def _parse_rounds(
    segments: List[dict], team_a: str, team_b: str,
    game_teams: List[dict] = None,
) -> List[RoundResult]:
    """Parse GRID segments into RoundResult objects.

    Also extracts GRID's actual money/loadout values and first-kill
    attribution per round.
    """
    results = []
    for seg in segments:
        seq = seg.get("sequenceNumber")
        teams = seg.get("teams", [])
        if len(teams) < 2 or not seg.get("finished"):
            continue

        # Find winner and loser
        t0, t1 = teams[0], teams[1]
        if t0.get("won"):
            winner_data, loser_data = t0, t1
        elif t1.get("won"):
            winner_data, loser_data = t1, t0
        else:
            continue  # Draw or unresolved

        # Parse weapon kills per team
        weapon_kills = {}
        team_kills = {}
        for t in teams:
            tname = t.get("name", "")
            wk = {}
            for wkill in (t.get("weaponKills") or []):
                wname = wkill.get("weaponName", "unknown")
                wcount = wkill.get("count", 0)
                wk[wname] = wcount
            weapon_kills[tname] = wk
            team_kills[tname] = t.get("kills", 0)

        # Check objectives for bomb plant/defuse
        bomb_planted = False
        bomb_defused = False
        for t in teams:
            for obj in (t.get("objectives") or []):
                if obj.get("type") == "plantBomb":
                    bomb_planted = True
                if obj.get("type") == "defuseBomb":
                    bomb_defused = True

        # ── GRID actual money: sum player money per team ──────────────
        team_money = {}
        team_loadout = {}
        for t in teams:
            tname = t.get("name", "")
            # Team-level money (if GRID provides it)
            tmoney = t.get("money")
            tloadout = t.get("loadoutValue")
            if tmoney is not None:
                team_money[tname] = int(tmoney)
            else:
                # Sum from players
                players = t.get("players") or []
                psum = sum(int(p.get("money", 0) or 0) for p in players)
                if psum > 0:
                    team_money[tname] = psum
            if tloadout is not None:
                team_loadout[tname] = int(tloadout)
            else:
                players = t.get("players") or []
                lsum = sum(int(p.get("loadoutValue", 0) or 0) for p in players)
                if lsum > 0:
                    team_loadout[tname] = lsum

        # ── First kill attribution ────────────────────────────────────
        first_kill_team = ""
        for t in teams:
            # GRID segment team has firstKill boolean
            if t.get("firstKill"):
                first_kill_team = t.get("name", "")
                break
            # Also check player-level
            for p in (t.get("players") or []):
                if p.get("firstKill"):
                    first_kill_team = t.get("name", "")
                    break
            if first_kill_team:
                break

        results.append(RoundResult(
            round_num=seq,
            winner=winner_data.get("name", ""),
            loser=loser_data.get("name", ""),
            winner_side=winner_data.get("side", ""),
            loser_side=loser_data.get("side", ""),
            bomb_planted=bomb_planted,
            bomb_defused=bomb_defused,
            weapon_kills=weapon_kills,
            team_kills=team_kills,
            team_money=team_money,
            team_loadout=team_loadout,
            first_kill_team=first_kill_team,
        ))

    return results


def features_to_dict(f: RoundFeatures) -> dict:
    """Convert RoundFeatures to a flat dict for model input."""
    return {
        "score_diff": f.score_diff,
        "score_a": f.score_a,
        "score_b": f.score_b,
        "rounds_played": f.rounds_played,
        "rounds_remaining": f.rounds_remaining,
        "team_a_is_ct": 1 if f.team_a_side == "ct" else 0,
        "is_second_half": 1 if f.is_second_half else 0,
        "rounds_until_switch": f.rounds_until_switch,
        "money_diff": f.money_diff,
        "money_a": f.money_a,
        "money_b": f.money_b,
        "buy_full_a": 1 if f.buy_type_a == "full" else 0,
        "buy_force_a": 1 if f.buy_type_a == "force" else 0,
        "buy_eco_a": 1 if f.buy_type_a == "eco" else 0,
        "buy_full_b": 1 if f.buy_type_b == "full" else 0,
        "buy_force_b": 1 if f.buy_type_b == "force" else 0,
        "buy_eco_b": 1 if f.buy_type_b == "eco" else 0,
        "loss_tier_a": f.loss_tier_a,
        "loss_tier_b": f.loss_tier_b,
        "consec_losses_a": f.consec_losses_a,
        "consec_losses_b": f.consec_losses_b,
        "last_3_wins_a": f.last_3_wins_a,
        "last_5_wins_a": f.last_5_wins_a,
        "streak_a": f.current_streak_a,
        "kill_diff": f.total_kills_a - f.total_kills_b,
        "first_kill_diff": f.first_kills_a - f.first_kills_b,
        "map_ct_rate": f.map_ct_rate,
        "pistol_1_a": 1 if f.pistol_1_winner == "a" else 0,
        "pistol_1_b": 1 if f.pistol_1_winner == "b" else 0,
        "pistol_2_a": 1 if f.pistol_2_winner == "a" else 0,
        "pistol_2_b": 1 if f.pistol_2_winner == "b" else 0,
        # Map one-hot
        "map_dust2": 1 if f.map_name == "dust2" else 0,
        "map_mirage": 1 if f.map_name == "mirage" else 0,
        "map_inferno": 1 if f.map_name == "inferno" else 0,
        "map_nuke": 1 if f.map_name == "nuke" else 0,
        "map_ancient": 1 if f.map_name == "ancient" else 0,
        "map_anubis": 1 if f.map_name == "anubis" else 0,
        "map_overpass": 1 if f.map_name == "overpass" else 0,
        "map_vertigo": 1 if f.map_name == "vertigo" else 0,
    }
