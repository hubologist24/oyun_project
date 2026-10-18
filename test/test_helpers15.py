"""
test_helpers14_extreme.py - extreme edge cases probing for hidden bugs.

Run:  python -m test.test_helpers14_extreme
"""

import os
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import math
import random
import tempfile
import traceback
from types import SimpleNamespace

import pygame


# ---------------------------------------------------------------------------
# harness (matches helpers11-14)
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
        self.log = []

    def emit(self, event_type, **payload):
        self.events.append((event_type, payload))
        self.log.append((event_type, payload))

    def subscribe(self, *a, **k):
        pass


def _make_game():
    from core.game import Game
    return Game()


def _open_no_ui(game):
    if game.inventory_ui.visible:
        game.inventory_ui.toggle()
    for ui, hide in ((game.evolution_banner, "dismiss"),
                     (game.profile_screen, "hide"),
                     (game.gateway_menu, "hide")):
        if ui.visible:
            getattr(ui, hide)()
    game._move_button_held = False
    game._attack_button_held = False
    game.player.move_target = None


def _clean_arena(game):
    _open_no_ui(game)
    a = game.current_area
    a.enemies.clear()
    a.bosses.clear()
    a.loot_drops.clear()
    game.player_projectiles.clear()
    game.damage_numbers.numbers.clear()
    game.crit_bursts.clear()
    game.player_swings.clear()
    p = game.player
    p.alive = True
    p.effective_stats.hp = p.effective_stats.max_hp
    p._invuln_timer = 0.0
    p._dash_timer = 0.0
    p._dash_cd = 0.0
    p.combo_count = 0
    p.combo_timer = 0.0
    p.trophies = []
    p.recalc_stats()
    game.reactions.reset()
    game.terrain.reset()
    game.kinetic.reset()
    game.nemesis.registry.records.clear()
    game.nemesis.reset_transient()
    game.dialogue = None
    game.event_bus.log.clear()
    return game


def _dummy_enemy(x, y, hp=100000, **kw):
    from enemies.enemy import Enemy
    return Enemy(x, y, hp=hp, **kw)


def _fake_player(primary, extra=(), subtype="sword"):
    affixes = [SimpleNamespace(damage_type=t) for t in extra]
    weapon = SimpleNamespace(all_affixes=lambda: affixes, weapon_subtype=subtype)
    return SimpleNamespace(
        effective_stats=SimpleNamespace(primary_damage_type=primary),
        equipped={"weapon": weapon},
    )


# ===========================================================================
# 1. Crit-splash kills 5 enemies at once - no double processing
# ===========================================================================

def test_crit_splash_kills_five_enemies_no_double_processing():
    # NOTE on why the crit point must not sit on an enemy:
    #   _on_critical_hit intentionally skips anything at `d < 1.0` from the
    #   crit origin (see "not the original target" comment in core/game.py).
    #   In production the skipped enemy is the one that already took the
    #   direct crit via _apply_player_hit; the splash is only meant for the
    #   *other* nearby enemies. When we call _on_critical_hit directly the
    #   "original target" never actually takes damage, so we must place the
    #   crit point in the middle of the cluster with no enemy sitting on it.
    game = _clean_arena(_make_game())
    p = game.player
    cx, cy = p.x + 80, p.y
    # 5 enemies arranged in a rough ring, min distance 15 from the crit point
    positions = [
        (cx - 15, cy),
        (cx + 15, cy),
        (cx, cy - 15),
        (cx, cy + 15),
        (cx + 15, cy + 15),
    ]
    enemies = [_dummy_enemy(x, y, hp=1) for (x, y) in positions]
    game.current_area.enemies.extend(enemies)

    counts = {}
    orig = game._on_enemy_killed

    def spy(enemy, _depth=0):
        counts[id(enemy)] = counts.get(id(enemy), 0) + 1
        return orig(enemy, _depth)

    game._on_enemy_killed = spy
    game._on_critical_hit(x=cx, y=cy, damage_type="physical",
                          crit_damage=500)

    return {
        "all_dead": all(not e.alive for e in enemies),
        "each_handled_exactly_once": all(counts.get(id(e), 0) == 1
                                         for e in enemies),
        "all_removed_from_area": all(e not in game.current_area.enemies
                                     for e in enemies),
        "info_counts": list(counts.values()),
    }



