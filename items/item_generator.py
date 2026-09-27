"""
Full item generation: rarity -> affix counts -> concrete prefix/suffix
rolls -> tier resolution -> value rolls -> Item object with legacy
creation-context metadata baked in.

world_context is optional and defaults to the starting world for
Phase 2 (no extensions exist yet). Its shape is designed to match what
Phase 3's World/Extension objects will provide.
"""
import random
from items.item import Item
from items.affix import RolledAffix
from items.modifiers import ModifierPools
from items.tiers import resolve_tier_for_roll, roll_value_for_tier, get_tier_table

WEAPON_BASES = [
    ("Rusty Sword", "physical_damage"),
    ("Iron Dagger", "physical_damage"),
    ("Worn Axe", "physical_damage"),
    ("Bone Club", "physical_damage"),
    ("Cracked Spear", "physical_damage"),
    ("Ember Wand", "fire_damage"),
    ("Frost Blade", "cold_damage"),
    ("Storm Rod", "lightning_damage"),
    ("Void Shard", "chaos_damage"),
]

ARMOR_BASES = [
    "Leather Vest", "Padded Cloth", "Scrap Mail", "Worn Buckler", "Tattered Cloak",
    "Iron Chestplate", "Bone Pauldrons",
]

DEFAULT_WORLD_CONTEXT = {
    "world_id": "world_0",
    "extension_id": "starting_world",
    "world_rules": None,  # Phase 3+ WorldModifier set; None => default tables
}


def _pick_base(slot, rng):
    if slot == "weapon":
        name, base_damage_modifier = rng.choice(WEAPON_BASES)
        return name, base_damage_modifier
    else:
        name = rng.choice(ARMOR_BASES)
        return name, "armor"


def _roll_affix_from_pool(pool, rng, item_level, world_rules, used_modifiers, kind):
    candidates = [a for a in pool if a["modifier"] not in used_modifiers]
    if not candidates:
        return None
    chosen = rng.choice(candidates)
    modifier_id = chosen["modifier"]
    tier = resolve_tier_for_roll(modifier_id, item_level, rng, world_rules)
    value = roll_value_for_tier(modifier_id, tier, rng, world_rules)
    table = get_tier_table(world_rules)
    creation_range = list(table[modifier_id][tier])
    used_modifiers.add(modifier_id)
    return RolledAffix(
        kind=kind,
        affix_id=chosen["id"],
        display_name=chosen["name"],
        modifier=modifier_id,
        damage_type=chosen.get("damage_type"),
        tier=tier,
        value=round(value, 1),
        creation_tier_range=creation_range,
    )


