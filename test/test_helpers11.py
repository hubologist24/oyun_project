"""
test_helpers11.py - micro-regression tests for the smallest details
that can cause subtle bugs across the codebase.

Sections:
  A. RNG service            - determinism, stream independence
  B. Event bus              - subscribe/emit, log capping
  C. Damage pipeline edges  - 0 dmg floor, conversion %, res caps,
                              armor formula, "dealt" scoping
  D. Item entity            - ID monotonicity, id reservation from
                              load, serialization roundtrip fidelity,
                              legacy flat-item preservation
  E. Inventory / Stash      - specific-cell placement, collision,
                              out-of-bounds, swap, duplicate ids
  F. Equipment slots        - ring_1/ring_2 fallback, all mappings
  G. Stats accumulation     - life, resists, weapon dmg type, stats
                              carried through to effective
  H. Progression            - exact level-up threshold, multi-level,
                              XP loss never underflows
  I. Boss phases            - exact-threshold transitions, vulnerable
                              multiplier, phase clamping
  J. Enemy behaviors        - dispatch, unknown falls back to chaser,
                              ranged fires projectiles
  K. World modifiers        - aggregation, unknown rule types,
                              tier_override merge across modifiers
  L. Weapon subtypes        - all loaded subtypes have required fields
  M. Damage types           - registry integrity
  N. Tier tables            - default modifiers, world_rules override,
                              unknown modifier raises
  O. Validators bounds      - exact min/max accepted, just-over rejected
  P. Extension / World      - roundtrip, gate id reservation
  Q. Personalization        - every branch derived
  R. Profile aggregation    - empty log, full event stream
  S. Config consistency     - referenced names exist
  T. Camera                 - clamping, coord roundtrip, small world
  U. Player entity          - facing edge cases, invuln, heal
  V. Save manager           - corrupt file, missing, wrong type
  W. Attack patterns        - arc filter, distance sort, zero facing
  X. LootRules / modifiers  - roundtrip, tier override on ruleset
  Y. DamageNumbers / HUD    - spawn/expire, draw smoke
  Z. Misc bug panics        - item comparison, tooltip edge cases

Run:  python -m test.test_helpers11
"""
import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import copy
import json
import math
import random
import tempfile
import traceback

import pygame

import core.config as config


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
    for k, v in list(result.items()):
        if v is False:
            result["errors"].append(f"check failed: {k}")
    print(result)
    print()
    return result


class _RecordingBus:
    def __init__(self):
        self.events = []

    def emit(self, event_type, **payload):
        self.events.append((event_type, payload))

    def subscribe(self, *a, **k):
        pass

    def unsubscribe(self, *a, **k):
        pass


class _StubStats:
    def __init__(self):
        self.hp = 100
        self.max_hp = 100
        self.armor = 0
        self.resistances = {}

    def clamp_hp(self):
        pass


class _StubPlayer:
    def __init__(self, x=0, y=0):
        self.x, self.y = x, y
        self.alive = True
        self.effective_stats = _StubStats()
        self.width = 32
        self.height = 32

    def take_damage(self, amount, cause="physical"):
        self.effective_stats.hp -= amount
        return True


class _OpenTilemap:
    def collides_rect(self, rect):
        return False


def _make_game(seed=None):
    if seed is not None:
        random.seed(seed)
    from core.game import Game
    return Game()


def _ruleset(*modifier_dicts):
    from world.world_modifiers import WorldRuleSet, WorldModifier
    return WorldRuleSet(modifiers=[WorldModifier.from_dict(m) for m in modifier_dicts])


def _mod(name, *rules):
    return {"name": name, "description": "", "rules": list(rules)}


# ===========================================================================
# A. RNG service
# ===========================================================================

def test_rng_service_determinism():
    from core.rng import RNGService
    r1 = RNGService(9999)
    r2 = RNGService(9999)
    a = [r1.get_stream("loot").random() for _ in range(8)]
    b = [r2.get_stream("loot").random() for _ in range(8)]
    return {
        "same_seed_identical": a == b,
        "errors": [],
    }


def test_rng_streams_independent_and_repeatable():
    from core.rng import RNGService
    a1 = [RNGService(1).get_stream("alpha").random() for _ in range(5)]
    a2 = [RNGService(1).get_stream("alpha").random() for _ in range(5)]
    b1 = [RNGService(1).get_stream("beta").random() for _ in range(5)]
    return {
        "same_stream_repeatable": a1 == a2,
        "different_streams_diverge": a1 != b1,
        "errors": [],
    }


def test_rng_derive_child_isolated():
    from core.rng import RNGService
    rs = RNGService(42)
    c1 = rs.derive_child("world", "extension:a:area")
    c2 = rs.derive_child("world", "extension:b:area")
    c1_again = rs.derive_child("world", "extension:a:area")
    # Same key -> same object (cached)
    same_key_cached = c1 is c1_again
    seq1 = [c1.random() for _ in range(4)]
    seq2 = [c2.random() for _ in range(4)]
    return {
        "child_cache_hits": same_key_cached,
        "children_diverge": seq1 != seq2,
        "errors": [],
    }


# ===========================================================================
# B. Event bus
# ===========================================================================

def test_event_bus_subscribe_and_emit():
    from core.events import EventBus
    bus = EventBus()
    received = []
    bus.subscribe("hit", lambda **p: received.append(p))
    bus.emit("hit", damage=10, who="x")
    bus.emit("hit", damage=20, who="y")
    return {
        "callback_fired_twice": len(received) == 2,
        "payload_preserved": received[0] == {"damage": 10, "who": "x"},
        "errors": [],
    }


def test_event_bus_log_is_capped():
    from core.events import EventBus
    bus = EventBus()
    for i in range(2500):
        bus.emit("tick", i=i)
    return {
        "log_capped_at_2000": len(bus.log) == 2000,
        "newest_event_preserved": bus.log[-1][1].get("i") == 2499,
        "errors": [],
    }


def test_event_bus_emit_with_unknown_type_is_noop():
    from core.events import EventBus
    bus = EventBus()
    # No subscribers, should not raise
    bus.emit("nobody_listens", x=1)
    return {
        "still_logged": len(bus.log) == 1,
        "errors": [],
    }


# ===========================================================================
# C. Damage pipeline edges
# ===========================================================================

def test_damage_zero_or_negative_returns_one():
    from combat.damage import resolve_damage
    zero = resolve_damage(0, "physical", None, target_category="minion")
    neg = resolve_damage(-75, "physical", None, target_category="minion")
    return {
        "zero_clamps_to_1": zero == 1,
        "negative_clamps_to_1": neg == 1,
        "errors": [],
    }


def test_damage_armor_formula():
    from combat.damage import resolve_damage
    # armor 100 => 50% physical reduction
    d100 = resolve_damage(100, "physical", None, target_armor=100)
    # armor 300 => 75%
    d300 = resolve_damage(100, "physical", None, target_armor=300)
    # armor doesn't reduce non-physical
    fire = resolve_damage(100, "fire", None, target_armor=100)
    return {
        "armor_100_halves": d100 == 50,
        "armor_300_quarters": d300 == 25,
        "armor_only_physical": fire == 100,
        "errors": [],
    }


def test_damage_conversion_percent_zero_and_one():
    from combat.damage import resolve_damage_breakdown, ConversionRule
    zero_conv = [ConversionRule("physical", "chaos", 0.0)]
    full_conv = [ConversionRule("physical", "chaos", 1.0)]

    total_z, br_z = resolve_damage_breakdown(100, "physical", None,
                                             conversion_rules=zero_conv,
                                             target_category="minion")
    total_f, br_f = resolve_damage_breakdown(100, "physical", None,
                                             conversion_rules=full_conv,
                                             target_category="minion")
    return {
        "zero_conv_keeps_physical": set(br_z) == {"physical"},
        "full_conv_only_chaos": set(br_f) == {"chaos"},
        "zero_total_is_100": total_z == 100,
        "full_total_is_100": total_f == 100,
        "errors": [],
    }