# ===========================================================================
# 2. Crit-splash kills a nemesis -> trophy awarded
# ===========================================================================

def test_crit_splash_kills_nemesis_awards_trophy():
    # Same origin-skip caveat as test #1: crit point must not sit on the
    # target. In production the crit was already dealt by _apply_player_hit,
    # so this mirrors "player crit another enemy, and the nemesis happens to
    # be caught in the burst".
    from history.nemesis import Nemesis
    game = _clean_arena(_make_game())
    p = game.player
    nem = Nemesis(name="Grix, the Flame-Eater", damage_type="fire")
    game.nemesis.registry.add(nem)

    e = _dummy_enemy(p.x + 60, p.y, hp=1)
    e.nemesis = nem
    e.is_elite = True
    game.current_area.enemies.append(e)
    before = len(p.trophies)

    # Crit ten pixels to the left of the nemesis; within CRIT_BURST_RADIUS=70
    # and far enough from the origin to not hit the d < 1.0 skip.
    game._on_critical_hit(x=p.x + 50, y=p.y, damage_type="physical",
                          crit_damage=500)

    return {
        "nemesis_enemy_dead": not e.alive,
        "trophy_awarded": len(p.trophies) == before + 1,
        "nemesis_status_pending": nem.status == "pending",
        "generation_bumped": nem.generation == 2,
    }


# ===========================================================================
# 3. Boss with huge summon_requests capped
# ===========================================================================

def test_boss_with_huge_summon_requests_is_capped():
    from enemies.boss import Boss
    from enemies.boss_factory import build_personalized_boss_template
    game = _clean_arena(_make_game())
    game.current_area.enemies.clear()
    sx, sy = game.current_area.start_pos
    t = build_personalized_boss_template(
        "Test Summoner",
        {"mechanic_archetype": "summoner", "difficulty_bias": "normal"},
        5, random.Random(0))
    b = Boss(sx, sy, t)
    b.region_id = "starting_area"
    b.summon_requests = 9999

    game._spawn_boss_adds(b)
    adds = sum(1 for e in game.current_area.enemies
               if getattr(e, "is_add", False))

    return {
        "adds_within_cap": adds <= 14,
        "adds_positive": adds > 0,
        "requests_reset": b.summon_requests == 0,
        "info_adds": adds,
    }


# ===========================================================================
# 4. Shatter kills target mid-on_hit -> aura still cleaned on next tick
# ===========================================================================

def test_shatter_kill_leaves_no_orphan_aura():
    game = _clean_arena(_make_game())
    rs = game.reactions
    e = _dummy_enemy(0, 0, hp=1)
    # Pre-freeze + pre-populate an aura entry
    rs._frozen[id(e)] = {"target": e, "left": 2.0}
    rs._entry(e)["types"]["fire"] = rs.clock + 3.0
    aura_present_before = id(e) in rs._auras

    p = _fake_player("fire")
    rs.on_hit(e, 1000, p, None)      # _shatter will kill it

    aura_still_there = id(e) in rs._auras
    rs.tick(10.0)                    # long cleanup tick

    return {
        "enemy_dead": not e.alive,
        "aura_was_present_after_shatter": aura_still_there,
        "aura_cleaned_after_tick": id(e) not in rs._auras,
        "aura_present_before_shatter": aura_present_before,
    }


# ===========================================================================
# 5. 1000 entropy_frost _delayed entries - no blow up
# ===========================================================================

