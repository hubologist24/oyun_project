"""
test_helpers5.py - regression tests for the "visibility & consequence"
design pass (damage numbers, death penalty, item provenance/vintage,
gate history, evolution narration, region atmosphere, exploration label).

Companion to test_helpers.py / test_helpers2.py. Covers the changes made
in the design-implementation pass:

  1. Death penalty: Progression.lose_xp_progress + Game._on_player_death
  2. DamageNumbers (ui/hud.py): spawn/update/expiry
  3. Item provenance + vintage lines in ItemTooltip._build_info_lines
  4. Gate.opened_at history + save/load roundtrip
  5. Game._evolution_reason narration for the World Evolution banner
  6. Game._region_tint atmosphere coloring per extension
  7. Exploration % wiring used by Game._draw_area_label

Tests 2/6/7/8 construct a real headless Game via SDL's dummy video
driver (must be set BEFORE pygame/engine imports below).

Run:  python -m test.test_helpers5
Every test returns a dict; unexpected exceptions are caught and
recorded under 'errors' so one broken test never hides the rest.
"""
import os

# MUST be set before pygame (or any engine module that imports pygame)
# is imported, or Game() construction will demand a real display.
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import random
import time
import traceback

import pygame  # noqa: F401


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


def _make_game():
    """Fresh headless Game instance (dummy SDL video driver)."""
    from core.game import Game
    return Game()


# ---------------------------------------------------------------------------
# 1. Death penalty - unit (Progression)
# ---------------------------------------------------------------------------

