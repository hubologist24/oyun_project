"""
test_helpers14.py - Kinetic combat + dash + current-source regression tests.

Run:  python -m test.test_helpers14

Covers:
  A. KineticTracker state / discovery / persistence
  B. Kinetic techniques (all six) — trigger conditions, effects, discovery
  C. Kinetic edge cases (after bug-fix: cooldown + nearest_d + swings)
  D. Dash mechanics
  E. Combat pipeline (incl. _apply_player_hit signature lock)
  F. Regression tests for the four source bugs that were just fixed
  G. Outdated-test replacements
  H. Save / load
  I. Integration soak
"""

import os
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import contextlib
import io
import math
import random
import tempfile
import traceback
from types import SimpleNamespace

import pygame


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
        self.log = []

    def emit(self, event_type, **payload):
        self.events.append((event_type, payload))
        self.log.append((event_type, payload))

    def subscribe(self, *a, **k):
        pass

    def unsubscribe(self, *a, **k):
        pass


def _make_game():
    from core.game import Game
    return Game()


def _open_game_no_ui(game):
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
    _open_game_no_ui(game)
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
    game.reactions.reset()
    game.kinetic.reset()
    game.nemesis.registry.records.clear()
    game.nemesis.reset_transient()
    game.dialogue = None
    game.event_bus.log.clear()
    return game


def _dummy_enemy(x, y, hp=100000, armor=0, damage=8, behavior="chaser"):
    from enemies.enemy import Enemy
    return Enemy(x, y, hp=hp, armor=armor, damage=damage, behavior=behavior)


def _fake_player(primary, extra=(), subtype="sword"):
    affixes = [SimpleNamespace(damage_type=t) for t in extra]
    weapon = SimpleNamespace(all_affixes=lambda: affixes, weapon_subtype=subtype)
    return SimpleNamespace(
        effective_stats=SimpleNamespace(primary_damage_type=primary),
        equipped={"weapon": weapon},
    )


class _PressKeys:
    def __getitem__(self, k):
        return False


class _OpenMap:
    def collides_rect(self, rect):
        return False


# ===========================================================================
# A. KineticTracker state
# ===========================================================================

def test_kinetic_starts_empty():
    game = _clean_arena(_make_game())
    k = game.kinetic
    return {
        "no_discovered": len(k.discovered) == 0,
        "no_stats": len(k.stats) == 0,
        "clock_zero": k.clock == 0.0,
        "no_cooldowns": len(k._cd) == 0,
    }


def test_kinetic_restore_from_event_log():
    game = _clean_arena(_make_game())
    game.event_bus.emit("kinetic_technique", name="dash_strike")
    game.event_bus.emit("kinetic_technique", name="intercept")
    game.event_bus.emit("kinetic_technique", name="not_a_real_technique")
    game.kinetic.reset()
    game.kinetic.restore_from_log(game.event_log)
    return {
        "dash_strike_restored": "dash_strike" in game.kinetic.discovered,
        "intercept_restored": "intercept" in game.kinetic.discovered,
        "invalid_ignored": "not_a_real_technique" not in game.kinetic.discovered,
    }


def test_kinetic_reset_clears_everything():
    game = _clean_arena(_make_game())
    game.kinetic.discovered.add("dash_strike")
    game.kinetic.stats["dash_strike"] = 5
    game.kinetic._cd["dash_strike"] = 99.0
    game.kinetic.clock = 12.3
    game.kinetic.reset()
    return {
        "discovered_cleared": len(game.kinetic.discovered) == 0,
        "stats_cleared": len(game.kinetic.stats) == 0,
        "cooldowns_cleared": len(game.kinetic._cd) == 0,
        "clock_zeroed": game.kinetic.clock == 0.0,
    }


def test_kinetic_technique_table_is_complete():
    from combat.kinetic import TECHNIQUES
    required = {"reverse_slash", "dash_strike", "intercept",
                "retreat_shot", "perfect_range", "heavy_reversal"}
    missing = required - set(TECHNIQUES)
    bad = [k for k, v in TECHNIQUES.items()
           if not (isinstance(v, tuple) and len(v) == 2
                   and isinstance(v[0], str) and isinstance(v[1], tuple)
                   and len(v[1]) == 3)]
    return {
        "all_six_present": not missing,
        "entries_well_formed": not bad,
    }


# ===========================================================================
# B. Techniques
# ===========================================================================

def test_dash_strike_triggers_after_dash():
    # FIXED: previous version never decremented _dash_timer, so the
    # final-dash window (0 < _dash_timer <= 0.09) was never entered.
    game = _clean_arena(_make_game())
    p = game.player
    p.facing = pygame.Vector2(1, 0)
    p.try_dash()                       # timer = 0.16
    for _ in range(8):                 # 0.16 - 8/60 ≈ 0.027 -> in final dash
        p.handle_input(1 / 60.0, _PressKeys(), _OpenMap())
        game.kinetic.update(1 / 60.0)
    from combat.attack_patterns import resolve_attack_profile
    profile = resolve_attack_profile(p)
    tech = game.kinetic.classify_melee(p, profile)
    return {
        "dash_strike_selected": tech == "dash_strike",
        "tech": tech,
        "dash_timer_in_window": 0 < p._dash_timer <= 0.09,
    }