def test_many_entropy_frost_delayed_queue_does_not_blow_up():
    game = _clean_arena(_make_game())
    rs = game.reactions
    targets = [_dummy_enemy(i * 10.0, 0, hp=1_000_000) for i in range(20)]
    game.current_area.enemies.extend(targets)

    for t in targets:
        for _ in range(50):
            rs._fire("entropy_frost", t, 10)

    initial = len(rs._delayed)
    rs.tick(2.0)                     # all delays expire and resolve
    final = len(rs._delayed)

    return {
        "no_crash": True,
        "delayed_drained": final < initial,
        "info_initial": initial,
        "info_final": final,
    }


# ===========================================================================
# 6. Multi-step conversion does NOT chain across rules
# ===========================================================================

def test_conversion_does_not_chain_across_rules():
    from combat.damage import resolve_damage_breakdown, ConversionRule
    rules = [
        ConversionRule("physical", "chaos", 0.5),
        ConversionRule("chaos", "fire", 1.0),   # would fire only if chaos re-processed
    ]
    total, br = resolve_damage_breakdown(100, "physical", None,
                                          conversion_rules=rules,
                                          target_category="minion")
    return {
        "total_preserved": total == 100,
        "chaos_produced": "chaos" in br,
        "physical_left": "physical" in br,
        "no_fire_produced": "fire" not in br,
        "chaos_amount": round(br.get("chaos", 0), 2),
        "physical_amount": round(br.get("physical", 0), 2),
    }


# ===========================================================================
# 7. Two extensions survive save/load - both bosses rebuilt
# ===========================================================================

def test_two_extensions_save_load_both_bosses():
    from world.extension import ExtensionSpec
    from world.expansion import attach_extension
    with tempfile.TemporaryDirectory() as d:
        game = _clean_arena(_make_game())
        game.save_manager.path = os.path.join(d, "save.json")
        game.save_manager.stash_path = os.path.join(d, "stash.json")

        for i in range(2):
            spec = ExtensionSpec(
                extension_id=f"ext_{i+1}",
                extension_name=f"Ext {i+1}",
                area_level=3, biome="ash_canyon", room_count=6,
                boss_template_id="personalized",
                boss_personalization={"mechanic_archetype": "dash_predator",
                                      "difficulty_bias": "normal"})
            ext = game.world.add_extension(spec)
            gate = game.world.pick_next_gate(
                game.world.rng_service.get_stream("gate_selection"))
            if gate is None:
                return {"errors": ["precondition: no closed gate"]}
            game.world.open_gate_to(gate, spec.extension_id)
            attach_extension(game.current_area, ext, gate,
                             game.world.rng_service, game.world)

        pre_ext = len(game.world.extensions)
        pre_boss = sum(1 for b in game.current_area.bosses
                       if b.name.startswith("Guardian"))
        game._save_game()
        game._load_game()
        post_ext = len(game.world.extensions)
        post_boss = sum(1 for b in game.current_area.bosses
                        if b.name.startswith("Guardian"))

        return {
            "two_saved": pre_ext == 2,
            "two_restored": post_ext == 2,
            "two_bosses_saved": pre_boss == 2,
            "two_bosses_restored": post_boss == 2,
        }


# ===========================================================================
# 8. Two nemeses in play simultaneously
# ===========================================================================

def test_two_nemeses_both_in_play_update():
    from history.nemesis import Nemesis
    game = _clean_arena(_make_game())
    p = game.player
    n1 = Nemesis(name="A, the Flame-Eater", damage_type="fire")
    n2 = Nemesis(name="B, the Frostbound", damage_type="cold")
    game.nemesis.registry.add(n1)
    game.nemesis.registry.add(n2)

    e1 = _dummy_enemy(p.x + 100, p.y)
    e1.nemesis = n1
    e2 = _dummy_enemy(p.x - 100, p.y)
    e2.nemesis = n2
    game.current_area.enemies.extend([e1, e2])

    for _ in range(120):
        game.nemesis.update(1 / 60.0)

    return {
        "both_counted_in_play": len(game.nemesis._in_play()) >= 2,
        "both_alive": e1.alive and e2.alive,
        "no_crash": True,
    }


