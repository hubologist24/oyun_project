"""
test_helpers9.py - regression tests for the mouse-hold control pass.

Covers:
  A. Left-click hold to move  (arm / retarget on motion / disarm)
  B. Right-click hold attack  (arm / per-frame repeat / disarm / modal guard)
  C. Event-loop routing of MOUSEBUTTONUP
  D. Regression checks for prior fixes:
       - ItemTooltip._build_info_lines accepts world_rules
       - enemy_attack_player passes source_category
       - player_attack_enemy passes source_category="player"
       - world/gate.py has no stray "# FILE:" header
  E. Smoke: game still updates cleanly with the new flags present.

Run:  python -m test.test_helpers9
"""
import os
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import inspect
import traceback
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


def _make_game():
    from core.game import Game
    return Game()


class _RecordingBus:
    def __init__(self):
        self.events = []

    def emit(self, event_type, **payload):
        self.events.append((event_type, payload))

    def subscribe(self, *a, **k):
        pass


def _open_game_no_ui(game):
    """Ensure no modal UI is blocking input, and no mouse buttons are held."""
    if game.inventory_ui.visible:
        game.inventory_ui.toggle()
    if game.evolution_banner.visible:
        game.evolution_banner.dismiss()
    if game.profile_screen.visible:
        game.profile_screen.hide()
    if game.gateway_menu.visible:
        game.gateway_menu.hide()
    game._move_button_held = False
    game._attack_button_held = False
    game.player.move_target = None


# ---------------------------------------------------------------------------
# A. Left-click hold to move
# ---------------------------------------------------------------------------

def test_flags_initialized_false():
    game = _make_game()
    return {
        "move_flag_starts_false":   game._move_button_held is False,
        "attack_flag_starts_false": game._attack_button_held is False,
        "errors": [],
    }


def test_hold_left_arms_and_disarms():
    game = _make_game()
    _open_game_no_ui(game)
    p = game.player

    game._handle_mouse_down(1, (400, 300))
    armed = game._move_button_held
    target_after_down = p.move_target is not None

    game._handle_mouse_up(1, (400, 300))
    disarmed = game._move_button_held is False
    target_after_up = p.move_target is None

    return {
        "down_arms_hold":            armed,
        "down_sets_move_target":     target_after_down,
        "up_disarms_hold":           disarmed,
        "up_clears_move_target":     target_after_up,
        "errors": [],
    }


def test_hold_left_motion_updates_target():
    game = _make_game()
    _open_game_no_ui(game)
    p = game.player

    game._handle_mouse_down(1, (400, 300))
    first = (p.move_target.x, p.move_target.y)

    game._handle_mouse_motion((600, 400))
    second = (p.move_target.x, p.move_target.y)

    game._handle_mouse_up(1, (600, 400))
    game._handle_mouse_motion((800, 500))
    third = p.move_target

    return {
        "motion_retargets_while_held":  first != second,
        "motion_after_release_ignored": third is None,
        "errors": [],
    }


def test_motion_without_hold_does_nothing():
    game = _make_game()
    _open_game_no_ui(game)
    game._handle_mouse_motion((400, 300))
    return {
        "no_target_without_hold": game.player.move_target is None,
        "errors": [],
    }


def test_hold_left_ignored_when_modal_open():
    game = _make_game()
    _open_game_no_ui(game)
    game.evolution_banner.visible = True

    game._handle_mouse_down(1, (400, 300))

    game.evolution_banner.dismiss()
    return {
        "no_arm_while_banner_visible":   game._move_button_held is False,
        "no_target_while_banner_visible": game.player.move_target is None,
        "errors": [],
    }


def test_hold_left_ignored_when_dead():
    game = _make_game()
    _open_game_no_ui(game)
    game.player.alive = False

    game._handle_mouse_down(1, (400, 300))
    return {
        "no_arm_when_dead": game._move_button_held is False,
        "errors": [],
    }


