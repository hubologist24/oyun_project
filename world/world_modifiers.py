"""
WorldModifier: the data-driven rule system that lets an extension
change how damage types interact with enemy categories, without any
code changes. This is the mechanical backbone of the "rule mutation"
requirement (Chaos Ascendancy, Lightning Chain, etc.).

Schema (per spec, refined slightly during implementation):

{
    "name": "Chaos Ascendancy",
    "description": "...",
    "rules": [
        {
            "type": "damage_taken_multiplier",
            "target": "minion",          # minion | elite | boss | all
            "damage_type": "chaos",       # or null = all types
            "multiplier": 2.0
        },
        {
            "type": "tier_override",
            "modifier": "physical_damage",
            "tiers": {"T1": [25, 35]}
        },
        {
            "type": "conversion",
            "from_type": "physical",
            "to_type": "chaos",
            "percent": 0.5,
            "scope": "player"            # player | enemy | all
        }
    ]
}

Rule "type" values are the extensible part -- new rule types can be
added by (a) adding a handler here and (b) the validator recognizing
the new type. This keeps AI-generated rules bounded to a known set of
mechanically-supported effects (never arbitrary code).
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional


SUPPORTED_RULE_TYPES = {
    "damage_taken_multiplier",
    "damage_dealt_multiplier",
    "tier_override",
    "conversion",
    "resistance_modifier",
    "on_kill_effect",       # e.g. "bleeding enemies explode" - Phase 3 stub, hook point only
}

VALID_TARGETS = {"minion", "elite", "boss", "player", "all"}


@dataclass
class WorldRule:
    type: str
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self):
        d = {"type": self.type}
        d.update(self.data)
        return d

    @staticmethod
    def from_dict(d):
        d = dict(d)
        rule_type = d.pop("type")
        return WorldRule(type=rule_type, data=d)


@dataclass
class WorldModifier:
    name: str
    description: str = ""
    rules: List[WorldRule] = field(default_factory=list)

    def tier_overrides(self) -> Dict[str, Dict[str, list]]:
        """Collects all tier_override rules into items/tiers.py's expected shape."""
        overrides = {}
        for rule in self.rules:
            if rule.type == "tier_override":
                modifier_id = rule.data.get("modifier")
                tiers = rule.data.get("tiers", {})
                if modifier_id:
                    overrides.setdefault(modifier_id, {}).update(tiers)
        return overrides

    def damage_multiplier_for(self, target_category: str, damage_type: str, direction="taken") -> float:
        """
        direction: "taken" (damage_taken_multiplier) or "dealt" (damage_dealt_multiplier)
        target_category: "minion" | "elite" | "boss" | "player"
        """
        rule_type = "damage_taken_multiplier" if direction == "taken" else "damage_dealt_multiplier"
        multiplier = 1.0
        for rule in self.rules:
            if rule.type != rule_type:
                continue
            rule_target = rule.data.get("target", "all")
            rule_dtype = rule.data.get("damage_type")
            if rule_target not in (target_category, "all"):
                continue
            if rule_dtype is not None and rule_dtype != damage_type:
                continue
            multiplier *= rule.data.get("multiplier", 1.0)
        return multiplier

    def conversion_rules_for(self, scope: str):
        """Returns combat.damage.ConversionRule list applicable to a scope (player/enemy/all)."""
        from combat.damage import ConversionRule
        result = []
        for rule in self.rules:
            if rule.type != "conversion":
                continue
            rule_scope = rule.data.get("scope", "all")
            if rule_scope not in (scope, "all"):
                continue
            result.append(ConversionRule(
                from_type=rule.data["from_type"],
                to_type=rule.data["to_type"],
                percent=rule.data["percent"],
            ))
        return result

    def resistance_modifiers_for(self, target_category: str) -> Dict[str, float]:
        """Returns {damage_type: flat_fraction_delta} for resistance_modifier rules."""
        result = {}
        for rule in self.rules:
            if rule.type != "resistance_modifier":
                continue
            rule_target = rule.data.get("target", "all")
            if rule_target not in (target_category, "all"):
                continue
            dtype = rule.data.get("damage_type")
            delta = rule.data.get("delta", 0.0)
            if dtype:
                result[dtype] = result.get(dtype, 0.0) + delta
        return result

    def to_dict(self):
        return {
            "name": self.name,
            "description": self.description,
            "rules": [r.to_dict() for r in self.rules],
        }

    @staticmethod
    def from_dict(d):
        return WorldModifier(
            name=d.get("name", "Unnamed Modifier"),
            description=d.get("description", ""),
            rules=[WorldRule.from_dict(r) for r in d.get("rules", [])],
        )


class WorldRuleSet:
    """
    Aggregates all active WorldModifiers (from all unlocked extensions)
    into one queryable object. This is what gets passed as `world_rules`
    throughout combat.py, tiers.py, item_generator.py.
    """
    def __init__(self, modifiers: Optional[List[WorldModifier]] = None):
        self.modifiers: List[WorldModifier] = modifiers or []

    def add(self, modifier: WorldModifier):
        self.modifiers.append(modifier)

    @property
    def tier_overrides(self) -> Dict[str, Dict[str, list]]:
        merged = {}
        for mod in self.modifiers:
            for modifier_id, tiers in mod.tier_overrides().items():
                merged.setdefault(modifier_id, {}).update(tiers)
        return merged

    def damage_multiplier_for(self, target_category: str, damage_type: str, direction="taken") -> float:
        total = 1.0
        for mod in self.modifiers:
            total *= mod.damage_multiplier_for(target_category, damage_type, direction)
        return total

    def conversion_rules_for(self, scope: str):
        result = []
        for mod in self.modifiers:
            result.extend(mod.conversion_rules_for(scope))
        return result

    def resistance_modifiers_for(self, target_category: str) -> Dict[str, float]:
        merged = {}
        for mod in self.modifiers:
            for dtype, delta in mod.resistance_modifiers_for(target_category).items():
                merged[dtype] = merged.get(dtype, 0.0) + delta
        return merged

    def summary_lines(self) -> List[str]:
        lines = []
        for mod in self.modifiers:
            lines.append(f"[{mod.name}] {mod.description}")
        return lines

    def to_dict(self):
        return {"modifiers": [m.to_dict() for m in self.modifiers]}

    @staticmethod
    def from_dict(d):
        return WorldRuleSet(modifiers=[WorldModifier.from_dict(m) for m in d.get("modifiers", [])])