# ===========================================================================
# 9. Boss phase transition only fires once across rapid hits
# ===========================================================================

def test_boss_phase_transition_fires_once_across_rapid_hits():
    from enemies.boss import Boss, BossTemplate
    game = _clean_arena(_make_game())
    p = game.player
    p.facing = pygame.Vector2(1, 0)
    b = Boss(p.x + 30, p.y, BossTemplate.hollow_warden())
    b.region_id = "starting_area"
    b.hp = int(b.max_hp * 0.61)
    game.current_area.bosses.append(b)

    calls = [0]
    orig = game._trigger_phase_transition

    def spy(boss):
        calls[0] += 1
        return orig(boss)

    game._trigger_phase_transition = spy
    rules = game._active_world_rules()

    for _ in range(5):
        game._apply_player_hit(b, rules, damage_multiplier=2.0)

    return {
        "entered_phase_1": b.phase_index == 1,
        "transition_fired_once": calls[0] == 1,
        "boss_alive": b.alive,
    }


# ===========================================================================
# 10. Boss killed by DoT, then re-killed by direct call - idempotent
# ===========================================================================

def test_boss_killed_by_dot_then_direct_no_double_process():
    from enemies.boss import Boss, BossTemplate
    game = _clean_arena(_make_game())
    p = game.player
    b = Boss(p.x + 30, p.y, BossTemplate.hollow_warden())
    b.region_id = "starting_area"
    b.hp = 100
    game.current_area.bosses.append(b)

    calls = [0]
    orig = game._on_boss_killed

    def spy(boss):
        already = getattr(boss, "_death_handled", False)
        result = orig(boss)
        if not already:
            calls[0] += 1
        return result

    game._on_boss_killed = spy

    # Kill via DoT tick (this path calls _on_boss_killed internally)
    game.reactions._add_dot(b, "bleed", 1000, 2.0)
    game.reactions.tick(0.6)

    # Try to kill again via direct call (should be a no-op)
    game._on_boss_killed(b)

    return {
        "boss_dead": not b.alive,
        "on_boss_killed_processed_once": calls[0] == 1,
        "loot_dropped": len(game.current_area.loot_drops) > 0,
    }


# ===========================================================================
# 11. Terrain fire respects a permanently-invulnerable player
# ===========================================================================

def test_terrain_fire_blocked_by_persistent_invuln():
    game = _clean_arena(_make_game())
    p = game.player
    p._invuln_timer = 100.0          # never expires during the test
    hp0 = p.effective_stats.hp
    game.terrain.add_surface(p.x, p.y, 60, "fire", 10.0, power=200)
    for s in game.terrain.surfaces:
        if s["kind"] == "fire":
            s["arm"] = 0.0
    for _ in range(20):
        game.terrain.update(0.25)
    return {
        "no_damage_while_invuln": p.effective_stats.hp == hp0,
    }


def test_terrain_fire_hurts_vulnerable_player():
    game = _clean_arena(_make_game())
    p = game.player
    p._invuln_timer = 0.0
    hp0 = p.effective_stats.hp
    game.terrain.add_surface(p.x, p.y, 60, "fire", 10.0, power=200)
    for s in game.terrain.surfaces:
        if s["kind"] == "fire":
            s["arm"] = 0.0
    for _ in range(8):
        game.terrain.update(0.25)
    return {
        "player_took_damage": p.effective_stats.hp < hp0,
        "hp_now": p.effective_stats.hp,
        "hp_before": hp0,
    }


# ===========================================================================
# 12. Enemy interrupt does not remove mid-flight projectile
# ===========================================================================

def test_enemy_interrupt_does_not_remove_mid_flight_projectile():
    game = _clean_arena(_make_game())
    e = _dummy_enemy(0, 0, damage=10, behavior="ranged")
    e.projectiles.append({
        "x": 50, "y": 50, "vx": 0, "vy": 0,
        "damage": 10, "life": 2.0,
    })
    before = len(e.projectiles)
    e.interrupt()
    after = len(e.projectiles)
    return {
        "projectile_persists": after == before == 1,
        "stagger_set": e.stagger_timer > 0,
        "ranged_timer_extended": e._ranged_timer >= 1.5,
    }


