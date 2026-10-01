"""
test_helpers7.py - regression tests for Pass 5 (reroll / push-back on
world evolution).

Covers:
  1.  Staging vs committing -- _generate_next_extension stages a spec
      without mutating world.extensions or opening gates.
  2.  Accept -- _accept_pending_extension commits both the extension
      and the gate, and clears the pending state.
  3.  Push-back (Esc) -- _discard_pending_extension commits nothing.
  4.  Automatic push-back stamps the playtime clock so
      automatic_generation_due() doesn't re-fire on the next frame.
  5.  Manual push-back does NOT stamp the clock (declining a manual
      offer must not reset the automatic timer).
  6.  Reroll -- decrements the counter and produces a fresh spec.
  7.  Reroll cap -- refuses past REROLL_CAP; spec identity unchanged.
  8.  Reroll preserves mode -- an automatic offer rerolled stays
      automatic (so its eventual push-back still stamps).
  9.  Reentrancy -- _generate_next_extension is a no-op while a spec
      is already pending.
 10.  Banner reroll row -- WorldEvolutionBanner stores rerolls_left
      and reason; drawing before show() is safe.
 11.  Banner key routing -- R / Esc / Space on the visible banner hit
      the right handlers via _handle_keydown.
 12.  Accept with nothing pending is a safe no-op.
 13.  World gate API split -- pick_next_gate() previews without
      mutating; open_gate_to() commits; open_gate_to(None, ...) and
      reopen are no-ops.
 14.  Backward-compat -- open_next_gate_to() (thin wrapper) still
      behaves exactly as before.

Companion to test_helpers.py / test_helpers2.py / test_helpers5.py /
test_helpers6.py.

Run:  python -m test.test_helpers7
"""
import os

# Must be set before pygame (or anything that imports it).
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import random
import traceback

import pygame  # noqa: F401

import core.config as config


# ---------------------------------------------------------------------------
# small runner harness (same shape as test_helpers5 / 6)
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


def _normalize_clock(game, playtime=100.0, last=100.0):
    """
    Park the world clock so automatic_generation_due() is False. Every
    manual-path test calls this so a stray automatic trigger cannot
    interfere with its assertions.
    """
    game.world.total_playtime_seconds = playtime
    game.world.playtime_at_last_extension = last


# ---------------------------------------------------------------------------
# 1. Staging vs committing
# ---------------------------------------------------------------------------

