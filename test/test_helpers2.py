
"""
test_helpers2.py - supplementary systems tests for the AI-Evolving ARPG.

Companion to test_helpers.py. Covers systems that test_helpers only
touches indirectly:

  save/load roundtrip integrity
  gate system (register / open / travel linkage)
  world modifier rule math (multipliers, conversions, tier overrides,
  resistance modifiers)
  damage pipeline ordering (conversion -> resistance -> world rules)
  validator behavior (WorldValidator / ProgressionValidator rejecting
  bad specs, BossValidator capping HP)
  generator fallback chain (mock AI failing -> procedural fallback)
  personalization hints derivation
  legacy item immutability (creation_tier_range survives tier overrides)
  grid inventory + stash + equipment slot resolution
  RNG determinism (same world_seed -> identical streams)
  exploration tracker fractions
  event log -> player profile aggregation

Run:  python -m test.test_helpers2
Every test returns a dict; unexpected exceptions are caught and
recorded under 'errors' so one broken test never hides the rest.
"""
import random
import statistics
import traceback

import pygame  # noqa: F401  (required by several engine modules on import)


# ---------------------------------------------------------------------------
# small runner harness
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
    print(result)
    print()
    return result


# ---------------------------------------------------------------------------
# 1. RNG determinism
# ---------------------------------------------------------------------------