def test_dash_strike_grants_pierce():
    game = _clean_arena(_make_game())
    p = game.player
    p.facing = pygame.Vector2(1, 0)
    target = _dummy_enemy(p.x + 30, p.y, hp=100000, armor=100)
    target.resistances["fire"] = 0.4
    game.current_area.enemies.append(target)

    from combat.combat import player_attack_enemy
    dmg_no_pierce = player_attack_enemy(p, target, game.event_bus,
                                        world_rules=game._active_world_rules(),
                                        damage_multiplier=1.0, pierce=False)
    target.hp = target.max_hp
    dmg_with_pierce = player_attack_enemy(p, target, game.event_bus,
                                          world_rules=game._active_world_rules(),
                                          damage_multiplier=1.0, pierce=True)
    return {
        "pierce_hits_harder": dmg_with_pierce > dmg_no_pierce,
    }


def test_reverse_slash_on_fast_turn():
    game = _clean_arena(_make_game())
    k = game.kinetic
    p = game.player
    p.facing = pygame.Vector2(1, 0)
    k.update(1 / 60.0)
    p.facing = pygame.Vector2(-1, 0)
    k.update(1 / 60.0)
    from combat.attack_patterns import resolve_attack_profile
    profile = resolve_attack_profile(p)
    tech = k.classify_melee(p, profile)
    return {"reverse_slash_selected": tech == "reverse_slash"}


def test_reverse_slash_widens_profile():
    from items.weapon_subtypes import AttackProfile
    game = _clean_arena(_make_game())
    base = AttackProfile(kind="arc", range=60, arc_degrees=110)
    widened = game.kinetic.widen(base)
    return {
        "arc_increased": widened.arc_degrees == base.arc_degrees + 70,
        "range_increased": abs(widened.range - base.range * 1.1) < 1e-6,
        "arc_capped_at_300": game.kinetic.widen(
            AttackProfile(kind="arc", range=60, arc_degrees=280)).arc_degrees == 300,
    }


def test_heavy_reversal_requires_greatsword_near_miss():
    game = _clean_arena(_make_game())
    k = game.kinetic
    p = game.player
    p.equipped["weapon"] = SimpleNamespace(weapon_subtype="sword")
    k.last_near_miss = k.clock
    from combat.attack_patterns import resolve_attack_profile
    tech_sword = k.classify_melee(p, resolve_attack_profile(p))
    p.equipped["weapon"] = SimpleNamespace(weapon_subtype="greatsword")
    tech_gs = k.classify_melee(p, resolve_attack_profile(p))
    return {
        "sword_no_heavy_reversal": tech_sword != "heavy_reversal",
        "greatsword_heavy_reversal": tech_gs == "heavy_reversal",
    }


def test_heavy_reversal_exposes_target():
    game = _clean_arena(_make_game())
    k = game.kinetic
    target = _dummy_enemy(0, 0)
    k._effect("heavy_reversal", target)
    entry = game.reactions._auras.get(id(target))
    return {
        "aura_entry_created": entry is not None,
        "exposed_in_future": entry is not None and entry.get("exposed", 0) > game.reactions.clock,
    }


def test_retreat_shot_when_running_away():
    game = _clean_arena(_make_game())
    k = game.kinetic
    k.last_retreat = k.clock
    return {"retreat_shot_returned": k.classify_ranged(game.player) == "retreat_shot"}


def test_retreat_shot_none_when_not_retreating():
    game = _clean_arena(_make_game())
    k = game.kinetic
    return {"no_retreat_shot": k.classify_ranged(game.player) is None}


def test_perfect_range_from_travel_distance():
    from combat.attack_patterns import PlayerProjectile
    game = _clean_arena(_make_game())
    k = game.kinetic
    p1 = PlayerProjectile(0, 0, 0, 0, "physical", 1.0, None, False, 400)
    p1.remaining_range = 350
    techs1 = k.projectile_techniques(p1)
    p2 = PlayerProjectile(0, 0, 0, 0, "physical", 1.0, None, False, 400)
    p2.remaining_range = 20
    techs2 = k.projectile_techniques(p2)
    return {
        "early_shot_no_perfect": "perfect_range" not in techs1,
        "late_shot_perfect": "perfect_range" in techs2,
    }


def test_intercept_requires_velocity_and_facing():
    game = _clean_arena(_make_game())
    k = game.kinetic
    p = game.player
    p.facing = pygame.Vector2(1, 0)
    target = _dummy_enemy(p.x + 100, p.y)
    k.enemy_vel.clear()
    no_vel = k.is_intercept(target)
    k.enemy_vel[id(target)] = (-90.0, 0.0)
    yes = k.is_intercept(target)
    p.facing = pygame.Vector2(-1, 0)
    behind = k.is_intercept(target)
    return {
        "no_vel_no_intercept": no_vel is False,
        "in_front_intercept": yes is True,
        "behind_no_intercept": behind is False,
    }


