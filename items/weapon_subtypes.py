"""
Weapon subtype registry: loads data/weapon_subtypes.json and exposes
the attack profile / stat requirements for each subtype.

Design invariant (per design requirement):
  - Attack profile is determined ONLY by the weapon subtype.
  - Player attributes / character build NEVER override the base attack type.
  - Stat quality (tier rolls, affix counts) is NOT derived from subtype.
    Subtype only decides HOW the attack resolves (arc / projectile / cone).
"""
import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

_DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "weapon_subtypes.json")


@dataclass
class AttackProfile:
    kind: str                                # "arc" | "cone_aoe" | "projectile"
    range: float
    hits_all: bool = True
    arc_degrees: float = 90.0
    projectile_speed: float = 520.0
    piercing: bool = False
    attack_cooldown_multiplier: float = 1.0
    damage_multiplier: float = 1.0
    display_pattern: str = ""


class WeaponSubtypes:
    _categories: Optional[Dict[str, dict]] = None

    @classmethod
    def load(cls) -> Dict[str, dict]:
        if cls._categories is None:
            with open(_DATA_PATH, "r") as f:
                data = json.load(f)
            cls._categories = {c["id"]: c for c in data["categories"]}
        return cls._categories

    @classmethod
    def all_ids(cls) -> List[str]:
        return list(cls.load().keys())

    @classmethod
    def is_valid(cls, subtype_id: str) -> bool:
        return subtype_id in cls.load()

    @classmethod
    def get(cls, subtype_id: str) -> Optional[dict]:
        return cls.load().get(subtype_id)

    @classmethod
    def display_name(cls, subtype_id: str) -> str:
        entry = cls.get(subtype_id)
        if entry:
            return entry["display_name"]
        return (subtype_id or "unknown").replace("_", " ").title()

    @classmethod
    def stat_requirements(cls, subtype_id: str) -> Dict[str, int]:
        entry = cls.get(subtype_id)
        return dict(entry.get("stat_requirements", {})) if entry else {}

    @classmethod
    def attack_profile(cls, subtype_id: str) -> AttackProfile:
        entry = cls.get(subtype_id)
        if entry is None:
            return _fallback_profile()
        p = entry.get("base_attack", {})
        return AttackProfile(
            kind=p.get("kind", "arc"),
            range=float(p.get("range", 56)),
            hits_all=bool(p.get("hits_all", True)),
            arc_degrees=float(p.get("arc_degrees", 90.0)),
            projectile_speed=float(p.get("projectile_speed", 520.0)),
            piercing=bool(p.get("piercing", False)),
            attack_cooldown_multiplier=float(p.get("attack_cooldown_multiplier", 1.0)),
            damage_multiplier=float(p.get("damage_multiplier", 1.0)),
            display_pattern=entry.get("attack_pattern", ""),
        )


def _fallback_profile() -> AttackProfile:
    """Used only if a subtype id is unknown -- behaves like a bare fist."""
    return AttackProfile(kind="arc", range=42, arc_degrees=80, hits_all=True,
                          attack_cooldown_multiplier=1.0, damage_multiplier=0.7)