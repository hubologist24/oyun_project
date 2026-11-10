"""Per-slot affix restrictions for non-weapon items.

Weapons keep their existing (unrestricted) design.
Non-weapon slots are restricted:
  - Max 4 total affixes
  - Base stats: life, armor, resist_* (per slot)
  - At most 1 'special' stat, slot-specific
"""

SLOT_RULES = {
    "head": {
        "allowed_base": ["life", "armor", "resist_fire", "resist_cold",
                         "resist_lightning", "resist_chaos"],
        "allowed_special": [],
        "max_affixes": 4,
    },
    "chest": {
        "allowed_base": ["life", "armor", "resist_fire", "resist_cold",
                         "resist_lightning", "resist_chaos"],
        "allowed_special": [],
        "max_affixes": 4,
    },
    "legs": {
        "allowed_base": ["life", "armor", "resist_fire", "resist_cold",
                         "resist_lightning", "resist_chaos"],
        "allowed_special": [],
        "max_affixes": 4,
    },
    "boots": {
        # Boots are movement only - no HP/def/resist at all.
        "allowed_base": [],
        "allowed_special": ["movement_speed"],
        "max_affixes": 1,
    },
    "gloves": {
        "allowed_base": ["life", "armor", "resist_fire", "resist_cold",
                         "resist_lightning", "resist_chaos"],
        "allowed_special": ["attack_speed"],
        "max_affixes": 4,
    },
    "ring": {
        "allowed_base": ["life", "armor", "resist_fire", "resist_cold",
                         "resist_lightning", "resist_chaos"],
        "allowed_special": ["fork_count", "pierce_count", "chain_count",
                            "ricochet_count", "critical_chance"],
        "max_affixes": 4,
    },
    "amulet": {
        "allowed_base": ["life", "armor", "resist_fire", "resist_cold",
                         "resist_lightning", "resist_chaos"],
        "allowed_special": ["critical_chance", "movement_speed"],
        "max_affixes": 4,
    },
}

# Probability an item that *can* roll a special actually does.
SPECIAL_CHANCE_DEFAULT = 0.35


def get_slot_rule(slot):
    return SLOT_RULES.get(slot)


def get_max_affixes(slot):
    r = SLOT_RULES.get(slot)
    return r["max_affixes"] if r else None


def get_special_chance(slot):
    r = SLOT_RULES.get(slot)
    if r is None or not r["allowed_special"]:
        return 0.0
    if not r["allowed_base"]:
        return 1.0  # forced special (boots)
    return SPECIAL_CHANCE_DEFAULT


def is_modifier_allowed(slot, modifier):
    r = SLOT_RULES.get(slot)
    if r is None:
        return True
    return modifier in r["allowed_base"] or modifier in r["allowed_special"]