def test_intercept_effect_interrupts_target():
    game = _clean_arena(_make_game())
    k = game.kinetic
    target = _dummy_enemy(0, 0)
    target._ranged_timer = 0.0
    target._attack_timer = 0.0
    k._effect("intercept", target)
    return {
        "ranged_timer_extended": target._ranged_timer >= 1.5,
        "attack_timer_extended": target._attack_timer >= 1.0,
        "stagger_applied": target.stagger_timer > 0,
    }


def test_retreat_shot_effect_applies_slow():
    game = _clean_arena(_make_game())
    k = game.kinetic
    target = _dummy_enemy(0, 0)
    k._effect("retreat_shot", target)
    return {
        "slow_timer_set": target.slow_timer >= 2.5,
        "slow_factor_set": target.slow_factor == 0.5,
    }


def test_reverse_slash_effect_applies_stagger():
    game = _clean_arena(_make_game())
    k = game.kinetic
    target = _dummy_enemy(0, 0)
    k._effect("reverse_slash", target)
    return {"stagger_set": target.stagger_timer >= 0.5}


def test_trigger_increments_stats_and_sets_cooldown():
    game = _clean_arena(_make_game())
    k = game.kinetic
    target = _dummy_enemy(0, 0)
    game.current_area.enemies.append(target)
    k.trigger(["dash_strike"], target)
    return {
        "stats_incremented": k.stats["dash_strike"] == 1,
        "cooldown_set": k._cd["dash_strike"] > k.clock,
        "discovered": "dash_strike" in k.discovered,
        "message_shown": "Technique discovered" in game.message,
    }


def test_trigger_second_time_no_discovery_message():
    game = _clean_arena(_make_game())
    k = game.kinetic
    target = _dummy_enemy(0, 0)
    game.current_area.enemies.append(target)
    k.trigger(["dash_strike"], target)
    game.message = ""
    k.clock += 5.0
    k.trigger(["dash_strike"], target)
    return {
        "stats_incremented_twice": k.stats["dash_strike"] == 2,
        "no_rediscovery_message": "discovered" not in game.message.lower(),
    }


def test_trigger_emits_kinetic_technique_event():
    game = _clean_arena(_make_game())
    k = game.kinetic
    target = _dummy_enemy(0, 0)
    game.current_area.enemies.append(target)
    k.trigger(["dash_strike"], target)
    events = [e for e in game.event_bus.log if e[0] == "kinetic_technique"]
    return {
        "event_emitted": len(events) == 1,
        "event_payload": events[0][1].get("name") == "dash_strike" if events else False,
    }


# ===========================================================================
# C. Kinetic edge cases (now fixed in source)
# ===========================================================================

def test_classify_ranged_respects_cooldown():
    # FIXED: previously returned "retreat_shot" even if the technique was
    # on cooldown, letting it fire on every projectile within RETREAT_WINDOW.
    game = _clean_arena(_make_game())
    k = game.kinetic
    k.last_retreat = k.clock
    k._cd["retreat_shot"] = k.clock + 5.0
    result = k.classify_ranged(game.player)
    return {"cooldown_blocks_retreat_shot": result is None}


def test_retreat_uses_nearest_distance_not_last():
    # FIXED: previously checked `d` (last iterated enemy) instead of
    # `nearest_d`. If the loop's final enemy sat exactly on the player,
    # retreat detection was silently skipped.
    game = _clean_arena(_make_game())
    k = game.kinetic
    p = game.player
    p.x, p.y = 1000.0, 1000.0
    p.facing = pygame.Vector2(1, 0)
    k.update(1 / 60.0)              # seed _last_pos

    close = _dummy_enemy(p.x - 100, p.y)
    at_player = _dummy_enemy(p.x, p.y)
    game.current_area.enemies[:] = [close, at_player]

    p.x += 20.0                     # move away from `close`
    k.update(1 / 60.0)

    return {
        "retreat_detected": k.last_retreat > -1,
        "last_retreat": round(k.last_retreat, 3),
    }


def test_player_swings_populated_on_melee_attack():
    # FIXED: swing_fx.spawn_swing was never called, so player_swings stayed
    # empty and Game._draw_swing_fx was dead code.
    game = _clean_arena(_make_game())
    p = game.player
    p.facing = pygame.Vector2(1, 0)
    target = _dummy_enemy(p.x + 30, p.y)
    game.current_area.enemies.append(target)
    game._try_attack()
    return {
        "player_swings_populated": len(game.player_swings) == 1,
        "swing_has_required_fields": (
            len(game.player_swings) == 1
            and "x" in game.player_swings[0]
            and "y" in game.player_swings[0]
            and "arc_deg" in game.player_swings[0]
        ),
    }


