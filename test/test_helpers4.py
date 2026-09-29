# -*- coding: utf-8 -*-
"""
TEST FILE 4 - Extreme Conditions & Bug-Hunting Suite
=====================================================
Run with either:
    pytest test_file_4.py -v          (recommended)
    python test_file_4.py             (standalone runner, no pytest needed)

This suite deliberately attacks the weakest seams of the codebase:
edge-case inputs, corrupt data, empty pools, extreme values, save/load
mismatch, determinism across processes, and integration paths that the
happy-path tests of files 1-3 never touch.

CONFIRMED BUGS THIS FILE DOCUMENTS (tests marked @bug_xfail fail against
current code and encode the CORRECT expected behavior):

  B1  core/rng.py            - RNGService uses builtin hash() on a str for
                              stream derivation -> PYTHONHASHSEED randomizes
                              it -> generation is NOT reproducible across
                              process runs (breaks save/load determinism).
  B2  combat/combat.py       - enemy_attack_player() computes damage from
                              PLAYER.base_damage (not enemy.damage), applies
                              ENEMY resistances (not player's), and calls
                              enemy.take_damage() -- the enemy damages ITSELF.
  B3  combat/combat.py       - player_attack_enemy() has NO damage_multiplier
                              parameter, but core/game.py:_apply_player_hit
                              passes damage_multiplier=... -> EVERY melee /
                              cone / projectile hit raises TypeError at
                              runtime. The game crashes on first attack.
  B4  combat/damage.py       - "resistance_modifier" rules are validated but
                              NEVER applied in the damage pipeline; the
                              "dealt" direction of apply_world_rules is never
                              used by any caller (Conductive Water does
                              nothing for player-dealt damage).
  B5  combat/damage.py       - apply_conversion() allows multiple conversion
                              rules to sum > 100% -> negative remainder is
                              silently dropped, DAMAGE IS INFLATED.
  B6  enemies/enemy.py       - spawn_basic_enemy() picks the name with the
                              module-global random, ignoring the seeded rng
                              -> enemy layout is nondeterministic per world.
  B7  ai/validators.py       - WorldModifierValidator crashes (AttributeError)
                              on non-dict rule entries; WorldValidator crashes
                              (TypeError) when world_modifiers is None.
  B8  items/item.py          - Item.from_dict() never advances the global id
                              counter -> a loaded item's id can collide with
                              the next generated item's id (inventory corruption).
  B9  save/save_manager.py   - No corruption handling: malformed savegame.json
                              or stash.json crashes json.load() (whole game).
  B10 world/map_generator.py - With few/small rooms, gate_room falls back to
                              the SAME room as start_room or boss_room
                              (overlapping gate/boss/spawn in one room).
  B11 world/map_generator.py - The whole enemies/boss.py file is copy-pasted
                              into map_generator.py; the copy is missing the
                              `cause=` param on take_damage (divergent dead code).
  B12 ai/procedural_generator- dict(get_modifier_template(id)) copies the dict
                              but SHARES the 'rules' list -> mutating a
                              generated spec corrupts the global template cache.
  B13 items/item_generator.py- Rarity is computed from REQUESTED affix counts,
                              even if the affix pool was exhausted and fewer
                              affixes were actually rolled (fake-rare items).
  B14 items/inventory.py     - from_dict() accepts two cells pointing at the
                              same item_id; removing the item leaves a
                              dangling grid cell -> KeyError on .items.
"""

import os
import sys
import json
import random
import subprocess
import tempfile
import time
from collections import Counter, deque

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")



try:
    import pygame
    PYGAME_OK = True
except Exception:
    pygame = None
    PYGAME_OK = False

try:
    import pytest
    _HAS_PYTEST = True
except Exception:
    pytest = None
    _HAS_PYTEST = False

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

if PYGAME_OK:
    pygame.font.init()

# --------------------------------------------------------------------------
# Markers usable by BOTH pytest and the standalone runner
# --------------------------------------------------------------------------
def bug_xfail(reason):
    def deco(fn):
        fn._xfail = reason
        if _HAS_PYTEST:
            return pytest.mark.xfail(reason=reason, strict=False)(fn)
        return fn
    return deco

def needs_pygame(fn):
    fn._needs_pygame = True
    if _HAS_PYTEST:
        return pytest.mark.skipif(not PYGAME_OK, reason="pygame required")(fn)
    return fn

# --------------------------------------------------------------------------
# Non-pygame imports (pure logic)
# --------------------------------------------------------------------------
from core.rng import RNGService
from core.events import EventBus
from combat.damage import (DamageInstance, ConversionRule, apply_conversion,
                           apply_resistance, apply_world_rules, resolve_damage,
                           resolve_damage_breakdown)
from combat.damage_types import DamageTypes
from ai.validators import (WorldValidator, WorldModifierValidator, ItemValidator,
                           EnemyValidator, BossValidator, ProgressionValidator)
from world.extension import ExtensionSpec
from world.world_modifiers import WorldModifier, WorldRuleSet, SUPPORTED_RULE_TYPES
from world.loot_rules import LootRules
from world.gate import Gate, next_gate_id, reset_gate_id_counter
from items.affix import RolledAffix
from items.item import Item
from items.inventory import Inventory
from items.stash import Stash
from items.modifiers import ModifierPools
from items.tiers import (get_tier_table, resolve_tier_for_roll,
                         roll_value_for_tier, get_tier_unlock_levels)
from items.comparison import compare_items, format_diff_line
from items.item_generator import generate_random_item
from ai.personalization import derive_hints
from ai.prompt_builder import PromptBuilder
from history.player_profile import PlayerProfile
from history.event_log import EventLog, HistoryEvent
from history.aggregator import build_player_profile
from history.exploration import ExplorationTracker
from ai.procedural_generator import ProceduralWorldGenerator
from ai.mock_generator import MockAIWorldGenerator
from ai.world_generator import WorldGenerator
from world.extension_generator import WorldExtensionGenerator
from save.save_manager import SaveManager
import core.config as config

if PYGAME_OK:
    from player.player import Player
    from player.stats import Stats, compute_effective_stats
    from player.progression import Progression, xp_required_for_level
    from enemies.enemy import Enemy, spawn_basic_enemy
    from enemies.boss import Boss, BossTemplate, HollowWarden
    from enemies.boss_factory import build_personalized_boss_template
    from combat.combat import player_attack_enemy, enemy_attack_player
    from combat.attack_patterns import (find_arc_targets, spawn_projectile,
                                        resolve_attack_profile,
                                        DEFAULT_UNARMED_PROFILE)
    from world.tilemap import TileMap
    from world.map_generator import build_procedural_area, generate_dungeon_grid
    from world.level import build_level, AreaInstance, Level
    from world.world import World
    from world.extension import Extension
    from ui.tooltip import ItemTooltip
    from ui.gateway_menu import GatewayMenu
    from ui.world_evolution import WorldEvolutionBanner
    from ui.profile_screen import ProfileScreen
    from ui.hud import HUD, InventoryUI
    from core.game import Game


# ==========================================================================
# HELPERS
# ==========================================================================
def make_spec(**kw):
    defaults = dict(extension_id="ext_test", extension_name="Test Region",
                    area_level=10, biome="test_biome", is_anomaly=False,
                    world_modifiers=[], boss_template_id=None, room_count=6)
    defaults.update(kw)
    return ExtensionSpec(**defaults)

def make_modifier(rules, name="Test Modifier", description="d"):
    return {"name": name, "description": description, "rules": rules}

def rule_mult(target="minion", dtype="fire", mult=2.0, rtype="damage_taken_multiplier"):
    return {"type": rtype, "target": target, "damage_type": dtype, "multiplier": mult}

def rule_conv(frm="physical", to="chaos", pct=0.5, scope="player"):
    return {"type": "conversion", "from_type": frm, "to_type": to,
            "percent": pct, "scope": scope}

def make_profile(**kw):
    p = PlayerProfile()
    for k, v in kw.items():
        setattr(p, k, v)
    return p

def flood_reachable(grid, start):
    rows, cols = len(grid), len(grid[0])
    seen = {start}
    q = deque([start])
    while q:
        c, r = q.popleft()
        for dc, dr in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nc, nr = c + dc, r + dr
            if 0 <= nr < rows and 0 <= nc < cols and grid[nr][nc] == 0 and (nc, nr) not in seen:
                seen.add((nc, nr))
                q.append((nc, nr))
    return seen

