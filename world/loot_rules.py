"""
LootRules: per-extension loot bias, personalized from the player's
profile. Data-driven (matches the design doc's "loot_rules": {} field
in the WorldSpecification schema) and consumed by item_generator.py as
an additional optional weighting layer -- it does NOT replace the
existing tier/affix probability system, it biases WHICH damage-type
affix pool is favored when a choice exists.
"""
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class LootRules:
    favored_damage_types: Dict[str, float] = field(default_factory=dict)  # damage_type -> weight multiplier
    rare_chance_multiplier: float = 1.0
    six_mod_chance_multiplier: float = 1.0
    guarantee_enabling_affix: Optional[str] = None  # e.g. "chaos_damage" -- ensures at least one drop biased this way

    def to_dict(self):
        return {
            "favored_damage_types": self.favored_damage_types,
            "rare_chance_multiplier": self.rare_chance_multiplier,
            "six_mod_chance_multiplier": self.six_mod_chance_multiplier,
            "guarantee_enabling_affix": self.guarantee_enabling_affix,
        }

    @staticmethod
    def from_dict(d):
        return LootRules(
            favored_damage_types=d.get("favored_damage_types", {}),
            rare_chance_multiplier=d.get("rare_chance_multiplier", 1.0),
            six_mod_chance_multiplier=d.get("six_mod_chance_multiplier", 1.0),
            guarantee_enabling_affix=d.get("guarantee_enabling_affix"),
        )

    @staticmethod
    def default():
        return LootRules()