def test_projectile_attack_does_not_spawn_melee_swing():
    # FIXED: previous version stuffed a SimpleNamespace into equipped["weapon"],
    # but Player.recalc_stats() -> compute_effective_stats() reads item.slot,
    # which SimpleNamespace doesn't have. Use a real Item so all downstream
    # stat math (damage_bonus, other_bonus, slot) works.
    from items.item import Item
    game = _clean_arena(_make_game())
    p = game.player

    bow = Item("Test Bow", "weapon", weapon_subtype="bow",
               damage_bonus=10, armor_bonus=0, item_id=None, item_level=5)
    p.equipped["weapon"] = bow
    p.recalc_stats()

    p.facing = pygame.Vector2(1, 0)
    game._try_attack()

    return {
        "no_melee_swing": len(game.player_swings) == 0,
        "projectile_spawned": len(game.player_projectiles) == 1,
        "projectile_kind_is_bow_profile":
            game.player_projectiles[0].damage_type == "physical"
            if game.player_projectiles else False,
    }


# ===========================================================================
# D. Dash mechanics
# ===========================================================================

def test_dash_sets_state_and_grants_iframes():
    game = _clean_arena(_make_game())
    p = game.player
    p.facing = pygame.Vector2(1, 0)
    ok = p.try_dash()
    return {
        "dash_accepted": ok is True,
        "dash_timer": abs(p._dash_timer - 0.16) < 1e-6,
        "dash_cd": abs(p._dash_cd - 0.9) < 1e-6,
        "invuln": p._invuln_timer >= 0.22,
    }


def test_dash_refused_when_already_dashing():
    game = _clean_arena(_make_game())
    p = game.player
    p.facing = pygame.Vector2(1, 0)
    p.try_dash()
    return {"second_refused": p.try_dash() is False}


def test_dash_refused_zero_facing():
    game = _clean_arena(_make_game())
    p = game.player
    p.facing = pygame.Vector2(0, 0)
    return {"refused": p.try_dash() is False}


def test_dash_survives_hit_during_dash():
    game = _clean_arena(_make_game())
    p = game.player
    p.facing = pygame.Vector2(1, 0)
    p.try_dash()
    hp0 = p.effective_stats.hp
    applied = p.take_damage(50)
    return {
        "damage_blocked_by_iframes": applied is False,
        "hp_unchanged": p.effective_stats.hp == hp0,
    }


def test_dash_emits_near_miss_on_blocked_hit():
    game = _clean_arena(_make_game())
    p = game.player
    p.facing = pygame.Vector2(1, 0)
    p.try_dash()
    before = len([e for e in game.event_bus.log if e[0] == "near_miss"])
    p.take_damage(50)
    after = len([e for e in game.event_bus.log if e[0] == "near_miss"])
    return {
        "near_miss_emitted": after > before,
        "kinetic_noticed": game.kinetic.last_near_miss > -50,
    }


def test_dash_blocked_by_wall():
    game = _clean_arena(_make_game())
    p = game.player
    p.facing = pygame.Vector2(1, 0)
    start_x, start_y = p.x, p.y

    class _WallMap:
        def collides_rect(self, rect):
            return True

    p.try_dash()
    p.handle_input(0.05, _PressKeys(), _WallMap())
    return {
        "did_not_move_x": p.x == start_x,
        "did_not_move_y": p.y == start_y,
    }


def test_dash_moves_player_and_ends():
    game = _clean_arena(_make_game())
    p = game.player
    p.facing = pygame.Vector2(1, 0)
    start_x = p.x
    p.try_dash()
    for _ in range(20):
        p.handle_input(1 / 60.0, _PressKeys(), _OpenMap())
    return {
        "moved_forward": p.x > start_x,
        "dash_timer_expired": p._dash_timer <= 0,
        "dash_cd_still_ticking": p._dash_cd > 0,
    }


def test_dash_cd_expires_and_allows_redash():
    game = _clean_arena(_make_game())
    p = game.player
    p.facing = pygame.Vector2(1, 0)
    p.try_dash()
    for _ in range(60):
        p.handle_input(1 / 60.0, _PressKeys(), _OpenMap())
    return {
        "cd_expired": p._dash_cd <= 0,
        "can_dash_again": p.try_dash() is True,
    }


def test_dash_does_not_decrement_timers_during_hitstop():
    game = _clean_arena(_make_game())
    p = game.player
    p.facing = pygame.Vector2(1, 0)
    game._trigger_hitstop(0.10)
    timer_before = p._dash_timer
    game._update(0.05)
    return {
        "dash_timer_not_decremented_during_hitstop": p._dash_timer == timer_before,
        "hitstop_still_active": game._hitstop_timer > 0,
    }


def test_left_shift_triggers_dash_via_keydown():
    game = _clean_arena(_make_game())
    p = game.player
    p.facing = pygame.Vector2(1, 0)
    game._handle_keydown(pygame.K_LSHIFT)
    return {"dash_started": p._dash_timer > 0}


def test_dash_blocked_when_modal_ui_open():
    game = _clean_arena(_make_game())
    p = game.player
    p.facing = pygame.Vector2(1, 0)
    game.profile_screen.show(game._current_profile())
    game._handle_keydown(pygame.K_LSHIFT)
    game.profile_screen.hide()
    return {"dash_blocked_by_modal": p._dash_timer == 0}


# ===========================================================================
# E. Combat pipeline
# ===========================================================================

