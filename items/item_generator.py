################################################################################
# FILE: items\item_generator.py  (PATCH - add JEWELRY_BASES + slot routing)
################################################################################

"""
Full item generation: rarity -> affix counts -> concrete prefix/suffix
rolls -> tier resolution -> value rolls -> Item object with legacy
creation-context metadata baked in.

Phase 8: adds ring/amulet generation. Jewelry has NO implicit affix
(unlike weapons/armor) -- its entire value comes from prefixes/suffixes,
which keeps it a pure "stat stick" category per typical ARPG convention
and avoids needing a jewelry-specific implicit tier table.
"""
import random
from items.item import Item
from items.affix import RolledAffix
from items.modifiers import ModifierPools
from items.tiers import resolve_tier_for_roll, roll_value_for_tier, get_tier_table

GUARANTEE_CHANCE = 0.25

WEAPON_BASES_BY_SUBTYPE = {
    "sword": [
        ("Rusty Sword", "physical_damage"),
        ("Iron Dagger", "physical_damage"),
        ("Worn Axe", "physical_damage"),
        ("Bone Club", "physical_damage"),
        ("Cracked Spear", "physical_damage"),
    ],
    "greatsword": [
        ("Worn Greatsword", "physical_damage"),
        ("Iron Claymore", "physical_damage"),
        ("Bone Reaver", "physical_damage"),
        ("Notched Broadsword", "physical_damage"),
    ],
    "bow": [
        ("Short Bow", "physical_damage"),
        ("Hunting Bow", "physical_damage"),
        ("Yew Longbow", "physical_damage"),
    ],
    "short_wand": [
        ("Apprentice Wand", "physical_damage"),
        ("Ember Wand", "fire_damage"),
        ("Frost Wand", "cold_damage"),
        ("Storm Wand", "lightning_damage"),
        ("Void Wand", "chaos_damage"),
    ],
    "long_wand": [
        ("Archmage Staff", "physical_damage"),
        ("Ember Scepter", "fire_damage"),
        ("Frost Scepter", "cold_damage"),
        ("Storm Scepter", "lightning_damage"),
        ("Void Scepter", "chaos_damage"),
    ],
}

# Relative drop weight per subtype (independent of quality, which is
# rolled later on the shared tier tables).
WEAPON_SUBTYPE_WEIGHTS = {
    "sword": 5,
    "greatsword": 3,
    "bow": 3,
    "short_wand": 4,
    "long_wand": 2,
}

ARMOR_BASES = [
    ("Leather Vest", "chest"), ("Iron Chestplate", "chest"), ("Scrap Mail", "chest"),
    ("Bone Pauldrons", "chest"), ("Tattered Cloak", "chest"),
    ("Padded Cap", "head"), ("Iron Helm", "head"), ("Bone Circlet", "head"),
    ("Cloth Leggings", "legs"), ("Iron Greaves", "legs"), ("Scaled Leggings", "legs"),
    ("Worn Boots", "boots"), ("Reinforced Boots", "boots"), ("Swift Striders", "boots"),
    ("Leather Gloves", "gloves"), ("Iron Gauntlets", "gloves"), ("Bone Claws", "gloves"),
]

# Jewelry has no inherent damage-type/armor implicit -- names are purely
# cosmetic. All mechanical value comes from rolled prefixes/suffixes.
RING_BASES = ["Iron Band", "Bone Ring", "Tarnished Loop", "Ember Signet", "Void Circlet"]
AMULET_BASES = ["Bone Talisman", "Cracked Pendant", "Storm Charm", "Withered Locket"]

# Relative weight when picking a random slot category for un-forced drops.
# Jewelry is intentionally rarer than weapon/armor drops.
SLOT_CATEGORY_WEIGHTS = {"weapon": 4, "armor": 4, "ring": 1, "amulet": 1}

DEFAULT_WORLD_CONTEXT = {
    "world_id": "world_0",
    "extension_id": "starting_world",
    "world_rules": None,
}


def _roll_slot_category(rng, force_slot=None):
    if force_slot:
        return force_slot
    categories = list(SLOT_CATEGORY_WEIGHTS.keys())
    weights = list(SLOT_CATEGORY_WEIGHTS.values())
    return rng.choices(categories, weights=weights, k=1)[0]