def test_damage_multiple_conversions_dont_inflate():
    from combat.damage import resolve_damage_breakdown, ConversionRule
    rules = [ConversionRule("physical", "chaos", 0.3),
             ConversionRule("physical", "fire", 0.2)]
    total, breakdown = resolve_damage_breakdown(100, "physical", None,
                                                conversion_rules=rules,
                                                target_category="minion")
    return {
        "total_preserved": total == 100,
        "all_types_present": set(breakdown) == {"chaos", "fire", "physical"},
        "chaos_is_30": abs(breakdown["chaos"] - 30) < 0.01,
        "fire_is_14": abs(breakdown["fire"] - 14) < 0.01,
        "physical_is_56": abs(breakdown["physical"] - 56) < 0.01,
        "errors": [],
    }


def test_damage_resistance_clamped():
    from combat.damage import resolve_damage
    # resistance -1.0 -> damage doubled
    d_neg = resolve_damage(100, "fire", None,
                           target_resistances={"fire": -1.0},
                           target_category="minion")
    # resistance 0.75 -> 25% damage
    d_pos = resolve_damage(100, "fire", None,
                           target_resistances={"fire": 0.75},
                           target_category="minion")
    # resistance 5.0 -> clamps at 0.75
    d_hi = resolve_damage(100, "fire", None,
                          target_resistances={"fire": 5.0},
                          target_category="minion")
    return {
        "negative_doubles": d_neg == 200,
        "max_resist_quarter": d_pos == 25,
        "over_max_clamps": d_hi == 25,
        "errors": [],
    }


def test_damage_dealt_rule_scoped_by_source():
    from combat.damage import resolve_damage
    rules = _ruleset(_mod("Frenzy", {"type": "damage_dealt_multiplier",
                                     "target": "minion",
                                     "damage_type": "physical",
                                     "multiplier": 2.0}))
    from_minion = resolve_damage(100, "physical", None, world_rules=rules,
                                 target_category="player", source_category="minion")
    from_player = resolve_damage(100, "physical", None, world_rules=rules,
                                 target_category="minion", source_category="player")
    return {
        "minion_source_doubles": from_minion == 200,
        "player_source_untouched": from_player == 100,
        "errors": [],
    }


def test_damage_taken_all_dtype_null():
    """damage_type: None means "all damage types"."""
    from combat.damage import resolve_damage
    rules = _ruleset(_mod("Sharp", {"type": "damage_taken_multiplier",
                                    "target": "minion",
                                    "damage_type": None,
                                    "multiplier": 1.5}))
    fire = resolve_damage(100, "fire", None, world_rules=rules,
                          target_category="minion")
    cold = resolve_damage(100, "cold", None, world_rules=rules,
                          target_category="minion")
    return {
        "applies_to_fire": fire == 150,
        "applies_to_cold": cold == 150,
        "errors": [],
    }


def test_damage_taken_target_filter():
    from combat.damage import resolve_damage
    rules = _ruleset(_mod("Only Boss", {"type": "damage_taken_multiplier",
                                        "target": "boss",
                                        "damage_type": "physical",
                                        "multiplier": 2.0}))
    to_minion = resolve_damage(100, "physical", None, world_rules=rules,
                               target_category="minion")
    to_boss = resolve_damage(100, "physical", None, world_rules=rules,
                             target_category="boss")
    return {
        "minion_unaffected": to_minion == 100,
        "boss_doubled": to_boss == 200,
        "errors": [],
    }


# ===========================================================================
# D. Item entity
# ===========================================================================

def test_item_id_monotonic_for_new_items():
    from items.item import Item
    a = Item("A", "weapon")
    b = Item("B", "weapon")
    c = Item("C", "weapon")
    return {
        "b_after_a": b.item_id > a.item_id,
        "c_after_b": c.item_id > b.item_id,
        "all_unique": len({a.item_id, b.item_id, c.item_id}) == 3,
        "errors": [],
    }


def test_item_id_reserved_from_explicit_load():
    import items.item as item_mod
    from items.item import Item
    current = item_mod._id_counter
    big_id = current + 100_000
    restored = Item("X", "weapon", item_id=big_id)
    fresh = Item("Y", "weapon")
    return {
        "explicit_id_kept": restored.item_id == big_id,
        "next_id_above_restored": fresh.item_id > big_id,
        "errors": [],
    }


def test_item_six_mod_detection():
    from items.item import Item
    from items.affix import RolledAffix

    def affix(kind, i):
        return RolledAffix(kind=kind, affix_id=f"a{i}", display_name=f"A{i}",
                           modifier="physical_damage", damage_type="physical",
                           tier="T3", value=10, creation_tier_range=[5, 15])

    it = Item("X", "weapon")
    it.prefixes = [affix("prefix", i) for i in range(3)]
    it.suffixes = [affix("suffix", i) for i in range(3)]
    six = it.is_six_mod()
    label = it.affix_count_label()

    it.suffixes = it.suffixes[:2]
    five = it.is_six_mod()

    return {
        "six_mod_true_at_3_3": six,
        "six_mod_false_at_3_2": five is False,
        "affix_label_format": label == "3P / 3S",
        "errors": [],
    }


def test_item_serialization_roundtrip_preserves_everything():
    from items.item_generator import generate_random_item
    rng = random.Random(77)
    for _ in range(10):
        it = generate_random_item(rng, area_level=30)
        d = it.to_dict()
        clone = type(it).from_dict(d)
        if (clone.display_name != it.display_name
                or clone.rarity != it.rarity
                or clone.item_level != it.item_level
                or clone.slot != it.slot
                or clone.damage_bonus != it.damage_bonus
                or clone.armor_bonus != it.armor_bonus
                or len(clone.prefixes) != len(it.prefixes)
                or len(clone.suffixes) != len(it.suffixes)
                or clone.weapon_subtype != it.weapon_subtype):
            return {"roundtrip_faithful": False, "errors": []}
    return {"roundtrip_faithful": True, "errors": []}


def test_item_legacy_flat_item_preserves_numbers():
    """Old Phase-1 style items (no affixes, flat bonuses only) must
    keep their exact flat values across save/load."""
    from items.item import Item
    it = Item("Legacy Sword", "weapon", damage_bonus=42, armor_bonus=7,
              item_id=999_000, item_level=1)
    d = it.to_dict()
    clone = Item.from_dict(d)
    return {
        "damage_preserved": clone.damage_bonus == 42,
        "armor_preserved": clone.armor_bonus == 7,
        "no_affixes": not clone.prefixes and not clone.suffixes,
        "errors": [],
    }


def test_item_other_bonus_unknown_modifier_is_zero():
    from items.item import Item
    it = Item("X", "weapon")
    return {
        "unknown_mod_returns_zero": it.other_bonus("made_up_mod") == 0,
        "unknown_resist_returns_zero": it.resist_bonus("plasma") == 0.0,
        "errors": [],
    }


# ===========================================================================
# E. Inventory / Stash
# ===========================================================================

def test_inventory_specific_cell_placement():
    from items.inventory import Inventory
    from items.item import Item
    inv = Inventory(cols=5, rows=4)
    a = Item("A", "weapon")
    b = Item("B", "weapon")
    ok_a = inv.add_item(a, cell=(2, 1))
    ok_b = inv.add_item(b, cell=(2, 1))   # occupied -> reject
    return {
        "specific_cell_added": ok_a,
        "cell_occupied_rejected": ok_b is False,
        "cell_holds_a": inv.get_at((2, 1)) is a,
        "errors": [],
    }


def test_inventory_out_of_bounds_rejected():
    from items.inventory import Inventory
    from items.item import Item
    inv = Inventory(cols=3, rows=3)
    ok = inv.add_item(Item("X", "weapon"), cell=(3, 0))
    neg = inv.add_item(Item("Y", "weapon"), cell=(-1, 0))
    return {
        "oob_x_rejected": ok is False,
        "neg_cell_rejected": neg is False,
        "errors": [],
    }


def test_inventory_move_swaps_occupied_cells():
    from items.inventory import Inventory
    from items.item import Item
    inv = Inventory(cols=3, rows=3)
    a = Item("A", "weapon")
    b = Item("B", "weapon")
    inv.add_item(a, cell=(0, 0))
    inv.add_item(b, cell=(1, 0))
    ok = inv.move_item((0, 0), (1, 0))
    return {
        "swap_returns_true": ok,
        "a_now_at_1_0": inv.get_at((1, 0)) is a,
        "b_now_at_0_0": inv.get_at((0, 0)) is b,
        "errors": [],
    }