def generate_random_item(rng, area_level: int = 1, world_context: dict = None,
                          force_slot: str = None, force_rarity: str = None,
                          force_affix_counts: tuple = None) -> Item:
    ctx = dict(DEFAULT_WORLD_CONTEXT)
    if world_context:
        ctx.update(world_context)
    world_rules = ctx.get("world_rules")
    loot_rules = ctx.get("loot_rules")  # Phase 7: LootRules or None

    slot = force_slot or rng.choice(["weapon", "armor"])
    item_level = max(1, area_level + rng.randint(-1, 2))

    base_name, implicit_modifier = _pick_base_biased(slot, rng, loot_rules)

    if force_affix_counts:
        num_prefixes, num_suffixes = force_affix_counts
    else:
        num_prefixes, num_suffixes = ModifierPools.roll_affix_counts(item_level, rng)
        if loot_rules and loot_rules.six_mod_chance_multiplier != 1.0:
            # Small nudge toward re-rolling once more if the initial roll
            # was low, biased by six_mod_chance_multiplier. Kept modest
            # and probabilistic -- per spec, exceptional items must
            # remain rare even with personalization.
            if num_prefixes < 3 and num_suffixes < 3 and rng.random() < (loot_rules.six_mod_chance_multiplier - 1.0):
                num_prefixes, num_suffixes = ModifierPools.roll_affix_counts(item_level, rng)

    total_affixes = num_prefixes + num_suffixes
    if force_rarity:
        rarity = force_rarity
    elif total_affixes == 0:
        rarity = Item.RARITY_NORMAL
    elif total_affixes <= 2:
        rarity = Item.RARITY_MAGIC
    else:
        rarity = Item.RARITY_RARE

    if rarity == Item.RARITY_NORMAL and loot_rules and loot_rules.rare_chance_multiplier > 1.0:
        if rng.random() < (loot_rules.rare_chance_multiplier - 1.0) * 0.1:
            rarity = Item.RARITY_MAGIC

    used_modifiers = set()
    implicit_tier = resolve_tier_for_roll(implicit_modifier, item_level, rng, world_rules)
    implicit_value = round(roll_value_for_tier(implicit_modifier, implicit_tier, rng, world_rules), 1)
    table = get_tier_table(world_rules)
    implicit_affix = RolledAffix(
        kind="implicit",
        affix_id="implicit_base",
        display_name="",
        modifier=implicit_modifier,
        damage_type=implicit_modifier.replace("_damage", "") if "_damage" in implicit_modifier else None,
        tier=implicit_tier,
        value=implicit_value,
        creation_tier_range=list(table[implicit_modifier][implicit_tier]),
    )
    used_modifiers.add(implicit_modifier)

    prefix_pool = ModifierPools.prefixes_for_slot(slot)
    suffix_pool = ModifierPools.suffixes_for_slot(slot)

    prefixes = []
    for _ in range(num_prefixes):
        a = _roll_affix_from_pool_biased(prefix_pool, rng, item_level, world_rules, used_modifiers,
                                          "prefix", loot_rules)
        if a:
            prefixes.append(a)

    suffixes = []
    for _ in range(num_suffixes):
        a = _roll_affix_from_pool_biased(suffix_pool, rng, item_level, world_rules, used_modifiers,
                                          "suffix", loot_rules)
        if a:
            suffixes.append(a)

    prefix_name = prefixes[0].display_name if prefixes else ""
    suffix_name = suffixes[0].display_name if suffixes else ""
    full_name_parts = [p for p in [prefix_name, base_name, suffix_name] if p]
    display_name = " ".join(full_name_parts)

    item = Item(
        base_name=base_name,
        slot=slot,
        rarity=rarity,
        item_id=None,
    )
    item.display_name_override = display_name
    item.item_level = item_level
    item.implicit = implicit_affix
    item.prefixes = prefixes
    item.suffixes = suffixes
    item.creation_context = {
        "world_id": ctx["world_id"],
        "extension_id": ctx["extension_id"],
        "item_level": item_level,
        "area_level": area_level,
    }

    item.recompute_flat_bonuses()
    return item


def _pick_base_biased(slot, rng, loot_rules):
    """Same as Phase 2's _pick_base, but weights weapon damage-type choice by loot_rules."""
    if slot != "weapon":
        return rng.choice(ARMOR_BASES), "armor"

    if not loot_rules or not loot_rules.favored_damage_types:
        name, dmg_mod = rng.choice(WEAPON_BASES)
        return name, dmg_mod

    weights = []
    for name, dmg_mod in WEAPON_BASES:
        dtype = dmg_mod.replace("_damage", "")
        weights.append(loot_rules.favored_damage_types.get(dtype, 1.0))
    chosen = rng.choices(WEAPON_BASES, weights=weights, k=1)[0]
    return chosen


def _roll_affix_from_pool_biased(pool, rng, item_level, world_rules, used_modifiers, kind, loot_rules):
    candidates = [a for a in pool if a["modifier"] not in used_modifiers]
    if not candidates:
        return None

    if loot_rules and loot_rules.favored_damage_types:
        weights = []
        for a in candidates:
            dtype = a.get("damage_type")
            if dtype and dtype in loot_rules.favored_damage_types:
                weights.append(loot_rules.favored_damage_types[dtype])
            else:
                weights.append(1.0)
        chosen = rng.choices(candidates, weights=weights, k=1)[0]
    else:
        chosen = rng.choice(candidates)

    modifier_id = chosen["modifier"]
    tier = resolve_tier_for_roll(modifier_id, item_level, rng, world_rules)
    value = roll_value_for_tier(modifier_id, tier, rng, world_rules)
    table = get_tier_table(world_rules)
    creation_range = list(table[modifier_id][tier])
    used_modifiers.add(modifier_id)
    return RolledAffix(
        kind=kind,
        affix_id=chosen["id"],
        display_name=chosen["name"],
        modifier=modifier_id,
        damage_type=chosen.get("damage_type"),
        tier=tier,
        value=round(value, 1),
        creation_tier_range=creation_range,
    )