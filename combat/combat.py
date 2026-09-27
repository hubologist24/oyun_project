"""
Combat resolution helpers - Phase 4: also emits a `player_attack` event
carrying attacker->target distance and weapon name, feeding
history/aggregator.py's combat-distance and weapon-usage inference.
"""
import math
from combat.damage import resolve_damage


def _enemy_category(enemy) -> str:
    if getattr(enemy, "is_boss", False):
        return "boss"
    if getattr(enemy, "is_elite", False):
        return "elite"
    return "minion"


def player_attack_enemy(player, enemy, event_bus, world_rules=None) -> int:
    conversion_rules = world_rules.conversion_rules_for("player") if world_rules else None
    dmg = resolve_damage(
        base_amount=player.effective_stats.base_damage,
        damage_type=player.effective_stats.primary_damage_type,
        source_stats=player.effective_stats,
        target_resistances=getattr(enemy, "resistances", {"armor_flat": getattr(enemy, "armor", 0)}),
        world_rules=world_rules,
        conversion_rules=conversion_rules,
        target_category=_enemy_category(enemy),
    )
    enemy.take_damage(dmg)

    weapon = player.equipped.get("weapon")
    distance = math.hypot(enemy.x - player.x, enemy.y - player.y)

    event_bus.emit("skill_used", skill="basic_attack", damage=dmg, target=enemy.name,
                   damage_type=player.effective_stats.primary_damage_type,
                   weapon_name=weapon.display_name if weapon else None)
    event_bus.emit("player_attack", target_category=_enemy_category(enemy), distance=distance,
                   damage=dmg, damage_type=player.effective_stats.primary_damage_type)
    return dmg


def enemy_attack_player(enemy, player, event_bus, world_rules=None) -> int:
    conversion_rules = world_rules.conversion_rules_for("enemy") if world_rules else None
    dmg = resolve_damage(
        base_amount=enemy.damage,
        damage_type=getattr(enemy, "damage_type", "physical"),
        source_stats=None,
        target_resistances=dict(player.effective_stats.resistances, armor_flat=player.effective_stats.armor),
        world_rules=world_rules,
        conversion_rules=conversion_rules,
        target_category="player",
    )
    applied = player.take_damage(dmg, cause=getattr(enemy, "damage_type", "physical"))
    if applied:
        event_bus.emit("player_hit", source=enemy.name, damage=dmg,
                       damage_type=getattr(enemy, "damage_type", "physical"))
    return dmg if applied else 0