def test_inventory_roundtrip_preserves_cells():
    from items.inventory import Inventory
    from items.item import Item
    inv = Inventory(cols=4, rows=3)
    items = [(0, 0), (2, 1), (3, 2)]
    stored = []
    for i, cell in enumerate(items):
        it = Item(f"I{i}", "weapon")
        inv.add_item(it, cell=cell)
        stored.append((cell, it))
    clone = Inventory.from_dict(inv.to_dict())
    ok = all(clone.get_at(c) is not None and
             clone.get_at(c).base_name == it.base_name
             for c, it in stored)
    return {
        "all_cells_restored": ok,
        "count_matches": len(clone.items) == len(stored),
        "errors": [],
    }


def test_inventory_from_dict_reassigns_duplicate_ids():
    from items.inventory import Inventory
    from items.item import Item
    it = Item("A", "weapon", item_id=12345)
    d = {
        "cols": 2, "rows": 2,
        "cells": [
            {"col": 0, "row": 0, "item": it.to_dict()},
            {"col": 1, "row": 0, "item": it.to_dict()},  # duplicate id
        ],
    }
    inv = Inventory.from_dict(d)
    ids = [inv.get_at((0, 0)).item_id, inv.get_at((1, 0)).item_id]
    return {
        "ids_are_unique": len(set(ids)) == 2,
        "errors": [],
    }


def test_inventory_is_full_and_find_free_cell():
    from items.inventory import Inventory
    from items.item import Item
    inv = Inventory(cols=2, rows=2)
    for i in range(4):
        inv.add_item(Item(f"I{i}", "weapon"))
    return {
        "is_full_after_4": inv.is_full(),
        "find_free_none": inv.find_free_cell() is None,
        "add_more_fails": inv.add_item(Item("X", "weapon")) is False,
        "errors": [],
    }


def test_stash_roundtrip():
    from items.stash import Stash
    from items.item import Item
    st = Stash(cols=4, rows=4)
    it = Item("X", "weapon", item_id=77777)
    st.add_item(it, cell=(1, 1))
    clone = Stash.from_dict(st.to_dict())
    return {
        "item_restored": clone.get_at((1, 1)) is not None,
        "item_id_restored": clone.get_at((1, 1)).item_id == 77777,
        "empty_stash_factory": Stash.empty(cols=3, rows=3).cols == 3,
        "errors": [],
    }


# ===========================================================================
# F. Equipment slots
# ===========================================================================

def test_resolve_equip_slot_ring_prefers_empty():
    from items.equipment_slots import resolve_equip_slot
    from items.item import Item
    ring_a = Item("Ring A", "ring")
    ring_b = Item("Ring B", "ring")

    equipped = {"ring_1": None, "ring_2": None}
    s1 = resolve_equip_slot("ring", equipped)
    equipped["ring_1"] = ring_a
    s2 = resolve_equip_slot("ring", equipped)
    equipped["ring_2"] = ring_b
    s3 = resolve_equip_slot("ring", equipped)  # both full -> falls back

    return {
        "first_ring_goes_to_ring_1": s1 == "ring_1",
        "second_ring_goes_to_ring_2": s2 == "ring_2",
        "both_full_falls_back": s3 == "ring_1",
        "errors": [],
    }


def test_resolve_equip_slot_all_single_targets():
    from items.equipment_slots import resolve_equip_slot
    empty = {}
    expectations = {
        "weapon": "weapon",
        "offhand": "offhand",
        "head": "head",
        "chest": "chest",
        "legs": "legs",
        "boots": "boots",
        "gloves": "gloves",
        "amulet": "amulet",
    }
    bad = []
    for slot, expected in expectations.items():
        if resolve_equip_slot(slot, empty) != expected:
            bad.append((slot, expected))
    return {
        "all_single_targets_map": not bad,
        "unknown_slot_returns_none": resolve_equip_slot("nonsense", empty) is None,
        "bad": bad,
        "errors": [],
    }


# ===========================================================================
# G. Stats accumulation
# ===========================================================================

def test_effective_stats_accumulate_from_equipment():
    from player.stats import Stats, compute_effective_stats
    from items.item import Item

    base = Stats()
    weapon = Item("Sword", "weapon")
    weapon._damage_bonus = 15
    armor = Item("Chest", "chest")
    armor._armor_bonus = 10
    armor._other_bonuses = {"life": 50}

    eff = compute_effective_stats(base, [weapon, armor])
    return {
        "damage_added": eff.base_damage == base.base_damage + 15,
        "armor_added": eff.armor == base.armor + 10,
        "life_added": eff.max_hp == base.max_hp + 50,
        "errors": [],
    }


def test_effective_stats_resist_accumulation():
    from player.stats import Stats, compute_effective_stats
    from items.item import Item
    base = Stats()
    a = Item("Chest", "chest")
    a._resist_bonus = {"fire": 30, "cold": 20}
    b = Item("Helm", "head")
    b._resist_bonus = {"fire": 15}
    eff = compute_effective_stats(base, [a, b])
    return {
        "fire_sums": abs(eff.resistances.get("fire", 0) - 0.45) < 1e-9,
        "cold_single": abs(eff.resistances.get("cold", 0) - 0.20) < 1e-9,
        "errors": [],
    }


def test_effective_stats_weapon_sets_damage_type():
    from player.stats import Stats, compute_effective_stats
    from items.item_generator import generate_random_item
    rng = random.Random(5)
    # Try a bunch of weapon rolls until we get one with a non-physical dtype
    for _ in range(50):
        w = generate_random_item(rng, area_level=5, force_slot="weapon")
        base = Stats()
        eff = compute_effective_stats(base, [w])
        if eff.primary_damage_type != "physical":
            break
    return {
        "primary_damage_type_is_str": isinstance(eff.primary_damage_type, str),
        "errors": [],
    }


def test_effective_stats_carries_character_attributes():
    from player.stats import Stats, compute_effective_stats
    base = Stats()
    base.strength = 12
    base.dexterity = 9
    base.intelligence = 20
    eff = compute_effective_stats(base, [])
    return {
        "strength_carried": eff.strength == 12,
        "dexterity_carried": eff.dexterity == 9,
        "intelligence_carried": eff.intelligence == 20,
        "errors": [],
    }


# ===========================================================================
# H. Progression boundaries
# ===========================================================================

def test_progression_exact_threshold_levels_up():
    from player.progression import Progression, xp_required_for_level
    p = Progression(level=1, xp=0)
    exact = xp_required_for_level(1)
    levels = p.add_xp(exact)
    return {
        "exact_threshold_levels": levels == [2],
        "new_xp_is_zero": p.xp == 0,
        "errors": [],
    }


def test_progression_one_below_threshold_no_levelup():
    from player.progression import Progression, xp_required_for_level
    p = Progression(level=1, xp=0)
    levels = p.add_xp(xp_required_for_level(1) - 1)
    return {
        "no_level": levels == [],
        "xp_preserved": p.xp == xp_required_for_level(1) - 1,
        "errors": [],
    }


def test_progression_multi_level():
    from player.progression import Progression, xp_required_for_level
    p = Progression(level=1, xp=0)
    total = xp_required_for_level(1) + xp_required_for_level(2) + xp_required_for_level(3)
    levels = p.add_xp(total)
    return {
        "three_levels": levels == [2, 3, 4],
        "xp_zero_after": p.xp == 0,
        "errors": [],
    }


def test_progression_lose_xp_never_below_zero():
    from player.progression import Progression
    p = Progression(level=5, xp=30)
    lost = p.lose_xp_progress(0.10)
    return {
        "loss_correct": lost == 3,
        "xp_now_27": p.xp == 27,
        "level_unchanged": p.level == 5,
        "never_negative_on_huge_loss":
            Progression(level=3, xp=2).lose_xp_progress(10.0) >= 0,
        "errors": [],
    }


# ===========================================================================
# I. Boss phases
# ===========================================================================

