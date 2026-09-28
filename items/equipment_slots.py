"""
Equipment slot registry. Expands the old {"weapon","armor"} model into
a full slot set, per spec section 3.

Rings: modeled as ONE shared "ring" item-slot type (Item.slot == "ring")
that resolves into whichever of ring_1/ring_2 is free at equip time.
"""
from typing import Optional

EQUIPMENT_SLOTS = [
    "weapon", "offhand",
    "head", "chest", "legs", "boots", "gloves",
    "ring_1", "ring_2", "amulet",
]

ITEM_SLOT_TO_EQUIP_SLOTS = {
    "weapon": ["weapon"],
    "offhand": ["offhand"],
    "head": ["head"],
    "chest": ["chest"],
    "legs": ["legs"],
    "boots": ["boots"],
    "gloves": ["gloves"],
    "ring": ["ring_1", "ring_2"],
    "amulet": ["amulet"],
}

SLOT_CATEGORY = {
    "weapon": "weapon", "offhand": "weapon",
    "head": "armor", "chest": "armor", "legs": "armor",
    "boots": "armor", "gloves": "armor",
    "ring_1": "jewelry", "ring_2": "jewelry", "amulet": "jewelry",
}


def resolve_equip_slot(item_slot: str, currently_equipped: dict) -> Optional[str]:
    """
    Given an Item.slot value and the player's current equipped dict
    (equip_slot -> Item|None), returns which concrete equip_slot this
    item should go into. For multi-target types (rings), prefers an
    empty slot; if both are full, falls back to the first candidate.
    """
    candidates = ITEM_SLOT_TO_EQUIP_SLOTS.get(item_slot)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    for slot in candidates:
        if currently_equipped.get(slot) is None:
            return slot
    return candidates[0]