"""
test_helpers6.py - regression tests for the combat-feel pass (Passes 1-4
of the post-review plan):

  1. Hit-stop            - Game._trigger_hitstop, timer cap, frozen
                           simulation with live UI feedback
  2. Screen shake        - Camera.shake/tick, offset applied inside
                           world_to_screen, and the inverse invariant
                           screen_to_world(world_to_screen(p)) == p
  3. Telegraph audio     - core.audio is a no-op in headless envs and
                           the boss wires the cue at telegraph start
  4. Enemy behavior      - chaser/ambusher/circler/ranged all spawn
                           and each archetype moves the way its
                           docstring claims

Companion to test_helpers.py / test_helpers2.py / test_helpers5.py.

Tests that need a live Game construct one via SDL's dummy video driver
(set BEFORE pygame is imported, hence the env var at the very top).

Run:  python -m test.test_helpers6
"""
import os

# Must be set before pygame (or anything that imports it).
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import math
import random
import traceback

import pygame  # noqa: F401


# ---------------------------------------------------------------------------
# small runner harness (same shape as test_helpers5)
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


def _make_game():
    from core.game import Game
    return Game()


# ---------------------------------------------------------------------------
# stubs for behavior tests (no live Game needed, so tests are fast and
# deterministic)
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


class _StubBus:
    def emit(self, *a, **k):
        pass

    def subscribe(self, *a, **k):
        pass


class _OpenTilemap:
    """Every tile is walkable -- isolates behavior logic from map gen."""
    def collides_rect(self, rect):
        return False


# ---------------------------------------------------------------------------
# 1. Hit-stop
# ---------------------------------------------------------------------------

def test_hitstop_state_machine():
    game = _make_game()
    initial = game._hitstop_timer

    game._trigger_hitstop(0.03)
    after_first = game._hitstop_timer

    # Smaller trigger must NOT reduce an already-running (larger) timer.
    game._trigger_hitstop(0.01)
    after_smaller = game._hitstop_timer

    # Larger trigger is capped at the 0.12 hard ceiling.
    game._trigger_hitstop(0.50)
    after_big = game._hitstop_timer

    return {
        "starts_at_zero": initial == 0.0,
        "sets_timer": after_first > 0.0,
        "smaller_does_not_reduce": after_smaller >= after_first,
        "caps_at_max": after_big <= 0.12 + 1e-9,
        "errors": [],
    }


def test_hitstop_freezes_simulation():
    game = _make_game()
    # Give the UI something to tick while the world is frozen.
    game.damage_numbers.spawn(0, 0, "5", (255, 255, 255))

    game._trigger_hitstop(0.05)
    pt_before = game.world.total_playtime_seconds
    n_before = len(game.damage_numbers.numbers)

    # dt=1.0 is safely longer than both the hit-stop timer and the 0.8s
    # damage-number lifetime, so we can assert both effects in one call.
    game._update(1.0)

    pt_after = game.world.total_playtime_seconds
    n_after = len(game.damage_numbers.numbers)

    return {
        "world_playtime_frozen": pt_after == pt_before,
        "damage_numbers_advanced": n_after < n_before,
        "errors": [],
    }


def test_hitstop_and_shake_triggered_by_player_hit_event():
    game = _make_game()
    game._hitstop_timer = 0.0

    emit_ok = True
    try:
        game.event_bus.emit("player_hit", source="test", damage=5,
                            damage_type="fire")
    except Exception as e:
        emit_ok = f"{type(e).__name__}: {e}"

    hitstop = game._hitstop_timer > 0.0
    # getattr defensive: if the shake patch wasn't applied, this reports
    # False instead of raising and hiding the hit-stop result.
    shake = getattr(game.camera, "_shake_time", 0.0) > 0.0

    return {
        "emit_no_crash": emit_ok is True,
        "hitstop_triggered_on_hit": hitstop,
        "shake_triggered_on_hit": shake,
        "errors": [] if emit_ok is True else [str(emit_ok)],
    }


# ---------------------------------------------------------------------------
# 2. Screen shake
# ---------------------------------------------------------------------------

def test_camera_shake_state_machine():
    from world.camera import Camera
    cam = Camera(2000, 2000)
    cam.update(500, 500)

    base = cam.world_to_screen((500, 500))

    cam.shake(magnitude=6, duration=0.15)
    cam.tick(0.05)
    shaken = cam.world_to_screen((500, 500))
    actually_shakes = shaken != base

    # Calling shake with a smaller magnitude must not weaken an active
    # (larger) shake.
    cam.shake(magnitude=2, duration=0.05)
    mag_after_small = cam._shake_mag

    # Advance past duration -- offset should return to zero.
    cam.tick(1.0)
    settled = cam.world_to_screen((500, 500))
    returns_to_base = settled == base

    return {
        "shakes_on_trigger": actually_shakes,
        "does_not_weaken_on_smaller_call": mag_after_small >= 6,
        "settles_after_duration": returns_to_base,
        "errors": [],
    }