def test_boss_phase_exact_thresholds():
    from enemies.boss import Boss, BossTemplate
    b = Boss(0, 0, BossTemplate.hollow_warden())
    phase_checks = []

    b.hp = int(b.max_hp * 1.00)
    b._update_phase(); phase_checks.append(b.phase_index == 0)

    b.hp = int(b.max_hp * 0.60)
    b._update_phase(); phase_checks.append(b.phase_index == 1)

    b.hp = int(b.max_hp * 0.59)
    b._update_phase(); phase_checks.append(b.phase_index == 1)

    b.hp = int(b.max_hp * 0.25)
    b._update_phase(); phase_checks.append(b.phase_index == 2)

    b.hp = int(b.max_hp * 0.10)
    b._update_phase(); phase_checks.append(b.phase_index == 2)

    return {
        "all_thresholds_hit": all(phase_checks),
        "errors": [],
    }


def test_boss_vulnerable_double_damage():
    from enemies.boss import Boss, BossTemplate
    b = Boss(0, 0, BossTemplate.hollow_warden())
    hp0 = b.hp
    b.take_damage(100)
    normal_loss = hp0 - b.hp
    b.enraged_vulnerable_timer = 1.0
    hp1 = b.hp
    b.take_damage(100)
    buff_loss = hp1 - b.hp
    return {
        "double_in_vuln": buff_loss == normal_loss * 2,
        "errors": [],
    }


def test_boss_death_clamps_hp_zero():
    from enemies.boss import Boss, BossTemplate
    b = Boss(0, 0, BossTemplate.hollow_warden())
    b.take_damage(10 ** 9)
    return {
        "hp_clamped_to_zero": b.hp == 0,
        "alive_false": b.alive is False,
        "errors": [],
    }


# ===========================================================================
# J. Enemy behaviors
# ===========================================================================

def test_enemy_unknown_behavior_falls_back_to_chaser():
    from enemies.enemy import Enemy
    e = Enemy(0, 0, behavior="nonsense")
    p = _StubPlayer(1000, 1000)  # far away, no contact
    try:
        e.update(1 / 60, p, _OpenTilemap(), _RecordingBus())
        ok = True
    except Exception as ex:
        ok = f"{type(ex).__name__}: {ex}"
    return {
        "no_crash_on_unknown_behavior": ok is True,
        "errors": [] if ok is True else [str(ok)],
    }


def test_enemy_ranged_fires_projectile_when_in_strafe_band():
    from enemies.enemy import Enemy
    e = Enemy(0, 0, behavior="ranged", damage=10)
    e._ranged_timer = 0.0
    p = _StubPlayer(200, 0)  # 160 < dist < 300 -> strafe + fire
    e.update(1 / 60, p, _OpenTilemap(), _RecordingBus())
    return {
        "projectile_spawned": len(e.projectiles) >= 1,
        "errors": [],
    }


def test_enemy_dead_does_not_update():
    from enemies.enemy import Enemy
    e = Enemy(100, 100, behavior="chaser")
    e.alive = False
    p = _StubPlayer(110, 100)
    e.update(1 / 60, p, _OpenTilemap(), _RecordingBus())
    # no error and enemy didn't move
    return {
        "position_unchanged": (e.x, e.y) == (100, 100),
        "errors": [],
    }


# ===========================================================================
# K. World modifiers aggregation
# ===========================================================================

def test_world_rule_set_empty_multiplies_to_one():
    from world.world_modifiers import WorldRuleSet
    rs = WorldRuleSet()
    m = rs.damage_multiplier_for("minion", "physical", "taken")
    return {
        "empty_returns_one": m == 1.0,
        "summary_empty": rs.summary_lines() == [],
        "errors": [],
    }


def test_world_rule_set_multiple_modifiers_multiply():
    rules = _ruleset(
        _mod("A", {"type": "damage_taken_multiplier", "target": "minion",
                   "damage_type": "fire", "multiplier": 2.0}),
        _mod("B", {"type": "damage_taken_multiplier", "target": "minion",
                   "damage_type": "fire", "multiplier": 1.5}),
    )
    m = rules.damage_multiplier_for("minion", "fire", "taken")
    return {
        "multiplicative": abs(m - 3.0) < 1e-9,
        "errors": [],
    }


def test_world_modifier_unknown_rule_type_ignored():
    from world.world_modifiers import WorldModifier
    md = WorldModifier.from_dict({
        "name": "X",
        "description": "",
        "rules": [{"type": "future_type", "some_field": "whatever"}],
    })
    m = md.damage_multiplier_for("minion", "physical", "taken")
    return {
        "unknown_rule_returns_1": m == 1.0,
        "no_conversion_rules": md.conversion_rules_for("player") == [],
        "no_on_kill_rules": md.on_kill_effects_for("minion") == [],
        "errors": [],
    }


def test_world_rule_set_tier_overrides_merge():
    rules = _ruleset(
        _mod("A", {"type": "tier_override", "modifier": "physical_damage",
                   "tiers": {"T1": [50, 70], "T2": [30, 49]}}),
        _mod("B", {"type": "tier_override", "modifier": "physical_damage",
                   "tiers": {"T1": [90, 120]}}),
    )
    overrides = rules.tier_overrides
    return {
        "t1_latest_wins": overrides["physical_damage"]["T1"] == [90, 120],
        "t2_preserved": overrides["physical_damage"]["T2"] == [30, 49],
        "errors": [],
    }


# ===========================================================================
# L. Weapon subtypes
# ===========================================================================

def test_weapon_subtypes_registry_integrity():
    from items.weapon_subtypes import WeaponSubtypes
    ids = WeaponSubtypes.all_ids()
    missing = []
    for sid in ids:
        entry = WeaponSubtypes.get(sid)
        if not entry or "base_attack" not in entry or "display_name" not in entry:
            missing.append(sid)
        profile = WeaponSubtypes.attack_profile(sid)
        if profile.kind not in ("arc", "cone_aoe", "projectile"):
            missing.append(f"{sid}: bad kind {profile.kind}")
    return {
        "all_have_required_fields": not missing,
        "at_least_one_subtype": len(ids) >= 1,
        "missing": missing,
        "errors": [],
    }


def test_weapon_subtypes_fallback_for_unknown():
    from items.weapon_subtypes import WeaponSubtypes
    p = WeaponSubtypes.attack_profile("nonexistent_weapon")
    return {
        "returns_arc_fallback": p.kind == "arc",
        "is_valid_false": WeaponSubtypes.is_valid("nope") is False,
        "display_name_falls_back": isinstance(WeaponSubtypes.display_name("nope"), str),
        "errors": [],
    }


# ===========================================================================
# M. Damage types registry
# ===========================================================================

def test_damage_types_registry():
    from combat.damage_types import DamageTypes
    all_types = DamageTypes.all()
    return {
        "includes_physical": "physical" in all_types,
        "includes_fire": "fire" in all_types,
        "is_valid_true": DamageTypes.is_valid("fire") is True,
        "is_valid_false": DamageTypes.is_valid("plasma") is False,
        "errors": [],
    }


# ===========================================================================
# N. Tier tables
# ===========================================================================

def test_tier_table_default_and_override():
    from items.tiers import get_tier_table
    base = get_tier_table()
    rules = _ruleset(_mod("Buff", {"type": "tier_override",
                                   "modifier": "physical_damage",
                                   "tiers": {"T1": [200, 220]}}))
    overridden = get_tier_table(rules)
    return {
        "default_has_physical": "physical_damage" in base,
        "override_applied": overridden["physical_damage"]["T1"] == [200, 220],
        "base_unchanged": base["physical_damage"]["T1"] != [200, 220],
        "errors": [],
    }


def test_tier_table_unknown_modifier_raises():
    from items.tiers import resolve_tier_for_roll
    raised = False
    try:
        resolve_tier_for_roll("made_up_stat", 10, random.Random(0))
    except ValueError:
        raised = True
    return {
        "raises_value_error": raised,
        "errors": [],
    }


def test_tier_resolution_respects_unlock_level():
    from items.tiers import resolve_tier_for_roll
    rng = random.Random(0)
    # At ilvl 1, T1 should not be rollable (T1 unlocks at ilvl 35).
    seen = set()
    for _ in range(200):
        t = resolve_tier_for_roll("physical_damage", 1, rng)
        seen.add(t)
    return {
        "no_t1_at_ilvl_1": "T1" not in seen,
        "has_some_tier": len(seen) > 0,
        "errors": [],
    }


# ===========================================================================
# O. Validators bounds
# ===========================================================================