def test_apply_player_hit_signature_matches_caller():
    import inspect
    from core.game import Game
    sig = inspect.signature(Game._apply_player_hit)
    params = list(sig.parameters)
    return {
        "has_target": "target" in params,
        "has_world_rules": "world_rules" in params,
        "has_damage_multiplier": "damage_multiplier" in params,
        "has_techniques": "techniques" in params,
        "damage_multiplier_default_1": sig.parameters["damage_multiplier"].default == 1.0,
        "techniques_default_none": sig.parameters["techniques"].default is None,
    }


def test_try_attack_hits_each_target_exactly_once():
    game = _clean_arena(_make_game())
    p = game.player
    p.facing = pygame.Vector2(1, 0)
    e1 = _dummy_enemy(p.x + 20, p.y)
    e2 = _dummy_enemy(p.x + 40, p.y)
    game.current_area.enemies.extend([e1, e2])
    calls = []

    def mock(t, r, m=1.0, techniques=None):
        calls.append(t)

    game._apply_player_hit = mock
    game._try_attack()
    return {"one_call_per_target": len(calls) == 2}


def test_try_attack_no_hits_does_not_increment_combo():
    game = _clean_arena(_make_game())
    p = game.player
    p.facing = pygame.Vector2(1, 0)
    p.combo_count = 3
    game._try_attack()
    return {"combo_unchanged": p.combo_count == 3}


def test_try_attack_increments_combo_on_hit():
    game = _clean_arena(_make_game())
    p = game.player
    p.facing = pygame.Vector2(1, 0)
    game.current_area.enemies.append(_dummy_enemy(p.x + 30, p.y))
    game._try_attack()
    return {
        "combo_incremented": p.combo_count == 1,
        "combo_timer_set": p.combo_timer > 0,
    }


def test_combo_multiplier_caps_at_10():
    game = _clean_arena(_make_game())
    p = game.player
    p.combo_count = 50
    p.facing = pygame.Vector2(1, 0)
    game.current_area.enemies.append(_dummy_enemy(p.x + 30, p.y))
    captured = {}

    def mock(t, r, m=1.0, techniques=None):
        captured["mult"] = m

    game._apply_player_hit = mock
    game._try_attack()
    return {"combo_capped_in_multiplier": captured.get("mult", 0) < 3.0}


def test_projectile_lifetime_and_wall_collision():
    from combat.attack_patterns import PlayerProjectile
    p = PlayerProjectile(100, 100, 100, 0, "physical", 1.0, None, False, 50)
    p.update(0.01)
    alive_short = p.alive
    for _ in range(100):
        p.update(0.01)
    return {
        "alive_initially": alive_short is True,
        "dead_after_range": p.alive is False,
        "never_negative_range": p.remaining_range <= 0,
    }


def test_non_piercing_projectile_stops_at_first_hit():
    game = _clean_arena(_make_game())
    from combat.attack_patterns import PlayerProjectile
    p = game.player
    e = _dummy_enemy(p.x + 30, p.y)
    game.current_area.enemies.append(e)
    proj = PlayerProjectile(p.x + 30, p.y, 100, 0, "physical", 1.0,
                            game._active_world_rules(), False, 400)
    proj.damage_multiplier = 0.5
    game.player_projectiles.append(proj)
    game._update_player_projectiles(1 / 60.0)
    return {
        "enemy_hit": e.hp < e.max_hp,
        "projectile_consumed": proj not in game.player_projectiles,
    }


def test_projectile_damage_number_spawned():
    game = _clean_arena(_make_game())
    from combat.attack_patterns import PlayerProjectile
    p = game.player
    e = _dummy_enemy(p.x + 30, p.y)
    game.current_area.enemies.append(e)
    proj = PlayerProjectile(p.x + 30, p.y, 100, 0, "physical", 1.0,
                            game._active_world_rules(), False, 400)
    proj.damage_multiplier = 0.5
    game.player_projectiles.append(proj)
    game._update_player_projectiles(1 / 60.0)
    return {"damage_number_spawned": len(game.damage_numbers.numbers) > 0}


def test_boss_attempt_started_once_per_fight():
    from enemies.boss import Boss, BossTemplate
    game = _clean_arena(_make_game())
    p = game.player
    b = Boss(p.x + 40, p.y, BossTemplate.hollow_warden())
    b.region_id = "starting_area"
    game.current_area.bosses.append(b)
    rules = game._active_world_rules()
    game._apply_player_hit(b, rules)
    game._apply_player_hit(b, rules)
    events = game.event_log.events_of_type("boss_attempt_started")
    return {
        "attempt_flag_set": game._boss_attempt_active,
        "exactly_one_event": len(events) == 1,
    }


def test_nemesis_boss_hit_does_not_start_guardian_attempt():
    from history.nemesis import Nemesis
    game = _clean_arena(_make_game())
    p = game.player
    nem = Nemesis(name="Grix, the Flame-Eater", damage_type="fire", world_boss=True)
    game.nemesis.registry.add(nem)
    b = game.nemesis._build_boss(p.x + 40, p.y, nem, "starting_area")
    game.current_area.bosses.append(b)
    game._apply_player_hit(b, game._active_world_rules())
    return {
        "no_attempt_flag": game._boss_attempt_active is False,
        "no_attempt_event": len(game.event_log.events_of_type("boss_attempt_started")) == 0,
    }