# ===========================================================================
# 13. Boss blink distance is bounded (never lands on player)
# ===========================================================================

def test_boss_blink_never_lands_exactly_on_player():
    from enemies.boss import Boss
    from enemies.boss_factory import build_personalized_boss_template
    game = _clean_arena(_make_game())
    p = game.player
    p.x, p.y = 1000, 1000

    t = build_personalized_boss_template(
        "Blinker",
        {"mechanic_archetype": "dash_predator", "difficulty_bias": "normal"},
        10, random.Random(0))
    b = Boss(p.x + 200, p.y, t)

    min_dist = float("inf")
    for _ in range(100):
        b.x, b.y = p.x + 200, p.y
        b._pending_action = "blink"
        b._execute_attack(p, _RecordingBus(), b.current_phase, None)
        min_dist = min(min_dist, math.hypot(b.x - p.x, b.y - p.y))

    return {
        "never_lands_on_player": min_dist > 100,
        "min_distance": round(min_dist, 2),
    }


# ===========================================================================
# 14. Two bosses in same area both tick without sharing mutable state
# ===========================================================================

def test_two_bosses_same_area_both_update():
    from enemies.boss import Boss, BossTemplate
    game = _clean_arena(_make_game())
    p = game.player
    b1 = Boss(p.x + 200, p.y, BossTemplate.hollow_warden())
    b1.region_id = "starting_area"
    b2 = Boss(p.x - 200, p.y, BossTemplate.hollow_warden())
    b2.region_id = "ext_x"
    game.current_area.bosses.extend([b1, b2])

    for _ in range(60):
        game._update(1 / 60.0)

    return {
        "both_alive": b1.alive and b2.alive,
        "both_still_in_list": (b1 in game.current_area.bosses
                                and b2 in game.current_area.bosses),
        "distinct_templates": b1.template is not b2.template,
        "distinct_resistances": b1.resistances is not b2.resistances,
    }


# ===========================================================================
# 15. Splash-only boss kill (no direct hit) fires full death path
# ===========================================================================

def test_splash_only_boss_kill_full_death_path():
    from enemies.boss import Boss, BossTemplate
    game = _clean_arena(_make_game())
    p = game.player
    b = Boss(p.x + 40, p.y, BossTemplate.hollow_warden())
    b.region_id = "starting_area"
    b.hp = 5
    game.current_area.bosses.append(b)

    game._on_critical_hit(x=b.x, y=b.y, damage_type="physical",
                          crit_damage=1000)

    return {
        "boss_dead": not b.alive,
        "death_handled_flag": getattr(b, "_death_handled", False),
        "loot_dropped": len(game.current_area.loot_drops) >= 5,
        "dialogue_set": game.dialogue is not None,
        "boss_killed_event": len(game.event_log.events_of_type("boss_killed")) == 1,
    }


# ===========================================================================
# 16. Greatsword delivers 3 elements in one hit -> at most 1 reaction
# ===========================================================================

def test_greatsword_multi_element_single_reaction():
    game = _clean_arena(_make_game())
    rs = game.reactions
    e = _dummy_enemy(0, 0, hp=1_000_000)
    game.current_area.enemies.append(e)

    p = _fake_player("fire", extra=("cold", "lightning"), subtype="greatsword")
    result = rs.on_hit(e, 20, p, None)

    # greatsword's _pick returns ALL types in one call.
    # Verify only ONE reaction fires (the ICD blocks subsequent ones)
    return {
        "single_reaction_fired": result is not None,
        "reaction_name": result,
        "aura_cleared_by_icd": "cd" in rs._auras.get(id(e), {})
            and rs._auras[id(e)]["cd"] > rs.clock,
    }


# ===========================================================================
# 17. DoT kill on a frozen boss add - cleanup of _frozen
# ===========================================================================