def test_world_validator_accepts_boundary_values():
    from ai.validators import WorldValidator
    from world.extension import ExtensionSpec
    # multiplier at exactly 0.25 (MIN) and 4.0 (MAX) should be accepted
    spec = ExtensionSpec(
        extension_id="b1", extension_name="Boundary",
        area_level=1, biome="ash_canyon", is_anomaly=False,
        world_modifiers=[{
            "name": "Edge",
            "description": "",
            "rules": [
                {"type": "damage_taken_multiplier", "target": "minion",
                 "damage_type": "physical", "multiplier": 0.25},
            ],
        }],
        room_count=4,
    )
    ok_min, _ = WorldValidator.validate(spec, set(), 1)

    spec2 = ExtensionSpec(
        extension_id="b2", extension_name="Boundary",
        area_level=1, biome="ash_canyon", is_anomaly=False,
        world_modifiers=[{
            "name": "Edge",
            "description": "",
            "rules": [
                {"type": "damage_taken_multiplier", "target": "minion",
                 "damage_type": "physical", "multiplier": 4.0},
            ],
        }],
        room_count=4,
    )
    ok_max, _ = WorldValidator.validate(spec2, set(), 1)

    return {
        "multiplier_min_accepted": ok_min,
        "multiplier_max_accepted": ok_max,
        "errors": [],
    }


def test_world_validator_rejects_just_over_bounds():
    from ai.validators import WorldValidator
    from world.extension import ExtensionSpec

    def make_spec(rid, mult):
        return ExtensionSpec(
            extension_id=rid, extension_name="Edge",
            area_level=1, biome="ash_canyon", is_anomaly=False,
            world_modifiers=[{
                "name": "E", "description": "",
                "rules": [{"type": "damage_taken_multiplier", "target": "minion",
                           "damage_type": "physical", "multiplier": mult}],
            }],
            room_count=4,
        )

    low = WorldValidator.validate(make_spec("l", 0.24), set(), 1)[0]
    high = WorldValidator.validate(make_spec("h", 4.01), set(), 1)[0]
    return {
        "below_min_rejected": low is False,
        "above_max_rejected": high is False,
        "errors": [],
    }


def test_world_modifier_validator_delta_boundary():
    from ai.validators import WorldModifierValidator
    def check(delta):
        return WorldModifierValidator.validate({
            "name": "R",
            "rules": [{"type": "resistance_modifier", "target": "minion",
                       "damage_type": "fire", "delta": delta}],
        })[0]

    return {
        "delta_at_0_5_accepted": check(0.5),
        "delta_at_neg_0_5_accepted": check(-0.5),
        "delta_at_0_51_rejected": check(0.51) is False,
        "delta_at_neg_0_51_rejected": check(-0.51) is False,
        "errors": [],
    }


def test_world_validator_no_player_target_on_kill():
    from ai.validators import WorldModifierValidator
    # on_kill_effect cannot target the player
    ok = WorldModifierValidator.validate({
        "name": "X",
        "rules": [{"type": "on_kill_effect", "target": "player",
                   "effect": "explode", "damage_type": "fire"}],
    })[0]
    return {
        "player_target_rejected": ok is False,
        "errors": [],
    }


# ===========================================================================
# P. Extension / World serialization
# ===========================================================================

def test_extension_serialization_roundtrip():
    from world.extension import Extension, ExtensionSpec
    spec = ExtensionSpec(
        extension_id="ext_a", extension_name="Test Ext",
        area_level=7, biome="frozen_reach", is_anomaly=True,
        world_modifiers=[{"name": "M", "description": "",
                          "rules": [{"type": "damage_taken_multiplier",
                                     "target": "minion",
                                     "damage_type": "cold",
                                     "multiplier": 1.5}]}],
        boss_template_id="personalized",
        room_count=8,
        loot_rules={"favored_damage_types": {"cold": 1.3}},
        boss_personalization={"mechanic_archetype": "reflector"},
    )
    ext = Extension(spec, 4242, created_at=123.45)
    clone = Extension.from_dict(ext.to_dict())
    return {
        "id_preserved": clone.extension_id == "ext_a",
        "seed_preserved": clone.extension_seed == 4242,
        "anomaly_preserved": clone.spec.is_anomaly is True,
        "loot_rules_preserved": clone.spec.loot_rules.get("favored_damage_types", {}).get("cold") == 1.3,
        "boss_personalization_preserved":
            clone.spec.boss_personalization.get("mechanic_archetype") == "reflector",
        "errors": [],
    }


def test_world_serialization_roundtrip():
    from world.world import World
    from world.extension import ExtensionSpec
    w = World(4242)
    spec = ExtensionSpec(extension_id="e1", extension_name="E1",
                         area_level=3, biome="ash_canyon", room_count=6)
    w.add_extension(spec)
    w.register_starting_gate(0, 5, 5)
    w.total_playtime_seconds = 1234.5
    w.playtime_at_last_extension = 1000.0

    clone = World.from_dict(w.to_dict())
    return {
        "seed_preserved": clone.world_seed == 4242,
        "one_extension": len(clone.extensions) == 1,
        "one_gate": len(clone.gates) == 1,
        "playtime_preserved": abs(clone.total_playtime_seconds - 1234.5) < 1e-6,
        "errors": [],
    }


def test_gate_ids_unique_after_load():
    from world.world import World
    from world.gate import next_gate_id

    w = World(1)
    g1 = w.register_starting_gate(0, 1, 1)
    g2 = w.register_starting_gate(0, 2, 2)

    clone = World.from_dict(w.to_dict())
    g3 = clone.register_starting_gate(0, 3, 3)

    return {
        "fresh_gate_id_above_existing":
            g3.gate_id > max(g1.gate_id, g2.gate_id),
        "errors": [],
    }


# ===========================================================================
# Q. Personalization
# ===========================================================================

def test_personalization_weak_defense_and_enabler():
    from ai.personalization import derive_hints
    from history.player_profile import PlayerProfile
    p = PlayerProfile(level=10, main_damage="fire",
                      secondary_damage=None,
                      defensive_strength="low",
                      deaths={"fire": 5, "physical": 1},
                      boss_attempts=4, boss_successes=0)
    h = derive_hints(p)
    return {
        "weak_defense_set": h.weak_defense is True,
        "boss_struggling": h.boss_struggling is True,
        "fire_is_challenge": h.suggested_challenge_damage_type == "fire",
        "enabler_is_fallback_or_secondary":
            h.suggested_enabling_damage_type in ("chaos", "physical"),
        "frequent_death_fire_present": "fire" in h.frequent_death_causes,
        "errors": [],
    }


def test_personalization_fallback_enabler_table():
    from ai.personalization import _fallback_enabler
    pairs = {
        "physical": "lightning",
        "fire": "chaos",
        "cold": "physical",
        "lightning": "fire",
        "chaos": "physical",
    }
    bad = []
    for k, v in pairs.items():
        if _fallback_enabler(k) != v:
            bad.append((k, v, _fallback_enabler(k)))
    return {
        "table_matches": not bad,
        "unknown_defaults_physical": _fallback_enabler("unknown") == "physical",
        "bad": bad,
        "errors": [],
    }


def test_personalization_hints_serializable():
    from ai.personalization import PersonalizationHints
    h = PersonalizationHints(dominant_damage_type="fire", weak_defense=False)
    d = h.to_dict()
    return {
        "dict_has_dominant": d.get("dominant_damage_type") == "fire",
        "dict_has_weak_defense": d.get("weak_defense") is False,
        "json_roundtrips": json.loads(json.dumps(d)) == d,
        "errors": [],
    }


# ===========================================================================
# R. Profile aggregation
# ===========================================================================

def test_profile_from_empty_log():
    from history.aggregator import build_player_profile
    from history.event_log import EventLog
    from player.player import Player
    from world.world import World

    bus = _RecordingBus()
    log = EventLog(bus)
    player = Player(0, 0, bus)
    world = World(1)
    profile = build_player_profile(player, log, world)
    return {
        "level_default_1": profile.level == 1,
        "main_damage_physical": profile.main_damage == "physical",
        "no_weapon": profile.most_used_weapon is None,
        "errors": [],
    }





# ===========================================================================
# S. Config consistency
# ===========================================================================