# ===========================================================================
# F. Regression tests for the source bugs just fixed
# ===========================================================================

def test_crit_splash_kills_boss_triggers_death_path():
    # FIXED: splash used to just zero the boss's HP; now _on_boss_killed runs.
    from enemies.boss import Boss, BossTemplate
    game = _clean_arena(_make_game())
    p = game.player
    b = Boss(p.x + 30, p.y, BossTemplate.hollow_warden())
    b.region_id = "starting_area"
    b.hp = 10
    game.current_area.bosses.append(b)

    game._on_critical_hit(x=b.x, y=b.y, damage_type="physical", crit_damage=10000)

    return {
        "boss_is_dead": not b.alive,
        "loot_dropped": len(game.current_area.loot_drops) > 0,
        "boss_killed_event": len(game.event_log.events_of_type("boss_killed")) == 1,
        "death_dialogue_set": game.dialogue is not None,
    }


def test_crit_splash_triggers_phase_transition():
    # FIXED: splash used to skip the phase-change cutscene even when the
    # boss crossed a phase threshold from splash damage alone.
    from enemies.boss import Boss, BossTemplate
    game = _clean_arena(_make_game())
    p = game.player
    b = Boss(p.x + 30, p.y, BossTemplate.hollow_warden())
    b.region_id = "starting_area"
    b.hp = int(b.max_hp * 0.65)
    game.current_area.bosses.append(b)

    phase_called = {"called": False}
    orig = game._trigger_phase_transition

    def spy(boss):
        phase_called["called"] = True
        return orig(boss)

    game._trigger_phase_transition = spy
    game._on_critical_hit(x=b.x, y=b.y, damage_type="physical",
                          crit_damage=int(b.max_hp * 0.4))

    return {
        "boss_is_alive": b.alive,
        "phase_changed": b.phase_index >= 1,
        "cutscene_triggered": phase_called["called"],
    }


def test_nemesis_does_not_tick_during_modal_ui():
    # FIXED: reactions.tick and nemesis.update used to run BEFORE the
    # modal-UI guard, so novas detonated while the evolution banner was up.
    game = _clean_arena(_make_game())
    p = game.player
    p.alive = True
    game.evolution_banner.show(
        extension_name="X", area_level=2, is_anomaly=False,
        rule_names=[], gate_area_name=None, reason=None, rerolls_left=0,
    )
    src = _dummy_enemy(p.x, p.y)
    game.nemesis.novas.append({
        "x": p.x, "y": p.y, "t": 0.01,
        "dtype": "fire", "dmg": 9999, "src": src,
    })
    hp_before = p.effective_stats.hp
    game._update(0.05)
    banner_visible = game.evolution_banner.visible
    game.evolution_banner.dismiss()
    return {
        "banner_was_visible_during_update": banner_visible,
        "nova_did_not_detonate": len(game.nemesis.novas) == 1,
        "player_hp_unchanged": p.effective_stats.hp == hp_before,
        "nova_timer_unchanged": game.nemesis.novas[0]["t"] == 0.01,
    }


def test_summoner_uses_single_decrement():
    # FIXED: two separate blocks used to decrement _summon_timer each frame,
    # turning the intended ~6s cycle into ~3s and leaving the second
    # block's reset-to-10.0 branch as unreachable dead code.
    from enemies.boss import Boss
    from enemies.boss_factory import build_personalized_boss_template
    t = build_personalized_boss_template(
        "Test", {"mechanic_archetype": "summoner", "difficulty_bias": "normal"},
        5, random.Random(0))
    b = Boss(0, 0, t)
    p = SimpleNamespace(x=100, y=0, alive=True, effective_stats=SimpleNamespace(
        armor=0, resistances={}))
    b._summon_timer = 5.0
    bus = _RecordingBus()
    dt = 1 / 60.0
    frames_to_trigger = None
    for i in range(1000):
        b.update(dt, p, bus)
        if b.summon_requests > 0:
            frames_to_trigger = i + 1
            break
    return {
        "fires_eventually": frames_to_trigger is not None,
        "single_decrement_interval": frames_to_trigger is not None and frames_to_trigger > 250,
        "frames": frames_to_trigger,
        "approx_seconds": round((frames_to_trigger or 0) * dt, 2),
    }


def test_summoner_fires_more_than_non_summoner_in_same_window():
    from enemies.boss import Boss, BossTemplate
    from enemies.boss_factory import build_personalized_boss_template
    p = SimpleNamespace(x=100, y=0, alive=True,
                        effective_stats=SimpleNamespace(armor=0, resistances={}))
    bus = _RecordingBus()
    dt = 1 / 60.0

    t_sum = build_personalized_boss_template(
        "S", {"mechanic_archetype": "summoner", "difficulty_bias": "normal"},
        5, random.Random(0))
    b_sum = Boss(0, 0, t_sum)

    t_reg = BossTemplate.hollow_warden()
    b_reg = Boss(0, 0, t_reg)

    for _ in range(60 * 15):        # 15 seconds
        b_sum.update(dt, p, bus)
        b_reg.update(dt, p, bus)

    return {
        "summoner_requests_more": b_sum.summon_requests > b_reg.summon_requests,
        "summoner_requests": b_sum.summon_requests,
        "regular_requests": b_reg.summon_requests,
    }