def test_camera_shake_inverse_invariant():
    """
    screen_to_world(world_to_screen(p)) must equal p *while shaking*,
    because hit-testing (right-click-to-attack) goes through
    screen_to_world and must not drift with the visual jitter.
    """
    from world.camera import Camera
    cam = Camera(2000, 2000)
    cam.update(500, 500)
    cam.shake(magnitude=10, duration=0.5)
    cam.tick(0.05)  # rolls a random offset into _shake_dx/_shake_dy

    # int() truncation in world_to_screen means we only get within a pixel.
    worst_err = 0
    for wx, wy in [(400.0, 300.0), (512.0, 512.0), (610.0, 480.0)]:
        sx, sy = cam.world_to_screen((wx, wy))
        rx, ry = cam.screen_to_world((sx, sy))
        worst_err = max(worst_err, abs(rx - wx), abs(ry - wy))

    return {
        "roundtrip_within_one_pixel": worst_err <= 1.0,
        "worst_error": worst_err,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 3. Telegraph audio
# ---------------------------------------------------------------------------

def test_audio_module_headless_safe():
    """
    In a headless test the mixer is often unavailable. The audio module
    must degrade to silent no-ops rather than raising -- otherwise every
    test that touches a boss would fail on machines without a device.
    """
    from core import audio

    problems = []

    try:
        audio.init()
        audio.init()   # double-init must be safe
    except Exception as e:
        problems.append(f"init: {type(e).__name__}: {e}")

    try:
        audio.play("does_not_exist")   # unknown name -> silent no-op
        audio.play("telegraph_melee")  # known name -> silent if no mixer
        audio.play("hit_player")
    except Exception as e:
        problems.append(f"play: {type(e).__name__}: {e}")

    return {
        "init_idempotent": True,
        "unknown_play_safe": not any("play" in p for p in problems),
        "errors": problems,
    }


def test_boss_telegraph_audio_hook():
    """
    Monkeypatch core.audio.play and confirm the boss fires a
    'telegraph_*' cue the moment it commits to a wind-up (not when it
    executes the hit).
    """
    from core import audio
    calls = []
    orig_play = audio.play
    audio.play = lambda name: calls.append(name)
    try:
        from enemies.boss import Boss, BossTemplate
        random.seed(0)
        b = Boss(500, 500, BossTemplate.hollow_warden())
        b._choose_action(dist_to_player=200, phase=b.current_phase)
        state_ok = b._state == "telegraph"
        hook_fired = any(c.startswith("telegraph_") for c in calls)
    finally:
        audio.play = orig_play

    return {
        "boss_enters_telegraph": state_ok,
        "audio_telegraph_cue_played": hook_fired,
        "calls": calls,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 4. Enemy behavior variety
# ---------------------------------------------------------------------------

def test_enemy_behavior_spawner():
    from enemies.enemy import spawn_basic_enemy, BEHAVIOR_TABLE

    rng = random.Random(42)
    sample = spawn_basic_enemy(0, 0, rng)
    has_kwarg = hasattr(sample, "behavior")

    seen = set()
    for _ in range(400):
        e = spawn_basic_enemy(100, 100, rng, area_level=1)
        seen.add(e.behavior)

    expected = {b for b, *_ in BEHAVIOR_TABLE}
    return {
        "behavior_attr_present": has_kwarg,
        "all_behaviors_appear_over_400_rolls": expected.issubset(seen),
        "expected": sorted(expected),
        "seen": sorted(seen),
        "errors": [],
    }


def test_behavior_chaser_approaches():
    from enemies.enemy import Enemy
    random.seed(0)
    tm, bus = _OpenTilemap(), _StubBus()

    e = Enemy(100, 100, behavior="chaser", speed=200)
    p = _StubPlayer(250, 250)     # dist ~212, inside 260 aggro range

    d0 = math.hypot(p.x - e.x, p.y - e.y)
    for _ in range(30):           # 0.5s
        e.update(1 / 60.0, p, tm, bus)
    d1 = math.hypot(p.x - e.x, p.y - e.y)

    return {
        "approaches_player": d1 < d0 - 20,   # meaningfully, not noise
        "start_dist": round(d0, 1),
        "end_dist": round(d1, 1),
        "errors": [],
    }


def test_behavior_ambusher_dormant_then_lunges():
    from enemies.enemy import Enemy
    random.seed(0)
    tm, bus = _OpenTilemap(), _StubBus()

    # Case A: out of trigger range -> completely still.
    e_far = Enemy(100, 100, behavior="ambusher", speed=200)
    p_far = _StubPlayer(500, 500)
    for _ in range(60):
        e_far.update(1 / 60.0, p_far, tm, bus)
    stayed_put = (e_far.x == 100 and e_far.y == 100)

    # Case B: player nearby -> closes distance.
    e_near = Enemy(100, 100, behavior="ambusher", speed=200)
    p_near = _StubPlayer(180, 180)   # dist ~113
    d0 = math.hypot(p_near.x - e_near.x, p_near.y - e_near.y)
    for _ in range(30):
        e_near.update(1 / 60.0, p_near, tm, bus)
    d1 = math.hypot(p_near.x - e_near.x, p_near.y - e_near.y)

    return {
        "dormant_when_far": stayed_put,
        "closes_distance_when_player_near": d1 < d0,
        "start_dist": round(d0, 1),
        "end_dist": round(d1, 1),
        "errors": [],
    }


def test_behavior_circler_holds_midrange():
    from enemies.enemy import Enemy
    random.seed(0)
    tm, bus = _OpenTilemap(), _StubBus()

    # Too close -> should back off.
    e1 = Enemy(300, 300, behavior="circler", speed=200)
    p1 = _StubPlayer(330, 300)       # dist 30, well under 90 threshold
    d1a = 30.0
    for _ in range(30):
        e1.update(1 / 60.0, p1, tm, bus)
    d1b = math.hypot(p1.x - e1.x, p1.y - e1.y)

    # Too far -> should approach.
    e2 = Enemy(100, 100, behavior="circler", speed=200)
    p2 = _StubPlayer(320, 100)       # dist 220, under 260 aggro
    d2a = 220.0
    for _ in range(30):
        e2.update(1 / 60.0, p2, tm, bus)
    d2b = math.hypot(p2.x - e2.x, p2.y - e2.y)

    return {
        "backs_off_when_too_close": d1b > d1a,
        "approaches_when_far": d2b < d2a,
        "close_case": (round(d1a, 1), round(d1b, 1)),
        "far_case": (round(d2a, 1), round(d2b, 1)),
        "errors": [],
    }


def test_behavior_ranged_fires_bolt():
    from enemies.enemy import Enemy
    random.seed(0)
    tm, bus = _OpenTilemap(), _StubBus()

    # Place at dist 250 (inside the 160-300 sweet spot). _ranged_timer
    # starts at 1.2s, so fire should happen within ~90 ticks.
    e = Enemy(100, 100, behavior="ranged", speed=200, damage=10)
    p = _StubPlayer(350, 100)

    fired = False
    for _ in range(120):     # 2s
        e.update(1 / 60.0, p, tm, bus)
        if len(e.projectiles) > 0:
            fired = True
            break

    return {
        "ranged_fires_projectile": fired,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    tests = [
        ("test_hitstop_state_machine", test_hitstop_state_machine),
        ("test_hitstop_freezes_simulation", test_hitstop_freezes_simulation),
        ("test_hitstop_and_shake_triggered_by_player_hit_event",
         test_hitstop_and_shake_triggered_by_player_hit_event),
        ("test_camera_shake_state_machine", test_camera_shake_state_machine),
        ("test_camera_shake_inverse_invariant",
         test_camera_shake_inverse_invariant),
        ("test_audio_module_headless_safe", test_audio_module_headless_safe),
        ("test_boss_telegraph_audio_hook", test_boss_telegraph_audio_hook),
        ("test_enemy_behavior_spawner", test_enemy_behavior_spawner),
        ("test_behavior_chaser_approaches", test_behavior_chaser_approaches),
        ("test_behavior_ambusher_dormant_then_lunges",
         test_behavior_ambusher_dormant_then_lunges),
        ("test_behavior_circler_holds_midrange",
         test_behavior_circler_holds_midrange),
        ("test_behavior_ranged_fires_bolt", test_behavior_ranged_fires_bolt),
    ]
    failures = 0
    for name, fn in tests:
        r = _run(name, fn)
        if r.get("errors"):
            failures += 1
    print(f"=== SUMMARY: {len(tests) - failures}/{len(tests)} tests clean ===")


if __name__ == "__main__":
    main()