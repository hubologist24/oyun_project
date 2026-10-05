"""
test_helpers8.py - regression tests for the review-fix pass:

  1. Tooltip            - `compare` flag, vintage check uses world_rules
  2. Game cleanup       - single _on_player_death, single level-up
                          message, no stray `damage` import, region
                          atmosphere drawn, projectiles cleared on travel
  3. Damage pipeline    - source_category scoping for "dealt" rules,
                          no double-dip, resolve_damage == breakdown total
  4. Item generator     - guarantee_enabling_affix wired up, rarity is
                          derived from the affixes actually rolled
  5. Dead-code cleanup  - aggregator tuple, gate module
  6. Challenge pipeline - ProceduralWorldGenerator challenge + enabler
  7. Validators         - bounds, crash-proofing, boss bounds,
                          existing generators/templates still pass
  8. on_kill_effect     - rule-set query, explode, heal, chain-depth cap
  9. Bigger bugs        - equip item-loss, enemy world_rules, boss
                          player_hit event, EventLog leak on load

Run:  python -m test.test_helpers8
"""
import os

# Must be set before pygame (or anything that imports it).
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import json
import random
import tempfile
import traceback
from dataclasses import asdict

import pygame  # noqa: F401


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------

def _run(name, fn):
    print(f"=== {name} ===")
    try:
        result = fn()
    except Exception as e:
        result = {"errors": [f"{type(e).__name__}: {e}"],
                  "traceback": traceback.format_exc()}
    if not isinstance(result, dict):
        result = {"result": result}
    result.setdefault("errors", [])
    # Any False-valued boolean check counts as a failure.
    for k, v in list(result.items()):
        if v is False:
            result["errors"].append(f"check failed: {k}")
    print(result)
    print()
    return result


def _make_game():
    from core.game import Game
    return Game()


# ---------------------------------------------------------------------------
# stubs / helpers
# ---------------------------------------------------------------------------

class _StubStats:
    def __init__(self):
        self.hp = 100
        self.max_hp = 100
        self.armor = 0
        self.resistances = {}

    def clamp_hp(self):
        pass


class _StubPlayer:
    def __init__(self, x, y):
        self.x = x
        self.y = y
        self.alive = True
        self.effective_stats = _StubStats()

    def take_damage(self, amount, cause="physical"):
        self.effective_stats.hp -= amount
        return True


class _RecordingBus:
    def __init__(self):
        self.events = []

    def emit(self, event_type, **payload):
        self.events.append((event_type, payload))

    def subscribe(self, *a, **k):
        pass


class _OpenTilemap:
    def collides_rect(self, rect):
        return False


def _ruleset(*modifier_dicts):
    from world.world_modifiers import WorldRuleSet, WorldModifier
    return WorldRuleSet(modifiers=[WorldModifier.from_dict(m) for m in modifier_dicts])


def _mod(name, *rules):
    return {"name": name, "description": "", "rules": list(rules)}


def _install_rules(game, *modifier_dicts):
    """Registers an Extension carrying the given modifiers so that
    game._active_world_rules() includes them."""
    from world.extension import Extension, ExtensionSpec
    spec = ExtensionSpec(extension_id="test_ext", extension_name="Test",
                         area_level=1, biome="ash_canyon",
                         world_modifiers=list(modifier_dicts))
    game.world.extensions.append(Extension(spec, 123))


def _valid_spec(**overrides):
    from world.extension import ExtensionSpec
    base = dict(extension_id="x_1", extension_name="Test Region", area_level=5,
                biome="ash_canyon", is_anomaly=False, world_modifiers=[],
                room_count=6)
    base.update(overrides)
    return ExtensionSpec(**base)


# ---------------------------------------------------------------------------
# 1. Tooltip
# ---------------------------------------------------------------------------

def test_tooltip_compare_flag():
    import ui.tooltip as tt
    from items.item_generator import generate_random_item

    pygame.font.init()
    surf = pygame.Surface((900, 700))
    item = generate_random_item(random.Random(1), area_level=5, force_slot="weapon")

    calls = []
    orig = tt.compare_items
    tt.compare_items = lambda a, b=None: (calls.append(1), orig(a, b))[1]
    try:
        tip = tt.ItemTooltip()
        tip.draw(surf, item, 10, 10, compare_against=None, compare=False)
        calls_no_compare = len(calls)
        tip.draw(surf, item, 10, 10, compare_against=None, compare=True)
        calls_compare = len(calls) - calls_no_compare
    finally:
        tt.compare_items = orig

    return {
        "compare_false_skips_diff": calls_no_compare == 0,
        "compare_true_runs_diff": calls_compare == 1,
        "errors": [],
    }


