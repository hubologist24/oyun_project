"""Gate tiers define destination character for multi-gate regions.

Every gate gets a tier id. That tier shapes the generated extension:
level jump, loot multiplier, and boss difficulty bias.
"""

GATE_TIERS = {
    "safe": {
        "id": "safe",
        "index": 0,
        "label": "Safer",
        "dest_prefix": "A Calmer Road",
        "description": "Lower enemy density, richer healing.",
        "level_offset": 1,
        "loot_mult": 0.85,
        "boss_bias": "low",
        "color": (140, 220, 140),
    },
    "standard": {
        "id": "standard",
        "index": 1,
        "label": "Standard",
        "dest_prefix": "The Next Chapter",
        "description": "Balanced challenge and rewards.",
        "level_offset": 2,
        "loot_mult": 1.0,
        "boss_bias": "normal",
        "color": (200, 200, 220),
    },
    "dangerous": {
        "id": "dangerous",
        "index": 2,
        "label": "Dangerous",
        "dest_prefix": "A Dangerous Road",
        "description": "Elite guardians, exceptional loot.",
        "level_offset": 5,
        "loot_mult": 1.5,
        "boss_bias": "high",
        "color": (255, 180, 80),
    },
    "extreme": {
        "id": "extreme",
        "index": 3,
        "label": "Anomaly",
        "dest_prefix": "Anomaly",
        "description": "Reality is unstable here. Exceptionally rewarding.",
        "level_offset": 15,
        "loot_mult": 2.2,
        "boss_bias": "high",
        "color": (255, 80, 80),
    },
}


def get_tier(tier_id):
    return GATE_TIERS.get(tier_id, GATE_TIERS["standard"])


def assign_tiers_for_gate_count(count):
    """Return a list of tier dicts for N gates (ordered easiest first)."""
    if count <= 1:
        return [get_tier("standard")]
    if count == 2:
        return [get_tier("safe"), get_tier("dangerous")]
    return [get_tier("safe"), get_tier("standard"), get_tier("dangerous")]


def gate_count_for_region(area_level: int, size_tier: int = -1) -> int:
    base = 3 if area_level >= 8 else 2
    if size_tier == 0:
        base = max(1, base - 1)
    return min(3, base)