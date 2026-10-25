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

    def set_percent(self, p):
        self.percent = max(0, min(100, p))
        return self.percent

    def adjust(self, delta):
        return self.set_percent(self.percent + delta)

    def _pct_for(self, name, group):
        kp = getattr(config, "DIFFICULTY_KNOB_PERCENT", {})
        gp = getattr(config, "DIFFICULTY_GROUP_PERCENT", {})
        if name in kp:
            return kp[name]
        if group in gp:
            return gp[group]
        return self.percent

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
        return f"{self.percent}% (~{best[0]})"


D = _Difficulty()