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

import re
from items.tiers import get_tier_table

MAX_MAP_DIM = 128
MIN_MAP_DIM = 20
MAX_MODIFIERS = 3
MAX_RULES_PER_MODIFIER = 3
MULT_MIN, MULT_MAX = 0.25, 4.0
RES_DELTA_LIMIT = 0.5
TIER_VALUE_MAX = 500
TIER_NAME_RE = re.compile(r"^T[1-9]$")
BIOME_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
ON_KILL_EFFECTS = {"explode", "heal_player"}

def _num(v): return isinstance(v, (int, float)) and not isinstance(v, bool)
def _int(v): return isinstance(v, int) and not isinstance(v, bool)


class WorldValidator:
    @staticmethod
    def validate(spec, existing_extension_ids, player_level=1):
        errors = []
        if not spec.extension_id or not isinstance(spec.extension_id, str):
            errors.append("extension_id must be a non-empty string")
        elif spec.extension_id in existing_extension_ids:
            errors.append(f"extension_id '{spec.extension_id}' already exists")

        if (not isinstance(spec.extension_name, str) or not spec.extension_name.strip()
                or len(spec.extension_name) > 60):
            errors.append("extension_name must be a non-empty string (<=60 chars)")
        if not isinstance(spec.biome, str) or not BIOME_RE.match(spec.biome):
            errors.append("biome must be snake_case (2-41 chars)")
        if not isinstance(spec.is_anomaly, bool):
            errors.append("is_anomaly must be a bool")

        if not _int(spec.area_level) or spec.area_level < 1:
            errors.append("area_level must be a positive integer")
        elif spec.area_level > MAX_AREA_LEVEL:
            errors.append(f"area_level exceeds {MAX_AREA_LEVEL}")
        elif spec.is_anomaly is True and spec.area_level - player_level > MAX_ANOMALY_LEVEL_JUMP:
            errors.append("anomaly level jump too large")

        if not _int(spec.room_count) or not (MIN_ROOM_COUNT <= spec.room_count <= MAX_ROOM_COUNT):
            errors.append(f"room_count must be an int in [{MIN_ROOM_COUNT}, {MAX_ROOM_COUNT}]")
        for attr in ("map_cols", "map_rows"):
            v = getattr(spec, attr)
            if not _int(v) or not (MIN_MAP_DIM <= v <= MAX_MAP_DIM):
                errors.append(f"{attr} must be an int in [{MIN_MAP_DIM}, {MAX_MAP_DIM}]")

        mods = spec.world_modifiers
        if not isinstance(mods, list):
            errors.append("world_modifiers must be a list")
        else:
            if len(mods) > MAX_MODIFIERS:
                errors.append(f"too many world_modifiers (max {MAX_MODIFIERS})")
            for m in mods:
                errors.extend(WorldModifierValidator.validate(m)[1])
        return (not errors, errors)


