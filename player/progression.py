"""
XP / leveling curve. Isolated so later phases can swap the curve or
make item level / area level reference it without touching Player.
"""
import core.config as config


def xp_required_for_level(level: int) -> int:
    """XP required to go from `level` to `level+1`."""
    return int(config.XP_CURVE_BASE * (config.XP_CURVE_GROWTH ** (level - 1)))


class Progression:
    def __init__(self, level: int = 1, xp: int = 0):
        self.level = level
        self.xp = xp

    def add_xp(self, amount: int):
        leveled_up = []
        self.xp += amount
        needed = xp_required_for_level(self.level)
        while self.xp >= needed:
            self.xp -= needed
            self.level += 1
            leveled_up.append(self.level)
            needed = xp_required_for_level(self.level)
        return leveled_up

    def xp_to_next(self) -> int:
        return xp_required_for_level(self.level)

    def to_dict(self):
        return {"level": self.level, "xp": self.xp}

    @staticmethod
    def from_dict(d):
        return Progression(level=d.get("level", 1), xp=d.get("xp", 0))