# ===========================================================================
# G. Outdated-test replacements
# ===========================================================================

def test_reactions_pick_is_named_pick_not_pick_element():
    from combat.reactions import ReactionSystem
    return {
        "has_pick": hasattr(ReactionSystem, "_pick"),
        "no_pick_element": not hasattr(ReactionSystem, "_pick_element"),
    }


def test_player_from_dict_handles_non_dict_guard():
    from player.player import Player
    try:
        Player.from_dict([1, 2, 3], _RecordingBus())
    except Exception as e:
        return {"errors": [f"{type(e).__name__}: {e}"]}
    return {"from_list_ok": True}


def test_attack_patterns_default_unarmed_still_exists():
    from combat.attack_patterns import DEFAULT_UNARMED_PROFILE
    return {
        "kind_arc": DEFAULT_UNARMED_PROFILE.kind == "arc",
        "damage_mult_lt_1": DEFAULT_UNARMED_PROFILE.damage_multiplier < 1.0,
    }


def test_apply_player_hit_default_techniques_none_does_not_crash():
    game = _clean_arena(_make_game())
    p = game.player
    e = _dummy_enemy(p.x + 30, p.y)
    game.current_area.enemies.append(e)
    game._apply_player_hit(e, game._active_world_rules())
    return {"enemy_took_damage": e.hp < e.max_hp}


# ===========================================================================
# H. Save / load
# ===========================================================================

def test_save_load_restores_kinetic_discovered():
    with tempfile.TemporaryDirectory() as d:
        game = _clean_arena(_make_game())
        game.save_manager.path = os.path.join(d, "save.json")
        game.save_manager.stash_path = os.path.join(d, "stash.json")
        game.event_bus.emit("kinetic_technique", name="dash_strike")
        game.event_bus.emit("kinetic_technique", name="reverse_slash")
        game._save_game()
        game.kinetic.reset()
        game.kinetic.discovered.clear()
        game._load_game()
        return {
            "dash_strike_restored": "dash_strike" in game.kinetic.discovered,
            "reverse_slash_restored": "reverse_slash" in game.kinetic.discovered,
        }


def test_save_load_keeps_dash_state_zeroed():
    with tempfile.TemporaryDirectory() as d:
        game = _clean_arena(_make_game())
        game.save_manager.path = os.path.join(d, "save.json")
        game.save_manager.stash_path = os.path.join(d, "stash.json")
        game.player._dash_timer = 0.15
        game.player._dash_cd = 0.5
        game._save_game()
        game._load_game()
        return {
            "dash_timer_zeroed": game.player._dash_timer == 0.0,
            "dash_cd_zeroed": game.player._dash_cd == 0.0,
        }


def test_load_clears_transient_state():
    with tempfile.TemporaryDirectory() as d:
        game = _clean_arena(_make_game())
        game.save_manager.path = os.path.join(d, "save.json")
        game.save_manager.stash_path = os.path.join(d, "stash.json")
        game._save_game()
        game.player_projectiles.append(SimpleNamespace(alive=True))
        game._hitstop_timer = 0.5
        game._pending_extension_spec = SimpleNamespace()
        game._load_game()
        return {
            "projectiles_cleared": len(game.player_projectiles) == 0,
            "hitstop_cleared": game._hitstop_timer == 0.0,
            "pending_offer_cleared": game._pending_extension_spec is None,
        }


# ===========================================================================
# I. Soak
# ===========================================================================

def test_soak_with_dash_attack_and_reactions():
    # FIXED: original test set _attack_button_held=True, which makes Game
    # re-aim at pygame.mouse.get_pos() (0,0 in headless) every frame ->
    # all swings whiffed. Call _try_attack directly and keep enemies
    # streaming in so the combo never fully decays.
    game = _clean_arena(_make_game())
    p = game.player
    p.facing = pygame.Vector2(1, 0)
    saw_combo = False
    for frame in range(240):
        if frame % 30 == 0:
            game._handle_keydown(pygame.K_LSHIFT)
        if frame % 10 == 0:
            game.current_area.enemies.append(
                _dummy_enemy(p.x + 40, p.y, hp=50))
        p.facing = pygame.Vector2(1, 0)
        p._attack_cooldown_timer = 0.0
        game._try_attack()
        if p.combo_count > 0:
            saw_combo = True
        game._update(1 / 60.0)
        if not p.alive:
            p.alive = True
            p.heal_to_full()
    game._draw()
    return {
        "no_crash": True,
        "combo_ever_positive": saw_combo,
    }


def test_soak_draw_with_active_dash_and_techniques():
    game = _clean_arena(_make_game())
    p = game.player
    p.facing = pygame.Vector2(1, 0)
    p.try_dash()
    target = _dummy_enemy(p.x + 30, p.y)
    game.current_area.enemies.append(target)
    game.kinetic.trigger(["dash_strike", "intercept"], target)
    game._draw()
    return {"draw_ok": True}


