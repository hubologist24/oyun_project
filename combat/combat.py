# combat/combat.py
import random
import math
from combat.damage import resolve_damage

CRIT_MULTIPLIER = 1.5

def _enemy_category(enemy) -> str:
    if getattr(enemy, "is_boss", False):
        return "boss"
    if getattr(enemy, "is_elite", False):
        return "elite"
    return "minion"


def player_attack_enemy(player, enemy, event_bus, world_rules=None,
                        damage_multiplier: float = 1.0,
                        rng: random.Random | None = None,pierce: bool = False) -> int:
    rng = rng or random
    conversion_rules = world_rules.conversion_rules_for("player") if world_rules else None
    stats = player.effective_stats

    base_amount = stats.base_damage * damage_multiplier


    from core.difficulty import D
    base_amount *= D.get("player_damage_mult")

    is_crit = stats.crit_chance > 0.0 and rng.random() < stats.crit_chance
    if is_crit:
        base_amount *= CRIT_MULTIPLIER

    target_res = dict(getattr(enemy, "resistances",
                              {"armor_flat": getattr(enemy, "armor", 0)}))
    if pierce:   # Dash Strike: ignore armor, halve positive resistances
        target_res["armor_flat"] = 0
        for k, v in list(target_res.items()):
            if k != "armor_flat" and v > 0:
                target_res[k] = v * 0.5

    dmg = resolve_damage(
        base_amount=base_amount,
        damage_type=stats.primary_damage_type,
        source_stats=stats,
        target_resistances=target_res,
        world_rules=world_rules,
        conversion_rules=conversion_rules,
        target_category=_enemy_category(enemy),
        source_category="player",
    )
    enemy.take_damage(dmg,source=player)

    if is_crit:
        event_bus.emit("critical_hit",
                    x=enemy.x, y=enemy.y,
                    damage_type=stats.primary_damage_type,
                    crit_damage=dmg)

    weapon = player.equipped.get("weapon")
    distance = math.hypot(enemy.x - player.x, enemy.y - player.y)

    event_bus.emit("skill_used", skill="basic_attack", damage=dmg, target=enemy.name,
                   damage_type=stats.primary_damage_type,
                   weapon_name=weapon.display_name if weapon else None,
                   is_crit=is_crit)
    event_bus.emit("player_attack", target_category=_enemy_category(enemy),
                   distance=distance, damage=dmg,
                   damage_type=stats.primary_damage_type,
                   is_crit=is_crit)
    return dmg


def enemy_attack_player(enemy, player, event_bus, world_rules=None,
                        damage_multiplier: float = 1.0) -> int:
    damage_type = getattr(enemy, "damage_type", "physical")
    conversion_rules = world_rules.conversion_rules_for("enemy") if world_rules else None

    base_amount = (getattr(enemy, "damage", 0) * damage_multiplier
                   * getattr(enemy, "pack_damage_mult", 1.0))
    
    from combat.damage import level_damage_mult
    base_amount *= level_damage_mult(player.progression.level,
                                 getattr(enemy, "area_level", player.progression.level))

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
        source_category=_enemy_category(enemy),   # <-- FIX
    )
    #event_bus.emit("attacker_info", enemy=enemy) 
    applied = player.take_damage(dmg, cause=damage_type)
    if applied:
        event_bus.emit("attacker_info", enemy=enemy)
        event_bus.emit("player_hit", source=enemy.name, damage=dmg,
                       damage_type=damage_type)
    return dmg if applied else 0