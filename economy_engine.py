"""
Deterministic CS2 economy reconstruction engine.

Given a sequence of round results from GRID data, reconstructs
each team's economy state (money, buy type, loss bonus tier)
for every round in a map.

CS2 uses the "step-down" loss bonus system:
- Loss: tier goes UP by 1 (more money next loss)
- Win: tier goes DOWN by 1 (less money if you lose again)
- Halftime: reset to $800, loss tier resets to 0
"""

from dataclasses import dataclass, field
from typing import List, Optional
import config


@dataclass
class RoundEconomy:
    """Economy state for one team at the start of a round."""
    round_num: int
    team_name: str
    money_estimate: int          # Estimated team bank (sum of 5 players)
    loss_bonus_tier: int         # 0-4 index into LOSS_BONUS_TIERS
    buy_type: str                # "full", "force", "eco", "pistol"
    consecutive_losses: int
    won_previous: Optional[bool]


@dataclass
class RoundResult:
    """Parsed round result from GRID data."""
    round_num: int
    winner: str                  # Team name that won
    loser: str
    winner_side: str             # "terrorists" or "counter-terrorists"
    loser_side: str
    bomb_planted: bool = False
    bomb_defused: bool = False
    # Per-team kill data: {team_name: {weapon_name: kill_count}}
    weapon_kills: dict = field(default_factory=dict)
    # Per-team total kills
    team_kills: dict = field(default_factory=dict)
    # GRID actual money per team: {team_name: total_money_across_5_players}
    team_money: dict = field(default_factory=dict)
    # GRID loadout value per team: {team_name: total_loadout_value}
    team_loadout: dict = field(default_factory=dict)
    # Team name that got first kill this round ("" if unknown)
    first_kill_team: str = ""


def classify_buy_type(money_per_player: int, side: str) -> str:
    """Classify buy type based on average money per player."""
    if money_per_player <= 800:
        return "pistol"

    # Full buy thresholds differ by side (CT equipment costs more)
    if side == "counter-terrorists":
        if money_per_player >= 5500:
            return "full"
        elif money_per_player >= 3000:
            return "force"
        else:
            return "eco"
    else:  # terrorists
        if money_per_player >= 4500:
            return "full"
        elif money_per_player >= 2500:
            return "force"
        else:
            return "eco"


def infer_buy_type_from_weapons(weapon_kills: dict) -> str:
    """
    Infer buy type from weapons used in kills.
    If a team only got kills with pistols → likely eco/force.
    If they used rifles/AWP → likely full buy.
    """
    if not weapon_kills:
        return "unknown"

    rifle_weapons = {"ak47", "m4a1", "m4a1_silencer", "aug", "sg556", "awp",
                     "scar20", "g3sg1", "famas", "galilar"}
    smg_weapons = {"mac10", "mp9", "mp7", "mp5sd", "ump45", "bizon", "p90"}
    pistol_weapons = {"glock", "usp_silencer", "p2000", "p250", "fiveseven",
                      "tec9", "deagle", "cz75auto", "elite", "revolver", "hkp2000"}

    has_rifle = any(w in rifle_weapons for w in weapon_kills)
    has_smg = any(w in smg_weapons for w in weapon_kills)
    has_pistol_only = all(w in pistol_weapons for w in weapon_kills)

    if has_rifle:
        return "full"
    elif has_smg:
        return "force"  # SMGs often indicate anti-eco or force buy
    elif has_pistol_only:
        return "eco"
    return "unknown"


