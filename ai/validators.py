"""
Validation layer. Per spec, AI-generated (or in Phase 5's case,
procedurally-generated) content must pass through explicit validators
before ANY game object is instantiated from it. This is enforced now,
before AI exists, so the pipeline's safety guarantee is real rather
than aspirational by the time Phase 6 introduces an LLM backend.

Each validator returns (is_valid: bool, errors: list[str]).
"""
from typing import List, Tuple
from world.extension import ExtensionSpec
from world.extension_templates import get_modifier_template, _load_modifier_templates
from world.world_modifiers import SUPPORTED_RULE_TYPES, VALID_TARGETS
from combat.damage_types import DamageTypes


MAX_ROOM_COUNT = 20
MIN_ROOM_COUNT = 2
MAX_AREA_LEVEL = 500
MAX_ANOMALY_LEVEL_JUMP = 60


class WorldValidator:
    """Validates an ExtensionSpec before it becomes a real Extension."""

    @staticmethod
    def validate(spec: ExtensionSpec, existing_extension_ids: set,
                 player_level: int = 1) -> Tuple[bool, List[str]]:
        errors = []

        if not spec.extension_id or not isinstance(spec.extension_id, str):
            errors.append("extension_id must be a non-empty string")
        elif spec.extension_id in existing_extension_ids:
            errors.append(f"extension_id '{spec.extension_id}' already exists (extensions are permanent)")

        if not spec.extension_name or not isinstance(spec.extension_name, str):
            errors.append("extension_name must be a non-empty string")

        if not isinstance(spec.area_level, int) or spec.area_level < 1:
            errors.append("area_level must be a positive integer")
        elif spec.area_level > MAX_AREA_LEVEL:
            errors.append(f"area_level {spec.area_level} exceeds MAX_AREA_LEVEL ({MAX_AREA_LEVEL})")

        if spec.is_anomaly:
            jump = spec.area_level - player_level
            if jump > MAX_ANOMALY_LEVEL_JUMP:
                errors.append(f"anomaly level jump {jump} exceeds MAX_ANOMALY_LEVEL_JUMP "
                              f"({MAX_ANOMALY_LEVEL_JUMP}) -- per spec, anomalies must remain "
                              f"'optional or escapable', not absurdly unfair")

        if not (MIN_ROOM_COUNT <= spec.room_count <= MAX_ROOM_COUNT):
            errors.append(f"room_count {spec.room_count} outside allowed range "
                          f"[{MIN_ROOM_COUNT}, {MAX_ROOM_COUNT}]")

        if spec.map_cols < 20 or spec.map_rows < 20:
            errors.append("map_cols/map_rows too small to fit room_count safely")

        for modifier_dict in (spec.world_modifiers or []):
            if not isinstance(modifier_dict, dict):
                errors.append(
                    f"world_modifier entry must be a dict, got "
                    f"{type(modifier_dict).__name__}"
                )
                continue
            valid, mod_errors = WorldModifierValidator.validate(modifier_dict)
            errors.extend(mod_errors)

        return (len(errors) == 0, errors)


class WorldModifierValidator:
    """Validates a single raw WorldModifier dict (before WorldModifier.from_dict)."""

    @staticmethod
    def validate(modifier_dict) -> Tuple[bool, List[str]]:
        errors = []
        if not isinstance(modifier_dict, dict):
            return (False, [f"world modifier must be a dict, got "
                            f"{type(modifier_dict).__name__}"])

        if "name" not in modifier_dict:
            errors.append("world modifier missing 'name'")

        rules = modifier_dict.get("rules", [])
        if not isinstance(rules, list):
            errors.append("world modifier 'rules' must be a list")
            return (False, errors)

        for i, rule in enumerate(rules):
            if not isinstance(rule, dict):
                errors.append(f"rule[{i}] must be a dict, got "
                              f"{type(rule).__name__}")
                continue

            rule_type = rule.get("type")
            if rule_type not in SUPPORTED_RULE_TYPES:
                errors.append(f"rule[{i}] has unsupported type '{rule_type}'. "
                              f"Supported: {sorted(SUPPORTED_RULE_TYPES)}")
                continue

            if rule_type in ("damage_taken_multiplier", "damage_dealt_multiplier"):
                target = rule.get("target", "all")
                if target not in VALID_TARGETS:
                    errors.append(f"rule[{i}] invalid target '{target}'")
                dtype = rule.get("damage_type")
                if dtype is not None and not DamageTypes.is_valid(dtype):
                    errors.append(f"rule[{i}] invalid damage_type '{dtype}'")
                mult = rule.get("multiplier")
                if not isinstance(mult, (int, float)) or mult < 0 or mult > 10:
                    errors.append(f"rule[{i}] multiplier must be a number in [0, 10], got {mult}")

            elif rule_type == "conversion":
                for field in ("from_type", "to_type", "percent"):
                    if field not in rule:
                        errors.append(f"rule[{i}] conversion missing '{field}'")
                if "from_type" in rule and not DamageTypes.is_valid(rule["from_type"]):
                    errors.append(f"rule[{i}] invalid from_type '{rule['from_type']}'")
                if "to_type" in rule and not DamageTypes.is_valid(rule["to_type"]):
                    errors.append(f"rule[{i}] invalid to_type '{rule['to_type']}'")
                pct = rule.get("percent")
                if isinstance(pct, (int, float)) and not (0.0 <= pct <= 1.0):
                    errors.append(f"rule[{i}] conversion percent must be within [0.0, 1.0]")

            elif rule_type == "tier_override":
                if "modifier" not in rule:
                    errors.append(f"rule[{i}] tier_override missing 'modifier'")
                tiers = rule.get("tiers", {})
                if not isinstance(tiers, dict) or not tiers:
                    errors.append(f"rule[{i}] tier_override 'tiers' must be a non-empty dict")
                else:
                    for tier_name, bounds in tiers.items():
                        if not (isinstance(bounds, list) and len(bounds) == 2):
                            errors.append(f"rule[{i}] tier '{tier_name}' must be [min, max]")
                        elif bounds[0] > bounds[1]:
                            errors.append(f"rule[{i}] tier '{tier_name}' min > max")
                        elif bounds[0] < 0:
                            errors.append(f"rule[{i}] tier '{tier_name}' has negative min")

            elif rule_type == "resistance_modifier":
                dtype = rule.get("damage_type")
                if not dtype or not DamageTypes.is_valid(dtype):
                    errors.append(f"rule[{i}] resistance_modifier invalid damage_type '{dtype}'")
                delta = rule.get("delta")
                if not isinstance(delta, (int, float)) or not (-1.0 <= delta <= 1.0):
                    errors.append(f"rule[{i}] resistance_modifier delta must be within [-1.0, 1.0]")

        return (len(errors) == 0, errors)


