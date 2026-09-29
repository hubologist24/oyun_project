# combat/damage.py
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


def apply_conversion(instances: List[DamageInstance],
                     conversion_rules: List[ConversionRule] = None
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
            if rule.percent <= 0:
                continue
            # Consume from the running remainder -> total never inflates.
            converted_amount = remaining * rule.percent
            if converted_amount <= 1e-9:
                continue
            produced.append(DamageInstance(amount=converted_amount,
                                           damage_type=rule.to_type))
            remaining -= converted_amount
            if remaining <= 1e-9:
                break
        if remaining > 1e-9:
            produced.append(DamageInstance(amount=remaining,
                                           damage_type=instance.damage_type))
        result.extend(produced)
    return result


def apply_modifiers(instances, source_stats):
    return instances


def apply_resistance(instances, target_resistances: Dict[str, float]):
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


def apply_world_rules(instances, world_rules=None, target_category="all",
                      direction="taken"):
    if world_rules is None:
        return instances
    result = []
    for instance in instances:
        mult = world_rules.damage_multiplier_for(target_category,
                                                 instance.damage_type, direction)
        result.append(DamageInstance(amount=instance.amount * mult,
                                     damage_type=instance.damage_type))
    return result


def _apply_resistance_modifiers(resistances: Dict[str, float], world_rules,
                                 target_category: str) -> Dict[str, float]:
    """B4: actually consume resistance_modifier rules."""
    if world_rules is None:
        return resistances
    res_mods = world_rules.resistance_modifiers_for(target_category)
    merged = dict(resistances)
    for dtype, delta in res_mods.items():
        merged[dtype] = merged.get(dtype, 0.0) + delta
    return merged


def resolve_damage(base_amount: float, damage_type: str, source_stats,
                   target_armor: float = 0.0,
                   world_rules=None, conversion_rules: List[ConversionRule] = None,
                   target_resistances: Dict[str, float] = None,
                   target_category: str = "all") -> int:
    resistances = dict(target_resistances) if target_resistances else {}
    if target_armor:
        resistances.setdefault("armor_flat", target_armor)

    # B4: fold resistance_modifier deltas in before mitigation runs.
    resistances = _apply_resistance_modifiers(resistances, world_rules, target_category)

    instances = [DamageInstance(amount=base_amount, damage_type=damage_type)]
    instances = apply_conversion(instances, conversion_rules)
    instances = apply_modifiers(instances, source_stats)
    instances = apply_resistance(instances, resistances)

    # "taken" applies to the target's category.
    instances = apply_world_rules(instances, world_rules,
                                  target_category=target_category, direction="taken")
    # B4: "dealt" now actually runs. Rules with target="all" apply to any
    # damage instance (e.g. "Conductive Water" lightning surge).
    instances = apply_world_rules(instances, world_rules,
                                  target_category="all", direction="dealt")

    total = sum(i.amount for i in instances)
    return max(1, int(round(total)))


def resolve_damage_breakdown(base_amount: float, damage_type: str, source_stats,
                              target_armor: float = 0.0, world_rules=None,
                              conversion_rules: List[ConversionRule] = None,
                              target_resistances: Dict[str, float] = None,
                              target_category: str = "all"):
    resistances = dict(target_resistances) if target_resistances else {}
    if target_armor:
        resistances.setdefault("armor_flat", target_armor)

    resistances = _apply_resistance_modifiers(resistances, world_rules, target_category)

    instances = [DamageInstance(amount=base_amount, damage_type=damage_type)]
    instances = apply_conversion(instances, conversion_rules)
    instances = apply_modifiers(instances, source_stats)
    instances = apply_resistance(instances, resistances)
    instances = apply_world_rules(instances, world_rules,
                                  target_category=target_category, direction="taken")
    instances = apply_world_rules(instances, world_rules,
                                  target_category="all", direction="dealt")

    breakdown = {}
    for i in instances:
        breakdown[i.damage_type] = breakdown.get(i.damage_type, 0) + i.amount
    total = max(1, int(round(sum(breakdown.values()))))
    return total, breakdown