class WorldModifierValidator:
    @staticmethod
    def validate(md):
        if not isinstance(md, dict):
            return False, [f"world modifier must be a dict, got {type(md).__name__}"]
        errors = []
        if not isinstance(md.get("name"), str) or not md["name"].strip():
            errors.append("world modifier missing/invalid 'name'")
        rules = md.get("rules", [])
        if not isinstance(rules, list):
            return False, errors + ["'rules' must be a list"]
        if len(rules) > MAX_RULES_PER_MODIFIER:
            errors.append(f"too many rules (max {MAX_RULES_PER_MODIFIER})")

        known_mods = {k for k in get_tier_table() if not k.startswith("_")}
        for i, r in enumerate(rules):
            if not isinstance(r, dict):
                errors.append(f"rule[{i}] must be a dict"); continue
            t = r.get("type")
            if t not in SUPPORTED_RULE_TYPES:
                errors.append(f"rule[{i}] unsupported type '{t}'"); continue

            if t in ("damage_taken_multiplier", "damage_dealt_multiplier"):
                if r.get("target", "all") not in VALID_TARGETS:
                    errors.append(f"rule[{i}] invalid target")
                dt = r.get("damage_type")
                if dt is not None and not DamageTypes.is_valid(dt):
                    errors.append(f"rule[{i}] invalid damage_type")
                m = r.get("multiplier")
                if not _num(m) or not (MULT_MIN <= m <= MULT_MAX):
                    errors.append(f"rule[{i}] multiplier must be in [{MULT_MIN}, {MULT_MAX}]")

            elif t == "conversion":
                for f in ("from_type", "to_type"):
                    if not DamageTypes.is_valid(r.get(f)):
                        errors.append(f"rule[{i}] invalid {f}")
                if r.get("from_type") == r.get("to_type"):
                    errors.append(f"rule[{i}] conversion from_type == to_type")
                p = r.get("percent")
                if not _num(p) or not (0.0 <= p <= 1.0):
                    errors.append(f"rule[{i}] percent must be in [0, 1]")
                if r.get("scope", "all") not in ("player", "enemy", "all"):
                    errors.append(f"rule[{i}] invalid scope")

            elif t == "tier_override":
                if r.get("modifier") not in known_mods:
                    errors.append(f"rule[{i}] unknown tier modifier '{r.get('modifier')}'")
                tiers = r.get("tiers")
                if not isinstance(tiers, dict) or not tiers:
                    errors.append(f"rule[{i}] tiers must be a non-empty dict")
                else:
                    for name, b in tiers.items():
                        if not TIER_NAME_RE.match(str(name)):
                            errors.append(f"rule[{i}] bad tier name '{name}'")
                        elif not (isinstance(b, list) and len(b) == 2
                                  and all(_num(x) for x in b)):
                            errors.append(f"rule[{i}] tier '{name}' must be [min, max] numbers")
                        elif not (0 <= b[0] <= b[1] <= TIER_VALUE_MAX):
                            errors.append(f"rule[{i}] tier '{name}' needs 0<=min<=max<={TIER_VALUE_MAX}")

            elif t == "resistance_modifier":
                if not DamageTypes.is_valid(r.get("damage_type")):
                    errors.append(f"rule[{i}] invalid damage_type")
                if r.get("target", "all") not in VALID_TARGETS:
                    errors.append(f"rule[{i}] invalid target")
                d = r.get("delta")
                if not _num(d) or abs(d) > RES_DELTA_LIMIT:
                    errors.append(f"rule[{i}] delta must be within ±{RES_DELTA_LIMIT}")

            elif t == "on_kill_effect":
                if r.get("effect") not in ON_KILL_EFFECTS:
                    errors.append(f"rule[{i}] effect must be one of {sorted(ON_KILL_EFFECTS)}")
                if r.get("target", "all") not in (VALID_TARGETS - {"player"}):
                    errors.append(f"rule[{i}] invalid target")
                if r.get("effect") == "explode":
                    if not DamageTypes.is_valid(r.get("damage_type", "fire")):
                        errors.append(f"rule[{i}] invalid damage_type")
                    if not (_num(r.get("radius", 80)) and 20 <= r.get("radius", 80) <= 200):
                        errors.append(f"rule[{i}] radius must be in [20, 200]")
                    if not (_num(r.get("damage_ratio", 0.3)) and 0 < r.get("damage_ratio", 0.3) <= 1.0):
                        errors.append(f"rule[{i}] damage_ratio must be in (0, 1]")
                if r.get("effect") == "heal_player":
                    if not (_num(r.get("percent", 0.05)) and 0 < r.get("percent", 0.05) <= 0.2):
                        errors.append(f"rule[{i}] percent must be in (0, 0.2]")
        return (not errors, errors)


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
    MAX_HP = 50000
    MAX_PHASES = 6
    MAX_HIT = 1500          # per-hit cap; level scaling reaches ~330 at lvl 100
    MAX_ARMOR = 300         # => 75% physical mitigation
    MIN_COOLDOWN = 0.2
    MIN_TELEGRAPH = 0.15    # fairness floor: player must be able to react

    @staticmethod
    def validate(b: dict):
        errors = []
        hp = b.get("max_hp", 0)
        if not _num(hp) or not (0 < hp <= BossValidator.MAX_HP):
            errors.append(f"max_hp {hp} out of bounds (max {BossValidator.MAX_HP})")
        for f in ("melee_damage", "ranged_damage", "dash_damage"):
            v = b.get(f, 0)
            if not _num(v) or not (0 <= v <= BossValidator.MAX_HIT):
                errors.append(f"{f} {v} out of bounds")
        armor = b.get("armor", 0)
        if not _num(armor) or not (0 <= armor <= BossValidator.MAX_ARMOR):
            errors.append(f"armor {armor} out of bounds")
        for k, v in (b.get("resistances") or {}).items():
            if not _num(v) or not (-1.0 <= v <= 0.75):
                errors.append(f"resistance '{k}' {v} out of [-1, 0.75]")

        phases = b.get("phases") or []
        if not phases:
            errors.append("boss must define at least one phase")
        elif len(phases) > BossValidator.MAX_PHASES:
            errors.append("too many phases")
        else:
            last = 1.01
            for i, p in enumerate(phases):
                if not isinstance(p, dict):
                    errors.append(f"phase[{i}] must be a dict"); continue
                th = p.get("hp_threshold")
                if not _num(th) or not (0 < th <= 1.0) or th >= last:
                    errors.append(f"phase[{i}] hp_threshold must be in (0,1] and strictly descending")
                else:
                    last = th
                if i == 0 and th != 1.0:
                    errors.append("phase[0] hp_threshold must be 1.0")
                ae = p.get("armor_effectiveness", 1.0)
                if not _num(ae) or not (0 <= ae <= 1.0):
                    errors.append(f"phase[{i}] armor_effectiveness must be in [0,1]")
                if p.get("attack_cooldown", 1.0) < BossValidator.MIN_COOLDOWN:
                    errors.append(f"phase[{i}] attack_cooldown too low")
                for tf in ("telegraph_time_melee", "telegraph_time_dash"):
                    if p.get(tf, 0.5) < BossValidator.MIN_TELEGRAPH:
                        errors.append(f"phase[{i}] {tf} too low")
        return (not errors, errors)


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