class ItemValidator:
    """
    Stub for Phase 6/7 AI-specified items. Real usage begins once the
    AI can propose unique item concepts (per spec: 'AI should specify
    the design concept, while the game engine validates the numerical
    implementation'). Implemented now with real bounds so it's not
    dead code when Phase 6 arrives.
    """
    MAX_AFFIX_VALUE = 10000
    VALID_SLOTS = {"weapon", "armor"}

    @staticmethod
    def validate(item_dict: dict) -> Tuple[bool, List[str]]:
        errors = []
        if item_dict.get("slot") not in ItemValidator.VALID_SLOTS:
            errors.append(f"invalid slot '{item_dict.get('slot')}'")
        for affix in item_dict.get("prefixes", []) + item_dict.get("suffixes", []):
            val = affix.get("value")
            if not isinstance(val, (int, float)) or val < 0 or val > ItemValidator.MAX_AFFIX_VALUE:
                errors.append(f"affix '{affix.get('affix_id')}' has out-of-bounds value {val}")
        return (len(errors) == 0, errors)


class EnemyValidator:
    """Stub for Phase 6/7 AI-specified enemy templates."""
    MAX_HP = 10_000_000
    MAX_DAMAGE = 100_000

    @staticmethod
    def validate(enemy_dict: dict) -> Tuple[bool, List[str]]:
        errors = []
        hp = enemy_dict.get("hp", 0)
        dmg = enemy_dict.get("damage", 0)
        if not isinstance(hp, (int, float)) or hp <= 0 or hp > EnemyValidator.MAX_HP:
            errors.append(f"enemy hp {hp} out of bounds")
        if not isinstance(dmg, (int, float)) or dmg < 0 or dmg > EnemyValidator.MAX_DAMAGE:
            errors.append(f"enemy damage {dmg} out of bounds")
        return (len(errors) == 0, errors)


class BossValidator:
    """
    Stub for Phase 6/7 AI-specified bosses. Per spec: difficulty must
    come from mechanics, not absurd HP walls -- so this explicitly caps
    max_hp far below "impossible" territory and requires at least one
    phase to exist.
    """
    MAX_HP = 50000
    MAX_PHASES = 6

    @staticmethod
    def validate(boss_dict: dict) -> Tuple[bool, List[str]]:
        errors = []
        hp = boss_dict.get("max_hp", 0)
        if not isinstance(hp, (int, float)) or hp <= 0 or hp > BossValidator.MAX_HP:
            errors.append(f"boss max_hp {hp} out of bounds (max {BossValidator.MAX_HP}) "
                          f"-- per design principle, difficulty must come from mechanics, not HP walls")
        phases = boss_dict.get("phases", [])
        if not phases:
            errors.append("boss must define at least one phase")
        elif len(phases) > BossValidator.MAX_PHASES:
            errors.append(f"boss has {len(phases)} phases, exceeds MAX_PHASES ({BossValidator.MAX_PHASES})")
        return (len(errors) == 0, errors)


class LootValidator:
    """Stub for Phase 6/7 AI-specified loot rules."""
    @staticmethod
    def validate(loot_rules: dict) -> Tuple[bool, List[str]]:
        errors = []
        drop_chance = loot_rules.get("drop_chance")
        if drop_chance is not None and not (0.0 <= drop_chance <= 1.0):
            errors.append(f"loot drop_chance {drop_chance} must be within [0.0, 1.0]")
        return (len(errors) == 0, errors)


class ProgressionValidator:
    """
    Stub: ensures a generated extension doesn't softlock progression
    (e.g. area_level so far beyond reach that normal progression stalls).
    Minimal check now; will grow with Phase 6/7 AI-driven pacing.
    """
    MAX_NORMAL_LEVEL_GAP = 10

    @staticmethod
    def validate(spec: ExtensionSpec, player_level: int) -> Tuple[bool, List[str]]:
        errors = []
        if not spec.is_anomaly:
            gap = spec.area_level - player_level
            if gap > ProgressionValidator.MAX_NORMAL_LEVEL_GAP:
                errors.append(f"non-anomaly extension area_level gap {gap} exceeds "
                              f"MAX_NORMAL_LEVEL_GAP ({ProgressionValidator.MAX_NORMAL_LEVEL_GAP})")
        return (len(errors) == 0, errors)