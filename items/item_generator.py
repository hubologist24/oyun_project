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

from items.modifiers import (ModifierPools, get_weapon_class,
                              WEAPON_CLASS_RANGED, WEAPON_CLASS_MELEE_SMALL)

from items.slot_rules import (
    SLOT_RULES, get_slot_rule, get_max_affixes, get_special_chance,
    is_modifier_allowed,
)

GUARANTEE_CHANCE = 0.25

UNIQUE_CHANCE = 0.02         # regular drops
UNIQUE_CHANCE_BOSS = 0.12    # boss slots (those pass force_affix_counts)

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

        "gun": [
        ("Flintlock Pistol", "physical_damage"),
        ("Scrap Revolver", "physical_damage"),
        ("Thunderbuss", "lightning_damage"),
        ("Hexbore Rifle", "chaos_damage"),
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
    "gun": 3,
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

def _affix_matches_weapon_class(affix, weapon_class):
    required = affix.get("applies_to_weapon_class")
    if required is None:
        return True
    return required == weapon_class


def _split_pools_by_slot(prefix_pool, suffix_pool, slot):
    """Return (p_base, p_spec, s_base, s_spec) filtered by slot rules.

    For unrestricted slots (weapons) this is a no-op:
    returns (prefix_pool, [], suffix_pool, []).
    """
    rule = SLOT_RULES.get(slot)
    if rule is None:
        return prefix_pool, [], suffix_pool, []
    base_mods = set(rule["allowed_base"])
    spec_mods = set(rule["allowed_special"])
    p_base = [a for a in prefix_pool if a["modifier"] in base_mods]
    p_spec = [a for a in prefix_pool if a["modifier"] in spec_mods]
    s_base = [a for a in suffix_pool if a["modifier"] in base_mods]
    s_spec = [a for a in suffix_pool if a["modifier"] in spec_mods]
    return p_base, p_spec, s_base, s_spec


def _cap_affix_counts(num_p, num_s, slot):
    """Enforce per-slot total affix limit."""
    max_aff = get_max_affixes(slot)
    if max_aff is None:
        return num_p, num_s
    while num_p + num_s > max_aff:
        if num_p >= num_s and num_p > 0:
            num_p -= 1
        elif num_s > 0:
            num_s -= 1
        else:
            break
    return num_p, num_s


def _roll_special_affix(p_spec, s_spec, num_p, num_s, rng, item_level,
                        world_rules, used_affix_ids, loot_rules):
    """Roll exactly one 'special' affix (or none). Returns
    (affix, kind, num_p, num_s) with counts decremented if successful."""
    if not (p_spec or s_spec):
        return None, None, num_p, num_s

    # Prefer the kind that still has room to spare
    if num_p > 0 and num_s <= 0:
        prefer = "prefix"
    elif num_s > 0 and num_p <= 0:
        prefer = "suffix"
    else:
        prefer = rng.choice(["prefix", "suffix"])

    if prefer == "prefix" and p_spec:
        spec_pool, kind = p_spec, "prefix"
    elif prefer == "suffix" and s_spec:
        spec_pool, kind = s_spec, "suffix"
    elif p_spec:
        spec_pool, kind = p_spec, "prefix"
    elif s_spec:
        spec_pool, kind = s_spec, "suffix"
    else:
        return None, None, num_p, num_s

    a = _roll_affix_from_pool_biased(
        spec_pool, rng, item_level, world_rules,
        used_affix_ids, kind, loot_rules)
    if a is None:
        return None, None, num_p, num_s

    if kind == "prefix":
        num_p = max(0, num_p - 1)
    else:
        num_s = max(0, num_s - 1)
    return a, kind, num_p, num_s


def _roll_slot_category(rng, force_slot=None):
    if force_slot:
        return force_slot
    categories = list(SLOT_CATEGORY_WEIGHTS.keys())
    weights = list(SLOT_CATEGORY_WEIGHTS.values())
    return rng.choices(categories, weights=weights, k=1)[0]


def generate_random_item(rng, area_level: int = 1, world_context: dict = None,
                          force_slot: str = None, force_rarity: str = None,
                          force_affix_counts: tuple = None,
                          force_weapon_subtype: str = None) -> Item:
    ctx = dict(DEFAULT_WORLD_CONTEXT)
    if world_context:
        ctx.update(world_context)
    world_rules = ctx.get("world_rules")
    loot_rules = ctx.get("loot_rules")

    if force_slot is None and force_rarity is None and force_weapon_subtype is None:
        chance = UNIQUE_CHANCE_BOSS if force_affix_counts else UNIQUE_CHANCE
        if rng.random() < chance:
            uniq = generate_unique_item(rng, area_level, ctx)
            if uniq is not None:
                return uniq

    slot_category = _roll_slot_category(rng, force_slot)
    item_level = max(1, area_level + rng.randint(-1, 2))

    if slot_category == "weapon":
        base_name, implicit_modifier, weapon_subtype = _pick_base_biased(
            "weapon", rng, loot_rules, force_subtype=force_weapon_subtype)
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

    slot_rule = SLOT_RULES.get(actual_slot)

    # Implicit: disable for slots with no allowed base stats (boots).
    has_implicit = slot_category in ("weapon", "armor")
    if slot_rule and not slot_rule["allowed_base"]:
        has_implicit = False

    # ---- determine affix counts ----
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

    # ---- cap to slot rules ----
    num_prefixes, num_suffixes = _cap_affix_counts(
        num_prefixes, num_suffixes, actual_slot)

    # Force at least 1 affix for forced-special slots (boots)
    if slot_rule and not slot_rule["allowed_base"] and slot_rule["allowed_special"]:
        if num_prefixes + num_suffixes < 1:
            num_prefixes = 1

    # ---- implicit ----
    used_affix_ids = set()
    implicit_affix = None
    if has_implicit:
        implicit_tier = resolve_tier_for_roll(
            implicit_modifier, item_level, rng, world_rules)
        implicit_value = round(roll_value_for_tier(
            implicit_modifier, implicit_tier, rng, world_rules), 1)
        table = get_tier_table(world_rules)
        implicit_affix = RolledAffix(
            kind="implicit",
            affix_id="implicit_base",
            display_name="",
            modifier=implicit_modifier,
            damage_type=(implicit_modifier.replace("_damage", "")
                         if "_damage" in implicit_modifier else None),
            tier=implicit_tier,
            value=implicit_value,
            creation_tier_range=list(table[implicit_modifier][implicit_tier]),
        )

    # ---- build pools ----
    if slot_category in ("ring", "amulet"):
        prefix_pool = (ModifierPools.prefixes_for_slot("armor")
                       + ModifierPools.prefixes_for_slot("weapon")
                       + ModifierPools.prefixes_for_slot(slot_category))
        suffix_pool = (ModifierPools.suffixes_for_slot("armor")
                       + ModifierPools.suffixes_for_slot("weapon")
                       + ModifierPools.suffixes_for_slot(slot_category))
        seen = set()
        prefix_pool = [a for a in prefix_pool
                       if not (a["id"] in seen or seen.add(a["id"]))]
        seen = set()
        suffix_pool = [a for a in suffix_pool
                       if not (a["id"] in seen or seen.add(a["id"]))]
        prefix_pool = [a for a in prefix_pool if not a.get("primary_slot_only")]
        suffix_pool = [a for a in suffix_pool if not a.get("primary_slot_only")]
    else:
        prefix_pool = ModifierPools.prefixes_for_slot(slot_category)
        suffix_pool = ModifierPools.suffixes_for_slot(slot_category)
        if slot_category == "weapon":
            wc = get_weapon_class(weapon_subtype)
            prefix_pool = [a for a in prefix_pool if _affix_matches_weapon_class(a, wc)]
            suffix_pool = [a for a in suffix_pool if _affix_matches_weapon_class(a, wc)]

    p_base, p_spec, s_base, s_spec = _split_pools_by_slot(
        prefix_pool, suffix_pool, actual_slot)

    prefixes, suffixes = [], []

    # ---- guaranteed affix (respect slot rules) ----
    guaranteed = (getattr(loot_rules, "guarantee_enabling_affix", None)
                  if loot_rules else None)
    if guaranteed and rng.random() < GUARANTEE_CHANCE:
        for kind, pool in (("prefix", p_base), ("suffix", s_base)):
            matching = [a for a in pool if a["modifier"] == guaranteed]
            if not matching:
                continue
            a = _roll_affix_from_pool_biased(
                matching, rng, item_level, world_rules,
                used_affix_ids, kind, loot_rules)
            if a:
                if kind == "prefix":
                    prefixes.append(a)
                    num_prefixes = max(0, num_prefixes - 1)
                else:
                    suffixes.append(a)
                    num_suffixes = max(0, num_suffixes - 1)
                break

    # ---- special roll ----
    has_special = rng.random() < get_special_chance(actual_slot)
    if has_special:
        affix, kind, num_prefixes, num_suffixes = _roll_special_affix(
            p_spec, s_spec, num_prefixes, num_suffixes, rng,
            item_level, world_rules, used_affix_ids, loot_rules)
        if affix is not None:
            if kind == "prefix":
                prefixes.append(affix)
            else:
                suffixes.append(affix)

    # ---- base rolls ----
    for _ in range(num_prefixes):
        a = _roll_affix_from_pool_biased(
            p_base, rng, item_level, world_rules,
            used_affix_ids, "prefix", loot_rules)
        if a:
            prefixes.append(a)

    for _ in range(num_suffixes):
        a = _roll_affix_from_pool_biased(
            s_base, rng, item_level, world_rules,
            used_affix_ids, "suffix", loot_rules)
        if a:
            suffixes.append(a)

    # ---- safety trim ----
    max_aff = get_max_affixes(actual_slot)
    if max_aff is not None:
        while len(prefixes) + len(suffixes) > max_aff:
            if len(prefixes) >= len(suffixes):
                prefixes.pop()
            else:
                suffixes.pop()

    # ---- rarity / display ----
    actual_total = len(prefixes) + len(suffixes)
    if force_rarity:
        rarity = force_rarity
    elif actual_total == 0:
        rarity = Item.RARITY_NORMAL
    elif actual_total <= 2:
        rarity = Item.RARITY_MAGIC
    else:
        rarity = Item.RARITY_RARE

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


def _pick_base_biased(slot, rng, loot_rules, force_subtype=None):
    """
    Returns (base_name, implicit_modifier, weapon_subtype).
    Subtype is rolled first (attack pattern); base name / implicit
    damage type is then chosen within that subtype, biased by the
    extension's LootRules if present.
    """
    #subtype = _roll_weapon_subtype(rng)
    subtype = force_subtype or _roll_weapon_subtype(rng)
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

def generate_unique_item(rng, area_level, ctx=None):
    from items.uniques import eligible_uniques
    pool = eligible_uniques(area_level)
    if not pool:
        return None
    u = rng.choice(pool)
    ctx = ctx or DEFAULT_WORLD_CONTEXT

    def mk(kind, spec, idx):
        v = spec["value"]
        return RolledAffix(kind=kind,
                           affix_id="implicit_base" if kind == "implicit" else f"unique_{idx}_{spec['modifier']}",
                           display_name="", modifier=spec["modifier"],
                           damage_type=spec.get("damage_type"), tier="U",
                           value=v, creation_tier_range=[v, v])

    item = Item(base_name=u["base_name"], slot=u["slot"], rarity=Item.RARITY_UNIQUE,
                item_id=None, weapon_subtype=u.get("weapon_subtype"))
    item.unique_id = u["id"]
    item.display_name_override = u["name"]
    item.item_level = max(1, area_level)
    item.implicit = mk("implicit", u["implicit"], 0) if u.get("implicit") else None
    item.prefixes = [mk("prefix", a, i) for i, a in enumerate(u.get("affixes", []))
                     if a.get("kind", "prefix") == "prefix"]
    item.suffixes = [mk("suffix", a, i) for i, a in enumerate(u.get("affixes", []))
                     if a.get("kind") == "suffix"]
    item.creation_context = {"world_id": ctx.get("world_id", "world_0"),
                             "extension_id": ctx.get("extension_id", "starting_world"),
                             "item_level": item.item_level, "area_level": area_level}
    item.recompute_flat_bonuses()
    return item