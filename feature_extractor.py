"""
Feature extractor: turns GRID round-by-round data + reconstructed economy
into feature vectors for the win probability model.
"""

from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from economy_engine import EconomyReconstructor, RoundResult, RoundEconomy
import config


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
    round_results = _parse_rounds(segments, team_a, team_b)

    # Reconstruct economy
    econ = EconomyReconstructor(team_a, team_b)
    for rr in round_results:
        econ.process_round(rr)

    # Build feature vectors
    features = []
    max_round = up_to_round or len(round_results)

    # Track cumulative stats
    kills_a, kills_b = 0, 0
    deaths_a, deaths_b = 0, 0
    fk_a, fk_b = 0, 0
    score_a, score_b = 0, 0
    round_winners = []  # List of "a" or "b"
    pistol_1 = "none"
    pistol_2 = "none"

    for i, rr in enumerate(round_results):
        if i >= max_round:
            break

        round_num = rr.round_num

        # Update cumulative stats
        winner_is_a = rr.winner == team_a
        round_winners.append("a" if winner_is_a else "b")

        tk_a = rr.team_kills.get(team_a, 0)
        tk_b = rr.team_kills.get(team_b, 0)
        kills_a += tk_a
        kills_b += tk_b
        deaths_a += tk_b  # A's deaths ≈ B's kills
        deaths_b += tk_a

        # First kills (approximate from GRID firstKill boolean)
        # We count rounds where each team got first kill
        # This is tracked in the round result parsing

        if winner_is_a:
            score_a += 1
        else:
            score_b += 1

        if round_num == 1:
            pistol_1 = "a" if winner_is_a else "b"
        elif round_num == config.ROUNDS_PER_HALF + 1:
            pistol_2 = "a" if winner_is_a else "b"

        # Get economy state for NEXT round (what we'd predict from)
        next_round = round_num + 1
        econ_state = econ.get_economy_at_round(round_num)
        econ_a = econ_state.get(team_a)
        econ_b = econ_state.get(team_b)

        if not econ_a or not econ_b:
            continue

        # Side info
        side_a = rr.winner_side if winner_is_a else rr.loser_side
        is_ct_a = side_a == "counter-terrorists"
        is_second_half = round_num > config.ROUNDS_PER_HALF

        rounds_until_switch = (config.ROUNDS_PER_HALF - round_num
                               if not is_second_half else 0)

        # Momentum
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

        # Map CT rate
        ct_rate = config.MAP_CT_WIN_RATE.get(map_name, 0.50)

        rounds_played = score_a + score_b
        max_rounds = config.ROUNDS_PER_HALF * 2 + 1  # 25 in MR12

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
            rounds_remaining=max(0, config.ROUNDS_TO_WIN - max(score_a, score_b)),
            team_a_side="ct" if is_ct_a else "t",
            is_second_half=is_second_half,
            rounds_until_switch=max(0, rounds_until_switch),
            money_a=econ_a.money_estimate,
            money_b=econ_b.money_estimate,
            money_diff=econ_a.money_estimate - econ_b.money_estimate,
            buy_type_a=econ_a.buy_type,
            buy_type_b=econ_b.buy_type,
            loss_tier_a=econ_a.loss_bonus_tier,
            loss_tier_b=econ_b.loss_bonus_tier,
            consec_losses_a=econ_a.consecutive_losses,
            consec_losses_b=econ_b.consecutive_losses,
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

    return features


def _parse_rounds(
    segments: List[dict], team_a: str, team_b: str
) -> List[RoundResult]:
    """Parse GRID segments into RoundResult objects."""
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
