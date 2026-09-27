"""
Rolled affix instance: a concrete (modifier, tier, value, damage_type)
tuple that got baked onto a specific item at generation time.

Once rolled, an affix's value/tier is IMMUTABLE -- this is the core of
the legacy-item mechanic. The affix remembers which tier table produced
it (creation_tier_ranges) even if the world's tier table changes later.
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RolledAffix:
    kind: str            # "prefix" | "suffix"
    affix_id: str         # e.g. "cruel"
    display_name: str     # e.g. "Cruel"
    modifier: str          # e.g. "physical_damage"
    damage_type: Optional[str]
    tier: str              # e.g. "T1"
    value: float
    creation_tier_range: list  # [min, max] at time of creation -- for display/legacy context

    def to_dict(self):
        return {
            "kind": self.kind,
            "affix_id": self.affix_id,
            "display_name": self.display_name,
            "modifier": self.modifier,
            "damage_type": self.damage_type,
            "tier": self.tier,
            "value": self.value,
            "creation_tier_range": self.creation_tier_range,
        }

    @staticmethod
    def from_dict(d):
        return RolledAffix(
            kind=d["kind"],
            affix_id=d["affix_id"],
            display_name=d["display_name"],
            modifier=d["modifier"],
            damage_type=d.get("damage_type"),
            tier=d["tier"],
            value=d["value"],
            creation_tier_range=d.get("creation_tier_range", [0, 0]),
        )

    def format_line(self) -> str:
        val = round(self.value, 1) if isinstance(self.value, float) else self.value
        pretty_mod = self.modifier.replace("_", " ").title()
        return f"[{self.tier}] +{val} {pretty_mod}"