def room_center_int(room):
    x0, y0, x1, y1 = room
    return ((x0 + x1) // 2, (y0 + y1) // 2)


# ==========================================================================
# 1. RNG DETERMINISM  (B1)
# ==========================================================================
class TestRNGDeterminism:
    def test_same_seed_same_stream_within_process(self):
        a = RNGService(999).get_stream("loot")
        b = RNGService(999).get_stream("loot")
        assert [a.random() for _ in range(10)] == [b.random() for _ in range(10)]

    def test_derived_child_deterministic_within_process(self):
        a = RNGService(7).derive_child("world", "extension_1")
        b = RNGService(7).derive_child("world", "extension_1")
        assert [a.randint(0, 10**6) for _ in range(20)] == [b.randint(0, 10**6) for _ in range(20)]

    def test_different_names_different_streams(self):
        r = RNGService(5)
        assert r.get_stream("a").random() != r.get_stream("b").random()

    @bug_xfail("B1: builtin hash() on str is randomized per process (PYTHONHASHSEED)")
    def test_same_seed_reproducible_across_processes(self):
        code = ("import sys; sys.path.insert(0, %r);"
                "from core.rng import RNGService;"
                "r = RNGService(12345);"
                "print(r.get_stream('loot').random())") % PROJECT_ROOT
        def run(seed):
            env = dict(os.environ)
            env["PYTHONHASHSEED"] = str(seed)
            out = subprocess.run([sys.executable, "-c", code], env=env,
                                 capture_output=True, text=True)
            assert out.returncode == 0, out.stderr
            return out.stdout.strip()
        assert run(0) == run(12345) == run(99999)


# ==========================================================================
# 2. COMBAT PIPELINE  (B2, B3, B4, B5)
# ==========================================================================
@needs_pygame
class TestCombatPipeline:
    def _mk(self):
        bus = EventBus()
        p = Player(0, 0, bus)
        e = Enemy(10, 0, hp=10**7, damage=20, armor=0, name="Dummy")
        return bus, p, e

    # ---------------- player_attack_enemy ----------------
    def test_player_attack_basic_damages_enemy(self):
        bus, p, e = self._mk()
        hp0 = e.hp
        player_attack_enemy(p, e, bus, world_rules=None)
        assert e.hp < hp0

    @bug_xfail("B3: game.py passes damage_multiplier= to player_attack_enemy -> TypeError")
    def test_player_attack_accepts_damage_multiplier_keyword(self):
        bus, p, e = self._mk()
        player_attack_enemy(p, e, bus, world_rules=None, damage_multiplier=2.0)

    @bug_xfail("B3: damage_multiplier is accepted but ignored by player_attack_enemy")
    def test_player_attack_multiplier_scales_damage(self):
        bus, p, e1 = self._mk()
        bus2, p2, e2 = self._mk()
        player_attack_enemy(p, e1, bus, world_rules=None)
        player_attack_enemy(p2, e2, bus2, world_rules=None, damage_multiplier=2.0)
        d1 = 10**7 - e1.hp
        d2 = 10**7 - e2.hp
        assert abs(d2 - 2 * d1) <= 2

    def test_player_attack_emits_events(self):
        bus, p, e = self._mk()
        types_seen = []
        bus.subscribe("skill_used", lambda **kw: types_seen.append("skill"))
        bus.subscribe("player_attack", lambda **kw: types_seen.append("attack"))
        player_attack_enemy(p, e, bus, world_rules=None)
        assert "skill" in types_seen and "attack" in types_seen

    def test_player_attack_respects_enemy_armor(self):
        bus = EventBus()
        p = Player(0, 0, bus)
        e_soft = Enemy(10, 0, hp=10**7, damage=1, armor=0)
        e_hard = Enemy(10, 0, hp=10**7, damage=1, armor=10000)
        player_attack_enemy(p, e_soft, bus, world_rules=None)
        player_attack_enemy(p, e_hard, EventBus(), world_rules=None)
        assert (10**7 - e_soft.hp) > (10**7 - e_hard.hp)

    # ---------------- enemy_attack_player (B2) ----------------
    @bug_xfail("B2: enemy_attack_player damages the ENEMY (itself) on its own attack")
    def test_enemy_attack_does_not_damage_enemy(self):
        bus, p, e = self._mk()
        hp0 = e.hp
        enemy_attack_player(e, p, bus, world_rules=None)
        assert e.hp == hp0

    @bug_xfail("B2: damage is computed from PLAYER stats instead of enemy.damage")
    def test_enemy_attack_uses_enemy_damage_stat(self):
        bus = EventBus()
        p = Player(0, 0, bus)
        p.base_stats.base_damage = 0
        p.recalc_stats()
        e = Enemy(10, 0, hp=10**7, damage=25, armor=0, name="Brute")
        hp0 = p.effective_stats.hp
        enemy_attack_player(e, p, bus, world_rules=None)
        taken = hp0 - p.effective_stats.hp
        assert taken >= 15  # should scale from enemy.damage=25, not max(1, 0)

    @bug_xfail("B2: player resistances are ignored; enemy's own resistances are used instead")
    def test_enemy_attack_respects_player_resistances(self):
        bus = EventBus()
        p_weak = Player(0, 0, bus)
        p_tough = Player(0, 0, bus)
        p_tough.effective_stats.resistances["fire"] = 0.75
        e = Enemy(10, 0, hp=10**7, damage=30, damage_type="fire", armor=0)
        h1, h2 = p_weak.effective_stats.hp, p_tough.effective_stats.hp
        enemy_attack_player(e, p_weak, bus, world_rules=None)
        enemy_attack_player(e, p_tough, EventBus(), world_rules=None)
        assert (h1 - p_weak.effective_stats.hp) > (h2 - p_tough.effective_stats.hp)

    @bug_xfail("B2: incoming damage is categorized by the ATTACKER's category, so "
               "'target: player' world rules never apply to damage the player takes")
    def test_world_rule_targeting_player_applies_to_incoming_damage(self):
        bus = EventBus()
        p = Player(0, 0, bus)
        wrs = WorldRuleSet([WorldModifier.from_dict(make_modifier(
            [rule_mult(target="player", dtype="fire", mult=2.0)]))])
        e = Enemy(10, 0, hp=10**7, damage=20, damage_type="fire", armor=0)
        h0 = p.effective_stats.hp
        enemy_attack_player(e, p, bus, world_rules=wrs)
        taken = h0 - p.effective_stats.hp
        bus2, p2 = EventBus(), Player(0, 0, EventBus())
        e2 = Enemy(10, 0, hp=10**7, damage=20, damage_type="fire", armor=0)
        h1 = p2.effective_stats.hp
        enemy_attack_player(e2, p2, bus2, world_rules=None)
        base = h1 - p2.effective_stats.hp
        assert taken > base * 1.5

    @bug_xfail("B4: 'dealt' direction is never used by any caller -> dealt-multiplier rules dead")
    def test_world_rule_dealt_multiplier_applies_to_outgoing_damage(self):
        bus = EventBus()
        p = Player(0, 0, bus)
        p.effective_stats.primary_damage_type = "lightning"
        wrs = WorldRuleSet([WorldModifier.from_dict(make_modifier(
            [rule_mult(target="all", dtype="lightning", mult=3.0,
                       rtype="damage_dealt_multiplier")]))])
        e1 = Enemy(10, 0, hp=10**7, damage=1, armor=0)
        e2 = Enemy(10, 0, hp=10**7, damage=1, armor=0)
        player_attack_enemy(p, e1, bus, world_rules=wrs)
        player_attack_enemy(p, e2, EventBus(), world_rules=None)
        d_mod = 10**7 - e1.hp
        d_base = 10**7 - e2.hp
        assert d_mod > d_base * 2

    # ---------------- damage.py unit edges ----------------
    @bug_xfail("B4: resistance_modifier rules are validated but never consumed anywhere")
    def test_resistance_modifier_rule_changes_outcome(self):
        wrs = WorldRuleSet([WorldModifier.from_dict(make_modifier(
            [{"type": "resistance_modifier", "target": "minion",
              "damage_type": "physical", "delta": -0.5}]))])
        dmg_plain = resolve_damage(100, "physical", None,
                                   target_resistances={"armor_flat": 0},
                                   world_rules=None, target_category="minion")
        dmg_mod = resolve_damage(100, "physical", None,
                                 target_resistances={"armor_flat": 0},
                                 world_rules=wrs, target_category="minion")
        assert dmg_mod > dmg_plain

    @bug_xfail("B5: conversion rules summing >100% produce negative remainder that is "
               "silently dropped, INFLATING total damage")
    def test_conversion_over_100_percent_never_inflates(self):
        rules = [ConversionRule("physical", "fire", 0.6),
                 ConversionRule("physical", "cold", 0.6)]
        out = apply_conversion([DamageInstance(100, "physical")], rules)
        total = sum(i.amount for i in out)
        assert 0 < total <= 100
        assert all(i.amount >= 0 for i in out)

    def test_conversion_exact_100_percent(self):
        out = apply_conversion([DamageInstance(100, "physical")],
                               [ConversionRule("physical", "chaos", 1.0)])
        assert len(out) == 1 and out[0].damage_type == "chaos" and out[0].amount == 100

    def test_resolve_damage_never_below_one(self):
        assert resolve_damage(1, "physical", None, target_armor=10**9) >= 1

    def test_resistance_clamped_high(self):
        out = apply_resistance([DamageInstance(100, "fire")], {"fire": 0.95})
        assert out[0].amount == 25  # clamped to 0.75 -> 25% damage

    def test_resistance_clamped_low_amplifies(self):
        out = apply_resistance([DamageInstance(100, "fire")], {"fire": -5.0})
        assert out[0].amount == 200  # clamped to -1.0 -> double damage

    def test_world_rules_multiply_across_modifiers(self):
        wrs = WorldRuleSet([WorldModifier.from_dict(make_modifier([rule_mult(mult=2.0)])),
                            WorldModifier.from_dict(make_modifier([rule_mult(mult=3.0)]))])
        assert wrs.damage_multiplier_for("minion", "fire", "taken") == 6.0

    def test_world_rules_none_is_noop(self):
        out = apply_world_rules([DamageInstance(50, "fire")], None, "minion")
        assert out[0].amount == 50

    def test_breakdown_matches_total(self):
        total, br = resolve_damage_breakdown(100, "physical", None,
                                             target_resistances={"armor_flat": 0})
        assert total == max(1, int(round(sum(br.values()))))


# ==========================================================================
# 3. VALIDATORS UNDER HOSTILE INPUT  (B7)
# ==========================================================================
class TestValidatorHostileInput:
    def test_valid_spec_passes(self):
        ok, errs = WorldValidator.validate(make_spec(), set(), player_level=10)
        assert ok and errs == []

    def test_duplicate_extension_id_rejected(self):
        ok, errs = WorldValidator.validate(make_spec(), {"ext_test"}, player_level=10)
        assert not ok and any("already exists" in e for e in errs)

    @bug_xfail("B7: validator crashes (AttributeError) on non-dict rule entries")
    def test_non_dict_rule_entry_returns_invalid_not_crash(self):
        mod = make_modifier([rule_mult(), "corrupted_garbage", 42, None])
        ok, errs = WorldModifierValidator.validate(mod)
        assert ok is False and errs

    @bug_xfail("B7: validator crashes (TypeError) when world_modifiers is None")
    def test_none_world_modifiers_handled(self):
        spec = make_spec(world_modifiers=None)
        ok, errs = WorldValidator.validate(spec, set(), player_level=10)
        assert ok is False and errs

    def test_empty_id_and_name_rejected(self):
        ok, _ = WorldValidator.validate(make_spec(extension_id=""), set())
        assert not ok
        ok, _ = WorldValidator.validate(make_spec(extension_name=""), set())
        assert not ok

    def test_area_level_type_and_bounds(self):
        for bad in [0, -5, "10", 10.5, None, 10**6]:
            ok, _ = WorldValidator.validate(make_spec(area_level=bad), set())
            assert not ok, f"area_level={bad!r} should be rejected"

    def test_anomaly_jump_cap(self):
        ok, _ = WorldValidator.validate(make_spec(is_anomaly=True, area_level=10 + 60),
                                        set(), player_level=10)
        assert ok
        ok, _ = WorldValidator.validate(make_spec(is_anomaly=True, area_level=10 + 61),
                                        set(), player_level=10)
        assert not ok

    def test_room_count_bounds(self):
        for bad in [0, 1, 21, 10**6]:
            ok, _ = WorldValidator.validate(make_spec(room_count=bad), set())
            assert not ok, f"room_count={bad} should be rejected"
        ok, _ = WorldValidator.validate(make_spec(room_count=2), set())
        assert ok
        ok, _ = WorldValidator.validate(make_spec(room_count=20), set())
        assert ok

    def test_multiplier_bounds(self):
        for bad in [-0.1, 10.1, "2", None]:
            ok, errs = WorldModifierValidator.validate(
                make_modifier([rule_mult(mult=bad)]))
            assert not ok, f"multiplier={bad!r} should be rejected"
        for good in [0, 10]:
            ok, _ = WorldModifierValidator.validate(make_modifier([rule_mult(mult=good)]))
            assert ok

    def test_damage_type_enum(self):
        ok, _ = WorldModifierValidator.validate(
            make_modifier([rule_mult(dtype="holy_radiance")]))
        assert not ok
        ok, _ = WorldModifierValidator.validate(make_modifier([rule_mult(dtype=None)]))
        assert ok  # null = all types

    def test_conversion_missing_fields_and_bounds(self):
        ok, _ = WorldModifierValidator.validate(make_modifier(
            [{"type": "conversion", "from_type": "fire"}]))
        assert not ok
        ok, _ = WorldModifierValidator.validate(make_modifier(
            [rule_conv(pct=1.5)]))
        assert not ok
        ok, _ = WorldModifierValidator.validate(make_modifier(
            [rule_conv(frm="nope", to="fire")]))
        assert not ok

    def test_tier_override_validation(self):
        bad = {"type": "tier_override", "modifier": "fire_damage",
               "tiers": {"T1": [50, 10]}}  # min > max
        ok, _ = WorldModifierValidator.validate(make_modifier([bad]))
        assert not ok
        bad2 = {"type": "tier_override", "modifier": "fire_damage",
                "tiers": {"T1": [-5, 10]}}
        ok, _ = WorldModifierValidator.validate(make_modifier([bad2]))
        assert not ok
        ok, _ = WorldModifierValidator.validate(make_modifier(
            [{"type": "tier_override", "modifier": "fire_damage", "tiers": {}}]))
        assert not ok

    def test_resistance_modifier_bounds(self):
        ok, _ = WorldModifierValidator.validate(make_modifier(
            [{"type": "resistance_modifier", "target": "minion",
              "damage_type": "fire", "delta": 1.5}]))
        assert not ok
        ok, _ = WorldModifierValidator.validate(make_modifier(
            [{"type": "resistance_modifier", "target": "minion",
              "damage_type": "nope", "delta": 0.1}]))
        assert not ok

    def test_unsupported_rule_type_rejected(self):
        ok, errs = WorldModifierValidator.validate(make_modifier(
            [{"type": "spawn_nukes_everywhere"}]))
        assert not ok and any("unsupported type" in e for e in errs)

    def test_progression_validator_gap(self):
        ok, _ = ProgressionValidator.validate(make_spec(area_level=21), player_level=10)
        assert not ok
        ok, _ = ProgressionValidator.validate(make_spec(area_level=20), player_level=10)
        assert ok
        ok, _ = ProgressionValidator.validate(
            make_spec(area_level=500, is_anomaly=True), player_level=10)
        assert ok  # anomalies exempt

    def test_item_validator(self):
        ok, _ = ItemValidator.validate({"slot": "weapon",
                                        "prefixes": [{"affix_id": "x", "value": 5}],
                                        "suffixes": []})
        assert ok
        ok, _ = ItemValidator.validate({"slot": "pants", "prefixes": [], "suffixes": []})
        assert not ok
        ok, _ = ItemValidator.validate({"slot": "weapon",
                                        "prefixes": [{"affix_id": "x", "value": 10**9}],
                                        "suffixes": []})
        assert not ok

    def test_enemy_validator(self):
        assert EnemyValidator.validate({"hp": 100, "damage": 10})[0]
        assert not EnemyValidator.validate({"hp": 0, "damage": 10})[0]
        assert not EnemyValidator.validate({"hp": 10**12, "damage": 10})[0]
        assert not EnemyValidator.validate({"hp": 100, "damage": 10**9})[0]

    def test_boss_validator(self):
        assert BossValidator.validate({"max_hp": 1000, "phases": [1]})[0]
        assert not BossValidator.validate({"max_hp": 50001, "phases": [1]})[0]
        assert not BossValidator.validate({"max_hp": 1000, "phases": []})[0]
        assert not BossValidator.validate({"max_hp": 1000,
                                           "phases": list(range(7))})[0]


# ==========================================================================
# 4. GENERATOR PIPELINE (backends, fallback, stress)
# ==========================================================================
class TestGeneratorPipeline:
    def test_procedural_output_always_valid(self):
        gen = ProceduralWorldGenerator()
        for seed in range(50):
            rng = random.Random(seed)
            profile = make_profile(level=rng.randint(1, 500),
                                   main_damage=rng.choice(DamageTypes.all()),
                                   secondary_damage=rng.choice([None] + DamageTypes.all()),
                                   defensive_strength=rng.choice(["low", "medium", "high"]),
                                   deaths={"fire": 5} if seed % 3 == 0 else {},
                                   boss_attempts=seed % 10, boss_successes=seed % 4,
                                   average_exploration=rng.random(),
                                   extensions_generated=seed,
                                   items_discarded=seed % 20, items_kept=seed % 9,
                                   six_mod_items_found=seed % 2)
            spec = gen.generate_next_extension(profile, set(), rng)
            assert spec is not None
            ok, errs = WorldValidator.validate(spec, set(), profile.level)
            assert ok, errs
            ok, errs = ProgressionValidator.validate(spec, profile.level)
            assert ok, errs
            assert 6 <= spec.room_count <= 9 or spec.is_anomaly
            assert isinstance(spec.loot_rules, dict)
            assert "mechanic_archetype" in spec.boss_personalization

    def test_procedural_ids_unique_per_call(self):
        gen = ProceduralWorldGenerator()
        rng = random.Random(1)
        ids = set()
        for i in range(30):
            spec = gen.generate_next_extension(make_profile(level=i + 1), ids, rng)
            assert spec.extension_id not in ids
            ids.add(spec.extension_id)

    def test_generator_backend_exception_falls_back(self):
        class ExplodingBackend(WorldGenerator):
            def generate_next_extension(self, profile, ids, rng):
                raise RuntimeError("LLM on fire")
        gen = WorldExtensionGenerator(backend=ExplodingBackend(),
                                      fallback_backend=ProceduralWorldGenerator())
        spec = gen.generate(make_profile(level=5), set(), random.Random(1))
        ok, _ = WorldValidator.validate(spec, set(), 5)
        assert ok

    def test_generator_invalid_output_falls_back(self):
        class BadBackend(WorldGenerator):
            def generate_next_extension(self, profile, ids, rng):
                return make_spec(area_level=10**9)  # absurd level
        gen = WorldExtensionGenerator(backend=BadBackend(),
                                      fallback_backend=ProceduralWorldGenerator())
        spec = gen.generate(make_profile(level=5), set(), random.Random(1))
        assert spec.area_level <= 500

    def test_generator_all_backends_dead_uses_safe_spec(self):
        class BadBackend(WorldGenerator):
            def generate_next_extension(self, profile, ids, rng):
                return make_spec(area_level=-1)
        gen = WorldExtensionGenerator(backend=BadBackend(), fallback_backend=BadBackend())
        spec = gen.generate(make_profile(level=5), set(), random.Random(1))
        ok, errs = WorldValidator.validate(spec, set(), 5)
        assert ok, errs  # hardcoded fallback must be safe

    def test_mock_stress_never_raises(self):
        mock = MockAIWorldGenerator(failure_rate=0.9, verbose=False)
        rng = random.Random(3)
        for i in range(300):
            spec = mock.generate_next_extension(make_profile(level=1 + i % 60),
                                                set(), rng)
            # may be None (broken) or a spec; never an exception

    def test_llm_unavailable_returns_none(self):
        from ai.llm_generator import LLMWorldGenerator
        gen = LLMWorldGenerator(provider="groq", api_key=None, verbose=False)
        assert not gen.is_available()
        assert gen.generate_next_extension(make_profile(), set(), random.Random(1)) is None

    def test_prompt_builder_contains_profile_and_schema(self):
        prompt = PromptBuilder.build_extension_prompt(make_profile(level=12), {"a", "b"})
        assert '"level": 12' in prompt
        assert "world_modifiers" in prompt
        assert "a, b" in prompt


# ==========================================================================
# 5. ITEMS - EXTREME CONDITIONS (B8, B13, B14)
# ==========================================================================
class TestItemSystem:
    def test_legacy_flat_item_preserved(self):
        it = Item("Old Sword", "weapon", damage_bonus=15, armor_bonus=0)
        d = it.to_dict()
        d["prefixes"] = []
        d["implicit"] = None
        loaded = Item.from_dict(d)
        assert loaded.damage_bonus == 15  # never break old items

    def test_affix_recompute_sums(self):
        it = Item("Wand", "weapon")
        it.prefixes = [
            RolledAffix("prefix", "burning", "Burning", "fire_damage", "fire", "T1", 80, [70, 90]),
            RolledAffix("prefix", "vital", "Vital", "life", None, "T1", 90, [80, 100]),
        ]
        it.suffixes = [
            RolledAffix("suffix", "of_storms", "of Storms", "resist_lightning", None, "T1", 35, [30, 40]),
            RolledAffix("suffix", "of_haste", "of Haste", "attack_speed", None, "T2", 9, [8, 11]),
        ]
        it.recompute_flat_bonuses()
        assert it.damage_bonus == 80
        assert it.other_bonus("life") == 90
        assert it.resist_bonus("lightning") == 35
        assert it.other_bonus("attack_speed") == 9

    def test_rolled_affix_survives_tier_table_change(self):
        """Legacy mechanic: rolled value immutable even if world tier tables mutate."""
        wrs = WorldRuleSet([WorldModifier.from_dict(make_modifier(
            [{"type": "tier_override", "modifier": "fire_damage", "tiers": {"T1": [500, 600]}}]))])
        rolled = RolledAffix("prefix", "b", "B", "fire_damage", "fire", "T1", 75, [70, 90])
        assert rolled.value == 75  # unchanged despite override table
        new_table = get_tier_table(wrs)
        assert new_table["fire_damage"]["T1"] == [500, 600]

    def test_primary_damage_type_detection(self):
        it = Item("Wand", "weapon")
        it.implicit = RolledAffix("implicit", "i", "", "fire_damage", "fire", "T1", 10, [0, 0])
        assert it.primary_damage_type() == "fire"
        it2 = Item("Stick", "weapon")
        assert it2.primary_damage_type() == "physical"

    def test_six_mod_detection(self):
        it = Item("X", "weapon")
        it.prefixes = [RolledAffix("p", str(i), "", "life", None, "T1", 1, [0, 0]) for i in range(3)]
        it.suffixes = [RolledAffix("s", str(i), "", "armor", None, "T1", 1, [0, 0]) for i in range(3)]
        assert it.is_six_mod()
        assert it.affix_count_label() == "3P / 3S"

    def test_generate_weapon_has_subtype_and_implicit(self):
        for seed in range(20):
            it = generate_random_item(random.Random(seed), area_level=20, force_slot="weapon")
            assert it.slot == "weapon"
            assert it.weapon_subtype in ("sword", "greatsword", "bow", "short_wand", "long_wand")
            assert it.implicit is not None
            assert it.item_level >= 1

    def test_generate_jewelry_has_no_implicit(self):
        for seed in range(20):
            for slot in ("ring", "amulet"):
                it = generate_random_item(random.Random(seed), area_level=20, force_slot=slot)
                assert it.slot == slot
                assert it.implicit is None  # pure stat-stick category

    def test_generate_extreme_area_levels(self):
        for alvl in [0, 1, 500, 10**6]:
            it = generate_random_item(random.Random(1), area_level=alvl)
            assert it.item_level >= 1

    def test_force_affix_counts_respected_when_pool_big_enough(self):
        it = generate_random_item(random.Random(5), area_level=60, force_slot="weapon",
                                  force_affix_counts=(3, 3))
        assert len(it.prefixes) == 3 and len(it.suffixes) == 3
        assert it.is_six_mod()

    @bug_xfail("B13: rarity computed from REQUESTED counts; pool exhaustion yields a "
               "'rare' item with fewer actual affixes")
    def test_pool_exhaustion_does_not_produce_fake_rare(self):
        orig = ModifierPools._prefixes
        ModifierPools._prefixes = [orig[0]]  # shrink pool to 1 entry
        try:
            it = generate_random_item(random.Random(1), area_level=60, force_slot="weapon",
                                      force_affix_counts=(3, 2))
        finally:
            ModifierPools._prefixes = orig
        actual = len(it.prefixes) + len(it.suffixes)
        if actual <= 2:
            assert it.rarity != Item.RARITY_RARE

    def test_affix_count_brackets_edges(self):
        for ilvl in [0, 1, 9, 10, 24, 25, 49, 50, 999, 10**9]:
            np_, ns = ModifierPools.roll_affix_counts(ilvl, random.Random(ilvl))
            assert 1 <= np_ <= 3 and 1 <= ns <= 3

    def test_tier_resolution_extreme_ilvl(self):
        rng = random.Random(1)
        for _ in range(200):
            t = resolve_tier_for_roll("fire_damage", 10**6, rng)
            assert t in ("T1", "T2", "T3", "T4", "T5")
            lo, hi = get_tier_table()["fire_damage"][t]
            v = roll_value_for_tier("fire_damage", t, rng)
            assert lo <= v <= hi

    def test_tier_override_applies_during_generation(self):
        wrs = WorldRuleSet([WorldModifier.from_dict(make_modifier(
            [{"type": "tier_override", "modifier": "fire_damage", "tiers": {"T1": [90, 115]}}]))])
        rng = random.Random(2)
        saw_boosted = False
        for _ in range(60):
            a = _roll_fire_affix(rng, 100, wrs)
            if a is not None:
                assert a.value <= 115.0  # never above overridden max
                if a.value >= 90:
                    saw_boosted = True
        assert saw_boosted

    def test_unknown_modifier_raises_clear_error(self):
        try:
            resolve_tier_for_roll("not_a_modifier", 10, random.Random(1))
            assert False, "should raise"
        except ValueError:
            pass

    @bug_xfail("B8: ...")
    def test_loaded_item_id_never_collides_with_generated(self):
        from items import item as item_mod
        # Probe the counter safely regardless of representation.
        probe = Item("probe", "weapon")
        reserved_id = probe.item_id
        loaded = Item.from_dict({"item_id": reserved_id,
                                "base_name": "Saved Sword", "slot": "weapon"})
        fresh = Item("Fresh Sword", "weapon")
        inv = Inventory()
        inv.add_item(loaded)
        assert inv.add_item(fresh)
        assert len({i.item_id for i in inv.items}) == 2

    def test_comparison_diffs(self):
        cand = Item("A", "weapon", damage_bonus=10)
        eq = Item("B", "weapon", damage_bonus=4)
        diffs = compare_items(cand, eq)
        dmg = [d for d in diffs if d["stat"] == "damage"]
        assert len(dmg) == 1 and dmg[0]["delta"] == 6 and dmg[0]["is_positive"]
        assert format_diff_line(dmg[0]).startswith("+")

    def test_comparison_against_empty_slot(self):
        diffs = compare_items(Item("A", "weapon", damage_bonus=7), None)
        assert all(d["is_positive"] for d in diffs)


def _roll_fire_affix(rng, ilvl, world_rules):
    from items.weapon_subtypes import WeaponSubtypes
    it = generate_random_item(rng, area_level=ilvl, force_slot="weapon",
                              world_context={"world_rules": world_rules})
    for a in it.prefixes + it.suffixes:
        if a.modifier == "fire_damage":
            return a
    return None


# ==========================================================================
# 6. INVENTORY / STASH (B14)
# ==========================================================================
class TestInventoryGrid:
    def _item(self, name="X"):
        return Item(name, "weapon")

    def test_add_until_full_then_fail(self):
        inv = Inventory(cols=2, rows=1)
        assert inv.add_item(self._item("a"))
        assert inv.add_item(self._item("b"))
        assert not inv.add_item(self._item("c"))
        assert inv.is_full()

    def test_explicit_cell_rules(self):
        inv = Inventory(cols=2, rows=2)
        it = self._item()
        assert inv.add_item(it, cell=(1, 1))
        assert not inv.add_item(self._item(), cell=(1, 1))     # occupied
        assert not inv.add_item(self._item(), cell=(9, 9))     # out of bounds
        assert inv.get_at((1, 1)) is it

    def test_move_swap(self):
        inv = Inventory(cols=2, rows=1)
        a, b = self._item("a"), self._item("b")
        inv.add_item(a, cell=(0, 0))
        inv.add_item(b, cell=(1, 0))
        assert inv.move_item((0, 0), (1, 0))
        assert inv.get_at((0, 0)) is b and inv.get_at((1, 0)) is a

    def test_move_same_cell_stable(self):
        inv = Inventory(cols=2, rows=1)
        a = self._item()
        inv.add_item(a, cell=(0, 0))
        inv.move_item((0, 0), (0, 0))
        assert inv.get_at((0, 0)) is a and len(inv.items) == 1

    def test_move_from_empty_fails(self):
        inv = Inventory(cols=2, rows=1)
        assert not inv.move_item((0, 0), (1, 0))

    def test_remove_then_readd(self):
        inv = Inventory(cols=1, rows=1)
        it = self._item()
        inv.add_item(it)
        inv.remove_item(it)
        assert inv.items == [] and inv.find_free_cell() == (0, 0)

    def test_reading_order_stable(self):
        inv = Inventory(cols=3, rows=1)
        items = [self._item(str(i)) for i in range(3)]
        inv.add_item(items[2], cell=(2, 0))
        inv.add_item(items[0], cell=(0, 0))
        inv.add_item(items[1], cell=(1, 0))
        assert inv.items == items

    def test_serialization_roundtrip(self):
        inv = Inventory(cols=4, rows=3)
        inv.add_item(self._item("a"), cell=(3, 2))
        inv.add_item(self._item("b"), cell=(0, 0))
        inv2 = Inventory.from_dict(inv.to_dict())
        assert inv2.items[0].base_name == "b" and inv2.items[1].base_name == "a"

    @bug_xfail("B14: duplicate item_id across cells leaves a dangling cell -> "
               "KeyError on .items after removal")
    def test_duplicate_item_id_cells_do_not_corrupt(self):
        d = self._item("dup").to_dict()
        payload = {"cols": 2, "rows": 1,
                   "cells": [{"col": 0, "row": 0, "item": dict(d)},
                             {"col": 1, "row": 0, "item": dict(d)}]}
        inv = Inventory.from_dict(payload)
        inv.remove_item(inv.get_at((0, 0)))
        _ = inv.items  # must not raise KeyError

    def test_stash_is_account_scoped_storage(self):
        s = Stash(cols=2, rows=1)
        it = self._item()
        assert s.add_item(it)
        s2 = Stash.from_dict(s.to_dict())
        assert s2.items[0].base_name == "X"
        s2.remove_item(s2.items[0])
        assert s2.items == []


# ==========================================================================
# 7. MAP GENERATOR - EXTREME CONDITIONS (B10)
# ==========================================================================
@needs_pygame
class TestMapGenerator:
    def test_default_area_wellformed(self):
        rng = random.Random(42)
        area = build_procedural_area(rng, area_level=10, room_count=6, boss_room=True)
        assert area["tilemap"].rows == 38 and area["tilemap"].cols == 50
        assert len(area["rooms"]) >= 7
        assert area["gate_room"] not in (area["start_room"], area["boss_room"])
        assert area["start_room"] != area["boss_room"]

    def test_room_connectivity_all_rooms_reachable(self):
        for seed in range(15):
            area = build_procedural_area(random.Random(seed), area_level=5, room_count=6)
            grid = area["tilemap"].grid
            reachable = flood_reachable(grid, room_center_int(area["start_room"]))
            for room in area["rooms"]:
                assert room_center_int(room) in reachable, f"seed {seed}: isolated room"
            assert room_center_int(area["gate_room"]) in reachable

    def test_map_deterministic_per_seed(self):
        a = build_procedural_area(random.Random(77), area_level=3)
        b = build_procedural_area(random.Random(77), area_level=3)
        assert a["tilemap"].grid == b["tilemap"].grid
        assert a["rooms"] == b["rooms"]

    def test_gate_tile_is_walkable_floor(self):
        area = build_procedural_area(random.Random(9), area_level=3)
        x0, y0, x1, y1 = area["gate_room"]
        c, r = (x0 + x1) // 2, (y0 + y1) // 2
        assert area["tilemap"].grid[r][c] == 0

    @bug_xfail("B10: fallback gate_room_index collides with boss/start room on small maps")
    def test_tiny_map_gate_room_distinct(self):
        area = build_procedural_area(random.Random(1), area_level=5,
                                     cols=16, rows=16, room_count=10, boss_room=True)
        assert area["gate_room"] != area["boss_room"]
        assert area["gate_room"] != area["start_room"]

    @bug_xfail("B10: with a single room, gate_room == start_room == boss_room")
    def test_single_room_map_still_distinct_rooms(self):
        area = build_procedural_area(random.Random(2), area_level=5,
                                     cols=14, rows=14, room_count=0, boss_room=True)
        assert area["gate_room"] != area["start_room"]

    def test_dungeon_grid_bounds_safe(self):
        grid, rooms = generate_dungeon_grid(random.Random(5), cols=30, rows=30,
                                            room_count=4, min_room=4, max_room=8)
        assert len(grid) == 30 and len(grid[0]) == 30
        for r in grid:
            for t in r:
                assert t in (0, 1)

    def test_map_generator_does_not_shadow_real_boss(self):
        """B11 regression guard: map_generator must not define its own Boss."""
        import world.map_generator as mg
        assert not hasattr(mg, "Boss"), (
            "world/map_generator.py still defines a Boss symbol; "
            "it should import from enemies/boss.py instead of duplicating it."
        )

# ==========================================================================
# 8. BOSS FACTORY
# ==========================================================================
@needs_pygame
class TestBossFactory:
    def test_all_archetypes_produce_valid_templates(self):
        for arch in ("dash_predator", "ranged_zoner", "reflector", "summoner", "berserker"):
            for bias in ("low", "normal", "high"):
                pers = {"mechanic_archetype": arch, "difficulty_bias": bias,
                        "punishes_damage_type": "fire",
                        "rewards_on_defeat_damage_type": "chaos"}
                t = build_personalized_boss_template("Guardian", pers, 50, random.Random(1))
                ok, errs = BossValidator.validate({"max_hp": t.max_hp, "phases": t.phases})
                assert ok, f"{arch}/{bias}: {errs}"
                assert t.phases and t.phases[0].hp_threshold == 1.0

    def test_reflector_resistance_capped_not_immunity(self):
        pers = {"mechanic_archetype": "reflector", "punishes_damage_type": "fire"}
        t = build_personalized_boss_template("G", pers, 10, random.Random(1))
        assert t.resistances.get("fire", 0) <= 0.35

    def test_unknown_archetype_falls_back_to_default_phases(self):
        pers = {"mechanic_archetype": "does_not_exist"}
        t = build_personalized_boss_template("G", pers, 10, random.Random(1))
        assert len(t.phases) == 3

    def test_difficulty_bias_scales_stats(self):
        base = {"mechanic_archetype": "berserker"}
        low = build_personalized_boss_template("G", {**base, "difficulty_bias": "low"},
                                               30, random.Random(1))
        high = build_personalized_boss_template("G", {**base, "difficulty_bias": "high"},
                                                30, random.Random(1))
        assert high.max_hp > low.max_hp and high.melee_damage > low.melee_damage

    def test_hollow_warden_backward_compat_alias(self):
        b = HollowWarden(0, 0)
        assert isinstance(b, Boss) and b.name == "The Hollow Warden" and len(b.template.phases) == 3

    def test_boss_phase_progression(self):
        b = HollowWarden(0, 0)
        b.take_damage(int(b.max_hp * 0.5))
        assert b.phase_index >= 1
        b.take_damage(int(b.max_hp * 0.3))
        assert b.phase_index == 2
        b.take_damage(10**9)
        assert not b.alive and b.hp == 0


# ==========================================================================
# 9. WORLD & GATES
# ==========================================================================
@needs_pygame
class TestWorldAndGates:
    def test_starting_gate_registration_and_idempotence(self):
        w = World(1)
        g = w.register_starting_gate(0, 5, 5)
        assert g.state == "closed" and g.is_starting_gate
        assert w.gates_in_area("starting_area") == [g]
        assert w.closed_gates() == [g] and w.open_gates() == []

    def test_open_gate_prefers_starting_area(self):
        w = World(1)
        gs = w.register_starting_gate(0, 5, 5)
        ge = w.register_extension_gate("ext_x", 0, 3, 3)
        chosen = w.open_next_gate_to("ext_new", random.Random(1))
        assert chosen is gs and gs.is_open() and gs.target_extension_id == "ext_new"
        chosen2 = w.open_next_gate_to("ext_new2", random.Random(1))
        assert chosen2 is ge

    def test_no_closed_gates_returns_none(self):
        w = World(1)
        assert w.open_next_gate_to("x", random.Random(1)) is None

    def test_gate_roundtrip_and_counter_monotonic(self):
        w = World(1)
        g1 = w.register_starting_gate(0, 5, 5)
        d = w.to_dict()
        w2 = World.from_dict(d)
        g2 = w2.register_starting_gate(0, 6, 6)
        assert g2.gate_id != g1.gate_id
        ids = [g.gate_id for g in w2.gates]
        assert len(ids) == len(set(ids))

    def test_gate_from_dict_fields(self):
        g = Gate.from_dict({"gate_id": 3, "owner_area_id": "a", "room_index": 1,
                            "tile_col": 2, "tile_row": 4, "state": "open",
                            "target_extension_id": "b", "is_starting_gate": True})
        assert g.is_open() and g.target_extension_id == "b" and g.is_starting_gate

    def test_extension_gate_registered_lazily_on_build(self):
        w = World(1)
        spec = make_spec(extension_id="ext_lazy", area_level=5, room_count=5)
        ext = w.add_extension(spec)
        assert w.gates_in_area("ext_lazy") == []
        ext.ensure_built(w.rng_service, world=w)
        gates = w.gates_in_area("ext_lazy")
        assert len(gates) == 1 and gates[0].state == "closed"

    def test_extension_lazy_build_caches(self):
        w = World(1)
        ext = w.add_extension(make_spec(extension_id="e1"))
        assert ext.area is None
        a1 = ext.ensure_built(w.rng_service)
        a2 = ext.ensure_built(w.rng_service)
        assert a1 is a2

    def test_world_serialization_roundtrip(self):
        w = World(42)
        w.total_playtime_seconds = 1234.5
        w.register_starting_gate(0, 5, 5)
        w.add_extension(make_spec(extension_id="e1", area_level=8))
        w2 = World.from_dict(w.to_dict())
        assert w2.world_seed == 42
        assert w2.total_playtime_seconds == 1234.5
        assert len(w2.extensions) == 1
        assert w2.extensions[0].spec.area_level == 8
        assert len(w2.gates) == 1

    def test_automatic_generation_threshold(self):
        w = World(1)
        assert not w.automatic_generation_due()
        w.total_playtime_seconds = config.WORLD_EXTENSION_SECONDS - 0.01
        assert not w.automatic_generation_due()
        w.total_playtime_seconds = config.WORLD_EXTENSION_SECONDS
        assert w.automatic_generation_due()

    def test_seconds_since_last_extension(self):
        w = World(1)
        w.total_playtime_seconds = 100
        assert w.seconds_since_last_extension() == 100
        w.add_extension(make_spec(extension_id="e1"))
        assert w.seconds_since_last_extension() == 0

    def test_aggregate_rule_set_multiplies(self):
        w = World(1)
        s1 = make_spec(extension_id="e1", world_modifiers=[make_modifier([rule_mult(mult=2.0)])])
        s2 = make_spec(extension_id="e2", world_modifiers=[make_modifier([rule_mult(mult=1.5)])])
        w.add_extension(s1)
        w.add_extension(s2)
        rs = w.aggregate_rule_set()
        assert rs.damage_multiplier_for("minion", "fire", "taken") == 3.0

    def test_aggregate_conversions_and_resistances(self):
        wrs = WorldRuleSet([WorldModifier.from_dict(make_modifier(
            [rule_conv(pct=0.5), {"type": "resistance_modifier", "target": "minion",
                                 "damage_type": "cold", "delta": -0.2}]))])
        convs = wrs.conversion_rules_for("player")
        assert len(convs) == 1 and convs[0].percent == 0.5
        assert wrs.conversion_rules_for("enemy") == []  # wrong scope
        assert wrs.resistance_modifiers_for("minion") == {"cold": -0.2}
        assert wrs.resistance_modifiers_for("boss") == {}

    def test_duplicate_extension_id_rejected_by_world_flow(self):
        w = World(1)
        w.add_extension(make_spec(extension_id="e1"))
        ok, errs = WorldValidator.validate(make_spec(extension_id="e1"),
                                           {"e1"}, player_level=1)
        assert not ok


# ==========================================================================
# 10. SAVE / LOAD  (B9)
# ==========================================================================
class TestSaveManager:
    def _mgr(self):
        tmp = tempfile.mkdtemp(prefix="arpg_save_")
        return SaveManager(path=os.path.join(tmp, "save.json"),
                           stash_path=os.path.join(tmp, "stash.json"))

    def test_save_load_roundtrip(self):
        sm = self._mgr()
        state = {"world": {"seed": 1}, "player": {"x": 1}, "version": config.SAVE_VERSION}
        sm.save(state)
        loaded = sm.load()
        assert loaded["world"]["seed"] == 1 and loaded["version"] == config.SAVE_VERSION

    def test_load_missing_file_returns_none(self):
        sm = self._mgr()
        assert sm.load() is None and not sm.exists()

    def test_stash_missing_file_returns_empty(self):
        sm = self._mgr()
        s = sm.load_stash()
        assert s.is_full() is False and s.items == []

    @bug_xfail("B9: corrupt save JSON crashes json.load() (no corruption handling)")
    def test_corrupt_save_file_handled_gracefully(self):
        sm = self._mgr()
        with open(sm.path, "w") as f:
            f.write('{"world": broken json,,,')
        assert sm.load() in (None, {})

    @bug_xfail("B9: corrupt stash JSON crashes the whole game on startup")
    def test_corrupt_stash_file_handled_gracefully(self):
        sm = self._mgr()
        with open(sm.stash_path, "w") as f:
            f.write("not json at all [[[")
        s = sm.load_stash()
        assert s is not None and s.items == []


# ==========================================================================
# 11. PLAYER / PROGRESSION EXTREMES
# ==========================================================================
@needs_pygame
class TestPlayerProgression:
    def test_huge_xp_terminates_fast(self):
        p = Progression()
        t0 = time.time()
        levels = p.add_xp(10**8)
        assert time.time() - t0 < 2.0
        assert len(levels) > 30

    def test_xp_curve_monotonic_growth(self):
        vals = [xp_required_for_level(l) for l in range(1, 30)]
        assert all(b > a for a, b in zip(vals, vals[1:]))

    def test_exact_threshold_level_up(self):
        p = Progression(level=1, xp=xp_required_for_level(1) - 1)
        assert p.add_xp(1) == [2]
        assert p.xp == 0

    def test_level_up_heals_and_grows_stats(self):
        bus = EventBus()
        p = Player(0, 0, bus)
        p.take_damage(50)
        hp_before = p.effective_stats.hp
        str_before = p.base_stats.strength
        p.gain_xp(xp_required_for_level(1))
        assert p.progression.level == 2
        assert p.effective_stats.hp == p.effective_stats.max_hp
        assert p.effective_stats.hp > hp_before
        assert p.base_stats.strength == str_before + 1

    def test_death_emits_event_and_flag(self):
        bus = EventBus()
        deaths = []
        bus.subscribe("player_death", lambda cause=None: deaths.append(cause))
        p = Player(0, 0, bus)
        p.take_damage(10**9, cause="chaos")
        assert not p.alive and deaths == ["chaos"]

    def test_invulnerability_window(self):
        bus = EventBus()
        p = Player(0, 0, bus)
        assert p.take_damage(10) is True
        hp = p.effective_stats.hp
        assert p.take_damage(10) is False  # invulnerable
        assert p.effective_stats.hp == hp

    def test_heal_to_full(self):
        bus = EventBus()
        p = Player(0, 0, bus)
        p.take_damage(30)
        p.heal_to_full()
        assert p.effective_stats.hp == p.effective_stats.max_hp

    def test_recalc_preserves_hp_ratio(self):
        bus = EventBus()
        p = Player(0, 0, bus)
        p.effective_stats.hp = p.effective_stats.max_hp // 2
        ratio = p.effective_stats.hp / p.effective_stats.max_hp
        life_item = Item("Amulet", "amulet", armor_bonus=5)
        life_item._other_bonuses = {"life": 100}
        p.equipped["amulet"] = life_item
        p.recalc_stats()
        new_ratio = p.effective_stats.hp / p.effective_stats.max_hp
        assert abs(new_ratio - ratio) < 0.02

    def test_weapon_requirements_enforced(self):
        bus = EventBus()
        p = Player(0, 0, bus)
        gs = Item("Claymore", "weapon", weapon_subtype="greatsword")
        ok, missing = p.meets_requirements(gs)
        assert not ok and any("Strength" in m for m in missing)
        for _ in range(10):  # level up enough to gain Strength
            p.gain_xp(xp_required_for_level(p.progression.level))
        ok, _ = p.meets_requirements(gs)
        assert ok

    def test_equip_ring_prefers_empty_then_replaces(self):
        bus = EventBus()
        p = Player(0, 0, bus)
        r1, r2, r3 = (Item(f"Ring{i}", "ring") for i in range(3))
        for r in (r1, r2, r3):
            p.inventory.add_item(r)
        p.equip(r1)
        assert p.equipped["ring_1"] is r1
        p.equip(r2)
        assert p.equipped["ring_2"] is r2
        p.equip(r3)
        assert r3 in p.inventory.items or p.equipped["ring_1"] is r3  # replaces + returns old

    def test_equip_returns_old_item_to_inventory(self):
        bus = EventBus()
        p = Player(0, 0, bus)
        w1, w2 = Item("Old", "weapon"), Item("New", "weapon")
        p.inventory.add_item(w1)
        p.inventory.add_item(w2)
        p.equip(w1)
        n0 = len(p.inventory.items)
        p.equip(w2)
        assert p.equipped["weapon"] is w2
        assert len(p.inventory.items) == n0  # w1 swapped back in

    def test_effective_stats_aggregate(self):
        base = Stats()
        w = Item("W", "weapon", damage_bonus=10)
        w._other_bonuses = {"attack_speed": 20}
        a = Item("A", "armor", armor_bonus=8)
        a._resist_bonus = {"fire": 30}
        eff = compute_effective_stats(base, [w, a])
        assert eff.base_damage == config.PLAYER_BASE_DAMAGE + 10
        assert eff.armor == config.PLAYER_BASE_ARMOR + 8
        assert abs(eff.resistances["fire"] - 0.30) < 1e-6
        assert eff.attack_speed_bonus == 0.20

    def test_serialization_roundtrip(self):
        bus = EventBus()
        p = Player(12, 34, bus)
        p.gain_xp(500)
        p.inventory.add_item(Item("Keep", "weapon"))
        p2 = Player.from_dict(p.to_dict(), EventBus())
        assert p2.x == 12 and p2.progression.level == p.progression.level
        assert len(p2.inventory.items) == 1
        assert p2.effective_stats.max_hp == p.effective_stats.max_hp

    def test_from_dict_garbage_payload_safe(self):
        p = Player.from_dict(["not", "a", "dict"], EventBus())
        assert p.progression.level == 1 and p.inventory.items == []

    def test_from_dict_legacy_equipped_list_shape(self):
        w = Item("Legacy", "weapon")
        payload = {"equipped": [{"slot": "weapon", "item": w.to_dict()}]}
        p = Player.from_dict(payload, EventBus())
        assert p.equipped["weapon"].base_name == "Legacy"


# ==========================================================================
# 12. HISTORY / AGGREGATION / PERSONALIZATION
# ==========================================================================
class TestHistory:
    def test_event_log_records_tracked_types(self):
        bus = EventBus()
        log = EventLog(bus)
        bus.emit("level_up", level=2)
        bus.emit("player_death", cause="fire")
        bus.emit("untracked_event", x=1)
        types = [e.event_type for e in log.events]
        assert types == ["level_up", "player_death"]

    def test_event_log_cap_trims_oldest(self):
        bus = EventBus()
        log = EventLog(bus, max_events=100)
        for i in range(250):
            bus.emit("enemy_killed", name=str(i))
        assert 0 < len(log.events) <= 100
        assert log.events[-1].payload["name"] == "249"  # newest kept

    def test_event_log_serialization_roundtrip(self):
        bus = EventBus()
        log = EventLog(bus)
        bus.emit("boss_killed", name="Warden")
        log2 = EventLog.from_dict(log.to_dict(), EventBus())
        assert len(log2.events) == 1
        assert log2.events[0].payload["name"] == "Warden"

    def test_aggregator_empty_log_defaults(self):
        bus = EventBus()
        p = Player(0, 0, bus) if PYGAME_OK else None
        if p is None:
            return
        profile = build_player_profile(p, EventLog(EventBus()), World(1), None)
        assert profile.main_damage == "physical"
        assert profile.total_deaths == 0 and profile.boss_attempts == 0
        assert profile.extensions_generated == 0

    def test_aggregator_inference(self):
        if not PYGAME_OK:
            return
        bus = EventBus()
        log = EventLog(bus)
        for _ in range(5):
            bus.emit("skill_used", skill="basic_attack", damage=10,
                     damage_type="fire", weapon_name="Wand")
        bus.emit("skill_used", skill="basic_attack", damage=10,
                 damage_type="cold", weapon_name="Wand")
        for _ in range(3):
            bus.emit("player_death", cause="fire")
        bus.emit("boss_attempt_started", boss_id="b")
        bus.emit("boss_attempt_started", boss_id="b")
        bus.emit("boss_killed", name="b")
        for _ in range(4):
            bus.emit("item_found", item_id=1, rarity="rare", is_six_mod=False)
        for _ in range(2):
            bus.emit("item_sold", item_id=1)
        p = Player(0, 0, bus)
        world = World(1)
        world.extensions.append("x")  # count only
        profile = build_player_profile(p, log, world, None)
        assert profile.main_damage == "fire"
        assert profile.secondary_damage == "cold"
        assert profile.deaths == {"fire": 3}
        assert profile.boss_attempts == 2 and profile.boss_successes == 1
        assert profile.rare_items_found == 4
        assert profile.items_discarded == 2 and profile.items_kept == 2

    def test_derive_hints_branches(self):
        h = derive_hints(make_profile(
            level=20, main_damage="fire", secondary_damage=None,
            defensive_strength="low", deaths={"fire": 6, "cold": 1},
            boss_attempts=6, boss_successes=0, average_exploration=0.1,
            extensions_generated=3, six_mod_items_found=1,
            items_discarded=9, items_kept=2))
        assert h.weak_defense and h.boss_struggling and h.under_explored
        assert h.has_notable_legacy_items and h.discarded_a_lot
        assert h.frequent_death_causes == ["fire"]
        assert h.suggested_challenge_damage_type == "fire"
        assert h.suggested_enabling_damage_type == "chaos"  # fallback pairing

    def test_derive_hints_boss_dominant_and_fallback_pairing(self):
        h = derive_hints(make_profile(main_damage="physical", secondary_damage=None,
                                      defensive_strength="high", deaths={},
                                      boss_attempts=10, boss_successes=9,
                                      average_exploration=0.9))
        assert h.boss_dominant and h.over_explored and not h.weak_defense
        assert h.suggested_enabling_damage_type == "lightning"

    def test_exploration_tracker_basic(self):
        grid = [[0] * 10 for _ in range(10)]
        tm = type("TM", (), {"grid": grid})()
        ex = ExplorationTracker()
        ex.register_area("a", tm)
        assert ex.fraction_for("a") == 0.0
        ex.update(0.3, "a", 5 * config.TILE_SIZE, 5 * config.TILE_SIZE)
        assert abs(ex.fraction_for("a") - 0.01) < 1e-9

    def test_exploration_sample_interval_respected(self):
        grid = [[0] * 10 for _ in range(10)]
        tm = type("TM", (), {"grid": grid})()
        ex = ExplorationTracker()
        ex.register_area("a", tm)
        ex.update(0.1, "a", 3 * config.TILE_SIZE, 3 * config.TILE_SIZE)
        assert ex.fraction_for("a") == 0.0  # below SAMPLE_INTERVAL
        ex.update(0.2, "a", 3 * config.TILE_SIZE, 3 * config.TILE_SIZE)
        assert ex.fraction_for("a") > 0

    def test_exploration_double_register_and_average(self):
        grid = [[0] * 10 for _ in range(10)]
        tm = type("TM", (), {"grid": grid})()
        ex = ExplorationTracker()
        ex.register_area("a", tm)
        ex.register_area("a", tm)  # must be idempotent
        assert ex.area_totals["a"] == 100
        ex.register_area("b", tm)
        assert 0.0 <= ex.average_fraction() <= 1.0

    def test_exploration_serialization_roundtrip(self):
        grid = [[0] * 10 for _ in range(10)]
        tm = type("TM", (), {"grid": grid})()
        ex = ExplorationTracker()
        ex.register_area("a", tm)
        ex.update(1.0, "a", 2 * config.TILE_SIZE, 2 * config.TILE_SIZE)
        ex2 = ExplorationTracker.from_dict(ex.to_dict())
        assert ex2.fraction_for("a") == ex.fraction_for("a")

    def test_event_bus_no_subscribers_and_cap(self):
        bus = EventBus()
        bus.emit("nothing_listening", x=1)  # no crash
        for i in range(2100):
            bus.emit("e", i=i)
        assert len(bus.log) == 2000


# ==========================================================================
# 13. UI SMOKE TESTS (dummy video driver) + FULL GAME INTEGRATION (B3)
# ==========================================================================
@needs_pygame
class TestUIAndIntegration:
    def test_tooltip_draw_no_crash_and_clamps(self):
        surf = pygame.Surface((config.SCREEN_WIDTH, config.SCREEN_HEIGHT))
        tip = ItemTooltip()
        it = Item("Test Sword", "weapon", damage_bonus=10)
        tip.draw(surf, it, config.SCREEN_WIDTH - 20, config.SCREEN_HEIGHT - 20,
                 compare_against=None)  # forced off-screen -> must clamp

    def test_gateway_menu_wraparound(self):
        gm = GatewayMenu()
        gm.show([{"label": "A", "target_area_id": "a"},
                 {"label": "B", "target_area_id": "b"},
                 {"label": "C", "target_area_id": "c"}])
        gm.move_selection(-1)
        assert gm.selected_index == 2
        gm.move_selection(1)
        assert gm.selected_index == 0
        assert gm.confirm_selection() == "a"
        gm.hide()
        assert not gm.visible and gm.confirm_selection() is None

    def test_inventory_ui_equip_flow(self):
        bus = EventBus()
        p = Player(0, 0, bus)
        p.inventory.add_item(Item("Sword", "weapon"))
        ui = InventoryUI()
        ui.visible = True
        msg = ui.do_primary_action(p)
        assert "Equipped" in msg and p.equipped["weapon"] is not None
        ui.focus = "equipped"
        msg = ui.do_primary_action(p)
        assert "Unequipped" in msg and p.equipped["weapon"] is None

    def test_inventory_ui_movement_bounds(self):
        bus = EventBus()
        p = Player(0, 0, bus)
        ui = InventoryUI()
        ui.move_cursor(-5, -5, p)
        assert ui.inv_cursor == (0, 0)
        ui.move_cursor(999, 999, p)
        assert ui.inv_cursor == (p.inventory.cols - 1, p.inventory.rows - 1)

    def test_banner_and_profile_screen_lifecycle(self):
        b = WorldEvolutionBanner()
        b.show("Test Region", 15, False, ["Rule A"], gate_area_name="the Starting Region")
        assert b.visible
        surf = pygame.Surface((config.SCREEN_WIDTH, config.SCREEN_HEIGHT))
        b.draw(surf)
        b.dismiss()
        assert not b.visible
        ps = ProfileScreen()
        ps.show(make_profile(level=9))
        ps.draw(surf)
        ps.hide()
        assert not ps.visible

    def test_hud_draw_smoke(self):
        bus = EventBus()
        p = Player(0, 0, bus)
        surf = pygame.Surface((config.SCREEN_WIDTH, config.SCREEN_HEIGHT))
        HUD().draw(surf, p, boss=None, message="hello")

    def test_game_constructs_headless(self):
        g = Game()
        assert g.running and g.current_area is not None
        assert len(g.world.gates_in_area("starting_area")) == 1
        g.running = False  # do not enter the loop

    def test_generate_extension_debug_flow(self):
        g = Game()
        n_ext = len(g.world.extensions)
        g._generate_next_extension(mode=config.EXTENSION_MODE_DEBUG)
        assert len(g.world.extensions) == n_ext + 1
        assert g.evolution_banner.visible
        assert len(g.world.open_gates()) >= 1
        g.running = False

    @bug_xfail("B3: every melee attack path in game.py passes damage_multiplier= -> "
               "TypeError on the very first hit")
    def test_game_melee_attack_no_crash(self):
        g = Game()
        if not g.current_area.enemies:
            g.current_area.enemies.append(spawn_basic_enemy(0, 0, random.Random(1)))
        e = g.current_area.enemies[0]
        e.x = g.player.x + 30
        e.y = g.player.y
        g.player.facing.update(1, 0)
        g._try_attack()  # currently raises TypeError
        g.running = False

    def test_save_load_via_game_roundtrip(self):
        g = Game()
        tmp = tempfile.mkdtemp(prefix="arpg_game_")
        g.save_manager = SaveManager(path=os.path.join(tmp, "s.json"),
                                     stash_path=os.path.join(tmp, "st.json"))
        g._generate_next_extension(mode=config.EXTENSION_MODE_DEBUG)
        g._save_game()
        g2 = Game()
        g2.save_manager = g.save_manager
        g2._load_game()
        assert len(g2.world.extensions) == len(g.world.extensions)
        assert len(g2.world.gates) == len(g.world.gates)
        g.running = False
        g2.running = False


# ==========================================================================
# STANDALONE RUNNER (works without pytest)
# ==========================================================================
def _iter_tests():
    import inspect
    for name, obj in sorted(globals().items()):
        if not (name.startswith("Test") and inspect.isclass(obj)):
            continue
        for mname in sorted(dir(obj)):
            if mname.startswith("test_"):
                yield f"{name}::{mname}", getattr(obj(), mname)

if __name__ == "__main__":
    passed = failed = skipped = xfailed = xpassed = 0
    failures = []
    for label, fn in _iter_tests():
        if getattr(fn, "_needs_pygame", False) and not PYGAME_OK:
            skipped += 1
            print(f"SKIP  {label} (pygame unavailable)")
            continue
        try:
            fn()
        except Exception as e:
            if getattr(fn, "_xfail", None):
                xfailed += 1
                print(f"XFAIL {label}  [{type(e).__name__}: {e}]")
            else:
                failed += 1
                failures.append((label, e))
                print(f"FAIL  {label}  [{type(e).__name__}: {e}]")
        else:
            if getattr(fn, "_xfail", None):
                xpassed += 1
                print(f"XPASS {label}  (bug appears fixed!)")
            else:
                passed += 1
                print(f"PASS  {label}")
    print("=" * 70)
    print(f"passed={passed}  failed={failed}  xfail(expected-bug)={xfailed}  "
          f"xpass={xpassed}  skipped={skipped}")
    if failures:
        print("\nUNEXPECTED FAILURES:")
        for label, e in failures:
            print(f"  - {label}: {type(e).__name__}: {e}")
    sys.exit(1 if failed else 0)