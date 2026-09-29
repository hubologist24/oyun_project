
from dataclasses import dataclass, field
import core.config as config

@dataclass
class Stats:
    max_hp: int = config.PLAYER_BASE_HP
    hp: int = config.PLAYER_BASE_HP
    base_damage: int = config.PLAYER_BASE_DAMAGE
    primary_damage_type: str = "physical"
    armor: int = config.PLAYER_BASE_ARMOR
    speed: float = config.PLAYER_SPEED
    resistances: dict = field(default_factory=dict)
    crit_chance: float = 0.05
    attack_speed_bonus: float = 0.0

    # NEW: character attributes. Used only for base weapon requirements.
    # They do NOT influence attack pattern (that is bound to weapon subtype)
    # and do NOT gate tier / affix quality.
    strength: int = 5
    dexterity: int = 5
    intelligence: int = 5

    def clamp_hp(self):
        self.hp = max(0, min(self.hp, self.max_hp))

    def is_alive(self) -> bool:
        return self.hp > 0

    def to_dict(self):
        return {
            "max_hp": self.max_hp,
            "hp": self.hp,
            "base_damage": self.base_damage,
            "primary_damage_type": self.primary_damage_type,
            "armor": self.armor,
            "speed": self.speed,
            "resistances": self.resistances,
            "crit_chance": self.crit_chance,
            "attack_speed_bonus": self.attack_speed_bonus,
            "strength": self.strength,
            "dexterity": self.dexterity,
            "intelligence": self.intelligence,
        }

    @staticmethod
    def from_dict(d):
        return Stats(
            max_hp=d.get("max_hp", config.PLAYER_BASE_HP),
            hp=d.get("hp", config.PLAYER_BASE_HP),
            base_damage=d.get("base_damage", config.PLAYER_BASE_DAMAGE),
            primary_damage_type=d.get("primary_damage_type", "physical"),
            armor=d.get("armor", config.PLAYER_BASE_ARMOR),
            speed=d.get("speed", config.PLAYER_SPEED),
            resistances=d.get("resistances", {}),
            crit_chance=d.get("crit_chance", 0.05),
            attack_speed_bonus=d.get("attack_speed_bonus", 0.0),
            strength=d.get("strength", 5),
            dexterity=d.get("dexterity", 5),
            intelligence=d.get("intelligence", 5),
        )


def compute_effective_stats(base: Stats, equipped_items: list) -> Stats:
    eff = Stats(
        max_hp=base.max_hp,
        hp=base.hp,
        base_damage=base.base_damage,
        primary_damage_type=base.primary_damage_type,
        armor=base.armor,
        speed=base.speed,
        resistances=dict(base.resistances),
        crit_chance=base.crit_chance,
        attack_speed_bonus=base.attack_speed_bonus,
        # NEW: carry character attributes through so requirement checks
        # see the leveled-up values, not the dataclass defaults.
        strength=base.strength,
        dexterity=base.dexterity,
        intelligence=base.intelligence,
    )

    weapon = None
    for item in equipped_items:
        if item is None:
            continue
        eff.base_damage += getattr(item, "damage_bonus", 0)
        eff.armor += getattr(item, "armor_bonus", 0)

        life_bonus = item.other_bonus("life") if hasattr(item, "other_bonus") else 0
        eff.max_hp += int(life_bonus)

        atkspd = item.other_bonus("attack_speed") if hasattr(item, "other_bonus") else 0
        eff.attack_speed_bonus += atkspd / 100.0

        crit = item.other_bonus("critical_chance") if hasattr(item, "other_bonus") else 0
        eff.crit_chance += crit / 100.0

        if hasattr(item, "_resist_bonus"):
            for dtype, val in item._resist_bonus.items():
                eff.resistances[dtype] = eff.resistances.get(dtype, 0.0) + val / 100.0

        if item.slot == "weapon":
            weapon = item

    if weapon is not None and hasattr(weapon, "primary_damage_type"):
        eff.primary_damage_type = weapon.primary_damage_type()

    eff.clamp_hp()
    return eff