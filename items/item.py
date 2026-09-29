"""
Item entity - Phase 2.

Now supports prefixes/suffixes/tiers/item level/legacy creation context.
Flat damage_bonus/armor_bonus are DERIVED (cached) from affixes so
combat code and the Phase 1 stat system keep working unchanged.

Phase 1 saves (pure flat-bonus items with no affixes) are migrated on
load into "legacy flat items": they keep their original numbers exactly
(per spec: never break old items) but carry no affix list.
"""
import itertools
from items.affix import RolledAffix

#_id_counter = itertools.count(1)
_id_counter = 1

def _allocate_id() -> int:
    global _id_counter
    val = _id_counter
    _id_counter += 1
    return val


def _reserve_id(item_id) -> None:
    """Ensure a restored id can never be re-issued to a future item."""
    global _id_counter
    if isinstance(item_id, int) and item_id >= _id_counter:
        _id_counter = item_id + 1


class Item:
    RARITY_NORMAL = "normal"
    RARITY_MAGIC = "magic"
    RARITY_RARE = "rare"
    RARITY_UNIQUE = "unique"

    def __init__(self, base_name: str, slot: str, rarity: str = RARITY_NORMAL,
                 damage_bonus: int = 0, armor_bonus: int = 0, item_id=None,
                 item_level: int = 1 ,weapon_subtype: str = None):
        #self.item_id = item_id if item_id is not None else next(_id_counter)
        if item_id is not None:
            _reserve_id(item_id)
            self.item_id = item_id
        else:
            self.item_id = _allocate_id()
        self.base_name = base_name
        self.slot = slot  # "weapon" | "armor"
        self.rarity = rarity
        self.item_level = item_level
        self.weapon_subtype = weapon_subtype

        # Phase 2 affix data (may be empty for legacy/migrated items)
        self.implicit = None          # RolledAffix or None
        self.prefixes = []            # list[RolledAffix]
        self.suffixes = []            # list[RolledAffix]
        self.display_name_override = None
        self.creation_context = {
            "world_id": "world_0",
            "extension_id": "starting_world",
            "item_level": item_level,
            "area_level": item_level,
        }

        # Derived/cached flat bonuses used by the stat system (Phase 1 compat)
        self._damage_bonus = damage_bonus
        self._armor_bonus = armor_bonus
        self._resist_bonus = {}  # damage_type -> flat percent
        self._other_bonuses = {}  # modifier_id -> value (attack_speed, life, etc.)

        if damage_bonus or armor_bonus:
            # Constructed directly with flat values (Phase 1 style / legacy path)
            pass
        else:
            self.recompute_flat_bonuses()

    # ---------- derived properties used elsewhere in the codebase ----------
    @property
    def damage_bonus(self):
        return self._damage_bonus

    @property
    def armor_bonus(self):
        return self._armor_bonus

    @property
    def display_name(self) -> str:
        if self.display_name_override:
            return self.display_name_override
        return self.base_name

    def resist_bonus(self, damage_type: str) -> float:
        return self._resist_bonus.get(damage_type, 0.0)

    def other_bonus(self, modifier_id: str) -> float:
        return self._other_bonuses.get(modifier_id, 0.0)

    def all_affixes(self):
        result = []
        if self.implicit:
            result.append(self.implicit)
        result.extend(self.prefixes)
        result.extend(self.suffixes)
        return result

    def affix_count_label(self) -> str:
        return f"{len(self.prefixes)}P / {len(self.suffixes)}S"

    def is_six_mod(self) -> bool:
        return len(self.prefixes) >= 3 and len(self.suffixes) >= 3

    # ---------- recompute flat stat caches from affixes ----------
    def recompute_flat_bonuses(self):
        """
        Walks implicit+prefixes+suffixes and rebuilds the flat bonus
        caches consumed by player/stats.py. This is the seam that keeps
        the Phase 1 Stats system working without modification while
        Phase 2's rich affix data lives underneath.
        """
        dmg = 0.0
        armor = 0.0
        resist = {}
        other = {}

        for affix in self.all_affixes():
            mod = affix.modifier
            val = affix.value
            if mod in ("physical_damage", "fire_damage", "cold_damage",
                       "lightning_damage", "chaos_damage"):
                dmg += val
            elif mod == "armor":
                armor += val
            elif mod == "life":
                other["life"] = other.get("life", 0) + val
            elif mod.startswith("resist_"):
                dtype = mod.replace("resist_", "")
                resist[dtype] = resist.get(dtype, 0) + val
            elif mod in ("attack_speed", "movement_speed", "critical_chance"):
                other[mod] = other.get(mod, 0) + val

        self._damage_bonus = int(round(dmg))
        self._armor_bonus = int(round(armor))
        self._resist_bonus = resist
        self._other_bonuses = other

    # ---------- primary damage type this item deals (for conversion mechanics) ----------
    def primary_damage_type(self) -> str:
        if self.implicit and self.implicit.damage_type:
            return self.implicit.damage_type
        for affix in self.prefixes + self.suffixes:
            if affix.damage_type:
                return affix.damage_type
        return "physical"

    def rarity_color(self):
        import core.config as config
        return {
            Item.RARITY_NORMAL: config.COLOR_LOOT_NORMAL,
            Item.RARITY_MAGIC: config.COLOR_LOOT_MAGIC,
            Item.RARITY_RARE: config.COLOR_LOOT_RARE,
            Item.RARITY_UNIQUE: (200, 130, 40),
        }.get(self.rarity, config.COLOR_LOOT_NORMAL)

    # ---------- serialization ----------
    def to_dict(self):
        return {
            "item_id": self.item_id,
            "base_name": self.base_name,
            "slot": self.slot,
            "rarity": self.rarity,
            "item_level": self.item_level,
            "weapon_subtype": self.weapon_subtype,   # NEW
            "display_name_override": self.display_name_override,
            "implicit": self.implicit.to_dict() if self.implicit else None,
            "prefixes": [p.to_dict() for p in self.prefixes],
            "suffixes": [s.to_dict() for s in self.suffixes],
            "creation_context": self.creation_context,
            "damage_bonus": self._damage_bonus,
            "armor_bonus": self._armor_bonus,
        }

    @staticmethod
    def from_dict(d):
        item = Item(
            base_name=d.get("base_name", "Unknown Item"),
            slot=d.get("slot", "weapon"),
            rarity=d.get("rarity", Item.RARITY_NORMAL),
            damage_bonus=d.get("damage_bonus", 0),
            armor_bonus=d.get("armor_bonus", 0),
            item_id=d.get("item_id"),
            item_level=d.get("item_level", 1),
            weapon_subtype=d.get("weapon_subtype"),
        )
        item.display_name_override = d.get("display_name_override")
        item.creation_context = d.get("creation_context", item.creation_context)

        implicit_d = d.get("implicit")
        item.implicit = RolledAffix.from_dict(implicit_d) if implicit_d else None
        item.prefixes = [RolledAffix.from_dict(p) for p in d.get("prefixes", [])]
        item.suffixes = [RolledAffix.from_dict(s) for s in d.get("suffixes", [])]

        if item.implicit or item.prefixes or item.suffixes:
            # Rich Phase 2 item: recompute caches from affix data (source of truth)
            item.recompute_flat_bonuses()
        else:
            # Legacy Phase 1 item / no-affix item: keep flat values exactly as saved.
            # This is the "never break old items" rule in action.
            item._damage_bonus = d.get("damage_bonus", 0)
            item._armor_bonus = d.get("armor_bonus", 0)

        return item


# ---- Backward-compat shim: Phase 1 call sites used generate_random_item() from here ----
def generate_random_item(rng, area_level: int = 1):
    from items.item_generator import generate_random_item as _gen
    return _gen(rng, area_level=area_level)