def test_rng_determinism():
    from core.rng import RNGService

    a = RNGService(12345)
    b = RNGService(12345)
    seq_a = [a.get_stream("loot").random() for _ in range(10)]
    seq_b = [b.get_stream("loot").random() for _ in range(10)]

    c = RNGService(99999)
    seq_c = [c.get_stream("loot").random() for _ in range(10)]

    return {
        "same_seed_same_stream": seq_a == seq_b,
        "different_seed_differs": seq_a != seq_c,
        "stream_isolation": (
            RNGService(7).get_stream("loot").random()
            != RNGService(7).get_stream("debug").random()
        ),
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 2. Save/load roundtrip (no display needed; uses raw to_dict/from_dict)
# ---------------------------------------------------------------------------

def test_save_load_roundtrip():
    from core.rng import RNGService
    from world.world import World
    from world.extension import ExtensionSpec
    from player.player import Player
    from core.events import EventBus
    from items.item_generator import generate_random_item

    bus = EventBus()
    world = World(42)
    spec = ExtensionSpec(
        extension_id="rt_1", extension_name="Roundtrip Region",
        area_level=5, biome="ash_canyon", is_anomaly=False,
        world_modifiers=[], boss_template_id=None, room_count=6,
    )
    ext = world.add_extension(spec)

    player = Player(100, 200, bus)
    player.progression.level = 9
    player.progression.xp = 123
    for _ in range(5):
        player.inventory.add_item(generate_random_item(random.Random(1), area_level=5))

    world_state = world.to_dict()
    player_state = player.to_dict()

    world2 = World.from_dict(world_state)
    player2 = Player.from_dict(player_state, bus)

    inv_ids_before = sorted(i.item_id for i in player.inventory.items)
    inv_ids_after = sorted(i.item_id for i in player2.inventory.items)

    return {
        "world_seed_preserved": world2.world_seed == 42,
        "extensions_preserved": [e.extension_id for e in world2.extensions] == ["rt_1"],
        "extension_spec_preserved": world2.extensions[0].spec.extension_name == "Roundtrip Region",
        "gates_survive_roundtrip": len(world2.gates) == len(world.gates),
        "player_level_preserved": player2.progression.level == 9,
        "player_xp_preserved": player2.progression.xp == 123,
        "inventory_ids_preserved": inv_ids_before == inv_ids_after,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 3. Gate system
# ---------------------------------------------------------------------------

def test_gate_system():
    from world.world import World
    from world.extension import ExtensionSpec

    world = World(7)

    # starting area gets its reserved closed gate
    g0 = world.register_starting_gate(room_index=0, tile_col=10, tile_row=10)
    assert not g0.is_open() and g0.is_starting_gate

    # generate two extensions; each must reserve its own closed gate
    spec_a = ExtensionSpec(extension_id="ext_a", extension_name="A",
                           area_level=2, biome="b", world_modifiers=[], room_count=5)
    spec_b = ExtensionSpec(extension_id="ext_b", extension_name="B",
                           area_level=3, biome="b", world_modifiers=[], room_count=5)
    ext_a = world.add_extension(spec_a)
    ext_b = world.add_extension(spec_b)
    ext_a.ensure_built(world.rng_service, world=world)
    ext_b.ensure_built(world.rng_service, world=world)

    gates_in_a = world.gates_in_area("ext_a")
    reserved_ok = len(gates_in_a) == 1 and not gates_in_a[0].is_open()

    # evolution opens a closed gate and links it to the new extension
    rng = random.Random(0)
    opened = world.open_next_gate_to("ext_b", rng)
    opened_ok = opened is not None and opened.is_open() and opened.target_extension_id == "ext_b"

    # open gates must never point at themselves' owner as a dead link
    open_gates = world.open_gates()
    links_valid = all(g.target_extension_id for g in open_gates)

    # roundtrip
    world2 = World.from_dict(world.to_dict())
    roundtrip_ok = (
        len(world2.gates) == len(world.gates)
        and world2.open_gates()[0].target_extension_id == "ext_b"
    )

    return {
        "starting_gate_registered_closed": not g0.is_open(),
        "extension_gate_reserved": reserved_ok,
        "evolution_opened_gate": opened_ok,
        "open_gate_links_valid": links_valid,
        "gates_survive_save_load": roundtrip_ok,
        "total_gates": len(world.gates),
        "open_gates": len(world.open_gates()),
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 4. World modifier rule math
# ---------------------------------------------------------------------------

def test_world_modifier_rules():
    from world.world_modifiers import WorldModifier, WorldRuleSet

    mod = WorldModifier.from_dict({
        "name": "Test Surge",
        "description": "",
        "rules": [
            {"type": "damage_taken_multiplier", "target": "minion",
             "damage_type": "fire", "multiplier": 2.0},
            {"type": "damage_dealt_multiplier", "target": "all",
             "damage_type": "lightning", "multiplier": 1.5},
            {"type": "conversion", "from_type": "physical", "to_type": "chaos",
             "percent": 0.5, "scope": "player"},
            {"type": "tier_override", "modifier": "fire_damage",
             "tiers": {"T1": [90, 115]}},
            {"type": "resistance_modifier", "target": "player",
             "damage_type": "cold", "delta": -0.25},
        ],
    })
    rs = WorldRuleSet([mod])

    return {
        "taken_mult_specific": rs.damage_multiplier_for("minion", "fire", "taken") == 2.0,
        "taken_mult_other_type_unaffected": rs.damage_multiplier_for("minion", "cold", "taken") == 1.0,
        "dealt_mult_all_targets": rs.damage_multiplier_for("boss", "lightning", "dealt") == 1.5,
        "conversion_scoped": (
            len(rs.conversion_rules_for("player")) == 1
            and len(rs.conversion_rules_for("enemy")) == 0
        ),
        "tier_override_shape": rs.tier_overrides.get("fire_damage", {}).get("T1") == [90, 115],
        "resistance_delta": rs.resistance_modifiers_for("player").get("cold") == -0.25,
        "multiplicative_stacking": (
    WorldRuleSet([
        WorldModifier.from_dict({
            "name": "x",
            "rules": [
                {"type": "damage_taken_multiplier", "target": "all",
                 "damage_type": None, "multiplier": 2.0},
                {"type": "damage_taken_multiplier", "target": "minion",
                 "damage_type": None, "multiplier": 3.0},
            ],
        }),          # <- closes from_dict(
    ]).damage_multiplier_for("minion", "fire", "taken") == 6.0
),
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 5. Damage pipeline ordering
# ---------------------------------------------------------------------------

def test_damage_pipeline():
    from combat.damage import resolve_damage_breakdown, ConversionRule
    from world.world_modifiers import WorldModifier, WorldRuleSet

    rs = WorldRuleSet([WorldModifier.from_dict({
        "name": "Corruption", "rules": [
            {"type": "damage_taken_multiplier", "target": "minion",
             "damage_type": "chaos", "multiplier": 2.0},
        ]})])

    # 100 physical, 50% converted to chaos, target has 50% physical resist.
    # physical: 50 * 0.5 = 25 ; chaos: 50 * 2.0(world) = 100 -> total 125
    total, breakdown = resolve_damage_breakdown(
        base_amount=100, damage_type="physical", source_stats=None,
        target_resistances={"physical": 0.5},
        conversion_rules=[ConversionRule("physical", "chaos", 0.5)],
        world_rules=rs, target_category="minion",
    )

    return {
        "total": total,
        "breakdown": {k: round(v, 2) for k, v in breakdown.items()},
        "conversion_applied": "chaos" in breakdown,
        "world_rule_applied_to_converted": round(breakdown.get("chaos", 0), 2) == 100.0,
        "resistance_applied_before_world_rule": total == 125,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 6. Validators
# ---------------------------------------------------------------------------

def test_validators():
    from ai.validators import (WorldValidator, WorldModifierValidator,
                               BossValidator, ProgressionValidator,
                               ItemValidator, EnemyValidator)
    from world.extension import ExtensionSpec

    good = ExtensionSpec(extension_id="v_1", extension_name="OK", area_level=5,
                         biome="b", world_modifiers=[], room_count=6)
    ok, errs = WorldValidator.validate(good, set(), player_level=5)

    dup = ExtensionSpec(extension_id="v_1", extension_name="Dup", area_level=5,
                        biome="b", world_modifiers=[], room_count=6)
    dup_ok, dup_errs = WorldValidator.validate(dup, {"v_1"}, player_level=5)

    absurd = ExtensionSpec(extension_id="v_2", extension_name="Absurd", area_level=9999,
                           biome="b", is_anomaly=True, world_modifiers=[], room_count=6)
    absurd_ok, absurd_errs = WorldValidator.validate(absurd, set(), player_level=1)

    bad_rule_ok, bad_rule_errs = WorldModifierValidator.validate({
        "name": "bad", "rules": [
            {"type": "damage_taken_multiplier", "target": "minion",
             "damage_type": "holy", "multiplier": 500},
        ]})

    boss_ok, boss_errs = BossValidator.validate({"max_hp": 10**9, "phases": []})
    item_ok, item_errs = ItemValidator.validate(
        {"slot": "weapon", "prefixes": [{"affix_id": "x", "value": 99999}]})
    enemy_ok, enemy_errs = EnemyValidator.validate({"hp": -5, "damage": 10**9})
    prog_ok, prog_errs = ProgressionValidator.validate(
        ExtensionSpec(extension_id="p", extension_name="P", area_level=50,
                      biome="b", world_modifiers=[], room_count=6), player_level=1)

    return {
        "good_spec_accepted": ok,
        "duplicate_id_rejected": not dup_ok and any("already exists" in e for e in dup_errs),
        "absurd_anomaly_rejected": not absurd_ok and absurd_errs != [],
        "invalid_rule_rejected": not bad_rule_ok and len(bad_rule_errs) >= 2,
        "boss_hp_capped": not boss_ok,
        "item_value_capped": not item_ok,
        "enemy_bounds_enforced": not enemy_ok,
        "progression_gap_enforced": not prog_ok,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 7. Generator fallback chain (mock AI forced to fail -> procedural)
# ---------------------------------------------------------------------------

def test_generator_fallback():
    from world.extension_generator import WorldExtensionGenerator
    from ai.mock_generator import MockAIWorldGenerator
    from ai.procedural_generator import ProceduralWorldGenerator
    from history.player_profile import PlayerProfile

    profile = PlayerProfile(level=10, main_damage="fire")
    existing = set()
    rng = random.Random(3)

    # failure_rate=1.0 -> mock ALWAYS produces broken output -> must fall back
    gen = WorldExtensionGenerator(
        backend=MockAIWorldGenerator(failure_rate=1.0, verbose=False),
        fallback_backend=ProceduralWorldGenerator(),
    )
    spec = gen.generate(profile, existing, rng)

    return {
        "fallback_spec_produced": spec is not None,
        "fallback_is_valid_extension": spec.extension_id not in existing and spec.area_level >= 1,
        "spec_source_is_not_mock": not spec.extension_id.startswith("ai_ext"),
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 8. Personalization hints
# ---------------------------------------------------------------------------

def test_personalization():
    from ai.personalization import derive_hints
    from history.player_profile import PlayerProfile

    p = PlayerProfile(
        level=20, main_damage="fire", secondary_damage=None,
        defensive_strength="low", deaths={"fire": 4, "physical": 1},
        average_exploration=0.1, extensions_generated=3,
        boss_attempts=5, boss_successes=0,
        six_mod_items_found=1, items_kept=2, items_discarded=9,
    )
    h = derive_hints(p)

    p2 = PlayerProfile(
        level=20, main_damage="cold", defensive_strength="high",
        boss_attempts=5, boss_successes=5, average_exploration=0.9,
        items_kept=20, items_discarded=1,
    )
    h2 = derive_hints(p2)

    return {
        "weak_defense_detected": h.weak_defense,
        "frequent_death_causes": h.frequent_death_causes == ["fire"],
        "under_explored": h.under_explored,
        "boss_struggling": h.boss_struggling and not h2.boss_struggling,
        "boss_dominant": h2.boss_dominant,
        "legacy_items_flagged": h.has_notable_legacy_items,
        "discarded_a_lot": h.discarded_a_lot and not h2.discarded_a_lot,
        "fallback_enabler_for_fire": h.suggested_enabling_damage_type == "chaos",
        "over_explored": h2.over_explored,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 9. Legacy item immutability under tier overrides
# ---------------------------------------------------------------------------

def test_legacy_item_immutability():
    from items.item_generator import generate_random_item
    from items.tiers import get_tier_table

    rng = random.Random(11)
    item = generate_random_item(rng, area_level=30)
    snapshot = [(a.affix_id, a.tier, a.value, list(a.creation_tier_range))
                for a in item.all_affixes()]

    # Mutate the world: fire_damage T1 range shifts drastically
    class FakeRules:
        tier_overrides = {"fire_damage": {"T1": [500, 600]}}

    mutated_table = get_tier_table(FakeRules())

    # The item's baked affixes must NOT change just because the table did
    after = [(a.affix_id, a.tier, a.value, list(a.creation_tier_range))
             for a in item.all_affixes()]

    fire_t1 = mutated_table["fire_damage"]["T1"]
    return {
        "item_affixes_unchanged_by_world_mutation": snapshot == after,
        "mutation_visible_in_table": fire_t1 == [500, 600],
        "creation_context_present": item.creation_context.get("item_level") == item.item_level,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 10. Grid inventory, stash, equipment slots
# ---------------------------------------------------------------------------

def test_inventory_stash_equipment():
    from items.inventory import Inventory
    from items.stash import Stash
    from items.item import Item
    from items.equipment_slots import resolve_equip_slot

    inv = Inventory(cols=4, rows=2)
    items = [Item(f"Item{i}", "weapon") for i in range(8)]
    added = sum(inv.add_item(it) for it in items)
    full_when_full = inv.is_full()
    ninth = Item("Overflow", "weapon")
    rejected = not inv.add_item(ninth)

    # swap move
    cell0 = inv._cell_for_id(items[0].item_id)
    cell1 = inv._cell_for_id(items[1].item_id)
    inv.move_item(cell0, cell1)

    # ring slot resolution prefers empty ring_1 then ring_2 then ring_1
    equipped = {"ring_1": None, "ring_2": None}
    r1 = resolve_equip_slot("ring", equipped)
    equipped["ring_1"] = items[0]
    r2 = resolve_equip_slot("ring", equipped)
    equipped["ring_2"] = items[1]
    r3 = resolve_equip_slot("ring", equipped)

    stash = Stash(cols=2, rows=1)
    s_added = stash.add_item(items[7])
    stash.remove_item(items[7])
    stash_dict = stash.to_dict()

    return {
        "filled_to_capacity": added == 8 and full_when_full,
        "overflow_rejected": rejected,
        "move_swap_works": inv.get_at(cell0).item_id == items[1].item_id,
        "ring_resolution_order": (r1, r2, r3) == ("ring_1", "ring_2", "ring_1"),
        "stash_add_remove": s_added and len(stash.items) == 0,
        "stash_roundtrip": Stash.from_dict(stash_dict).cols == stash.cols,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 11. Exploration tracker
# ---------------------------------------------------------------------------

def test_exploration_tracker():
    from history.exploration import ExplorationTracker
    from world.tilemap import TileMap

    grid = [[0, 0, 1], [0, 1, 1], [1, 1, 1]]  # 4 walkable tiles
    tm = TileMap(grid)
    tr = ExplorationTracker()
    tr.register_area("a1", tm)

    # stand still on tile (0,0); SAMPLE_INTERVAL forces a few updates
    for _ in range(3):
        tr.update(0.3, "a1", 10, 10)  # tile (0,0), tile_size=48

    frac = tr.fraction_for("a1")
    rt = ExplorationTracker.from_dict(tr.to_dict())
    return {
        "fraction_after_visits": round(frac, 3),
        "fraction_in_bounds": 0 < frac <= 0.25,
        "average_fraction": round(tr.average_fraction(), 3),
        "roundtrip_preserves_fraction": round(rt.fraction_for("a1"), 3) == round(frac, 3),
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 12. Event log -> profile aggregation
# ---------------------------------------------------------------------------

def test_event_log_aggregation():
    import core.config as config
    from core.events import EventBus
    from core.rng import RNGService
    from history.event_log import EventLog
    from history.aggregator import build_player_profile
    from player.player import Player
    from world.world import World

    bus = EventBus()
    log = EventLog(bus)
    world = World(1)
    player = Player(0, 0, bus)

    bus.emit("skill_used", skill="basic_attack", damage=10, target="x",
             damage_type="fire", weapon_name="Ember Wand")
    bus.emit("skill_used", skill="basic_attack", damage=10, target="x",
             damage_type="fire", weapon_name="Ember Wand")
    bus.emit("skill_used", skill="basic_attack", damage=10, target="x",
             damage_type="cold", weapon_name="Frost Blade")
    bus.emit("player_death", cause="fire")
    bus.emit("boss_attempt_started", boss_id="b")
    bus.emit("boss_killed", name="b")

    profile = build_player_profile(player, log, world, None)
    d = profile.to_dict()

    return {
        "main_damage_inferred": d["main_damage"] == "fire",
        "secondary_inferred": d["secondary_damage"] == "cold",
        "deaths_tracked": d["deaths"] == {"fire": 1} and d["total_deaths"] == 1,
        "boss_stats": d["boss_attempts"] == 1 and d["boss_successes"] == 1,
        "log_roundtrip_count": len(EventLog.from_dict(log.to_dict(), EventBus()).events) == 6,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 13. Boss factory + validator interplay
# ---------------------------------------------------------------------------

def test_boss_factory():
    import random as _r
    from enemies.boss_factory import build_personalized_boss_template
    from ai.validators import BossValidator

    rng = _r.Random(5)
    results = {}
    for archetype in ["dash_predator", "ranged_zoner", "reflector", "summoner", "berserker"]:
        t = build_personalized_boss_template(
            f"Boss of {archetype}",
            {"mechanic_archetype": archetype, "difficulty_bias": "normal",
             "punishes_damage_type": "fire"},
            area_level=10, rng=rng,
        )
        ok, errs = BossValidator.validate({"max_hp": t.max_hp, "phases": t.phases})
        results[archetype] = {
            "hp": t.max_hp, "phases": len(t.phases), "valid": ok,
        }

    # reflector must punish fire but never reach immunity
    ref = build_personalized_boss_template(
        "Ref", {"mechanic_archetype": "reflector", "punishes_damage_type": "fire"},
        area_level=10, rng=rng)
    punish_capped = ref.resistances.get("fire", 0) <= 0.35

    return {
        "archetypes": results,
        "reflector_punish_capped": punish_capped,
        "all_archetypes_valid": all(r["valid"] for r in results.values()),
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 14. Item generation sanity over many rolls (distribution + slot legality)
# ---------------------------------------------------------------------------

def test_item_generation_sanity():
    from items.item_generator import generate_random_item
    from combat.damage_types import DamageTypes

    rng = random.Random(2026)
    n = 300
    bad_affix = 0
    bad_slot = 0
    six_mod = 0
    ilvls = []
    for _ in range(n):
        it = generate_random_item(rng, area_level=20)
        ilvls.append(it.item_level)
        if it.is_six_mod():
            six_mod += 1
        if it.slot not in ("weapon", "armor", "ring", "amulet"):
            bad_slot += 1
        for a in it.all_affixes():
            if a.damage_type is not None and not DamageTypes.is_valid(a.damage_type):
                bad_affix += 1

    return {
        "rolls": n,
        "bad_slots": bad_slot,
        "invalid_damage_types": bad_affix,
        "six_mod_count": six_mod,
        "ilvl_min": min(ilvls), "ilvl_max": max(ilvls),
        "ilvl_range_valid": min(ilvls) >= 1 and max(ilvls) <= 22,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    tests = [
        ("test_rng_determinism", test_rng_determinism),
        ("test_save_load_roundtrip", test_save_load_roundtrip),
        ("test_gate_system", test_gate_system),
        ("test_world_modifier_rules", test_world_modifier_rules),
        ("test_damage_pipeline", test_damage_pipeline),
        ("test_validators", test_validators),
        ("test_generator_fallback", test_generator_fallback),
        ("test_personalization", test_personalization),
        ("test_legacy_item_immutability", test_legacy_item_immutability),
        ("test_inventory_stash_equipment", test_inventory_stash_equipment),
        ("test_exploration_tracker", test_exploration_tracker),
        ("test_event_log_aggregation", test_event_log_aggregation),
        ("test_boss_factory", test_boss_factory),
        ("test_item_generation_sanity", test_item_generation_sanity),
    ]
    failures = 0
    for name, fn in tests:
        r = _run(name, fn)
        if r.get("errors"):
            failures += 1
    print(f"=== SUMMARY: {len(tests) - failures}/{len(tests)} tests clean ===")


if __name__ == "__main__":
    main()