def test_dot_kill_on_frozen_enemy_cleans_frozen_entry():
    game = _clean_arena(_make_game())
    rs = game.reactions
    e = _dummy_enemy(0, 0, hp=5)
    game.current_area.enemies.append(e)
    rs._frozen[id(e)] = {"target": e, "left": 5.0}
    rs._add_dot(e, "bleed", 1000, 2.0)
    rs.tick(0.6)

    return {
        "enemy_dead": not e.alive,
        "frozen_cleaned": id(e) not in rs._frozen,
        "enemy_removed_from_area": e not in game.current_area.enemies,
    }


# ===========================================================================
# 18. Negative damage multiplier still clamps to at least 1
# ===========================================================================

def test_negative_damage_multiplier_still_deals_one():
    game = _clean_arena(_make_game())
    p = game.player
    p.facing = pygame.Vector2(1, 0)
    e = _dummy_enemy(p.x + 30, p.y, hp=100)
    game.current_area.enemies.append(e)
    hp0 = e.hp

    game._apply_player_hit(e, game._active_world_rules(), damage_multiplier=-5.0)

    return {
        "enemy_lost_at_least_1_hp": e.hp <= hp0 - 1,
        "enemy_not_overhealed": e.hp <= hp0,
    }


# ===========================================================================
# 19. Crit splash into boss that has 3 phases - single phase crossed
# ===========================================================================

def test_crit_splash_crosses_at_most_one_phase():
    from enemies.boss import Boss, BossTemplate
    game = _clean_arena(_make_game())
    p = game.player
    b = Boss(p.x + 40, p.y, BossTemplate.hollow_warden())
    b.region_id = "starting_area"
    b.hp = int(b.max_hp * 0.61)
    game.current_area.bosses.append(b)

    calls = [0]
    orig = game._trigger_phase_transition

    def spy(boss):
        calls[0] += 1
        return orig(boss)

    game._trigger_phase_transition = spy
    # Splash damage: 500*0.5 = 250. hp 854 -> 604. Crosses 0.60 (840) but not 0.25 (350)
    game._on_critical_hit(x=b.x, y=b.y, damage_type="physical",
                          crit_damage=500)

    return {
        "boss_alive": b.alive,
        "entered_phase_1": b.phase_index == 1,
        "single_transition": calls[0] == 1,
    }


# ===========================================================================
# main
# ===========================================================================

def main():
    tests = [
        test_crit_splash_kills_five_enemies_no_double_processing,
        test_crit_splash_kills_nemesis_awards_trophy,
        test_boss_with_huge_summon_requests_is_capped,
        test_shatter_kill_leaves_no_orphan_aura,
        test_many_entropy_frost_delayed_queue_does_not_blow_up,
        test_conversion_does_not_chain_across_rules,
        test_two_extensions_save_load_both_bosses,
        test_two_nemeses_both_in_play_update,
        test_boss_phase_transition_fires_once_across_rapid_hits,
        test_boss_killed_by_dot_then_direct_no_double_process,
        test_terrain_fire_blocked_by_persistent_invuln,
        test_terrain_fire_hurts_vulnerable_player,
        test_enemy_interrupt_does_not_remove_mid_flight_projectile,
        test_boss_blink_never_lands_exactly_on_player,
        test_two_bosses_same_area_both_update,
        test_splash_only_boss_kill_full_death_path,
        test_greatsword_multi_element_single_reaction,
        test_dot_kill_on_frozen_enemy_cleans_frozen_entry,
        test_negative_damage_multiplier_still_deals_one,
        test_crit_splash_crosses_at_most_one_phase,
    ]

    failures = 0
    failed = []
    for fn in tests:
        r = _run(fn.__name__, fn)
        if r.get("errors"):
            failures += 1
            failed.append(fn.__name__)
    print(f"=== SUMMARY: {len(tests) - failures}/{len(tests)} clean ===")
    if failed:
        print("Failed:")
        for n in failed:
            print(f"  - {n}")


if __name__ == "__main__":
    main()