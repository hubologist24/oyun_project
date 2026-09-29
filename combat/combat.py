# combat/combat.py
import math
from combat.damage import resolve_damage


def _enemy_category(enemy) -> str:
    if getattr(enemy, "is_boss", False):
        return "boss"
    if getattr(enemy, "is_elite", False):
        return "elite"
    return "minion"


def player_attack_enemy(player, enemy, event_bus, world_rules=None,
                        damage_multiplier: float = 1.0) -> int:
    conversion_rules = world_rules.conversion_rules_for("player") if world_rules else None
    base_amount = player.effective_stats.base_damage * damage_multiplier
    dmg = resolve_damage(
        base_amount=base_amount,
        damage_type=player.effective_stats.primary_damage_type,
        source_stats=player.effective_stats,
        target_resistances=getattr(enemy, "resistances",
                                    {"armor_flat": getattr(enemy, "armor", 0)}),
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
    event_bus.emit("player_attack", target_category=_enemy_category(enemy),
                   distance=distance, damage=dmg,
                   damage_type=player.effective_stats.primary_damage_type)
    return dmg


def enemy_attack_player(enemy, player, event_bus, world_rules=None,
                        damage_multiplier: float = 1.0) -> int:
    """
    The enemy attacks the player. Damage comes from the enemy's own
    damage stat, uses the enemy's damage type, and is mitigated by the
    PLAYER'S resistances/armor (not the enemy's).
    """
    damage_type = getattr(enemy, "damage_type", "physical")
    conversion_rules = world_rules.conversion_rules_for("enemy") if world_rules else None
    base_amount = getattr(enemy, "damage", 0) * damage_multiplier

    player_stats = player.effective_stats
    target_resistances = dict(player_stats.resistances)
    target_resistances["armor_flat"] = player_stats.armor

    dmg = resolve_damage(
        base_amount=base_amount,
        damage_type=damage_type,
        source_stats=None,
        target_resistances=target_resistances,
        world_rules=world_rules,
        conversion_rules=conversion_rules,
        target_category="player",
    )
    applied = player.take_damage(dmg, cause=damage_type)
    if applied:
        event_bus.emit("player_hit", source=enemy.name, damage=dmg,
                       damage_type=damage_type)
    return dmg if applied else 0