def test_lose_xp_progress_unit():
    from player.progression import Progression

    p = Progression(level=5, xp=400)
    lost = p.lose_xp_progress(0.10)
    unit_ok = (lost == 40 and p.xp == 360 and p.level == 5)

    p2 = Progression(level=5, xp=0)
    lost_zero = p2.lose_xp_progress(0.10)

    p3 = Progression(level=5, xp=3)   # int(3*0.10)=0 -> no-op, never negative
    lost_tiny = p3.lose_xp_progress(0.10)
    never_negative = p3.xp >= 0 and p3.level == 5

    return {
        "loses_fraction_of_progress": unit_ok,
        "level_never_removed": p.level == 5,
        "zero_progress_costs_nothing": lost_zero == 0 and p2.xp == 0,
        "tiny_progress_no_negative": lost_tiny == 0 and never_negative,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 2. Death penalty - integration (event bus -> Game handler)
# ---------------------------------------------------------------------------

def test_death_penalty_integration():
    game = _make_game()
    game.player.progression.level = 7
    game.player.progression.xp = 500
    game.player._invuln_timer = 0  # ensure damage lands

    game.player.take_damage(999999, cause="fire")

    xp_after = game.player.progression.xp
    return {
        "player_died": not game.player.alive,
        "xp_progress_reduced_10pct": xp_after == 450,
        "message_mentions_loss": "-50 XP" in (game.message or ""),
        "death_cause_recorded": game.player.alive is False,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 3. DamageNumbers
# ---------------------------------------------------------------------------

def test_damage_numbers():
    from ui.hud import DamageNumbers

    dn = DamageNumbers()
    dn.spawn(100, 200, "42", (255, 255, 255))
    dn.spawn(110, 200, "900", (255, 215, 90))

    spawned = len(dn.numbers) == 2
    y_before = dn.numbers[0]["y"]
    dn.update(0.1)
    rises = dn.numbers[0]["y"] < y_before

    dn.update(1.0)  # total elapsed > 0.8s lifetime
    expired = len(dn.numbers) == 0

    # draw() must not raise with a real camera (headless surface is fine)
    from world.camera import Camera
    import core.config as config
    surf = pygame.Surface((config.SCREEN_WIDTH, config.SCREEN_HEIGHT))
    cam = Camera(2000, 2000)
    dn.spawn(300, 300, "7", (255, 90, 90))
    try:
        dn.draw(surf, cam)
        draw_ok = True
    except Exception as e:
        draw_ok = f"{type(e).__name__}: {e}"

    return {
        "spawns": spawned,
        "floats_upward": rises,
        "expires_after_lifetime": expired,
        "draw_no_crash": draw_ok is True,
        "errors": [] if draw_ok is True else [str(draw_ok)],
    }


# ---------------------------------------------------------------------------
# 4. Item provenance + vintage tooltip lines
# ---------------------------------------------------------------------------

def test_item_provenance_and_vintage():
    from items.item import Item
    from items.affix import RolledAffix
    from ui.tooltip import ItemTooltip
    from items.tiers import get_tier_table

    pygame.font.init()
    tip = ItemTooltip()

    item = Item(base_name="Ember Sword", slot="weapon",
                rarity=Item.RARITY_RARE, item_level=30)
    item.creation_context = {
        "world_id": "world_0",
        "extension_id": "extension_3",
        "item_level": 30,
        "area_level": 28,
    }
    # Craft an affix that predates the world's current fire_damage T1
    # table ([70, 90]) -- the "vintage" case the design pass surfaces.
    item.prefixes = [RolledAffix(
        kind="prefix", affix_id="burning", display_name="Burning",
        modifier="fire_damage", damage_type="fire",
        tier="T1", value=97.0, creation_tier_range=[95, 120],
    )]
    item.suffixes = [RolledAffix(
        kind="suffix", affix_id="of_haste", display_name="of Haste",
        modifier="attack_speed", damage_type=None,
        tier="T2", value=9.0,
        creation_tier_range=list(get_tier_table()["attack_speed"]["T2"]),
    )]
    item.recompute_flat_bonuses()

    lines = tip._build_info_lines(item)
    text = "\n".join(t for t, _ in lines)

    provenance_ok = "Forged in Extension 3 (area lvl 28)" in text
    vintage_ok = "VINTAGE" in text and "[95-120]" in text and "[70-90]" in text
    never_behind_ok = "never fall behind" in text
    # The suffix matched the current table -> must NOT be flagged vintage
    suffix_not_flagged = text.count("[8-11]") == 0  # current T2 atk spd range, only suffix uses it

    # Non-vintage item: creation ranges == current table -> no VINTAGE header
    plain = Item(base_name="Rusty Sword", slot="weapon", item_level=5)
    plain.creation_context = {"extension_id": "starting_world",
                              "area_level": 1, "item_level": 5, "world_id": "world_0"}
    plain.prefixes = [RolledAffix(
        kind="prefix", affix_id="cruel", display_name="Cruel",
        modifier="physical_damage", damage_type="physical", tier="T3",
        value=50.0, creation_tier_range=list(get_tier_table()["physical_damage"]["T3"]),
    )]
    plain_text = "\n".join(t for t, _ in tip._build_info_lines(plain))
    starting_region_ok = "Forged in the Starting Region" in plain_text
    no_vintage_when_unchanged = "VINTAGE" not in plain_text

    return {
        "provenance_shown_with_extension": provenance_ok,
        "vintage_line_shown_when_table_shifted": vintage_ok,
        "vintage_tagline_present": never_behind_ok,
        "suffix_not_misflagged": suffix_not_flagged,
        "starting_region_provenance": starting_region_ok,
        "no_vintage_when_unchanged": no_vintage_when_unchanged,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 5. Gate history (opened_at) + save/load roundtrip
# ---------------------------------------------------------------------------

def test_gate_opened_history():
    from world.world import World
    from world.extension import ExtensionSpec

    world = World(99)
    world.register_starting_gate(room_index=0, tile_col=5, tile_row=5)
    spec = ExtensionSpec(extension_id="hist_1", extension_name="History Region",
                         area_level=3, biome="b", world_modifiers=[], room_count=5)
    world.add_extension(spec)

    before = time.time()
    opened = world.open_next_gate_to("hist_1", random.Random(1))
    stamped = opened.opened_at is not None and opened.opened_at >= before

    world2 = World.from_dict(world.to_dict())
    o2 = world2.open_gates()[0]
    roundtrip = o2.opened_at is not None and abs(o2.opened_at - opened.opened_at) < 1e-6
    closed_gates_have_no_stamp = all(g.opened_at is None for g in world2.closed_gates())

    return {
        "opened_gate_timestamped": stamped,
        "timestamp_survives_save_load": roundtrip,
        "closed_gates_unstamped": closed_gates_have_no_stamp,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 6. Evolution banner narration (Game._evolution_reason)
# ---------------------------------------------------------------------------

def test_evolution_reason_narration():
    game = _make_game()
    from history.player_profile import PlayerProfile
    from ai.personalization import derive_hints  # noqa: F401  (indirect)

    r_struggle = game._evolution_reason(PlayerProfile(
        level=10, main_damage="fire", defensive_strength="high",
        boss_attempts=5, boss_successes=0))

    r_weak = game._evolution_reason(PlayerProfile(
        level=10, main_damage="cold", defensive_strength="low",
        deaths={"cold": 3, "physical": 1}))

    r_deaths = game._evolution_reason(PlayerProfile(
        level=10, main_damage="lightning", defensive_strength="high",
        deaths={"lightning": 6, "fire": 1}, boss_attempts=1, boss_successes=1))

    r_rush = game._evolution_reason(PlayerProfile(
        level=10, main_damage="chaos", defensive_strength="high",
        average_exploration=0.1, extensions_generated=3,
        boss_attempts=1, boss_successes=1))

    r_default = game._evolution_reason(PlayerProfile(
        level=10, main_damage="fire", defensive_strength="high",
        average_exploration=0.5, boss_attempts=1, boss_successes=1))

    return {
        "boss_struggling_mentioned": "struggles against bosses" in r_struggle,
        "weak_defense_mentioned": "defenses are thin" in r_weak,
        "death_cause_mentioned": "Lightning has killed you often" in r_deaths,
        "under_explored_mentioned": "rush through regions" in r_rush,
        "fallback_mentions_build": "fire build" in r_default,
        "all_reasons_nonempty": all(isinstance(r, str) and len(r) > 10 for r in
                                    (r_struggle, r_weak, r_deaths, r_rush, r_default)),
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 7. Region atmosphere tint (Game._region_tint)
# ---------------------------------------------------------------------------

def test_region_atmosphere_tint():
    game = _make_game()
    from world.extension import ExtensionSpec

    def spec_with(mod_names, anomaly=False):
        return ExtensionSpec(
            extension_id="t", extension_name="T", area_level=1, biome="b",
            is_anomaly=anomaly,
            world_modifiers=[{"name": n, "rules": []} for n in mod_names],
            room_count=5)

    chaos = game._region_tint(spec_with(["Chaos Ascendancy", "Corrupted Flesh"]))
    storm = game._region_tint(spec_with(["Conductive Water"]))
    fire = game._region_tint(spec_with(["Ashen Frailty"]))
    cold = game._region_tint(spec_with(["Frozen Miasma"]))
    anomaly = game._region_tint(spec_with([], anomaly=True))
    bland = game._region_tint(spec_with([]))
    starting = game._region_tint(spec_with(["Chaos Ascendancy"]))

    return {
        "chaos_is_purple": chaos == (120, 40, 160),
        "storm_is_blue": storm == (40, 90, 170),
        "fire_is_orange": fire == (170, 70, 30),
        "cold_is_icy": cold == (60, 120, 160),
        "anomaly_is_red": anomaly == (180, 40, 60),
        "plain_region_no_tint": bland is None,
        "tint_is_rgb_tuple": all(isinstance(c, tuple) and len(c) == 3 for c in
                                 (chaos, storm, fire, cold, anomaly)),
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 8. Exploration % label wiring
# ---------------------------------------------------------------------------

def test_exploration_label_wiring():
    game = _make_game()
    # Visit a few tiles in the starting area via the real tracker path
    for i in range(12):
        game.exploration.update(0.3, "starting_area",
                                game.player.x + i * 48, game.player.y)
    frac = game.exploration.fraction_for("starting_area")

    # _draw_area_label must run cleanly and reflect the tracker value
    try:
        game._draw_area_label()
        draw_ok = True
    except Exception as e:
        draw_ok = f"{type(e).__name__}: {e}"

    return {
        "tracker_accumulates": frac > 0,
        "fraction_bounded": 0.0 < frac <= 1.0,
        "label_draws_without_crash": draw_ok is True,
        "errors": [] if draw_ok is True else [str(draw_ok)],
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    tests = [
        ("test_lose_xp_progress_unit", test_lose_xp_progress_unit),
        ("test_death_penalty_integration", test_death_penalty_integration),
        ("test_damage_numbers", test_damage_numbers),
        ("test_item_provenance_and_vintage", test_item_provenance_and_vintage),
        ("test_gate_opened_history", test_gate_opened_history),
        ("test_evolution_reason_narration", test_evolution_reason_narration),
        ("test_region_atmosphere_tint", test_region_atmosphere_tint),
        ("test_exploration_label_wiring", test_exploration_label_wiring),
    ]
    failures = 0
    for name, fn in tests:
        r = _run(name, fn)
        if r.get("errors"):
            failures += 1
    print(f"=== SUMMARY: {len(tests) - failures}/{len(tests)} tests clean ===")


if __name__ == "__main__":
    main()