def test_config_consistency():
    ok_seconds = config.WORLD_EXTENSION_SECONDS == config.WORLD_EXTENSION_HOURS * 3600
    ok_backend = config.AI_BACKEND_MODE in ("procedural", "mock_ai", "llm")
    ok_provider = config.LLM_PROVIDER in config.LLM_PROVIDER_PRESETS
    presets_have_base_url = all(
        "base_url" in p and "default_model" in p and "api_key_env" in p
        for p in config.LLM_PROVIDER_PRESETS.values())
    return {
        "extension_seconds_matches": ok_seconds,
        "backend_mode_valid": ok_backend,
        "default_provider_in_presets": ok_provider,
        "presets_complete": presets_have_base_url,
        "errors": [],
    }


# ===========================================================================
# T. Camera
# ===========================================================================

def test_camera_clamps_and_roundtrips():
    from world.camera import Camera
    cam = Camera(5000, 5000)
    cam.update(2500, 2500)
    # world_to_screen inverse
    world = (1234.5, 2345.6)
    screen = cam.world_to_screen(world)
    back = cam.screen_to_world(screen)
    roundtrip_ok = (abs(back[0] - world[0]) <= 1 and
                    abs(back[1] - world[1]) <= 1)

    # world smaller than screen -> clamps to 0
    small = Camera(200, 200)
    small.update(100, 100)
    clamped_to_zero = small.x == 0 and small.y == 0

    return {
        "roundtrip_ok": roundtrip_ok,
        "small_world_clamped": clamped_to_zero,
        "errors": [],
    }


def test_camera_shake_decays():
    from world.camera import Camera
    cam = Camera(5000, 5000)
    cam.shake(10, 0.1)
    cam.tick(0.05)
    mid = cam._shake_time
    cam.tick(0.1)
    after = cam._shake_time
    return {
        "shake_time_decreases": mid < 0.1,
        "shake_ends": after <= 0,
        "offsets_cleared": cam._shake_dx == 0 and cam._shake_dy == 0,
        "errors": [],
    }


# ===========================================================================
# U. Player entity
# ===========================================================================

def test_player_face_toward_zero_vector_noop():
    from player.player import Player
    p = Player(100, 100, _RecordingBus())
    original = (p.facing.x, p.facing.y)
    p.face_toward(100, 100)
    return {
        "facing_unchanged": (p.facing.x, p.facing.y) == original,
        "errors": [],
    }


def test_player_take_damage_invuln_window():
    from player.player import Player
    p = Player(0, 0, _RecordingBus())
    hp0 = p.effective_stats.hp
    applied1 = p.take_damage(10)
    hp1 = p.effective_stats.hp
    applied2 = p.take_damage(10)
    hp2 = p.effective_stats.hp
    return {
        "first_hit_applied": applied1 is True,
        "first_hit_reduced_hp": hp1 == hp0 - 10,
        "second_hit_blocked": applied2 is False,
        "hp_unchanged_on_block": hp2 == hp1,
        "errors": [],
    }


def test_player_death_emits_event():
    from player.player import Player
    bus = _RecordingBus()
    p = Player(0, 0, bus)
    p.take_damage(10 ** 6)
    names = [n for n, _ in bus.events]
    return {
        "death_event_emitted": "player_death" in names,
        "alive_false": p.alive is False,
        "errors": [],
    }


def test_player_meets_requirements():
    from player.player import Player
    from items.item import Item
    p = Player(0, 0, _RecordingBus())
    sword = Item("Sword", "weapon", weapon_subtype="sword")
    greatsword = Item("GS", "weapon", weapon_subtype="greatsword")
    armor = Item("Helm", "head")   # no subtype -> no requirement

    ok_sword, _ = p.meets_requirements(sword)
    ok_gs, missing = p.meets_requirements(greatsword)
    ok_armor, _ = p.meets_requirements(armor)
    return {
        "sword_ok": ok_sword is True,
        "greatsword_needs_more_str": ok_gs is False,
        "reports_missing_attr": any("Strength" in m for m in missing),
        "armor_ok": ok_armor is True,
        "errors": [],
    }


def test_player_heal_to_full():
    from player.player import Player
    p = Player(0, 0, _RecordingBus())
    p.effective_stats.hp = 1
    p.heal_to_full()
    return {
        "hp_is_max": p.effective_stats.hp == p.effective_stats.max_hp,
        "errors": [],
    }


# ===========================================================================
# V. Save manager
# ===========================================================================

def test_save_manager_missing_file_returns_none():
    from save.save_manager import SaveManager
    tmp = tempfile.mkdtemp(prefix="arpg_save_")
    try:
        sm = SaveManager(path=os.path.join(tmp, "does_not_exist.json"),
                         stash_path=os.path.join(tmp, "stash_missing.json"))
        return {
            "load_missing_none": sm.load() is None,
            "exists_false": sm.exists() is False,
            "stash_falls_back_to_empty": sm.load_stash() is not None,
            "errors": [],
        }
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_save_manager_corrupt_json_returns_none():
    from save.save_manager import SaveManager
    tmp = tempfile.mkdtemp(prefix="arpg_save_")
    try:
        path = os.path.join(tmp, "bad.json")
        with open(path, "w") as f:
            f.write("{not valid json at all")
        sm = SaveManager(path=path, stash_path=os.path.join(tmp, "st.json"))
        return {
            "corrupt_returns_none": sm.load() is None,
            "errors": [],
        }
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_save_manager_non_dict_top_level_returns_none():
    from save.save_manager import SaveManager
    tmp = tempfile.mkdtemp(prefix="arpg_save_")
    try:
        path = os.path.join(tmp, "list.json")
        with open(path, "w") as f:
            json.dump([1, 2, 3], f)
        sm = SaveManager(path=path, stash_path=os.path.join(tmp, "st.json"))
        return {
            "list_top_level_returns_none": sm.load() is None,
            "errors": [],
        }
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


# ===========================================================================
# W. Attack patterns
# ===========================================================================

def test_find_arc_targets_excludes_dead_and_sorts():
    from combat.attack_patterns import find_arc_targets
    from items.weapon_subtypes import AttackProfile
    from enemies.enemy import Enemy

    class _P:
        def __init__(self):
            self.x, self.y = 0, 0
            self.facing = pygame.Vector2(1, 0)

    profile = AttackProfile(kind="arc", range=200, arc_degrees=90, hits_all=True)
    p = _P()
    near = Enemy(50, 0, hp=10)
    mid = Enemy(120, 0, hp=10)
    dead = Enemy(80, 0, hp=0)
    dead.alive = False
    behind = Enemy(-100, 0, hp=10)  # behind facing -> excluded

    targets = [mid, dead, near, behind]
    hits = find_arc_targets(p, targets, profile)
    return {
        "dead_excluded": dead not in hits,
        "behind_excluded": behind not in hits,
        "nearest_first": hits[:2] == [near, mid],
        "errors": [],
    }


def test_find_arc_targets_zero_facing():
    from combat.attack_patterns import find_arc_targets
    from items.weapon_subtypes import AttackProfile

    class _P:
        def __init__(self):
            self.x, self.y = 0, 0
            self.facing = pygame.Vector2(0, 0)

    profile = AttackProfile(kind="arc", range=100)
    hits = find_arc_targets(_P(), [], profile)
    return {
        "returns_empty": hits == [],
        "errors": [],
    }


def test_resolve_attack_profile_unarmed_fallback():
    from combat.attack_patterns import resolve_attack_profile

    class _P:
        def __init__(self):
            self.equipped = {"weapon": None}

    prof = resolve_attack_profile(_P())
    return {
        "unarmed_kind_arc": prof.kind == "arc",
        "errors": [],
    }


# ===========================================================================
# X. LootRules
# ===========================================================================

def test_loot_rules_roundtrip():
    from world.loot_rules import LootRules
    r = LootRules(favored_damage_types={"fire": 1.3, "chaos": 1.5},
                  rare_chance_multiplier=1.2,
                  six_mod_chance_multiplier=1.1,
                  guarantee_enabling_affix="life")
    clone = LootRules.from_dict(r.to_dict())
    return {
        "damage_types_preserved": clone.favored_damage_types == r.favored_damage_types,
        "rare_mult_preserved": clone.rare_chance_multiplier == 1.2,
        "six_mod_mult_preserved": clone.six_mod_chance_multiplier == 1.1,
        "guarantee_preserved": clone.guarantee_enabling_affix == "life",
        "errors": [],
    }