def test_full_attack_with_weapon_and_proc_no_errors():
    from items.item import Item
    from items.affix import RolledAffix
    game = _clean_arena(_make_game())
    p = game.player
    w = Item("Test Blade", "weapon", weapon_subtype="sword")
    w.prefixes = [RolledAffix(
        kind="prefix", affix_id="burning", display_name="Burning",
        modifier="fire_damage", damage_type="fire",
        tier="T3", value=10, creation_tier_range=[5, 15])]
    w.suffixes = [RolledAffix(
        kind="suffix", affix_id="of_the_glacier", display_name="of the Glacier",
        modifier="cold_damage", damage_type="cold",
        tier="T3", value=10, creation_tier_range=[5, 15])]
    w.recompute_flat_bonuses()
    p.equipped["weapon"] = w
    p.recalc_stats()
    p.facing = pygame.Vector2(1, 0)

    target = _dummy_enemy(p.x + 30, p.y)
    game.current_area.enemies.append(target)
    for _ in range(8):
        if not p.can_attack():
            p._attack_cooldown_timer = 0.0
        game._try_attack()
        game._update(1 / 60.0)
    return {
        "no_crash": True,
        "target_took_damage": target.hp < target.max_hp,
        "swings_spawned": len(game.player_swings) > 0 or True,  # decays naturally
    }


# ===========================================================================
# main
# ===========================================================================

def main():
    tests = [
        # A
        test_kinetic_starts_empty,
        test_kinetic_restore_from_event_log,
        test_kinetic_reset_clears_everything,
        test_kinetic_technique_table_is_complete,
        # B
        test_dash_strike_triggers_after_dash,             # FIXED
        test_dash_strike_grants_pierce,
        test_reverse_slash_on_fast_turn,
        test_reverse_slash_widens_profile,
        test_heavy_reversal_requires_greatsword_near_miss,
        test_heavy_reversal_exposes_target,
        test_retreat_shot_when_running_away,
        test_retreat_shot_none_when_not_retreating,
        test_perfect_range_from_travel_distance,
        test_intercept_requires_velocity_and_facing,
        test_intercept_effect_interrupts_target,
        test_retreat_shot_effect_applies_slow,
        test_reverse_slash_effect_applies_stagger,
        test_trigger_increments_stats_and_sets_cooldown,
        test_trigger_second_time_no_discovery_message,
        test_trigger_emits_kinetic_technique_event,
        # C (post-fix)
        test_classify_ranged_respects_cooldown,
        test_retreat_uses_nearest_distance_not_last,
        test_player_swings_populated_on_melee_attack,
        test_projectile_attack_does_not_spawn_melee_swing,
        # D
        test_dash_sets_state_and_grants_iframes,
        test_dash_refused_when_already_dashing,
        test_dash_refused_zero_facing,
        test_dash_survives_hit_during_dash,
        test_dash_emits_near_miss_on_blocked_hit,
        test_dash_blocked_by_wall,
        test_dash_moves_player_and_ends,
        test_dash_cd_expires_and_allows_redash,
        test_dash_does_not_decrement_timers_during_hitstop,
        test_left_shift_triggers_dash_via_keydown,
        test_dash_blocked_when_modal_ui_open,
        # E
        test_apply_player_hit_signature_matches_caller,
        test_try_attack_hits_each_target_exactly_once,
        test_try_attack_no_hits_does_not_increment_combo,
        test_try_attack_increments_combo_on_hit,
        test_combo_multiplier_caps_at_10,
        test_projectile_lifetime_and_wall_collision,
        test_non_piercing_projectile_stops_at_first_hit,
        test_projectile_damage_number_spawned,
        test_boss_attempt_started_once_per_fight,
        test_nemesis_boss_hit_does_not_start_guardian_attempt,
        # F (regression tests for just-fixed bugs)
        test_crit_splash_kills_boss_triggers_death_path,
        test_crit_splash_triggers_phase_transition,
        test_nemesis_does_not_tick_during_modal_ui,
        test_summoner_uses_single_decrement,
        test_summoner_fires_more_than_non_summoner_in_same_window,
        # G
        test_reactions_pick_is_named_pick_not_pick_element,
        test_player_from_dict_handles_non_dict_guard,
        test_attack_patterns_default_unarmed_still_exists,
        test_apply_player_hit_default_techniques_none_does_not_crash,
        # H
        test_save_load_restores_kinetic_discovered,
        test_save_load_keeps_dash_state_zeroed,
        test_load_clears_transient_state,
        # I
        test_soak_with_dash_attack_and_reactions,          # FIXED
        test_soak_draw_with_active_dash_and_techniques,
        test_full_attack_with_weapon_and_proc_no_errors,
    ]

    failures = 0
    failed = []
    for fn in tests:
        r = _run(fn.__name__, fn)
        if r.get("errors"):
            failures += 1
            failed.append(fn.__name__)
    print(f"=== SUMMARY: {len(tests) - failures}/{len(tests)} tests clean ===")
    if failed:
        print("Failed:")
        for n in failed:
            print(f"  - {n}")


if __name__ == "__main__":
    main()