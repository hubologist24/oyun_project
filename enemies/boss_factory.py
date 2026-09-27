"""
BossFactory: builds a personalized BossTemplate from an ExtensionSpec's
boss_personalization data (Phase 7). This replaces the Phase 3
'always Hollow Warden clone' approach for extension bosses with real
mechanical variety, while keeping the actual combat engine
(enemies/boss.py's Boss/BossPhaseConfig runtime) completely unchanged
-- personalization only selects/tunes PARAMETERS, never new code paths.

Archetypes (mechanic_archetype):
    dash_predator  -> aggressive dasher, short cooldowns, high dash dmg
    ranged_zoner   -> favors ranged bolts, keeps distance, fewer melee windows
    reflector      -> punishes the player's dominant damage type via a
                      resistance layer, but has a clear vulnerability
                      window (per spec: never make things un-counterable)
    summoner       -> lower personal damage, but the fight is validated
                      to remain fair (still just a single Boss instance
                      in Phase 7 -- true add-spawning is a future extension
                      point, flagged below)
    berserker      -> enrages earlier and harder (for players already
                      dominating bosses, per hints.boss_dominant)

BossValidator (ai/validators.py, Phase 5) still governs hard caps
(max_hp, max phases) regardless of archetype -- this factory produces
data that must pass that validator before ever reaching AreaInstance.
"""
import random
from enemies.boss import BossTemplate, BossPhaseConfig


ARCHETYPE_BASE_STATS = {
    "dash_predator": dict(max_hp=1500, armor=20, melee_damage=30, ranged_damage=14, dash_damage=55),
    "ranged_zoner": dict(max_hp=1300, armor=18, melee_damage=22, ranged_damage=34, dash_damage=30),
    "reflector": dict(max_hp=1600, armor=30, melee_damage=32, ranged_damage=20, dash_damage=40),
    "summoner": dict(max_hp=1400, armor=22, melee_damage=24, ranged_damage=24, dash_damage=35),
    "berserker": dict(max_hp=1350, armor=15, melee_damage=38, ranged_damage=18, dash_damage=50),
}

DIFFICULTY_MULTIPLIER = {"low": 0.85, "normal": 1.0, "high": 1.2}


def build_personalized_boss_template(name: str, boss_personalization: dict,
                                      area_level: int, rng: random.Random) -> BossTemplate:
    archetype = boss_personalization.get("mechanic_archetype", "dash_predator")
    difficulty_bias = boss_personalization.get("difficulty_bias", "normal")
    punished_type = boss_personalization.get("punishes_damage_type")

    base = dict(ARCHETYPE_BASE_STATS.get(archetype, ARCHETYPE_BASE_STATS["dash_predator"]))
    mult = DIFFICULTY_MULTIPLIER.get(difficulty_bias, 1.0)

    level_scale = 1.0 + max(0, area_level - 1) * 0.04
    max_hp = int(base["max_hp"] * mult * level_scale)
    melee_damage = int(base["melee_damage"] * mult * level_scale)
    ranged_damage = int(base["ranged_damage"] * mult * level_scale)
    dash_damage = int(base["dash_damage"] * mult * level_scale)

    resistances = {}
    if archetype == "reflector" and punished_type:
        # Punishes the player's frequent death-cause damage type, but
        # deliberately capped well below immunity, and phase 3 still
        # opens a genuine vulnerability window (never uncounterable).
        resistances[punished_type] = 0.35

    phases = _build_phases(archetype)

    template = BossTemplate(
        name=name,
        max_hp=max_hp,
        armor=base["armor"],
        melee_damage=melee_damage,
        ranged_damage=ranged_damage,
        dash_damage=dash_damage,
        damage_type="physical",
        resistances=resistances,
        phases=phases,
    )
    return template


def _build_phases(archetype: str):
    if archetype == "dash_predator":
        return [
            BossPhaseConfig(hp_threshold=1.0, enables_dash=True, armor_effectiveness=1.0,
                             attack_cooldown=0.9, telegraph_time_dash=0.4),
            BossPhaseConfig(hp_threshold=0.55, enables_dash=True, armor_effectiveness=0.6,
                             attack_cooldown=0.65, telegraph_time_dash=0.3,
                             enrage_vulnerability_after_dash=1.3),
            BossPhaseConfig(hp_threshold=0.2, enables_dash=True, armor_effectiveness=0.5,
                             attack_cooldown=0.45, telegraph_time_dash=0.22,
                             enrage_vulnerability_after_dash=1.8),
        ]
    if archetype == "ranged_zoner":
        return [
            BossPhaseConfig(hp_threshold=1.0, enables_dash=False, armor_effectiveness=1.0,
                             attack_cooldown=1.1, telegraph_time_melee=0.5),
            BossPhaseConfig(hp_threshold=0.5, enables_dash=True, armor_effectiveness=0.7,
                             attack_cooldown=0.8, telegraph_time_dash=0.35,
                             enrage_vulnerability_after_dash=1.4),
        ]
    if archetype == "reflector":
        return [
            BossPhaseConfig(hp_threshold=1.0, enables_dash=False, armor_effectiveness=1.0,
                             attack_cooldown=1.0),
            BossPhaseConfig(hp_threshold=0.6, enables_dash=True, armor_effectiveness=0.6,
                             attack_cooldown=0.75, enrage_vulnerability_after_dash=1.6),
            BossPhaseConfig(hp_threshold=0.25, enables_dash=True, armor_effectiveness=0.5,
                             attack_cooldown=0.5, telegraph_time_melee=0.35,
                             enrage_vulnerability_after_dash=2.0),
        ]
    if archetype == "summoner":
        return [
            BossPhaseConfig(hp_threshold=1.0, enables_dash=False, armor_effectiveness=1.0,
                             attack_cooldown=1.2),
            BossPhaseConfig(hp_threshold=0.5, enables_dash=True, armor_effectiveness=0.65,
                             attack_cooldown=0.85, enrage_vulnerability_after_dash=1.5),
        ]
    if archetype == "berserker":
        return [
            BossPhaseConfig(hp_threshold=1.0, enables_dash=True, armor_effectiveness=0.9,
                             attack_cooldown=0.8, telegraph_time_dash=0.35),
            BossPhaseConfig(hp_threshold=0.7, enables_dash=True, armor_effectiveness=0.6,
                             attack_cooldown=0.55, telegraph_time_dash=0.25,
                             enrage_vulnerability_after_dash=1.2),
            BossPhaseConfig(hp_threshold=0.35, enables_dash=True, armor_effectiveness=0.4,
                             attack_cooldown=0.35, telegraph_time_dash=0.18,
                             enrage_vulnerability_after_dash=1.6),
        ]
    # Fallback: identical to Phase 3's Hollow Warden phase curve.
    return [
        BossPhaseConfig(hp_threshold=1.0, enables_dash=False, armor_effectiveness=1.0, attack_cooldown=1.2),
        BossPhaseConfig(hp_threshold=0.6, enables_dash=True, armor_effectiveness=0.55, attack_cooldown=0.9),
        BossPhaseConfig(hp_threshold=0.25, enables_dash=True, armor_effectiveness=0.55, attack_cooldown=0.6,
                        enrage_vulnerability_after_dash=1.5, telegraph_time_melee=0.4, telegraph_time_dash=0.32),
    ]