def test_tooltip_vintage_uses_world_rules():
    from ui.tooltip import ItemTooltip
    from items.item import Item
    from items.affix import RolledAffix

    pygame.font.init()
    item = Item("Test Sword", "weapon")
    item.prefixes = [RolledAffix(
        kind="prefix", affix_id="cruel", display_name="Cruel",
        modifier="physical_damage", damage_type="physical", tier="T1",
        value=30, creation_tier_range=[25, 35])]

    # World where T1 physical is [25, 35] -> item matches the CURRENT world.
    rules = _ruleset(_mod("Brittle", {"type": "tier_override",
                                      "modifier": "physical_damage",
                                      "tiers": {"T1": [25, 35]}}))
    tip = ItemTooltip()
    text_base = " ".join(t for t, _ in tip._build_info_lines(item, None))
    text_world = " ".join(t for t, _ in tip._build_info_lines(item, rules))

    return {
        "vintage_vs_base_table": "VINTAGE" in text_base,
        "not_vintage_vs_current_world": "VINTAGE" not in text_world,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 2. Game cleanup
# ---------------------------------------------------------------------------

def test_game_single_on_player_death():
    import inspect
    import core.game as game_mod
    src = inspect.getsource(game_mod.Game)
    return {
        "single_definition": src.count("def _on_player_death") == 1,
        "unused_damage_import_removed": not hasattr(game_mod, "damage"),
        "errors": [],
    }


def test_game_level_up_message_once():
    game = _make_game()
    shown = []
    game._show_message = lambda text, duration=2.5: shown.append(text)
    game.event_bus.emit("level_up", level=2)
    return {
        "message_shown_once": len(shown) == 1,
        "shown": shown,
        "errors": [],
    }


def test_game_death_applies_xp_penalty():
    """Confirms the surviving _on_player_death is the XP-penalty version."""
    game = _make_game()
    game.player.progression.xp = 100
    game.event_bus.emit("player_death", cause="fire")
    return {
        "xp_reduced": game.player.progression.xp < 100,
        "errors": [],
    }


def test_game_region_atmosphere_is_drawn():
    game = _make_game()
    calls = []
    game._draw_region_atmosphere = lambda: calls.append(1)
    game._draw()
    return {"atmosphere_called_in_draw": len(calls) >= 1, "errors": []}


def test_game_clears_projectiles_on_travel():
    from combat.attack_patterns import PlayerProjectile
    game = _make_game()
    game.player_projectiles.append(
        PlayerProjectile(0, 0, 1, 0, "physical", 1.0, None, False, 100))
    game._enter_starting_area()
    return {"projectiles_cleared": len(game.player_projectiles) == 0, "errors": []}


# ---------------------------------------------------------------------------
# 3. Damage pipeline
# ---------------------------------------------------------------------------

def test_damage_taken_rule_applies_once():
    from combat.damage import resolve_damage
    rules = _ruleset(_mod("X", {"type": "damage_taken_multiplier", "target": "all",
                                "damage_type": "physical", "multiplier": 2.0}))
    dmg = resolve_damage(100, "physical", None, world_rules=rules,
                         target_category="minion")
    return {"exactly_2x_not_4x": dmg == 200, "dmg": dmg, "errors": []}


def test_damage_dealt_rule_scoped_by_source():
    from combat.damage import resolve_damage
    rules = _ruleset(_mod("X", {"type": "damage_dealt_multiplier", "target": "minion",
                                "damage_type": "physical", "multiplier": 2.0}))
    from_minion = resolve_damage(100, "physical", None, world_rules=rules,
                                 target_category="player", source_category="minion")
    from_player = resolve_damage(100, "physical", None, world_rules=rules,
                                 target_category="minion", source_category="player")
    return {
        "minion_source_gets_bonus": from_minion == 200,
        "player_source_unaffected": from_player == 100,
        "errors": [],
    }


def test_damage_dealt_all_still_applies_once():
    from combat.damage import resolve_damage
    rules = _ruleset(_mod("Conductive", {"type": "damage_dealt_multiplier",
                                         "target": "all", "damage_type": "lightning",
                                         "multiplier": 1.5}))
    dmg = resolve_damage(100, "lightning", None, world_rules=rules,
                         target_category="minion", source_category="player")
    return {"applies_once": dmg == 150, "dmg": dmg, "errors": []}


def test_damage_resolve_matches_breakdown():
    from combat.damage import resolve_damage, resolve_damage_breakdown
    from combat.damage import ConversionRule
    conv = [ConversionRule("physical", "chaos", 0.4)]
    total_a = resolve_damage(100, "physical", None, conversion_rules=conv,
                             target_category="minion")
    total_b, breakdown = resolve_damage_breakdown(100, "physical", None,
                                                  conversion_rules=conv,
                                                  target_category="minion")
    return {
        "totals_match": total_a == total_b,
        "has_both_types": set(breakdown) == {"physical", "chaos"},
        "errors": [],
    }


def test_damage_callers_pass_source_category():
    """player_attack_enemy + enemy_attack_player must not crash with the
    new parameter, and a 'dealt' rule targeting minions must hit minion
    attackers only."""
    from combat.combat import enemy_attack_player
    from enemies.enemy import Enemy
    rules = _ruleset(_mod("Frenzy", {"type": "damage_dealt_multiplier",
                                     "target": "minion", "damage_type": "physical",
                                     "multiplier": 2.0}))
    p = _StubPlayer(0, 0)
    e = Enemy(0, 0, damage=10)
    dmg = enemy_attack_player(e, p, _RecordingBus(), world_rules=rules)
    return {"minion_attack_doubled": dmg == 20, "dmg": dmg, "errors": []}


# ---------------------------------------------------------------------------
# 4. Item generator
# ---------------------------------------------------------------------------

def test_item_guarantee_enabling_affix():
    import items.item_generator as ig
    from world.loot_rules import LootRules

    rules = LootRules(guarantee_enabling_affix="life")
    orig = getattr(ig, "GUARANTEE_CHANCE", None)
    ig.GUARANTEE_CHANCE = 1.0
    try:
        rng = random.Random(7)
        missing = 0
        for _ in range(60):
            it = ig.generate_random_item(rng, area_level=20, force_slot="weapon",
                                         world_context={"loot_rules": rules})
            if not any(a.modifier == "life" for a in it.prefixes + it.suffixes):
                missing += 1
    finally:
        if orig is not None:
            ig.GUARANTEE_CHANCE = orig

    return {
        "has_guarantee_constant": orig is not None,
        "every_item_has_life": missing == 0,
        "missing": missing,
        "errors": [],
    }


def test_item_rarity_matches_actual_affixes():
    from items.item_generator import generate_random_item
    rng = random.Random(11)
    bad = []
    for _ in range(300):
        it = generate_random_item(rng, area_level=random.randint(1, 80))
        n = len(it.prefixes) + len(it.suffixes)
        expected = "normal" if n == 0 else ("magic" if n <= 2 else "rare")
        if it.rarity != expected:
            bad.append((n, it.rarity))
    return {"rarity_consistent": not bad, "bad_samples": bad[:5], "errors": []}


def test_item_rare_multiplier_nudges_affix_totals():
    from items.item_generator import generate_random_item
    from world.loot_rules import LootRules

    def avg(rules):
        rng = random.Random(99)
        tot = 0
        for _ in range(600):
            it = generate_random_item(rng, area_level=5, force_slot="weapon",
                                      world_context={"loot_rules": rules})
            tot += len(it.prefixes) + len(it.suffixes)
        return tot / 600

    base = avg(LootRules())
    boosted = avg(LootRules(rare_chance_multiplier=2.0))
    return {
        "multiplier_has_effect": boosted > base,
        "base_avg": round(base, 3),
        "boosted_avg": round(boosted, 3),
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 5. Dead-code cleanup
# ---------------------------------------------------------------------------

def test_aggregator_item_stats_tuple():
    from history.event_log import EventLog
    from history.aggregator import _infer_item_stats

    log = EventLog(_RecordingBus())
    log._record("item_found", {"rarity": "rare", "is_six_mod": False})
    log._record("item_found", {"rarity": "unique", "is_six_mod": False})
    log._record("item_found", {"rarity": "rare", "is_six_mod": True})
    log._record("item_sold", {})

    rare, unique, six, kept, discarded = _infer_item_stats(log)
    return {
        "rare": rare == 2, "unique": unique == 1, "six": six == 1,
        "kept": kept == 2, "discarded": discarded == 1,
        "errors": [],
    }


def test_gate_module_clean():
    import inspect
    import world.gate as gate_mod
    src = inspect.getsource(gate_mod)
    return {
        "no_commented_open_to": "#def open_to" not in src,
        "no_stray_header": src.count("# FILE:") == 0,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 6. Challenge pipeline
# ---------------------------------------------------------------------------

def test_procedural_challenge_always_paired():
    from ai.procedural_generator import ProceduralWorldGenerator
    from ai.personalization import PersonalizationHints
    from ai.validators import WorldModifierValidator

    gen = ProceduralWorldGenerator()
    hints = PersonalizationHints(
        dominant_damage_type="fire", weak_defense=True,
        suggested_challenge_damage_type="fire",
        suggested_enabling_damage_type="chaos")

    rng = random.Random(5)
    challenge_runs = unpaired = oversized = bad_target = invalid = 0
    for _ in range(300):
        mods = gen._select_modifiers_with_pairing(hints, rng)
        names = [m["name"] for m in mods]
        has_challenge = any("Warded Denizens" in n for n in names)
        has_enabler = any(n.startswith("Echoes of") for n in names)
        if len(mods) > 3:
            oversized += 1
        if has_challenge:
            challenge_runs += 1
            if not has_enabler:
                unpaired += 1
            for m in mods:
                if "Warded Denizens" in m["name"]:
                    for r in m["rules"]:
                        if r["target"] not in ("minion", "elite"):
                            bad_target += 1
        for m in mods:
            if not WorldModifierValidator.validate(m)[0]:
                invalid += 1

    return {
        "challenge_modifier_appears": challenge_runs > 0,
        "always_paired_with_enabler": unpaired == 0,
        "never_exceeds_3_modifiers": oversized == 0,
        "challenge_never_targets_player_or_all": bad_target == 0,
        "all_modifiers_pass_validator": invalid == 0,
        "errors": [],
    }


def test_procedural_specs_pass_validators():
    from ai.procedural_generator import ProceduralWorldGenerator
    from ai.validators import WorldValidator, ProgressionValidator
    from history.player_profile import PlayerProfile

    gen = ProceduralWorldGenerator()
    rng = random.Random(3)
    failures = []
    for i in range(200):
        prof = PlayerProfile(level=rng.randint(1, 60),
                             main_damage=rng.choice(["fire", "cold", "chaos", "physical", "lightning"]),
                             defensive_strength=rng.choice(["low", "medium", "high"]))
        spec = gen.generate_next_extension(prof, {"a", "b"}, rng)
        ok, errs = WorldValidator.validate(spec, {"a", "b"}, prof.level)
        ok2, errs2 = ProgressionValidator.validate(spec, prof.level)
        if not (ok and ok2):
            failures.append(errs + errs2)
    return {"all_pass": not failures, "first_failures": failures[:3], "errors": []}


def test_loot_rules_guarantee_is_life():
    from ai.procedural_generator import ProceduralWorldGenerator
    from ai.personalization import PersonalizationHints
    from history.player_profile import PlayerProfile

    hints = PersonalizationHints(dominant_damage_type="fire", weak_defense=True,
                                 discarded_a_lot=True,
                                 suggested_enabling_damage_type="chaos")
    rules = ProceduralWorldGenerator()._build_loot_rules(
        PlayerProfile(), hints, random.Random(0))
    return {"guarantee_is_life": rules.guarantee_enabling_affix == "life", "errors": []}


# ---------------------------------------------------------------------------
# 7. Validators
# ---------------------------------------------------------------------------

def test_validator_accepts_valid_and_templates():
    from ai.validators import WorldValidator, WorldModifierValidator
    ok, errs = WorldValidator.validate(_valid_spec(), set(), 5)

    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "..", "data", "world_modifiers.json")
    with open(path) as f:
        templates = json.load(f)["templates"]
    bad = [t["id"] for t in templates if not WorldModifierValidator.validate(t)[0]]

    return {"valid_spec_accepted": ok, "spec_errors": errs,
            "shipped_templates_accepted": not bad, "bad_templates": bad,
            "errors": []}


def test_validator_rejects_out_of_bounds():
    from ai.validators import WorldValidator
    mult = {"name": "M", "rules": [{"type": "damage_taken_multiplier", "target": "boss",
                                    "damage_type": "chaos", "multiplier": 500}]}
    cases = {
        "map_cols_too_big": _valid_spec(map_cols=500),
        "map_rows_too_small": _valid_spec(map_rows=5),
        "multiplier_500": _valid_spec(world_modifiers=[mult]),
        "too_many_modifiers": _valid_spec(world_modifiers=[
            {"name": f"m{i}", "rules": []} for i in range(5)]),
        "bad_biome": _valid_spec(biome="Not Snake Case!"),
        "anomaly_jump": _valid_spec(is_anomaly=True, area_level=300),
    }
    results = {k: (not WorldValidator.validate(s, set(), 5)[0]) for k, s in cases.items()}
    return {**{f"rejects_{k}": v for k, v in results.items()}, "errors": []}


def test_validator_never_raises_on_garbage():
    from ai.validators import WorldValidator, WorldModifierValidator
    garbage_specs = [
        _valid_spec(room_count="six"),
        _valid_spec(area_level=None),
        _valid_spec(area_level="10"),
        _valid_spec(is_anomaly="yes", area_level=10),
        _valid_spec(world_modifiers="nope"),
        _valid_spec(world_modifiers=[None, 5, []]),
        _valid_spec(extension_name=None),
    ]
    garbage_mods = [
        {"name": "t", "rules": [{"type": "tier_override", "modifier": "armor",
                                 "tiers": {"T1": ["a", "b"]}}]},
        {"name": "t", "rules": [{"type": "tier_override", "modifier": "armor",
                                 "tiers": {"Tbig": [1, 2]}}]},
        {"name": "t", "rules": [{"type": "tier_override", "modifier": "armor",
                                 "tiers": {"T1": [None, 3]}}]},
        {"name": "t", "rules": [{"type": "damage_taken_multiplier", "multiplier": "x"}]},
        {"name": "t", "rules": ["not a dict"]},
        {"name": "t", "rules": [{"type": "resistance_modifier", "damage_type": "fire",
                                 "delta": None}]},
    ]
    crashes, accepted = [], []
    for s in garbage_specs:
        try:
            if WorldValidator.validate(s, set(), 5)[0]:
                accepted.append(("spec", repr(s)[:60]))
        except Exception as e:
            crashes.append(f"spec: {type(e).__name__}: {e}")
    for m in garbage_mods:
        try:
            if WorldModifierValidator.validate(m)[0]:
                accepted.append(("mod", repr(m)[:60]))
        except Exception as e:
            crashes.append(f"mod: {type(e).__name__}: {e}")
    return {"no_crashes": not crashes, "garbage_all_rejected": not accepted,
            "accepted": accepted, "errors": crashes}


def test_validator_tier_override_rules():
    from ai.validators import WorldModifierValidator as V
    def tier(modifier, tiers):
        return V.validate({"name": "t", "rules": [
            {"type": "tier_override", "modifier": modifier, "tiers": tiers}]})[0]
    return {
        "accepts_valid": tier("armor", {"T1": [10, 20]}),
        "rejects_unknown_modifier": not tier("made_up_stat", {"T1": [10, 20]}),
        "rejects_min_gt_max": not tier("armor", {"T1": [30, 20]}),
        "rejects_negative": not tier("armor", {"T1": [-5, 20]}),
        "rejects_huge": not tier("armor", {"T1": [10, 999999]}),
        "errors": [],
    }


def test_validator_on_kill_effect_rules():
    from ai.validators import WorldModifierValidator as V
    def rule(**kw):
        return V.validate({"name": "t", "rules": [{"type": "on_kill_effect", **kw}]})[0]
    return {
        "accepts_explode": rule(target="minion", effect="explode", damage_type="fire",
                                radius=80, damage_ratio=0.3),
        "accepts_heal": rule(target="minion", effect="heal_player", percent=0.05),
        "rejects_unknown_effect": not rule(target="minion", effect="nuke"),
        "rejects_huge_radius": not rule(target="minion", effect="explode", radius=5000),
        "rejects_huge_heal": not rule(target="minion", effect="heal_player", percent=0.9),
        "errors": [],
    }


def test_boss_validator_bounds():
    from ai.validators import BossValidator
    from enemies.boss import BossTemplate

    good = asdict(BossTemplate.hollow_warden())
    def mutated(**kw):
        d = json.loads(json.dumps(good))
        d.update(kw)
        return d

    bad_order = json.loads(json.dumps(good))
    bad_order["phases"][1]["hp_threshold"] = 0.9
    bad_order["phases"][2]["hp_threshold"] = 0.95

    fast_tele = json.loads(json.dumps(good))
    fast_tele["phases"][0]["telegraph_time_melee"] = 0.01

    first_not_one = json.loads(json.dumps(good))
    first_not_one["phases"][0]["hp_threshold"] = 0.8

    return {
        "accepts_hollow_warden": BossValidator.validate(good)[0],
        "rejects_hp_wall": not BossValidator.validate(mutated(max_hp=10**7))[0],
        "rejects_huge_armor": not BossValidator.validate(mutated(armor=5000))[0],
        "rejects_huge_hit": not BossValidator.validate(mutated(melee_damage=10**6))[0],
        "rejects_bad_resistance": not BossValidator.validate(mutated(resistances={"fire": 5.0}))[0],
        "rejects_non_descending_phases": not BossValidator.validate(bad_order)[0],
        "rejects_unreactable_telegraph": not BossValidator.validate(fast_tele)[0],
        "rejects_first_phase_not_1": not BossValidator.validate(first_not_one)[0],
        "rejects_no_phases": not BossValidator.validate(mutated(phases=[]))[0],
        "errors": [],
    }


def test_boss_factory_templates_pass_validator():
    from enemies.boss_factory import build_personalized_boss_template, ARCHETYPE_BASE_STATS
    from ai.validators import BossValidator
    failures = []
    for arch in ARCHETYPE_BASE_STATS:
        for bias in ("low", "normal", "high"):
            for lvl in (1, 25, 100):
                t = build_personalized_boss_template(
                    "B", {"mechanic_archetype": arch, "difficulty_bias": bias,
                          "punishes_damage_type": "fire"}, lvl, random.Random(0))
                ok, errs = BossValidator.validate(asdict(t))
                if not ok:
                    failures.append((arch, bias, lvl, errs[:2]))
    return {"all_archetypes_valid": not failures, "failures": failures[:3], "errors": []}


def test_mock_generator_valid_output_passes():
    from ai.mock_generator import MockAIWorldGenerator
    from ai.validators import WorldValidator
    from history.player_profile import PlayerProfile
    gen = MockAIWorldGenerator(failure_rate=0.0, verbose=False)
    rng = random.Random(21)
    bad = []
    for _ in range(100):
        prof = PlayerProfile(level=rng.randint(1, 40))
        spec = gen.generate_next_extension(prof, set(), rng)
        ok, errs = WorldValidator.validate(spec, set(), prof.level)
        if not ok:
            bad.append(errs[:2])
    return {"all_valid": not bad, "first_bad": bad[:3], "errors": []}


def test_extension_generator_survives_validator_crash():
    from world.extension_generator import WorldExtensionGenerator
    import world.extension_generator as eg
    from history.player_profile import PlayerProfile

    orig = eg.WorldValidator.validate
    eg.WorldValidator.validate = staticmethod(lambda *a, **k: 1 / 0)
    try:
        spec = WorldExtensionGenerator().generate(PlayerProfile(level=3), set(),
                                                  random.Random(0))
        ok = spec is not None and spec.extension_id.startswith("fallback_")
    finally:
        eg.WorldValidator.validate = orig
    return {"falls_back_to_safe_spec": ok, "errors": []}


# ---------------------------------------------------------------------------
# 8. on_kill_effect
# ---------------------------------------------------------------------------

def test_on_kill_rule_set_query():
    rs = _ruleset(
        _mod("A", {"type": "on_kill_effect", "target": "minion", "effect": "explode"}),
        _mod("B", {"type": "on_kill_effect", "target": "all", "effect": "heal_player"}),
        _mod("C", {"type": "on_kill_effect", "target": "boss", "effect": "explode"}),
    )
    minion = rs.on_kill_effects_for("minion")
    boss = rs.on_kill_effects_for("boss")
    return {
        "minion_gets_target_and_all": len(minion) == 2,
        "boss_gets_boss_and_all": len(boss) == 2,
        "elite_gets_only_all": len(rs.on_kill_effects_for("elite")) == 1,
        "errors": [],
    }


def test_on_kill_explode_hits_neighbors_only():
    from enemies.enemy import Enemy
    game = _make_game()
    _install_rules(game, _mod("Volatile", {
        "type": "on_kill_effect", "target": "minion", "effect": "explode",
        "damage_type": "fire", "radius": 80, "damage_ratio": 0.3}))

    area = game.current_area
    area.enemies.clear()
    victim = Enemy(500, 500, hp=100, damage=1)
    near = Enemy(540, 500, hp=5, damage=1)
    far = Enemy(900, 500, hp=5, damage=1)
    area.enemies.extend([victim, near, far])

    victim.take_damage(999)
    game._on_enemy_killed(victim)

    return {
        "victim_removed": victim not in area.enemies,
        "neighbor_killed": not near.alive,
        "far_enemy_untouched": far.alive and far.hp == 5,
        "errors": [],
    }


def test_on_kill_heal_player():
    from enemies.enemy import Enemy
    game = _make_game()
    _install_rules(game, _mod("Vampiric", {
        "type": "on_kill_effect", "target": "minion",
        "effect": "heal_player", "percent": 0.1}))

    game.current_area.enemies.clear()
    e = Enemy(500, 500, hp=10, damage=1)
    game.current_area.enemies.append(e)
    stats = game.player.effective_stats
    stats.hp = 10
    heal_expected = int(stats.max_hp * 0.1)

    e.take_damage(999)
    game._on_enemy_killed(e)

    return {"healed": stats.hp == min(stats.max_hp, 10 + heal_expected),
            "hp": stats.hp, "errors": []}


def test_on_kill_chain_is_depth_capped():
    from enemies.enemy import Enemy
    game = _make_game()
    _install_rules(game, _mod("Chain", {
        "type": "on_kill_effect", "target": "minion", "effect": "explode",
        "damage_type": "fire", "radius": 80, "damage_ratio": 1.0}))

    area = game.current_area
    area.enemies.clear()
    # Line of 1-hp-ish enemies, 50px apart: each explosion only reaches the next.
    line = [Enemy(500 + 50 * i, 500, hp=10, damage=1) for i in range(8)]
    area.enemies.extend(line)

    line[0].take_damage(999)
    game._on_enemy_killed(line[0])   # must not RecursionError

    dead = sum(1 for e in line if not e.alive)
    return {
        "chain_propagates": dead >= 3,
        "chain_is_capped": dead < len(line),
        "dead": dead,
        "errors": [],
    }


def test_apply_player_hit_ignores_dead_target():
    from enemies.enemy import Enemy
    game = _make_game()
    e = Enemy(500, 500, hp=10, damage=1)
    e.alive = False
    game.current_area.enemies.clear()
    ok = True
    try:
        game._apply_player_hit(e, game._active_world_rules(), 1.0)
    except Exception as ex:
        ok = f"{type(ex).__name__}: {ex}"
    return {"no_crash_or_double_kill": ok is True,
            "errors": [] if ok is True else [str(ok)]}


# ---------------------------------------------------------------------------
# 9. Bigger bugs
# ---------------------------------------------------------------------------

def test_equip_never_loses_item_when_inventory_full():
    from player.player import Player
    from items.item import Item

    p = Player(0, 0, _RecordingBus())
    a = Item("Sword A", "weapon", weapon_subtype="sword")
    b = Item("Sword B", "weapon", weapon_subtype="sword")
    p.inventory.add_item(a)
    p.equip(a)

    # Fill the inventory completely, with B inside it.
    p.inventory.add_item(b)
    n = 0
    while not p.inventory.is_full():
        p.inventory.add_item(Item(f"junk{n}", "ring"))
        n += 1

    ok = p.equip(b)
    return {
        "equip_succeeded": ok,
        "new_item_equipped": p.equipped["weapon"] is b,
        "old_item_not_lost": p.inventory.get_by_id(a.item_id) is a,
        "errors": [],
    }


def test_enemy_receives_world_rules():
    from enemies.enemy import Enemy
    rules = _ruleset(_mod("Thin Skin", {"type": "resistance_modifier", "target": "player",
                                        "damage_type": "physical", "delta": -0.5}))
    tm = _OpenTilemap()

    def hp_lost(world_rules):
        e = Enemy(100, 100, behavior="chaser", damage=20)
        p = _StubPlayer(110, 100)       # inside contact range
        e.update(1 / 60.0, p, tm, _RecordingBus(), world_rules=world_rules)
        return 100 - p.effective_stats.hp

    base, with_rules = hp_lost(None), hp_lost(rules)
    return {
        "update_accepts_world_rules": True,
        "rules_change_enemy_damage": with_rules > base,
        "base": base, "with_rules": with_rules,
        "errors": [],
    }


def test_boss_hits_emit_player_hit():
    from enemies.boss import Boss, BossTemplate
    bus = _RecordingBus()
    b = Boss(100, 100, BossTemplate.hollow_warden())
    p = _StubPlayer(120, 100)           # inside melee range (<90)
    b._pending_action = "melee"
    b._execute_attack(p, bus, b.current_phase, None)
    names = [n for n, _ in bus.events]
    return {"player_hit_emitted": "player_hit" in names, "events": names, "errors": []}


def test_load_game_does_not_leak_event_log_subscribers():
    from save.save_manager import SaveManager
    game = _make_game()
    tmp = tempfile.mkdtemp()
    game.save_manager = SaveManager(path=os.path.join(tmp, "s.json"),
                                    stash_path=os.path.join(tmp, "st.json"))
    game._save_game()

    before = len(game.event_bus._subscribers["item_found"])

    # Stale transient state that a load must reset.
    game._pending_extension_spec = object()
    game._boss_attempt_active = True
    game._load_game()
    game._load_game()

    after = len(game.event_bus._subscribers["item_found"])
    return {
        "no_new_subscribers_after_loads": after == before,
        "before": before, "after": after,
        "pending_reset": game._pending_extension_spec is None,
        "boss_attempt_reset": game._boss_attempt_active is False,
        "projectiles_reset": len(game.player_projectiles) == 0,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    tests = [
        # 1 tooltip
        test_tooltip_compare_flag,
        test_tooltip_vintage_uses_world_rules,
        # 2 game cleanup
        test_game_single_on_player_death,
        test_game_level_up_message_once,
        test_game_death_applies_xp_penalty,
        test_game_region_atmosphere_is_drawn,
        test_game_clears_projectiles_on_travel,
        # 3 damage
        test_damage_taken_rule_applies_once,
        test_damage_dealt_rule_scoped_by_source,
        test_damage_dealt_all_still_applies_once,
        test_damage_resolve_matches_breakdown,
        test_damage_callers_pass_source_category,
        # 4 items
        test_item_guarantee_enabling_affix,
        test_item_rarity_matches_actual_affixes,
        test_item_rare_multiplier_nudges_affix_totals,
        # 5 cleanup
        test_aggregator_item_stats_tuple,
        test_gate_module_clean,
        # 6 challenge pipeline
        test_procedural_challenge_always_paired,
        test_procedural_specs_pass_validators,
        test_loot_rules_guarantee_is_life,
        # 7 validators
        test_validator_accepts_valid_and_templates,
        test_validator_rejects_out_of_bounds,
        test_validator_never_raises_on_garbage,
        test_validator_tier_override_rules,
        test_validator_on_kill_effect_rules,
        test_boss_validator_bounds,
        test_boss_factory_templates_pass_validator,
        test_mock_generator_valid_output_passes,
        test_extension_generator_survives_validator_crash,
        # 8 on_kill_effect
        test_on_kill_rule_set_query,
        test_on_kill_explode_hits_neighbors_only,
        test_on_kill_heal_player,
        test_on_kill_chain_is_depth_capped,
        test_apply_player_hit_ignores_dead_target,
        # 9 bigger bugs
        test_equip_never_loses_item_when_inventory_full,
        test_enemy_receives_world_rules,
        test_boss_hits_emit_player_hit,
        test_load_game_does_not_leak_event_log_subscribers,
    ]
    failures = 0
    for fn in tests:
        r = _run(fn.__name__, fn)
        if r.get("errors"):
            failures += 1
    print(f"=== SUMMARY: {len(tests) - failures}/{len(tests)} tests clean ===")


if __name__ == "__main__":
    main()