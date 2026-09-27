"""
Damage pipeline - Phase 3: apply_world_rules now consults a WorldRuleSet
for damage_taken_multiplier / damage_dealt_multiplier rules. Conversion
rules can also be sourced from world modifiers (e.g. "Physical->Chaos
against corrupted enemies") in addition to item-granted conversions.
"""
from dataclasses import dataclass
from typing import Optional, Dict, List


@dataclass
class DamageInstance:
    amount: float
    damage_type: str = "physical"


@dataclass
class ConversionRule:
    from_type: str
    to_type: str
    percent: float  # 0.0 - 1.0


def apply_conversion(instances: List[DamageInstance], conversion_rules: List[ConversionRule] = None
                      ) -> List[DamageInstance]:
    if not conversion_rules:
        return instances
    result = []
    for instance in instances:
        remaining = instance.amount
        produced = []
        for rule in conversion_rules:
            if rule.from_type != instance.damage_type:
                continue
            converted_amount = instance.amount * rule.percent
            if converted_amount <= 0:
                continue
            produced.append(DamageInstance(amount=converted_amount, damage_type=rule.to_type))
            remaining -= converted_amount
        if remaining > 0:
            produced.append(DamageInstance(amount=remaining, damage_type=instance.damage_type))
        result.extend(produced)
    return result


def apply_modifiers(instances: List[DamageInstance], source_stats) -> List[DamageInstance]:
    return instances


def apply_resistance(instances: List[DamageInstance], target_resistances: Dict[str, float]
                      ) -> List[DamageInstance]:
    result = []
    armor_flat = target_resistances.get("armor_flat", 0.0)
    for instance in instances:
        amount = instance.amount
        if instance.damage_type == "physical" and armor_flat > 0:
            reduction = armor_flat / (armor_flat + 100.0)
            amount *= (1.0 - reduction)
        resist_pct = target_resistances.get(instance.damage_type, 0.0)
        resist_pct = max(-1.0, min(0.75, resist_pct))
        amount *= (1.0 - resist_pct)
        result.append(DamageInstance(amount=amount, damage_type=instance.damage_type))
    return result


def apply_world_rules(instances: List[DamageInstance], world_rules=None,
                       target_category: str = "all", direction: str = "taken"
                       ) -> List[DamageInstance]:
    """
    world_rules: a WorldRuleSet (or None). Applies damage_taken_multiplier /
    damage_dealt_multiplier rules matching the target_category + damage_type.
    """
    if world_rules is None:
        return instances
    result = []
    for instance in instances:
        mult = world_rules.damage_multiplier_for(target_category, instance.damage_type, direction)
        result.append(DamageInstance(amount=instance.amount * mult, damage_type=instance.damage_type))
    return result


def resolve_damage(base_amount: float, damage_type: str, source_stats, target_armor: float = 0.0,
                    world_rules=None, conversion_rules: List[ConversionRule] = None,
                    target_resistances: Dict[str, float] = None,
                    target_category: str = "all") -> int:
    resistances = dict(target_resistances) if target_resistances else {}
    if target_armor:
        resistances.setdefault("armor_flat", target_armor)

    instances = [DamageInstance(amount=base_amount, damage_type=damage_type)]
    instances = apply_conversion(instances, conversion_rules)
    instances = apply_modifiers(instances, source_stats)
    instances = apply_resistance(instances, resistances)
    instances = apply_world_rules(instances, world_rules, target_category=target_category)

    total = sum(i.amount for i in instances)
    return max(1, int(round(total)))


def resolve_damage_breakdown(base_amount: float, damage_type: str, source_stats, target_armor: float = 0.0,
                              world_rules=None, conversion_rules: List[ConversionRule] = None,
                              target_resistances: Dict[str, float] = None,
                              target_category: str = "all"):
    resistances = dict(target_resistances) if target_resistances else {}
    if target_armor:
        resistances.setdefault("armor_flat", target_armor)

    instances = [DamageInstance(amount=base_amount, damage_type=damage_type)]
    instances = apply_conversion(instances, conversion_rules)
    instances = apply_modifiers(instances, source_stats)
    instances = apply_resistance(instances, resistances)
    instances = apply_world_rules(instances, world_rules, target_category=target_category)

    breakdown = {}
    for i in instances:
        breakdown[i.damage_type] = breakdown.get(i.damage_type, 0) + i.amount
    total = max(1, int(round(sum(breakdown.values()))))
    return total, breakdown