def test_loot_rules_default_is_empty():
    from world.loot_rules import LootRules
    d = LootRules.default()
    return {
        "empty_damage_types": d.favored_damage_types == {},
        "neutral_multipliers": (d.rare_chance_multiplier == 1.0
                                and d.six_mod_chance_multiplier == 1.0),
        "no_guarantee": d.guarantee_enabling_affix is None,
        "errors": [],
    }


# ===========================================================================
# Y. DamageNumbers / HUD
# ===========================================================================

def test_damage_numbers_spawn_and_expire():
    pygame.font.init()
    from ui.hud import DamageNumbers
    dn = DamageNumbers()
    dn.spawn(0, 0, "10", (255, 255, 255))
    assert len(dn.numbers) == 1
    dn.update(0.5)
    alive_mid = len(dn.numbers)
    dn.update(0.5)   # total 1.0s > 0.8s lifetime
    return {
        "spawned": alive_mid >= 1,
        "expired_after_1s": len(dn.numbers) == 0,
        "errors": [],
    }


def test_hud_draw_smoke_zero_hp_and_full():
    pygame.font.init()
    from ui.hud import HUD
    from player.player import Player
    surf = pygame.Surface((config.SCREEN_WIDTH, config.SCREEN_HEIGHT))
    p = Player(0, 0, _RecordingBus())
    hud = HUD()

    errs = []
    try:
        hud.draw(surf, p, message="")
    except Exception as e:
        errs.append(f"full: {type(e).__name__}: {e}")

    p.effective_stats.hp = 0
    try:
        hud.draw(surf, p, message="dead")
    except Exception as e:
        errs.append(f"zero-hp: {type(e).__name__}: {e}")

    return {
        "draws_without_crash": not errs,
        "errors": errs,
    }


# ===========================================================================
# Z. Misc edge cases
# ===========================================================================

def test_item_comparison_none_and_real():
    from items.comparison import compare_items, format_diff_line
    from items.item_generator import generate_random_item
    rng = random.Random(1)

    no_compare = compare_items(None, None)
    one_sided = compare_items(generate_random_item(rng, area_level=5, force_slot="weapon"), None)

    lines = [format_diff_line(d) for d in one_sided]
    return {
        "both_none_empty": no_compare == [],
        "one_sided_non_empty": len(one_sided) >= 1,
        "lines_are_strings": all(isinstance(l, str) for l in lines),
        "errors": [],
    }


def test_tooltip_missing_creation_context_does_not_crash():
    from ui.tooltip import ItemTooltip
    from items.item import Item
    pygame.font.init()
    it = Item("Orphan", "weapon")
    it.creation_context = {}   # missing fields
    tip = ItemTooltip()
    try:
        lines = tip._build_info_lines(it, None)
        ok = True
    except Exception as e:
        ok = f"{type(e).__name__}: {e}"
        lines = []
    return {
        "no_crash": ok is True,
        "produced_lines": len(lines) > 0,
        "errors": [] if ok is True else [str(ok)],
    }


def test_extension_generator_fallback_chain():
    """If primary backend returns None, we must fall back to the next
    backend, and if all fail, to the guaranteed-safe hardcoded spec."""
    from world.extension_generator import WorldExtensionGenerator
    from ai.world_generator import WorldGenerator
    from history.player_profile import PlayerProfile

    class _NullGenerator(WorldGenerator):
        def generate_next_extension(self, player_profile, existing_ids, rng):
            return None

    gen = WorldExtensionGenerator(backend=_NullGenerator(),
                                  fallback_backend=_NullGenerator())
    spec = gen.generate(PlayerProfile(level=3), set(), random.Random(0))
    return {
        "returns_hardcoded_fallback": spec is not None,
        "id_prefix_correct": spec.extension_id.startswith("fallback_"),
        "valid_fields": (spec.area_level >= 1 and spec.room_count >= 2),
        "errors": [],
    }


def test_generator_handles_backend_exception():
    from world.extension_generator import WorldExtensionGenerator
    from ai.world_generator import WorldGenerator
    from history.player_profile import PlayerProfile

    class _Exploder(WorldGenerator):
        def generate_next_extension(self, *a, **k):
            raise RuntimeError("simulated backend failure")

    gen = WorldExtensionGenerator(backend=_Exploder(),
                                  fallback_backend=_Exploder())
    spec = gen.generate(PlayerProfile(level=3), set(), random.Random(0))
    return {
        "does_not_propagate_exception": spec is not None,
        "errors": [],
    }


def test_procedural_generator_repeated_ids_unique():
    from ai.procedural_generator import ProceduralWorldGenerator
    from history.player_profile import PlayerProfile

    gen = ProceduralWorldGenerator()
    rng = random.Random(1)
    existing = set()
    for _ in range(20):
        spec = gen.generate_next_extension(PlayerProfile(level=10), existing, rng)
        existing.add(spec.extension_id)

    return {
        "all_ids_unique": len(existing) == 20,
        "errors": [],
    }


def test_mock_generator_always_returns_spec_with_valid_json():
    from ai.mock_generator import MockAIWorldGenerator
    from history.player_profile import PlayerProfile

    # failure_rate=0 -> all valid JSON
    gen = MockAIWorldGenerator(failure_rate=0.0, verbose=False)
    rng = random.Random(7)
    fails = 0
    for _ in range(50):
        spec = gen.generate_next_extension(PlayerProfile(level=5), set(), rng)
        if spec is None:
            fails += 1
    return {
        "no_failures": fails == 0,
        "errors": [],
    }


def test_mock_generator_with_high_failure_rate_still_returns_something():
    """Some calls return None (broken), but the caller's fallback must
    handle it - not the mock itself."""
    from ai.mock_generator import MockAIWorldGenerator
    from history.player_profile import PlayerProfile

    gen = MockAIWorldGenerator(failure_rate=1.0, verbose=False)
    rng = random.Random(11)
    none_count = 0
    for _ in range(20):
        spec = gen.generate_next_extension(PlayerProfile(level=1), set(), rng)
        if spec is None:
            none_count += 1
    return {
        "some_nones_returned": none_count > 0,
        "errors": [],
    }


def test_prompt_builder_produces_strings():
    from ai.prompt_builder import PromptBuilder
    from history.player_profile import PlayerProfile
    prompt = PromptBuilder.build_extension_prompt(
        PlayerProfile(level=5, main_damage="fire"), {"ext_a", "ext_b"})
    schema = PromptBuilder.schema_description()
    return {
        "prompt_is_str": isinstance(prompt, str) and len(prompt) > 50,
        "mentions_level": "5" in prompt,
        "mentions_existing": "ext_a" in prompt and "ext_b" in prompt,
        "schema_is_str": isinstance(schema, str) and "extension_name" in schema,
        "errors": [],
    }


def test_damage_types_registry_all_via_json():
    import os as _os
    data_path = _os.path.join(_os.path.dirname(__file__), "..", "data", "damage_types.json")
    if not _os.path.exists(data_path):
        # Fallback: search near this file
        data_path = _os.path.join(_os.path.dirname(__file__), "data", "damage_types.json")
    with open(data_path) as f:
        data = json.load(f)
    return {
        "json_has_damage_types": "damage_types" in data,
        "non_empty": len(data["damage_types"]) >= 5,
        "errors": [],
    }


def test_tier_weights_shift_with_item_level():
    """Higher item level should produce better (lower-numbered) tiers
    more often - not strictly, but on average across many rolls."""
    from items.tiers import resolve_tier_for_roll

    def avg_tier(level):
        rng = random.Random(0)
        total = 0
        n = 400
        for _ in range(n):
            t = resolve_tier_for_roll("physical_damage", level, rng)
            total += int(t[1:])   # T1 = 1 (best), T5 = 5 (worst)
        return total / n

    lo = avg_tier(5)
    hi = avg_tier(80)
    return {
        "avg_tier_improves_with_level": hi < lo,
        "low": round(lo, 3),
        "high": round(hi, 3),
        "errors": [],
    }

