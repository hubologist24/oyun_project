"""
Player stat block - Phase 2: adds per-damage-type resistances and
"other" stat pool (life, attack_speed, movement_speed, crit chance)
aggregated from equipped item affixes.
"""
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
    resistances: dict = field(default_factory=dict)  # damage_type -> fraction (0.2 = 20%)
    crit_chance: float = 0.05
    attack_speed_bonus: float = 0.0

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
        )


def compute_effective_stats(base: Stats, equipped_items: list) -> Stats:
    """
    Aggregates base stats + all equipped item affixes into final
    effective stats. This is the seam Phase 3 world rules will extend
    further (e.g. "+resist" world buffs) without changing call sites.
    """
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
                eff.resistances[dtype] = eff.resistances.get(dtype, 0) + val / 100.0

        if item.slot == "weapon":
            weapon = item

    if weapon is not None and hasattr(weapon, "primary_damage_type"):
        eff.primary_damage_type = weapon.primary_damage_type()

    eff.clamp_hp()
    return eff