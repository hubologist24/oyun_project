# [PROJECT: combat]
"""
Rating -> effective value conversion (diminishing returns).

Raw stat values from items / perks / keystones / masteries accumulate as a
"rating".  The rating is converted to its effective in-game value through a
threshold ladder, so stacking the raw stat has diminishing practical returns.

This addresses runaway projectile-scaling (fork, pierce, chain, ricochet) and
caps out resist / armor stacking without removing the fantasy of "more is
better" -- it's just a slower curve.
"""

# ---------------------------------------------------------------------------
# Integer ladders for projectile-splitting stats.
# Each entry is (rating_threshold, effective_bonus).
# The effective bonus is the highest value whose threshold is met.
#
# Example: a +3 Fork affix contributes rating 3 -> effective +2 (not +3).
# A +2 Fork affix contributes rating 2 -> effective +1.
# ---------------------------------------------------------------------------
_PROJECTILE_LADDER = [
    (1, 1),
    (3, 2),
    (6, 3),
    (10, 4),
    (15, 5),
    (21, 6),
    (28, 7),
    (36, 8),
]

_PROJECTILE_LADDERS = {
    "fork_count":     _PROJECTILE_LADDER,
    "pierce_count":   _PROJECTILE_LADDER,
    "chain_count":    _PROJECTILE_LADDER,
    "ricochet_count": _PROJECTILE_LADDER,
}

# ---------------------------------------------------------------------------
# Resist ladder.  Rating is in raw percent points from item affixes
# (e.g. a "+12 fire resist" affix contributes rating 12).
# Effective value is the fraction added to the player's resistance.
# ---------------------------------------------------------------------------
_RESIST_LADDER = [
    (10, 0.06),
    (25, 0.12),
    (50, 0.20),
    (100, 0.32),
    (200, 0.45),
    (400, 0.58),
    (800, 0.68),
    (1600, 0.72),
]

# ---------------------------------------------------------------------------
# Armor ladder.  Rating is raw armor_bonus from item affixes.
# Armor already has its own diminishing returns inside the damage formula
# (reduction = armor_flat / (armor_flat + 100)); this ladder adds a second,
# gentler soft cap on the rating itself so many small armor items don't just
# linearly stack.
# ---------------------------------------------------------------------------
_ARMOR_LADDER = [
    (10, 10),
    (25, 24),
    (50, 47),
    (100, 90),
    (200, 165),
    (400, 280),
    (800, 440),
    (1600, 650),
]


def _ladder_scan(ladder, rating):
    """Return the highest effective value whose threshold is <= rating."""
    if rating <= 0:
        return 0
    best = 0
    for threshold, value in ladder:
        if rating >= threshold:
            best = value
        else:
            break
    return best


def rating_to_int(stat_name: str, rating) -> int:
    """Convert a projectile-splitting rating to an effective integer bonus."""
    ladder = _PROJECTILE_LADDERS.get(stat_name)
    if ladder is None:
        return max(0, int(rating))
    return _ladder_scan(ladder, float(rating))


def rating_to_resist(rating) -> float:
    """Convert a resist rating (percent points) to a fraction."""
    return _ladder_scan(_RESIST_LADDER, float(rating))


def rating_to_armor(rating) -> int:
    """Convert an armor rating to effective armor_flat."""
    return _ladder_scan(_ARMOR_LADDER, int(rating))