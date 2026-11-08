"""
Central difficulty tuning.

Every knob is (value_at_0_percent, value_at_100_percent).
0%  = the game exactly as it was before this system existed.
100% = maximum hardness.

Resolution order for a knob's value:
  1. config.DIFFICULTY_OVERRIDES[name]        -> exact value, ignores percent
  2. config.DIFFICULTY_KNOB_PERCENT[name]     -> percent for just this knob
  3. config.DIFFICULTY_GROUP_PERCENT[group]   -> percent for a group
  4. master percent (preset or DIFFICULTY_PERCENT, or the live value
     changed in-game with - / =)
"""
import core.config as config

KNOBS = dict(config.DIFFICULTY_KNOB_RANGES)
PRESETS = dict(config.DIFFICULTY_PRESETS)


class _Difficulty:
    def __init__(self):
        preset = getattr(config, "DIFFICULTY_PRESET", None)
        if preset and preset in PRESETS:
            self.percent = PRESETS[preset]
        else:
            self.percent = getattr(config, "DIFFICULTY_PERCENT", 0)
        self.percent = max(0, min(100, self.percent))
        self.player_level = 1                                  # NEW

    def set_player_level(self, level):                         # NEW
        self.player_level = max(1, int(level))

    def level_bonus(self):                                     # NEW
        per10 = getattr(config, "LEVEL_DIFFICULTY_PERCENT_PER_10", 0)
        cap   = getattr(config, "LEVEL_DIFFICULTY_BONUS_CAP", 100)
        return min(cap, (self.player_level // 10) * per10)

    def set_percent(self, p):
        self.percent = max(0, min(100, p))
        return self.percent

    def adjust(self, delta):
        return self.set_percent(self.percent + delta)

    def _pct_for(self, name, group):
        kp = getattr(config, "DIFFICULTY_KNOB_PERCENT", {})
        gp = getattr(config, "DIFFICULTY_GROUP_PERCENT", {})
        if name in kp:
            base = kp[name]
        elif group in gp:
            base = gp[group]
        else:
            base = self.percent
        return min(100, base + self.level_bonus())   # ← additive everywhere

    def get(self, name):
        ov = getattr(config, "DIFFICULTY_OVERRIDES", {})
        if name in ov:
            return ov[name]
        group, lo, hi = KNOBS[name]
        p = max(0, min(100, self._pct_for(name, group))) / 100.0
        return lo + (hi - lo) * p

    def get_int(self, name):
        return int(round(self.get(name)))

    def label(self):
        best = min(PRESETS.items(), key=lambda kv: abs(kv[1] - self.percent))
        bonus = self.level_bonus()
        if bonus:
            return f"{self.percent}% (~{best[0]}) +{bonus}% (level)"
        return f"{self.percent}% (~{best[0]})"


D = _Difficulty()


# --- area-level scaling helpers -------------------------------------------
# Tuned against the user's anchors: level 3 = 1x, level 23 = 5x density.

def area_density_mult(area_level: int) -> float:
    """Enemies-per-room multiplier. 1x at level 3, 5x at level 23, floor 0.8x."""
    return min(5.0, max(0.8, 1.0 + (area_level - 3) * 0.20))


def area_difficulty_mult(area_level: int) -> float:
    """Extra HP+damage multiplier stacked on top of the existing linear scaling.
    +2.5% per level past 5, capped at 2.5x."""
    return min(2.5, 1.0 + max(0, area_level - 5) * 0.025)


def area_loot_mult(area_level: int) -> float:
    """Drop-chance multiplier for items/orbs/stones. +5% per level past 5,
    capped at 4x."""
    #return min(4.0, 1.0 + max(0, area_level - 5) * 0.05)
    return min(5.0, 1.0 + max(0, area_level - 5) * 0.08)   # was 0.05 / cap 4


def area_quality_mult(area_level: int) -> float:
    """Rarity/affix-count bias multiplier. +3% per level past 5, capped at 3x."""
    #return min(3.0, 1.0 + max(0, area_level - 5) * 0.03)
    return min(4.0, 1.0 + max(0, area_level - 5) * 0.06)   # was 0.03 / cap 3