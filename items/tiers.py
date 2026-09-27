"""
Tier table access layer.

This is THE seam for Phase 3's tier-range mutation: right now
get_tier_table() always returns the static data/tiers.json content,
but its signature already accepts an optional `world_rules` override
so world/world_modifiers.py can later inject a mutated table without
any caller changing.
"""
import json
import os

_DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "tiers.json")

_default_table = None


def _load_default_table():
    global _default_table
    if _default_table is None:
        with open(_DATA_PATH, "r") as f:
            _default_table = json.load(f)
    return _default_table


def get_tier_table(world_rules=None) -> dict:
    """
    Returns the active tier table: {modifier_id: {tier_name: [min, max]}}.

    world_rules: optional object/dict from Phase 3+ that can override
    specific modifier tier ranges (tier-range mutation). If a modifier
    isn't overridden, the default table entry is used.
    """
    base = _load_default_table()
    if world_rules is None:
        return base

    merged = json.loads(json.dumps(base))  # deep copy
    #overrides = getattr(world_rules, "tier_overrides", None) or world_rules.get("tier_overrides", {})
    overrides = getattr(world_rules, "tier_overrides", {}) if world_rules else {}
    for modifier_id, tier_ranges in overrides.items():
        merged.setdefault(modifier_id, {})
        merged[modifier_id].update(tier_ranges)
    return merged


def get_tier_unlock_levels() -> dict:
    table = _load_default_table()
    return table.get("_tier_unlock_item_level", {"T1": 1, "T2": 1, "T3": 1, "T4": 1})


def resolve_tier_for_roll(modifier_id: str, item_level: int, rng, world_rules=None) -> str:
    """
    Choose which tier to roll for a given modifier at a given item level.
    Higher item level unlocks better (lower-numbered) tiers and weights
    them more heavily, per spec ('high tiers become more likely but
    should still be rare').
    """
    table = get_tier_table(world_rules)
    if modifier_id not in table:
        raise ValueError(f"Unknown modifier id: {modifier_id}")

    unlock_levels = get_tier_unlock_levels()
    tier_names = sorted(
        table[modifier_id].keys(),
        key=lambda t: int(t[1:])  # T1, T2, T3... sort numerically
    )
    available = [t for t in tier_names if item_level >= unlock_levels.get(t, 1)]
    if not available:
        available = [tier_names[-1]]  # weakest tier always available

    # Weight: higher tiers (lower number) get exponentially less weight,
    # but weight shifts upward (toward better tiers) as item_level grows.
    weights = []
    for t in available:
        tier_num = int(t[1:])  # 1 = best
        base_weight = 2 ** (tier_num - 1)  # T1=1, T2=2, T3=4, T4=8...
        level_bonus = max(0, item_level - unlock_levels.get(t, 1))
        adjusted = base_weight / (1 + level_bonus * 0.04)
        weights.append(max(0.05, adjusted))

    chosen = rng.choices(available, weights=weights, k=1)[0]
    return chosen


def roll_value_for_tier(modifier_id: str, tier_name: str, rng, world_rules=None) -> float:
    table = get_tier_table(world_rules)
    lo, hi = table[modifier_id][tier_name]
    return rng.uniform(lo, hi)