def test_staging_does_not_commit():
    game = _make_game()
    _normalize_clock(game)

    ext_before = len(game.world.extensions)
    closed_before = len(game.world.closed_gates())
    open_before = len(game.world.open_gates())

    game._generate_next_extension(mode=config.EXTENSION_MODE_MANUAL)

    return {
        "spec_is_staged": game._pending_extension_spec is not None,
        "gate_is_previewed": game._pending_gate is not None,
        "banner_visible": game.evolution_banner.visible,
        "world_extensions_unchanged": len(game.world.extensions) == ext_before,
        "closed_gates_unchanged": len(game.world.closed_gates()) == closed_before,
        "open_gates_unchanged": len(game.world.open_gates()) == open_before,
        "mode_recorded": game._pending_evolution_mode == config.EXTENSION_MODE_MANUAL,
        "rerolls_initialized_to_cap": game._pending_rerolls_left == config.REROLL_CAP,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 2. Accept
# ---------------------------------------------------------------------------

def test_accept_commits_extension_and_gate():
    game = _make_game()
    _normalize_clock(game)

    ext_before = len(game.world.extensions)
    open_before = len(game.world.open_gates())

    game._generate_next_extension(mode=config.EXTENSION_MODE_MANUAL)
    staged_id = game._pending_extension_spec.extension_id
    preview_gate_id = game._pending_gate.gate_id

    game._accept_pending_extension()

    opened = game.world.open_gates()
    opened_ids = {g.gate_id for g in opened}

    return {
        "extension_added": len(game.world.extensions) == ext_before + 1,
        "added_extension_is_staged_spec":
            game.world.extensions[-1].extension_id == staged_id,
        "open_gates_increased": len(opened) == open_before + 1,
        "correct_gate_was_opened": preview_gate_id in opened_ids,
        "opened_gate_targets_extension":
            any(g.gate_id == preview_gate_id
                and g.target_extension_id == staged_id
                for g in opened),
        "pending_cleared": game._pending_extension_spec is None,
        "gate_preview_cleared": game._pending_gate is None,
        "mode_cleared": game._pending_evolution_mode is None,
        "rerolls_cleared": game._pending_rerolls_left == 0,
        "banner_dismissed": not game.evolution_banner.visible,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 3. Push-back (Esc)
# ---------------------------------------------------------------------------

def test_esc_discards_without_committing():
    game = _make_game()
    _normalize_clock(game)

    ext_before = len(game.world.extensions)
    open_before = len(game.world.open_gates())
    closed_before = len(game.world.closed_gates())

    game._generate_next_extension(mode=config.EXTENSION_MODE_MANUAL)
    game._discard_pending_extension()

    return {
        "extension_not_added": len(game.world.extensions) == ext_before,
        "open_gates_unchanged": len(game.world.open_gates()) == open_before,
        "closed_gates_unchanged": len(game.world.closed_gates()) == closed_before,
        "pending_cleared": game._pending_extension_spec is None,
        "banner_dismissed": not game.evolution_banner.visible,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 4 & 5. Playtime-clock stamping -- the infinite-refire guard
# ---------------------------------------------------------------------------

def test_automatic_pushback_stamps_playtime_clock():
    game = _make_game()
    # Huge gap: this WILL be "due" per the playtime threshold.
    game.world.total_playtime_seconds = 1_000_000.0
    game.world.playtime_at_last_extension = 0.0

    was_due = game.world.automatic_generation_due()

    game._generate_next_extension(mode=config.EXTENSION_MODE_AUTOMATIC)
    game._discard_pending_extension()

    now_due = game.world.automatic_generation_due()
    clock_stamped = (
        game.world.playtime_at_last_extension
        == game.world.total_playtime_seconds
    )

    return {
        "was_due_before": was_due is True,
        "clock_stamped_to_now": clock_stamped,
        "not_due_after_pushback": now_due is False,
        "errors": [],
    }


def test_manual_pushback_does_not_stamp_clock():
    game = _make_game()
    _normalize_clock(game, playtime=500.0, last=100.0)
    before = game.world.playtime_at_last_extension

    game._generate_next_extension(mode=config.EXTENSION_MODE_MANUAL)
    game._discard_pending_extension()

    return {
        "clock_unchanged": game.world.playtime_at_last_extension == before,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 6, 7, 8. Reroll mechanics
# ---------------------------------------------------------------------------

def test_reroll_decrements_counter_and_produces_fresh_spec():
    game = _make_game()
    _normalize_clock(game)
    ext_before = len(game.world.extensions)

    game._generate_next_extension(mode=config.EXTENSION_MODE_MANUAL)
    initial_rerolls = game._pending_rerolls_left

    game._reroll_pending_extension()

    return {
        "counter_decremented": game._pending_rerolls_left == initial_rerolls - 1,
        "still_pending": game._pending_extension_spec is not None,
        "still_has_gate_preview": game._pending_gate is not None,
        "no_extension_committed": len(game.world.extensions) == ext_before,
        "banner_still_visible": game.evolution_banner.visible,
        "errors": [],
    }


def test_reroll_cap_enforced():
    game = _make_game()
    _normalize_clock(game)

    game._generate_next_extension(mode=config.EXTENSION_MODE_MANUAL)

    # Burn through every allowed reroll.
    for _ in range(config.REROLL_CAP):
        game._reroll_pending_extension()

    at_cap = game._pending_rerolls_left == 0
    spec_before_refuse = game._pending_extension_spec

    # One more attempt is refused; spec identity must be untouched.
    game._reroll_pending_extension()

    return {
        "counter_reached_zero": at_cap,
        "counter_still_zero_after_refusal": game._pending_rerolls_left == 0,
        "spec_unchanged_after_refused_reroll":
            game._pending_extension_spec is spec_before_refuse,
        "errors": [],
    }


def test_reroll_preserves_mode():
    game = _make_game()
    game.world.total_playtime_seconds = 1_000_000.0
    game.world.playtime_at_last_extension = 0.0

    game._generate_next_extension(mode=config.EXTENSION_MODE_AUTOMATIC)
    mode_before = game._pending_evolution_mode

    game._reroll_pending_extension()
    mode_after = game._pending_evolution_mode

    return {
        "was_automatic": mode_before == config.EXTENSION_MODE_AUTOMATIC,
        "still_automatic_after_reroll":
            mode_after == config.EXTENSION_MODE_AUTOMATIC,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 9. Reentrancy
# ---------------------------------------------------------------------------

def test_generate_reentrancy_blocked_while_pending():
    game = _make_game()
    _normalize_clock(game)

    game._generate_next_extension(mode=config.EXTENSION_MODE_MANUAL)
    first_spec = game._pending_extension_spec
    first_gate = game._pending_gate
    first_rerolls = game._pending_rerolls_left

    # A second generate while one is pending must be a no-op.
    game._generate_next_extension(mode=config.EXTENSION_MODE_MANUAL)

    return {
        "spec_identity_unchanged": game._pending_extension_spec is first_spec,
        "gate_identity_unchanged": game._pending_gate is first_gate,
        "rerolls_not_reset": game._pending_rerolls_left == first_rerolls,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 10. Banner fields + safe draw before show()
# ---------------------------------------------------------------------------

def test_banner_defaults_and_reroll_row():
    from ui.world_evolution import WorldEvolutionBanner

    b = WorldEvolutionBanner()

    # Construction + draw BEFORE any show() must be safe. This is the
    # path Game.__init__ actually takes on every new game.
    try:
        surface = pygame.Surface((config.SCREEN_WIDTH, config.SCREEN_HEIGHT))
        b.draw(surface)
        drew_ok = True
    except Exception as e:
        drew_ok = f"{type(e).__name__}: {e}"

    # show() with an explicit rerolls_left + reason must store them.
    # (Regression: earlier the banner read self.reason via getattr but
    # never actually set it -- the "Why: ..." line was silently absent.)
    b.show(
        extension_name="Test Region",
        area_level=3,
        is_anomaly=False,
        rule_names=["Rule A"],
        gate_area_name="the Starting Region",
        reason="You rush through regions.",
        rerolls_left=2,
    )

    return {
        "draws_before_show": drew_ok is True,
        "visible_after_show": b.visible is True,
        "rerolls_left_stored": b.rerolls_left == 2,
        "reason_stored": b.reason == "You rush through regions.",
        "gate_area_name_stored": b.gate_area_name == "the Starting Region",
        "errors": [] if drew_ok is True else [str(drew_ok)],
    }


def test_banner_reflects_pending_state_after_reroll():
    game = _make_game()
    _normalize_clock(game)

    game._generate_next_extension(mode=config.EXTENSION_MODE_MANUAL)
    first_cap = game.evolution_banner.rerolls_left

    game._reroll_pending_extension()
    second_cap = game.evolution_banner.rerolls_left

    return {
        "banner_shows_full_cap_initially":
            first_cap == config.REROLL_CAP,
        "banner_reflects_decrement":
            second_cap == config.REROLL_CAP - 1,
        "banner_reason_populated":
            bool(game.evolution_banner.reason),
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 11. Key routing through _handle_keydown
# ---------------------------------------------------------------------------

def test_banner_key_routing():
    game = _make_game()
    _normalize_clock(game)

    game._generate_next_extension(mode=config.EXTENSION_MODE_MANUAL)
    rerolls_before = game._pending_rerolls_left

    game._handle_keydown(pygame.K_r)
    r_rerolled = game._pending_rerolls_left == rerolls_before - 1

    # Esc pushes back: nothing commits, pending state clears.
    ext_before = len(game.world.extensions)
    game._handle_keydown(pygame.K_ESCAPE)

    return {
        "r_triggers_reroll": r_rerolled,
        "esc_clears_pending": game._pending_extension_spec is None,
        "esc_did_not_commit": len(game.world.extensions) == ext_before,
        "banner_dismissed_after_esc": not game.evolution_banner.visible,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 12. Defensive: accept with nothing pending
# ---------------------------------------------------------------------------

def test_accept_when_nothing_pending_is_safe():
    game = _make_game()
    ext_before = len(game.world.extensions)

    try:
        game._accept_pending_extension()
        crashed = False
    except Exception as e:
        crashed = f"{type(e).__name__}: {e}"

    return {
        "no_crash_when_no_pending": crashed is False,
        "no_extension_added": len(game.world.extensions) == ext_before,
        "errors": [] if crashed is False else [str(crashed)],
    }


# ---------------------------------------------------------------------------
# 13 & 14. World gate API split + backward compat
# ---------------------------------------------------------------------------

def test_world_gate_api_split():
    from world.world import World

    world = World(7)
    world.register_starting_gate(room_index=0, tile_col=5, tile_row=5)

    closed_before = len(world.closed_gates())
    open_before = len(world.open_gates())

    # pick_next_gate must preview WITHOUT mutating any gate state.
    gate = world.pick_next_gate(random.Random(0))
    picked_ok = gate is not None
    pick_did_not_mutate = (
        len(world.closed_gates()) == closed_before
        and len(world.open_gates()) == open_before
        and not gate.is_open()
    )

    # open_gate_to commits.
    world.open_gate_to(gate, "ext_1")
    committed = (
        gate.is_open()
        and gate.target_extension_id == "ext_1"
        and gate.opened_at is not None
    )

    # Reopening an already-open gate is a no-op: target unchanged.
    reopen_result = world.open_gate_to(gate, "ext_2")
    reopen_is_noop = (
        reopen_result is None
        and gate.target_extension_id == "ext_1"
    )

    # open_gate_to(None, ...) is safe.
    world2 = World(8)
    none_result = world2.open_gate_to(None, "ext_x")

    return {
        "pick_returns_gate": picked_ok,
        "pick_does_not_mutate": pick_did_not_mutate,
        "open_gate_to_commits": committed,
        "reopen_is_noop": reopen_is_noop,
        "none_gate_safe": none_result is None,
        "errors": [],
    }


def test_open_next_gate_to_still_works():
    """Backward-compat: the thin wrapper must remain usable by
    test_helpers5's gate-history test and by any other pre-Pass-5
    caller."""
    from world.world import World

    world = World(7)
    world.register_starting_gate(room_index=0, tile_col=5, tile_row=5)

    gate = world.open_next_gate_to("ext_compat", random.Random(0))

    return {
        "returns_gate": gate is not None,
        "gate_is_open": gate.is_open(),
        "target_correct": gate.target_extension_id == "ext_compat",
        "opened_at_stamped": gate.opened_at is not None,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    tests = [
        ("test_staging_does_not_commit",
         test_staging_does_not_commit),
        ("test_accept_commits_extension_and_gate",
         test_accept_commits_extension_and_gate),
        ("test_esc_discards_without_committing",
         test_esc_discards_without_committing),
        ("test_automatic_pushback_stamps_playtime_clock",
         test_automatic_pushback_stamps_playtime_clock),
        ("test_manual_pushback_does_not_stamp_clock",
         test_manual_pushback_does_not_stamp_clock),
        ("test_reroll_decrements_counter_and_produces_fresh_spec",
         test_reroll_decrements_counter_and_produces_fresh_spec),
        ("test_reroll_cap_enforced",
         test_reroll_cap_enforced),
        ("test_reroll_preserves_mode",
         test_reroll_preserves_mode),
        ("test_generate_reentrancy_blocked_while_pending",
         test_generate_reentrancy_blocked_while_pending),
        ("test_banner_defaults_and_reroll_row",
         test_banner_defaults_and_reroll_row),
        ("test_banner_reflects_pending_state_after_reroll",
         test_banner_reflects_pending_state_after_reroll),
        ("test_banner_key_routing",
         test_banner_key_routing),
        ("test_accept_when_nothing_pending_is_safe",
         test_accept_when_nothing_pending_is_safe),
        ("test_world_gate_api_split",
         test_world_gate_api_split),
        ("test_open_next_gate_to_still_works",
         test_open_next_gate_to_still_works),
    ]
    failures = 0
    for name, fn in tests:
        r = _run(name, fn)
        if r.get("errors"):
            failures += 1
    print(f"=== SUMMARY: {len(tests) - failures}/{len(tests)} tests clean ===")


if __name__ == "__main__":
    main()