def generate_random_item(rng, area_level: int = 1, world_context: dict = None,
                          force_slot: str = None, force_rarity: str = None,
                          force_affix_counts: tuple = None) -> Item:
    ctx = dict(DEFAULT_WORLD_CONTEXT)
    if world_context:
        ctx.update(world_context)
    world_rules = ctx.get("world_rules")
    loot_rules = ctx.get("loot_rules")

    slot_category = _roll_slot_category(rng, force_slot)
    item_level = max(1, area_level + rng.randint(-1, 2))

    has_implicit = slot_category in ("weapon", "armor")

    if slot_category == "weapon":
        base_name, implicit_modifier, weapon_subtype = _pick_base_biased("weapon", rng, loot_rules)
        actual_slot = "weapon"
    elif slot_category == "armor":
        base_name, actual_slot = rng.choice(ARMOR_BASES)
        implicit_modifier = "armor"
        weapon_subtype = None
    elif slot_category == "ring":
        base_name = rng.choice(RING_BASES)
        actual_slot = "ring"
        implicit_modifier = None
        weapon_subtype = None
    elif slot_category == "amulet":
        base_name = rng.choice(AMULET_BASES)
        actual_slot = "amulet"
        implicit_modifier = None
        weapon_subtype = None
    else:
        raise ValueError(f"Unknown slot_category: {slot_category}")

    # --- affix counts ---
    # NOTE: roll_affix_counts always returns >=1P/>=1S, so this function
    # cannot organically produce a NORMAL (zero-affix) item; the NORMAL
    # branch below only fires for forced (0, 0) counts. rare_chance_multiplier
    # is wired into the affix-count roll (it grants an extra pass aimed at
    # the 3+ affix threshold) rather than a separate rarity roll.
    if force_affix_counts:
        num_prefixes, num_suffixes = force_affix_counts
    else:
        num_prefixes, num_suffixes = ModifierPools.roll_affix_counts(item_level, rng)
        if loot_rules:
            if (loot_rules.six_mod_chance_multiplier > 1.0
                    and num_prefixes < 3 and num_suffixes < 3
                    and rng.random() < loot_rules.six_mod_chance_multiplier - 1.0):
                num_prefixes, num_suffixes = ModifierPools.roll_affix_counts(item_level, rng)
            if (loot_rules.rare_chance_multiplier > 1.0
                    and num_prefixes + num_suffixes < 3
                    and rng.random() < loot_rules.rare_chance_multiplier - 1.0):
                p2, s2 = ModifierPools.roll_affix_counts(item_level, rng)
                if p2 + s2 > num_prefixes + num_suffixes:
                    num_prefixes, num_suffixes = p2, s2

    # --- implicit (weapons/armor only) ---
    #used_modifiers = set()
    used_affix_ids = set()
    implicit_affix = None
    if has_implicit:
        implicit_tier = resolve_tier_for_roll(implicit_modifier, item_level, rng, world_rules)
        implicit_value = round(roll_value_for_tier(implicit_modifier, implicit_tier,
                                                   rng, world_rules), 1)
        table = get_tier_table(world_rules)
        implicit_affix = RolledAffix(
            kind="implicit",
            affix_id="implicit_base",
            display_name="",
            modifier=implicit_modifier,
            damage_type=implicit_modifier.replace("_damage", "")
                if "_damage" in implicit_modifier else None,
            tier=implicit_tier,
            value=implicit_value,
            creation_tier_range=list(table[implicit_modifier][implicit_tier]),
        )
        #used_modifiers.add(implicit_modifier)

    # --- roll the actual prefixes/suffixes ---
    #pool_slot = "armor" if slot_category in ("ring", "amulet") else slot_category
    #prefix_pool = ModifierPools.prefixes_for_slot(pool_slot)
    #suffix_pool = ModifierPools.suffixes_for_slot(pool_slot)

    if slot_category in ("ring", "amulet"):
        prefix_pool = (ModifierPools.prefixes_for_slot("armor")
                       + ModifierPools.prefixes_for_slot("weapon"))
        suffix_pool = (ModifierPools.suffixes_for_slot("armor")
                       + ModifierPools.suffixes_for_slot("weapon"))
        # de-dupe by id
        seen = set()
        prefix_pool = [a for a in prefix_pool if not (a["id"] in seen or seen.add(a["id"]))]
        seen = set()
        suffix_pool = [a for a in suffix_pool if not (a["id"] in seen or seen.add(a["id"]))]
    else:
        prefix_pool = ModifierPools.prefixes_for_slot(slot_category)
        suffix_pool = ModifierPools.suffixes_for_slot(slot_category)


    prefixes, suffixes = [], []

    # guarantee_enabling_affix: occasionally seed one affix with this
    # modifier. Skipped when the modifier is already consumed by the
    # implicit (e.g. "armor" on armor pieces) so the guarantee can
    # actually fire across slots.
    guaranteed = getattr(loot_rules, "guarantee_enabling_affix", None) if loot_rules else None
    #if guaranteed and guaranteed not in used_modifiers and rng.random() < GUARANTEE_CHANCE:
    if guaranteed and rng.random() < GUARANTEE_CHANCE:
        for kind, pool in (("prefix", prefix_pool), ("suffix", suffix_pool)):
            matching = [a for a in pool if a["modifier"] == guaranteed]
            if not matching:
                continue
            a = _roll_affix_from_pool_biased(matching, rng, item_level, world_rules,
                                             used_affix_ids, kind, loot_rules)
            if a:
                if kind == "prefix":
                    prefixes.append(a)
                    num_prefixes = max(0, num_prefixes - 1)
                else:
                    suffixes.append(a)
                    num_suffixes = max(0, num_suffixes - 1)
                break

    for _ in range(num_prefixes):
        a = _roll_affix_from_pool_biased(prefix_pool, rng, item_level, world_rules,
                                          used_affix_ids, "prefix", loot_rules)
        if a:
            prefixes.append(a)

    for _ in range(num_suffixes):
        a = _roll_affix_from_pool_biased(suffix_pool, rng, item_level, world_rules,
                                          used_affix_ids, "suffix", loot_rules)
        if a:
            suffixes.append(a)

    # --- rarity is derived from ACTUAL rolled affixes ---
    # (pool exhaustion / forced counts can no longer mislabel the item)
    actual_total = len(prefixes) + len(suffixes)
    if force_rarity:
        rarity = force_rarity
    elif actual_total == 0:
        rarity = Item.RARITY_NORMAL
    elif actual_total <= 2:
        rarity = Item.RARITY_MAGIC
    else:
        rarity = Item.RARITY_RARE

    # --- name + build Item ---
    prefix_name = prefixes[0].display_name if prefixes else ""
    suffix_name = suffixes[0].display_name if suffixes else ""
    full_name_parts = [p for p in [prefix_name, base_name, suffix_name] if p]
    display_name = " ".join(full_name_parts)

    item = Item(
        base_name=base_name,
        slot=actual_slot,
        rarity=rarity,
        item_id=None,
        weapon_subtype=weapon_subtype,
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


def _roll_weapon_subtype(rng) -> str:
    ids = list(WEAPON_SUBTYPE_WEIGHTS.keys())
    weights = list(WEAPON_SUBTYPE_WEIGHTS.values())
    return rng.choices(ids, weights=weights, k=1)[0]


def _pick_base_biased(slot, rng, loot_rules):
    """
    Returns (base_name, implicit_modifier, weapon_subtype).
    Subtype is rolled first (attack pattern); base name / implicit
    damage type is then chosen within that subtype, biased by the
    extension's LootRules if present.
    """
    subtype = _roll_weapon_subtype(rng)
    bases = WEAPON_BASES_BY_SUBTYPE[subtype]

    if not loot_rules or not loot_rules.favored_damage_types:
        name, dmg_mod = rng.choice(bases)
        return name, dmg_mod, subtype

    weights = []
    for name, dmg_mod in bases:
        dtype = dmg_mod.replace("_damage", "")
        weights.append(loot_rules.favored_damage_types.get(dtype, 1.0))
    name, dmg_mod = rng.choices(bases, weights=weights, k=1)[0]
    return name, dmg_mod, subtype

#def _roll_affix_from_pool_biased(pool, rng, item_level, world_rules, used_modifiers, kind, loot_rules):
#    candidates = [a for a in pool if a["modifier"] not in used_modifiers]
def _roll_affix_from_pool_biased(pool, rng, item_level, world_rules, used_affix_ids, kind, loot_rules):
    candidates = [a for a in pool if a["id"] not in used_affix_ids]
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
    #used_modifiers.add(modifier_id)
    used_affix_ids.add(chosen["id"])
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