def test_profile_counts_from_events():
    from core.events import EventBus
    from history.aggregator import build_player_profile
    from history.event_log import EventLog
    from player.player import Player
    from world.world import World

    bus = EventBus()                     # <-- real bus, wires subs through
    log = EventLog(bus)
    player = Player(0, 0, bus)
    world = World(1)

    bus.emit("skill_used", skill="basic_attack", damage_type="fire",
             weapon_name="Ember Wand")
    bus.emit("skill_used", skill="basic_attack", damage_type="fire",
             weapon_name="Ember Wand")
    bus.emit("skill_used", skill="basic_attack", damage_type="fire",
             weapon_name="Ember Wand")
    bus.emit("item_found", item_id=1, rarity="rare", is_six_mod=True)
    bus.emit("item_found", item_id=2, rarity="rare", is_six_mod=False)
    bus.emit("boss_attempt_started", boss_id="x")
    bus.emit("boss_killed", name="x")

    profile = build_player_profile(player, log, world)
    return {
        "main_damage_fire": profile.main_damage == "fire",
        "rare_count_2": profile.rare_items_found == 2,
        "six_mod_count_1": profile.six_mod_items_found == 1,
        "boss_attempts_1": profile.boss_attempts == 1,
        "boss_successes_1": profile.boss_successes == 1,
        "errors": [],
    }


def test_inventory_iteration_is_row_major():
    from items.inventory import Inventory
    from items.item import Item
    inv = Inventory(cols=3, rows=2)
    a = Item("A", "weapon")
    b = Item("B", "weapon")
    c = Item("C", "weapon")
    inv.add_item(a, cell=(2, 0))   # row 0, col 2
    inv.add_item(b, cell=(0, 1))   # row 1, col 0
    inv.add_item(c, cell=(1, 0))   # row 0, col 1
    items = inv.items
    return {
        "ordered_by_row_then_col": items == [c, a, b],
        "errors": [],
    }


def test_player_equip_weapon_and_swap_updates_primary_damage_type():
    from player.player import Player
    from items.item import Item
    from items.affix import RolledAffix

    p = Player(0, 0, _RecordingBus())
    p.base_stats.intelligence = 20  

    sword = Item("Sword", "weapon", weapon_subtype="sword")
    wand = Item("Wand", "weapon", weapon_subtype="short_wand")
    wand.prefixes = [RolledAffix(
        kind="prefix", affix_id="burning", display_name="Burning",
        modifier="fire_damage", damage_type="fire",
        tier="T3", value=10, creation_tier_range=[5, 15])]
    wand.recompute_flat_bonuses()

    p.inventory.add_item(sword)
    p.inventory.add_item(wand)

    p.equip(sword)
    sword_type = p.effective_stats.primary_damage_type

    p.equip(wand)
    wand_type = p.effective_stats.primary_damage_type

    return {
        "sword_is_physical": sword_type == "physical",
        "wand_is_fire": wand_type == "fire",
        "errors": [],
    }


# ===========================================================================
# main
# ===========================================================================

def main():
    tests = [
        # A. RNG
        test_rng_service_determinism,
        test_rng_streams_independent_and_repeatable,
        test_rng_derive_child_isolated,
        # B. Event bus
        test_event_bus_subscribe_and_emit,
        test_event_bus_log_is_capped,
        test_event_bus_emit_with_unknown_type_is_noop,
        # C. Damage pipeline edges
        test_damage_zero_or_negative_returns_one,
        test_damage_armor_formula,
        test_damage_conversion_percent_zero_and_one,
        test_damage_multiple_conversions_dont_inflate,
        test_damage_resistance_clamped,
        test_damage_dealt_rule_scoped_by_source,
        test_damage_taken_all_dtype_null,
        test_damage_taken_target_filter,
        # D. Item entity
        test_item_id_monotonic_for_new_items,
        test_item_id_reserved_from_explicit_load,
        test_item_six_mod_detection,
        test_item_serialization_roundtrip_preserves_everything,
        test_item_legacy_flat_item_preserves_numbers,
        test_item_other_bonus_unknown_modifier_is_zero,
        # E. Inventory / Stash
        test_inventory_specific_cell_placement,
        test_inventory_out_of_bounds_rejected,
        test_inventory_move_swaps_occupied_cells,
        test_inventory_roundtrip_preserves_cells,
        test_inventory_from_dict_reassigns_duplicate_ids,
        test_inventory_is_full_and_find_free_cell,
        test_stash_roundtrip,
        # F. Equipment slots
        test_resolve_equip_slot_ring_prefers_empty,
        test_resolve_equip_slot_all_single_targets,
        # G. Stats
        test_effective_stats_accumulate_from_equipment,
        test_effective_stats_resist_accumulation,
        test_effective_stats_weapon_sets_damage_type,
        test_effective_stats_carries_character_attributes,
        # H. Progression
        test_progression_exact_threshold_levels_up,
        test_progression_one_below_threshold_no_levelup,
        test_progression_multi_level,
        test_progression_lose_xp_never_below_zero,
        # I. Boss
        test_boss_phase_exact_thresholds,
        test_boss_vulnerable_double_damage,
        test_boss_death_clamps_hp_zero,
        # J. Enemy
        test_enemy_unknown_behavior_falls_back_to_chaser,
        test_enemy_ranged_fires_projectile_when_in_strafe_band,
        test_enemy_dead_does_not_update,
        # K. World modifiers
        test_world_rule_set_empty_multiplies_to_one,
        test_world_rule_set_multiple_modifiers_multiply,
        test_world_modifier_unknown_rule_type_ignored,
        test_world_rule_set_tier_overrides_merge,
        # L. Weapon subtypes
        test_weapon_subtypes_registry_integrity,
        test_weapon_subtypes_fallback_for_unknown,
        # M. Damage types
        test_damage_types_registry,
        # N. Tier tables
        test_tier_table_default_and_override,
        test_tier_table_unknown_modifier_raises,
        test_tier_resolution_respects_unlock_level,
        # O. Validators
        test_world_validator_accepts_boundary_values,
        test_world_validator_rejects_just_over_bounds,
        test_world_modifier_validator_delta_boundary,
        test_world_validator_no_player_target_on_kill,
        # P. Extension / World
        test_extension_serialization_roundtrip,
        test_world_serialization_roundtrip,
        test_gate_ids_unique_after_load,
        # Q. Personalization
        test_personalization_weak_defense_and_enabler,
        test_personalization_fallback_enabler_table,
        test_personalization_hints_serializable,
        # R. Profile aggregation
        test_profile_from_empty_log,
        test_profile_counts_from_events,
        # S. Config
        test_config_consistency,
        # T. Camera
        test_camera_clamps_and_roundtrips,
        test_camera_shake_decays,
        # U. Player entity
        test_player_face_toward_zero_vector_noop,
        test_player_take_damage_invuln_window,
        test_player_death_emits_event,
        test_player_meets_requirements,
        test_player_heal_to_full,
        # V. Save manager
        test_save_manager_missing_file_returns_none,
        test_save_manager_corrupt_json_returns_none,
        test_save_manager_non_dict_top_level_returns_none,
        # W. Attack patterns
        test_find_arc_targets_excludes_dead_and_sorts,
        test_find_arc_targets_zero_facing,
        test_resolve_attack_profile_unarmed_fallback,
        # X. Loot rules
        test_loot_rules_roundtrip,
        test_loot_rules_default_is_empty,
        # Y. UI smoke
        test_damage_numbers_spawn_and_expire,
        test_hud_draw_smoke_zero_hp_and_full,
        # Z. Misc
        test_item_comparison_none_and_real,
        test_tooltip_missing_creation_context_does_not_crash,
        test_extension_generator_fallback_chain,
        test_generator_handles_backend_exception,
        test_procedural_generator_repeated_ids_unique,
        test_mock_generator_always_returns_spec_with_valid_json,
        test_mock_generator_with_high_failure_rate_still_returns_something,
        test_prompt_builder_produces_strings,
        test_damage_types_registry_all_via_json,
        test_tier_weights_shift_with_item_level,
        test_inventory_iteration_is_row_major,
        test_player_equip_weapon_and_swap_updates_primary_damage_type,
    ]

    failures = 0
    for fn in tests:
        r = _run(fn.__name__, fn)
        bad_flags = [k for k, v in r.items() if v is False]
        if r.get("errors") or bad_flags:
            failures += 1
            if bad_flags:
                print(f"   !! failing checks in {fn.__name__}: {bad_flags}\n")
    print(f"=== SUMMARY: {len(tests) - failures}/{len(tests)} tests clean ===")


if __name__ == "__main__":
    main()