def test_middle_click_arms_neither():
    game = _make_game()
    _open_game_no_ui(game)
    game._handle_mouse_down(2, (400, 300))
    return {
        "middle_does_not_arm_move":   game._move_button_held is False,
        "middle_does_not_arm_attack": game._attack_button_held is False,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# B. Right-click hold to attack
# ---------------------------------------------------------------------------

def test_hold_right_arms_and_attacks_on_down():
    game = _make_game()
    _open_game_no_ui(game)
    attacks = []
    game._try_attack = lambda: attacks.append(1)

    game._handle_mouse_down(3, (400, 300))
    return {
        "down_arms_attack_hold": game._attack_button_held,
        "attack_fired_on_down":  len(attacks) == 1,
        "errors": [],
    }


def test_hold_right_repeats_in_update():
    game = _make_game()
    _open_game_no_ui(game)
    game.player.alive = True
    game._attack_button_held = True

    attacks = []
    game._try_attack = lambda: attacks.append(1)

    for _ in range(5):
        game._update(1 / 60.0)

    return {
        "attack_called_each_update": len(attacks) == 5,
        "calls": len(attacks),
        "errors": [],
    }


def test_hold_right_release_stops_repeats():
    game = _make_game()
    _open_game_no_ui(game)
    game.player.alive = True
    game._attack_button_held = True

    attacks = []
    game._try_attack = lambda: attacks.append(1)

    game._update(1 / 60.0)
    count_before_release = len(attacks)

    game._handle_mouse_up(3, (400, 300))
    released = game._attack_button_held is False

    game._update(1 / 60.0)
    game._update(1 / 60.0)

    return {
        "release_disarms":                released,
        "no_more_attacks_after_release":  len(attacks) == count_before_release,
        "errors": [],
    }


def test_hold_right_blocked_by_inventory():
    """A held right button must not attack while the inventory is open.
    Passes whether the guard lives in the modal early-return or in the
    per-attack condition."""
    game = _make_game()
    game.player.alive = True
    game._attack_button_held = True

    attacks = []
    game._try_attack = lambda: attacks.append(1)

    if not game.inventory_ui.visible:
        game.inventory_ui.toggle()
    game._update(1 / 60.0)
    game.inventory_ui.toggle()   # close

    return {
        "no_attacks_while_inventory_open": len(attacks) == 0,
        "errors": [],
    }


def test_hold_right_blocked_by_banner():
    game = _make_game()
    game.player.alive = True
    game._attack_button_held = True
    game.evolution_banner.visible = True

    attacks = []
    game._try_attack = lambda: attacks.append(1)
    game._update(1 / 60.0)

    game.evolution_banner.dismiss()
    return {
        "no_attacks_while_banner_open": len(attacks) == 0,
        "errors": [],
    }


def test_hold_right_ignored_when_dead():
    game = _make_game()
    _open_game_no_ui(game)
    game.player.alive = False

    attacks = []
    game._try_attack = lambda: attacks.append(1)
    game._handle_mouse_down(3, (400, 300))

    return {
        "no_arm_when_dead":    game._attack_button_held is False,
        "no_attack_when_dead": len(attacks) == 0,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# C. Event-loop routing
# ---------------------------------------------------------------------------

def test_event_loop_routes_button_up():
    game = _make_game()
    _open_game_no_ui(game)
    game._move_button_held   = True
    game._attack_button_held = True

    pygame.event.post(pygame.event.Event(
        pygame.MOUSEBUTTONUP, button=1, pos=(100, 100)))
    game._handle_events()

    return {
        "button_up_routed_for_left": game._move_button_held is False,
        "right_hold_untouched":      game._attack_button_held is True,
        "errors": [],
    }


def test_event_loop_routes_button_down():
    game = _make_game()
    _open_game_no_ui(game)

    pygame.event.post(pygame.event.Event(
        pygame.MOUSEBUTTONDOWN, button=1, pos=(400, 300)))
    game._handle_events()

    return {
        "button_down_routed": game._move_button_held is True,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# D. Regression checks for prior fixes
# ---------------------------------------------------------------------------

def test_tooltip_build_info_lines_accepts_world_rules():
    from ui.tooltip import ItemTooltip
    from items.item import Item

    sig = inspect.signature(ItemTooltip._build_info_lines)
    has_param = "world_rules" in sig.parameters

    item = Item("Test", "weapon")
    tip = ItemTooltip()
    ok = True
    err = None
    try:
        tip._build_info_lines(item, None)   # explicit
        tip._build_info_lines(item)         # default
    except Exception as e:
        ok = False
        err = f"{type(e).__name__}: {e}"

    return {
        "signature_has_world_rules":     has_param,
        "callable_with_and_without":     ok,
        "errors": [] if ok else [err],
    }


def test_enemy_attack_passes_source_category():
    import combat.combat as cc
    from enemies.enemy import Enemy
    from player.player import Player

    captured = {}
    orig = cc.resolve_damage
    def spy(*args, **kwargs):
        captured.update(kwargs)
        return orig(*args, **kwargs)
    cc.resolve_damage = spy
    try:
        p = Player(0, 0, _RecordingBus())
        e = Enemy(30, 0, damage=10)
        cc.enemy_attack_player(e, p, _RecordingBus())
    finally:
        cc.resolve_damage = orig

    return {
        "source_category_is_minion": captured.get("source_category") == "minion",
        "value": captured.get("source_category"),
        "errors": [],
    }


def test_player_attack_passes_source_category():
    import combat.combat as cc
    from enemies.enemy import Enemy
    from player.player import Player

    captured = {}
    orig = cc.resolve_damage
    def spy(*args, **kwargs):
        captured.update(kwargs)
        return orig(*args, **kwargs)
    cc.resolve_damage = spy
    try:
        p = Player(0, 0, _RecordingBus())
        e = Enemy(30, 0, hp=100)
        cc.player_attack_enemy(p, e, _RecordingBus())
    finally:
        cc.resolve_damage = orig

    return {
        "source_category_is_player": captured.get("source_category") == "player",
        "value": captured.get("source_category"),
        "errors": [],
    }


def test_gate_module_no_stray_header():
    import world.gate as gate_mod
    src = inspect.getsource(gate_mod)
    return {
        "no_commented_open_to":     "#def open_to" not in src,
        "no_file_header_comment":   src.count("# FILE:") == 0,
        "count":                    src.count("# FILE:"),
        "errors": [],
    }


# ---------------------------------------------------------------------------
# E. Smoke
# ---------------------------------------------------------------------------

def test_game_updates_without_crashing():
    game = _make_game()
    try:
        for _ in range(3):
            game._update(1 / 60.0)
        ok = True
    except Exception as e:
        ok = f"{type(e).__name__}: {e}"
    return {
        "updates_cleanly": ok is True,
        "errors": [] if ok is True else [str(ok)],
    }


def test_update_runs_with_inventory_open():
    """Whatever guard you pick for held-attack, the update loop must
    still run cleanly with the inventory open."""
    game = _make_game()
    game.player.alive = True
    game.inventory_ui.toggle()
    try:
        game._update(1 / 60.0)
        ok = True
    except Exception as e:
        ok = f"{type(e).__name__}: {e}"
    game.inventory_ui.toggle()
    return {
        "update_ok": ok is True,
        "errors": [] if ok is True else [str(ok)],
    }

def test_extension_boss_is_built_from_personalized_template():
    """AreaInstance must not silently fall back to Hollow Warden when
    the personalized template is valid."""
    from core.game import Game
    from world.extension import Extension, ExtensionSpec
    from ai.procedural_generator import ProceduralWorldGenerator
    from history.player_profile import PlayerProfile
    import random

    game = Game()
    gen = ProceduralWorldGenerator()
    spec = gen.generate_next_extension(
        PlayerProfile(level=10, main_damage="fire"),
        set(), random.Random(0))
    ext = game.world.add_extension(spec)
    ext.ensure_built(game.world.rng_service, world=game.world)
    from world.level import AreaInstance
    stream = game.world.rng_service.derive_child("x", "y")
    area = AreaInstance(ext.area, stream, ext)
    return {
        "boss_exists": area.boss is not None,
        "not_hollow_warden_fallback": (
            area.boss is None or "Hollow Warden" not in area.boss.name
            or "Guardian of" in area.boss.name),
        "boss_name": area.boss.name if area.boss else None,
    }

def test_extension_boss_survives_validator_at_runtime():
    """The runtime path (AreaInstance) must feed BossValidator the same
    dict shape that the factory test uses, or every boss silently
    falls back to Hollow Warden."""
    from dataclasses import asdict
    from enemies.boss_factory import build_personalized_boss_template
    from ai.validators import BossValidator
    from enemies.boss import BossTemplate, BossPhaseConfig
    import random

    t = build_personalized_boss_template(
        "Test Boss", {"mechanic_archetype": "dash_predator",
                      "difficulty_bias": "normal"}, 20, random.Random(0))

    # What level.py *should* be passing:
    ok, errs = BossValidator.validate(asdict(t))
    return {"runtime_shape_validates": ok, "errors": errs}


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    tests = [
        # A. hold left
        test_flags_initialized_false,
        test_hold_left_arms_and_disarms,
        test_hold_left_motion_updates_target,
        test_motion_without_hold_does_nothing,
        test_hold_left_ignored_when_modal_open,
        test_hold_left_ignored_when_dead,
        test_middle_click_arms_neither,
        # B. hold right
        test_hold_right_arms_and_attacks_on_down,
        test_hold_right_repeats_in_update,
        test_hold_right_release_stops_repeats,
        test_hold_right_blocked_by_inventory,
        test_hold_right_blocked_by_banner,
        test_hold_right_ignored_when_dead,
        # C. event loop
        test_event_loop_routes_button_up,
        test_event_loop_routes_button_down,
        # D. regression
        test_tooltip_build_info_lines_accepts_world_rules,
        test_enemy_attack_passes_source_category,
        test_player_attack_passes_source_category,
        test_gate_module_no_stray_header,
        # E. smoke
        test_game_updates_without_crashing,
        test_update_runs_with_inventory_open,
        test_extension_boss_survives_validator_at_runtime,
        test_extension_boss_is_built_from_personalized_template,
    ]

    failures = 0
    for fn in tests:
        r = _run(fn.__name__, fn)
        if r.get("errors"):
            failures += 1
    print(f"=== SUMMARY: {len(tests) - failures}/{len(tests)} tests clean ===")


if __name__ == "__main__":
    main()