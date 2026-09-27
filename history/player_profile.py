"""
PlayerProfile: the compact, AI-ready summary of how a specific player
has played. Shaped closely after the example JSON in the design doc.

This is a plain data object -- history/aggregator.py is responsible
for actually computing one from an EventLog + live game state.
"""
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class PlayerProfile:
    level: int = 1
    main_damage: str = "physical"
    secondary_damage: Optional[str] = None
    favorite_skill: str = "basic_attack"
    most_used_weapon: Optional[str] = None
    defensive_strength: str = "unknown"   # "low" | "medium" | "high"

    deaths: Dict[str, int] = field(default_factory=dict)          # damage_type -> count
    total_deaths: int = 0

    exploration: float = 0.0               # 0.0 - 1.0, fraction of visited walkable tiles (current area)
    average_exploration: float = 0.0       # average across all areas visited

    boss_attempts: int = 0
    boss_successes: int = 0

    rare_items_found: int = 0
    six_mod_items_found: int = 0
    unique_items_found: int = 0

    playtime_seconds: float = 0.0
    extensions_generated: int = 0

    melee_ranged_preference: str = "melee"   # Phase 1-3 only has melee attack; placeholder for future skills
    average_combat_distance: float = 0.0

    items_kept: int = 0
    items_discarded: int = 0

    def to_dict(self):
        return {
            "level": self.level,
            "main_damage": self.main_damage,
            "secondary_damage": self.secondary_damage,
            "favorite_skill": self.favorite_skill,
            "most_used_weapon": self.most_used_weapon,
            "defensive_strength": self.defensive_strength,
            "deaths": self.deaths,
            "total_deaths": self.total_deaths,
            "exploration": round(self.exploration, 3),
            "average_exploration": round(self.average_exploration, 3),
            "boss_attempts": self.boss_attempts,
            "boss_successes": self.boss_successes,
            "rare_items_found": self.rare_items_found,
            "six_mod_items_found": self.six_mod_items_found,
            "unique_items_found": self.unique_items_found,
            "playtime_seconds": round(self.playtime_seconds, 1),
            "extensions_generated": self.extensions_generated,
            "melee_ranged_preference": self.melee_ranged_preference,
            "average_combat_distance": round(self.average_combat_distance, 1),
            "items_kept": self.items_kept,
            "items_discarded": self.items_discarded,
        }

    @staticmethod
    def from_dict(d):
        p = PlayerProfile()
        for k, v in d.items():
            if hasattr(p, k):
                setattr(p, k, v)
        return p