class EconomyReconstructor:
    """
    Reconstructs economy for both teams across an entire map
    using deterministic CS2 economy rules.
    """

    def __init__(self, team_a: str, team_b: str):
        self.team_a = team_a
        self.team_b = team_b

        # Per-team state
        self.money = {team_a: 800 * 5, team_b: 800 * 5}  # 5 players
        self.loss_tier = {team_a: 0, team_b: 0}
        self.consec_losses = {team_a: 0, team_b: 0}
        self.prev_won = {team_a: None, team_b: None}
        self.sides = {team_a: None, team_b: None}

        self.round_economies: List[RoundEconomy] = []

    def _clamp_money(self, team: str):
        """Cap money at $16,000 per player ($80,000 team)."""
        self.money[team] = min(self.money[team], config.MAX_MONEY * 5)

    def _loss_bonus(self, team: str) -> int:
        """Get current loss bonus for a team."""
        tier = min(self.loss_tier[team], len(config.LOSS_BONUS_TIERS) - 1)
        return config.LOSS_BONUS_TIERS[tier]

    def _kill_reward_total(self, weapon_kills: dict) -> int:
        """Calculate total kill reward money from weapon kills."""
        total = 0
        for weapon, count in weapon_kills.items():
            reward = config.KILL_REWARDS.get(weapon, 300)  # Default $300
            total += reward * count
        return total

    def process_round(self, result: RoundResult):
        """
        Process one round result and update economy state.
        Call this sequentially for each round in a map.
        """
        round_num = result.round_num

        # Halftime reset (round 13 in MR12)
        if round_num == config.ROUNDS_PER_HALF + 1:
            for team in [self.team_a, self.team_b]:
                self.money[team] = config.STARTING_MONEY * 5
                self.loss_tier[team] = 0
                self.consec_losses[team] = 0
                self.prev_won[team] = None

        # Pistol rounds (1 and 13)
        is_pistol = round_num in (1, config.ROUNDS_PER_HALF + 1)

        # Record economy STATE at the start of this round (before resolution)
        for team in [self.team_a, self.team_b]:
            side = (result.winner_side if team == result.winner
                    else result.loser_side)
            self.sides[team] = side

            buy_type = ("pistol" if is_pistol
                        else classify_buy_type(self.money[team] // 5, side))

            # Cross-reference with weapon data if available
            team_weapons = result.weapon_kills.get(team, {})
            weapon_buy = infer_buy_type_from_weapons(team_weapons)
            if buy_type == "eco" and weapon_buy == "full":
                buy_type = "force"  # Probably force-buying with saved money
            elif buy_type == "full" and weapon_buy == "eco":
                buy_type = "eco"  # Probably saving despite having money

            self.round_economies.append(RoundEconomy(
                round_num=round_num,
                team_name=team,
                money_estimate=self.money[team],
                loss_bonus_tier=self.loss_tier[team],
                buy_type=buy_type,
                consecutive_losses=self.consec_losses[team],
                won_previous=self.prev_won[team],
            ))

        # ── Apply round result to economy ────────────────────────────────

        winner = result.winner
        loser = result.loser

        # Winner income
        win_bonus = config.ROUND_WIN_BONUS
        if result.bomb_defused and result.winner_side == "counter-terrorists":
            win_bonus = config.DEFUSE_WIN_BONUS
        elif result.bomb_planted and result.winner_side == "terrorists":
            win_bonus = config.BOMB_EXPLODE_WIN_BONUS

        winner_kill_money = self._kill_reward_total(
            result.weapon_kills.get(winner, {}))
        self.money[winner] = (self.money[winner]
                              + win_bonus * 5
                              + winner_kill_money)

        # Winner loss tier steps DOWN by 1
        self.loss_tier[winner] = max(0, self.loss_tier[winner] - 1)
        self.consec_losses[winner] = 0
        self.prev_won[winner] = True

        # Loser income
        self.consec_losses[loser] += 1

        # Special case: first loss after pistol round
        if is_pistol:
            self.loss_tier[loser] = 1  # Start at tier 1 ($1900)
        else:
            self.loss_tier[loser] = min(
                self.loss_tier[loser] + 1,
                len(config.LOSS_BONUS_TIERS) - 1)

        loss_bonus = self._loss_bonus(loser)
        loser_kill_money = self._kill_reward_total(
            result.weapon_kills.get(loser, {}))

        # Bomb plant bonus for T side even on loss
        bomb_bonus = 0
        if result.bomb_planted and result.loser_side == "terrorists":
            bomb_bonus = config.BOMB_PLANT_TEAM_BONUS * 5

        self.money[loser] = (self.money[loser]
                             + loss_bonus * 5
                             + loser_kill_money
                             + bomb_bonus)

        self.prev_won[loser] = False

        # Clamp money
        self._clamp_money(winner)
        self._clamp_money(loser)

        # Deduct approximate buy cost for next round
        # We estimate based on buy type — this is the lossy part
        for team in [winner, loser]:
            side = self.sides[team]
            avg_money = self.money[team] // 5
            buy = classify_buy_type(avg_money, side)

            if buy == "full":
                cost = 5400 * 5 if side == "counter-terrorists" else 4800 * 5
            elif buy == "force":
                cost = 3000 * 5 if side == "counter-terrorists" else 2500 * 5
            elif buy == "eco":
                cost = 500 * 5  # Minimal spend
            else:
                cost = 0

            self.money[team] = max(0, self.money[team] - cost)

    def get_economy_at_round(self, round_num: int) -> dict:
        """Get economy state for both teams at start of a given round."""
        result = {}
        for econ in self.round_economies:
            if econ.round_num == round_num:
                result[econ.team_name] = econ
        return result

    def get_all_economies(self) -> List[RoundEconomy]:
        """Get full economy timeline."""
        return self.round_economies

    def get_economy_diff_at_round(self, round_num: int) -> Optional[int]:
        """Get money difference (team_a - team_b) at start of round."""
        econs = self.get_economy_at_round(round_num)
        if self.team_a in econs and self.team_b in econs:
            return (econs[self.team_a].money_estimate
                    - econs[self.team_b].money_estimate)
        return None
