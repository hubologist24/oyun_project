"""
test_helpers12.py - regression tests for the "stones / gating / summoner /
elite affixes / roaming spawns / dash / layout" pass.

Covers:
  A. Extension stones on Player (default, save round-trip, old saves)
  B. Level pickups (spawn_pickup / spawn_orb / spawn_loot kwargs / draw)
  C. World.pick_next_gate(eligible=...)
  D. Boss-gated gates in Game (_gate_unlocked / _pick_gate / _locked_hint)
  E. Stone economy through F11 (cost, free reroll/push-back, debug, no gate)
  F. Kill drops (stone chances, add drops, explosive, boss loot burst)
  G. Summoner boss (template, phase burst, timer, Game._spawn_boss_adds)
  H. New enemy types + elite affixes
  I. Expansion integration (biome element, summoner boss, gate unlock)
  J. Roaming spawns
  K. Dash
  L. UI layout (inventory panel fits, draws with stones, full frame)
  M. Save / load round-trip with stones + extension
  N. Hygiene (duplicate/missing Game methods, config, gateway menu)
  O. Soak (hundreds of updates with a summoner boss, roaming, dash)

Run:  python -m test.test_helpers12
"""
import os
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import inspect
import random
import tempfile
import traceback
from dataclasses import asdict

import pygame


# ---------------------------------------------------------------------------
# harness (same shape as test_helpers9)
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


class _FixedRng:
    """random() always returns the same number -> fully controlled drops."""
    def __init__(self, v):
        self.v = v

    def random(self):
        return self.v

    def randint(self, a, b):
        return a

    def choice(self, seq):
        return seq[0]


class _OpenMap:
    def collides_rect(self, rect):
        return False


class _WallMap:
    def collides_rect(self, rect):
        return True


class _Keys:
    def __getitem__(self, k):
        return False


def _make_game():
    from core.game import Game
    return Game()


def _clean(game):
    """No modal UI, no held buttons, no pending offer, empty loot."""
    if game.inventory_ui.visible:
        game.inventory_ui.toggle()
    for ui, hide in ((game.evolution_banner, "dismiss"),
                     (game.profile_screen, "hide"),
                     (game.gateway_menu, "hide")):
        if ui.visible:
            getattr(ui, hide)()
    game._move_button_held = False
    game._attack_button_held = False
    game._pending_extension_spec = None
    game._pending_gate = None
    game._pending_evolution_mode = None
    game.player.move_target = None
    game.current_area.loot_drops.clear()
    game.message = ""


def _drops(game, kind):
    return [d for d in game.current_area.loot_drops if d.get("kind") == kind]


def _item_drops(game):
    return [d for d in game.current_area.loot_drops
            if d.get("item") is not None]


def _with_mods(mods, fn):
    orig = pygame.key.get_mods
    pygame.key.get_mods = lambda: mods
    try:
        return fn()
    finally:
        pygame.key.get_mods = orig


def _expand(game, biome="ash_canyon", archetype="summoner",
            extension_id="extension_1"):
    """Attach a real extension to the live map. Returns (ext, gate)."""
    from world.extension import ExtensionSpec
    from world.expansion import attach_extension
    spec = ExtensionSpec(
        extension_id=extension_id, extension_name="Testlands",
        area_level=3, biome=biome, world_modifiers=[],
        boss_template_id="personalized", room_count=6,
        boss_personalization={"mechanic_archetype": archetype,
                              "difficulty_bias": "normal"})
    ext = game.world.add_extension(spec)
    gate = game.world.pick_next_gate(
        game.world.rng_service.get_stream("gate_selection"))
    if gate is None:
        raise RuntimeError("no closed gate available for _expand")
    game.world.open_gate_to(gate, spec.extension_id)
    attach_extension(game.current_area, ext, gate,
                     game.world.rng_service, game.world)
    game._on_map_expanded(spec.extension_id)
    return ext, gate


def _region_boss(game, rid):
    for b in game.current_area.bosses:
        if getattr(b, "region_id", None) == rid:
            return b
    return None


def _summoner_boss(x=0, y=0, rid="starting_area"):
    from enemies.boss import Boss
    from enemies.boss_factory import build_personalized_boss_template
    t = build_personalized_boss_template(
        "Test Summoner", {"mechanic_archetype": "summoner",
                          "difficulty_bias": "normal"}, 5, random.Random(0))
    b = Boss(x, y, t)
    b.region_id = rid
    return b


# ---------------------------------------------------------------------------
# A. Extension stones on Player
# ---------------------------------------------------------------------------

def test_stones_default_ten():
    from player.player import Player
    p = Player(0, 0, _RecordingBus())
    return {"starts_at_10": p.extension_stones == 10}


def test_stones_save_roundtrip():
    from player.player import Player
    p = Player(0, 0, _RecordingBus())
    p.extension_stones = 7
    d = p.to_dict()
    p2 = Player.from_dict(d, _RecordingBus())
    return {"key_in_dict": "extension_stones" in d,
            "roundtrip_7": p2.extension_stones == 7}


def test_stones_old_save_defaults_to_ten():
    from player.player import Player
    p = Player(0, 0, _RecordingBus())
    d = p.to_dict()
    d.pop("extension_stones", None)
    p2 = Player.from_dict(d, _RecordingBus())
    return {"old_save_gets_10": p2.extension_stones == 10}


def test_stones_zero_survives_roundtrip():
    """0 is falsy -- make sure it is not replaced by the default 10."""
    from player.player import Player
    p = Player(0, 0, _RecordingBus())
    p.extension_stones = 0
    p2 = Player.from_dict(p.to_dict(), _RecordingBus())
    return {"zero_stays_zero": p2.extension_stones == 0}


def test_stones_garbage_input_does_not_crash():
    from player.player import Player
    p = Player(0, 0, _RecordingBus())
    d = p.to_dict()
    d["extension_stones"] = "5"        # str that int() accepts
    p2 = Player.from_dict(d, _RecordingBus())
    return {"str_coerced": p2.extension_stones == 5}


# ---------------------------------------------------------------------------
# B. Level pickups
# ---------------------------------------------------------------------------

def test_spawn_pickup_and_orb():
    game = _make_game()
    _clean(game)
    area = game.current_area
    area.spawn_pickup(10, 10, "stone")
    area.spawn_orb(20, 20)
    stone = _drops(game, "stone")
    heal = _drops(game, "heal")
    return {"stone_spawned": len(stone) == 1,
            "heal_spawned": len(heal) == 1,
            "stone_item_is_none": stone[0]["item"] is None,
            "heal_item_is_none": heal[0]["item"] is None}


def test_spawn_loot_accepts_item_kwargs():
    game = _make_game()
    _clean(game)
    game.current_area.spawn_loot(10, 10, area_level=10, world_rules=None,
                                 force_affix_counts=(1, 1))
    drops = _item_drops(game)
    return {"one_item_dropped": len(drops) == 1,
            "has_affixes": len(drops[0]["item"].all_affixes()) >= 1}


def test_spawn_loot_default_still_works():
    game = _make_game()
    _clean(game)
    game.current_area.spawn_loot(10, 10, area_level=3)
    return {"default_call_ok": len(_item_drops(game)) == 1}


def test_level_draw_handles_every_drop_kind():
    from items.item_generator import generate_random_item
    game = _make_game()
    _clean(game)
    area = game.current_area
    area.spawn_pickup(game.player.x, game.player.y, "stone")
    area.spawn_orb(game.player.x + 20, game.player.y)
    item = generate_random_item(game.world.rng_service.get_stream("debug"))
    area.loot_drops.append({"x": game.player.x - 20, "y": game.player.y,
                            "item": item})
    area.draw(game.screen, game.camera)
    return {"drew_without_crash": True}


def test_heal_pickup_only_when_hurt():
    game = _make_game()
    _clean(game)
    p = game.player
    s = p.effective_stats
    s.hp = s.max_hp
    game.current_area.spawn_orb(p.x, p.y)
    game._pickup_loot()
    kept_when_full = len(_drops(game, "heal")) == 1

    s.hp = s.max_hp // 2
    before = s.hp
    game._pickup_loot()
    return {"orb_kept_at_full_hp": kept_when_full,
            "orb_consumed_when_hurt": len(_drops(game, "heal")) == 0,
            "hp_increased": s.hp > before,
            "hp_not_over_max": s.hp <= s.max_hp}


def test_stone_pickup_increments_and_skips_inventory():
    game = _make_game()
    _clean(game)
    p = game.player
    p.extension_stones = 3

    def boom(*a, **k):
        raise AssertionError("stone pickup touched inventory")
    p.inventory.add_item = boom

    game.current_area.spawn_pickup(p.x, p.y, "stone")
    game._pickup_loot()
    return {"stones_plus_one": p.extension_stones == 4,
            "drop_removed": len(_drops(game, "stone")) == 0,
            "message_mentions_stone": "Stone" in game.message}


def test_item_pickup_still_works_with_new_drop_kinds_present():
    from items.item_generator import generate_random_item
    game = _make_game()
    _clean(game)
    p = game.player
    area = game.current_area
    area.spawn_orb(p.x + 500, p.y)        # far away, must be ignored safely
    item = generate_random_item(game.world.rng_service.get_stream("debug"))
    area.loot_drops.append({"x": p.x, "y": p.y, "item": item})
    n_before = len(p.inventory.items)
    game._pickup_loot()
    return {"item_picked_up": len(p.inventory.items) == n_before + 1,
            "far_orb_untouched": len(_drops(game, "heal")) == 1}


# ---------------------------------------------------------------------------
# C. World.pick_next_gate(eligible=...)
# ---------------------------------------------------------------------------

def _world_with_two_gates():
    from world.world import World
    w = World(123)
    g_start = w.register_starting_gate(0, 1, 1)
    g_ext = w.register_extension_gate("ext_a", 0, 2, 2)
    return w, g_start, g_ext


def test_pick_gate_default_prefers_starting():
    w, g_start, g_ext = _world_with_two_gates()
    return {"starting_preferred": w.pick_next_gate(random.Random(0)) is g_start}


def test_pick_gate_eligible_filters():
    w, g_start, g_ext = _world_with_two_gates()
    only_ext = w.pick_next_gate(
        random.Random(0), eligible=lambda g: g.owner_area_id != "starting_area")
    none_ok = w.pick_next_gate(random.Random(0), eligible=lambda g: False)
    return {"only_ext_returned": only_ext is g_ext,
            "none_when_nothing_eligible": none_ok is None}


def test_pick_gate_backward_compat_helpers():
    w, g_start, g_ext = _world_with_two_gates()
    opened = w.open_next_gate_to("extension_1", random.Random(0))
    return {"open_next_gate_to_works": opened is g_start,
            "gate_now_open": g_start.is_open()}


# ---------------------------------------------------------------------------
# D. Boss-gated gates in Game
# ---------------------------------------------------------------------------

def test_gate_unlocked_rules():
    from world.gate import Gate
    from enemies.boss import Boss, BossTemplate
    game = _make_game()
    _clean(game)
    g_start = game.world.gates_in_area("starting_area")[0]
    g_ext = Gate(gate_id=99, owner_area_id="ext_x", room_index=0,
                 tile_col=1, tile_row=1)

    no_boss_ok = game._gate_unlocked(g_ext)

    boss = Boss(0, 0, BossTemplate.hollow_warden())
    boss.region_id = "ext_x"
    game.current_area.bosses.append(boss)
    locked_while_alive = game._gate_unlocked(g_ext) is False

    other = Boss(0, 0, BossTemplate.hollow_warden())
    other.region_id = "other_region"
    game.current_area.bosses.append(other)
    other_does_not_matter = game._gate_unlocked(g_ext) is False

    boss.alive = False
    unlocked_when_dead = game._gate_unlocked(g_ext) is True
    return {"starting_gate_always_unlocked": game._gate_unlocked(g_start),
            "region_without_boss_unlocked": no_boss_ok,
            "locked_while_boss_alive": locked_while_alive,
            "other_regions_boss_ignored": other_does_not_matter,
            "unlocked_when_boss_dead": unlocked_when_dead}


def test_pick_gate_respects_gating_and_debug_bypass():
    from world.gate import Gate
    from enemies.boss import Boss, BossTemplate
    game = _make_game()
    _clean(game)
    for g in game.world.gates:
        g.state = "open"
    locked = Gate(gate_id=99, owner_area_id="ext_x", room_index=0,
                  tile_col=1, tile_row=1)
    game.world.gates.append(locked)
    boss = Boss(0, 0, BossTemplate.hollow_warden())
    boss.region_id = "ext_x"
    game.current_area.bosses.append(boss)

    normal = game._pick_gate(debug=False)
    debug = game._pick_gate(debug=True)
    boss.alive = False
    after_kill = game._pick_gate(debug=False)
    return {"normal_blocked_by_boss": normal is None,
            "debug_bypasses_boss": debug is locked,
            "available_after_boss_dead": after_kill is locked}


def test_locked_hint_names_the_region():
    from world.gate import Gate
    from enemies.boss import Boss, BossTemplate
    game = _make_game()
    _clean(game)
    for g in game.world.gates:
        g.state = "open"
    game.world.gates.append(Gate(gate_id=99, owner_area_id="ext_x",
                                 room_index=0, tile_col=1, tile_row=1))
    boss = Boss(0, 0, BossTemplate.hollow_warden())
    boss.region_id = "ext_x"
    game.current_area.bosses.append(boss)
    hint_locked = game._locked_hint()

    for g in game.world.gates:
        g.state = "open"
    boss.alive = False
    hint_none = game._locked_hint()
    return {"locked_hint_mentions_region": "ext_x" in hint_locked,
            "fallback_hint_is_str": isinstance(hint_none, str) and hint_none != ""}


# ---------------------------------------------------------------------------
# E. Stone economy through F11
# ---------------------------------------------------------------------------

def _need_gate(game):
    if not game.world.closed_gates():
        raise RuntimeError("precondition: starting gate not registered")


def test_manual_generate_blocked_without_stones():
    game = _make_game()
    _clean(game)
    _need_gate(game)
    game.player.extension_stones = 0
    game._generate_next_extension(mode="manual")
    return {"no_pending_offer": game._pending_extension_spec is None,
            "banner_hidden": game.evolution_banner.visible is False,
            "message_mentions_stones": "Extension Stones" in game.message}


def test_debug_generate_ignores_stones():
    game = _make_game()
    _clean(game)
    _need_gate(game)
    game.player.extension_stones = 0
    game._generate_next_extension(mode="debug")
    ok = game._pending_extension_spec is not None
    game._accept_pending_extension()
    return {"offer_created_with_zero_stones": ok,
            "stones_unchanged_after_debug_accept": game.player.extension_stones == 0,
            "extension_added": len(game.world.extensions) == 1}


def test_manual_accept_spends_exactly_one_stone():
    game = _make_game()
    _clean(game)
    _need_gate(game)
    bus_events = []
    game.event_bus.subscribe("extension_generated",
                             lambda **p: bus_events.append(p))
    game._generate_next_extension(mode="manual")
    offered = game._pending_extension_spec is not None
    stones_at_offer = game.player.extension_stones
    game._accept_pending_extension()
    return {"offer_created": offered,
            "offer_is_free": stones_at_offer == 10,
            "accept_costs_one": game.player.extension_stones == 9,
            "extension_added": len(game.world.extensions) == 1,
            "pending_cleared": game._pending_extension_spec is None,
            "banner_closed": game.evolution_banner.visible is False,
            "event_emitted": len(bus_events) == 1}


def test_reroll_and_pushback_are_free():
    game = _make_game()
    _clean(game)
    _need_gate(game)
    game._generate_next_extension(mode="manual")
    left_before = game._pending_rerolls_left
    game._reroll_pending_extension()
    rerolled_ok = (game._pending_rerolls_left == left_before - 1
                   and game._pending_extension_spec is not None
                   and game._pending_gate is not None)
    free_after_reroll = game.player.extension_stones == 10
    game._discard_pending_extension()
    return {"reroll_keeps_offer": rerolled_ok,
            "reroll_free": free_after_reroll,
            "pushback_free": game.player.extension_stones == 10,
            "pushback_clears_offer": game._pending_extension_spec is None}


def test_last_stone_then_blocked():
    game = _make_game()
    _clean(game)
    _need_gate(game)
    game.player.extension_stones = 1
    game._generate_next_extension(mode="manual")
    game._accept_pending_extension()
    spent = game.player.extension_stones == 0
    game.message = ""
    game._generate_next_extension(mode="manual")
    return {"last_stone_spent": spent,
            "second_attempt_blocked": game._pending_extension_spec is None,
            "message_mentions_stones": "Extension Stones" in game.message}


def test_no_gate_available_blocks_cleanly():
    game = _make_game()
    _clean(game)
    for g in game.world.closed_gates():
        g.open_to("whatever")
    game._generate_next_extension(mode="manual")
    return {"no_pending_offer": game._pending_extension_spec is None,
            "message_set": game.message != "",
            "stones_untouched": game.player.extension_stones == 10}


def test_f11_keys_route_manual_and_debug():
    game = _make_game()
    _clean(game)
    _need_gate(game)
    game.player.extension_stones = 0

    _with_mods(0, lambda: game._handle_keydown(pygame.K_F11))
    manual_blocked = game._pending_extension_spec is None

    _with_mods(pygame.KMOD_SHIFT, lambda: game._handle_keydown(pygame.K_F11))
    debug_allowed = game._pending_extension_spec is not None
    mode = game._pending_evolution_mode
    return {"F11_blocked_without_stones": manual_blocked,
            "shift_F11_allowed": debug_allowed,
            "shift_F11_mode_is_debug": mode == "debug"}


def test_automatic_generation_is_disabled():
    game = _make_game()
    _clean(game)
    game.world.total_playtime_seconds = 10 ** 9
    game._check_automatic_extension()
    game._update(1 / 60.0)
    return {"no_offer_from_playtime": game._pending_extension_spec is None,
            "banner_hidden": game.evolution_banner.visible is False}


# ---------------------------------------------------------------------------
# F. Kill drops
# ---------------------------------------------------------------------------

def _spawn_at_player(game, **kw):
    from enemies.enemy import Enemy
    e = Enemy(game.player.x, game.player.y, **kw)
    game.current_area.enemies.append(e)
    return e


def _set_loot_rng(game, v):
    game.world.rng_service._streams["loot"] = _FixedRng(v)


def test_low_roll_drops_stone_and_item():
    game = _make_game()
    _clean(game)
    _set_loot_rng(game, 0.0)
    e = _spawn_at_player(game, hp=1)
    game._on_enemy_killed(e)
    return {"stone_dropped": len(_drops(game, "stone")) == 1,
            "item_dropped": len(_item_drops(game)) == 1,
            "enemy_removed": e not in game.current_area.enemies}


def test_high_roll_drops_nothing():
    game = _make_game()
    _clean(game)
    _set_loot_rng(game, 0.99)
    e = _spawn_at_player(game, hp=1)
    game._on_enemy_killed(e)
    return {"no_drops": len(game.current_area.loot_drops) == 0}


def test_elite_has_better_stone_chance_than_minion():
    game = _make_game()
    _clean(game)
    _set_loot_rng(game, 0.03)       # between 1% and 5%
    minion = _spawn_at_player(game, hp=1)
    game._on_enemy_killed(minion)
    minion_stone = len(_drops(game, "stone"))
    game.current_area.loot_drops.clear()
    elite = _spawn_at_player(game, hp=1, is_elite=True)
    game._on_enemy_killed(elite)
    elite_stone = len(_drops(game, "stone"))
    return {"minion_no_stone_at_3pct": minion_stone == 0,
            "elite_stone_at_3pct": elite_stone == 1}


def test_boss_adds_drop_nothing():
    game = _make_game()
    _clean(game)
    _set_loot_rng(game, 0.0)
    e = _spawn_at_player(game, hp=1)
    e.is_add = True
    game._on_enemy_killed(e)
    return {"add_dropped_nothing": len(game.current_area.loot_drops) == 0,
            "add_removed": e not in game.current_area.enemies}


def test_explosive_elite_hurts_nearby_player():
    game = _make_game()
    _clean(game)
    _set_loot_rng(game, 0.99)
    p = game.player
    p._invuln_timer = 0.0
    hp_before = p.effective_stats.hp
    e = _spawn_at_player(game, hp=1, damage=10)
    e.affixes = ["explosive"]
    game._on_enemy_killed(e)
    hurt = p.effective_stats.hp < hp_before

    # far away: no damage
    p._invuln_timer = 0.0
    hp_mid = p.effective_stats.hp
    far = _spawn_at_player(game, hp=1, damage=10)
    far.x, far.y = p.x + 500, p.y
    far.affixes = ["explosive"]
    game._on_enemy_killed(far)
    return {"near_blast_hurts": hurt,
            "far_blast_harmless": p.effective_stats.hp == hp_mid}


def test_kill_of_unknown_enemy_is_noop():
    from enemies.enemy import Enemy
    game = _make_game()
    _clean(game)
    stranger = Enemy(0, 0)
    game._on_enemy_killed(stranger)
    return {"no_crash": True, "no_drops": len(game.current_area.loot_drops) == 0}


def test_boss_kill_loot_burst():
    from enemies.boss import Boss, BossTemplate
    game = _make_game()
    _clean(game)
    p = game.player
    boss = Boss(p.x, p.y, BossTemplate.hollow_warden())
    boss.region_id = "starting_area"
    add = _spawn_at_player(game, hp=5)
    add.is_add = True
    normal = _spawn_at_player(game, hp=5)

    game._on_boss_killed(boss)
    items = _item_drops(game)
    return {"four_items": len(items) == 4,
            "items_have_affixes": all(len(d["item"].all_affixes()) >= 2 for d in items),
            "one_heal_orb": len(_drops(game, "heal")) == 1,
            "one_stone": len(_drops(game, "stone")) == 1,
            "adds_removed": add not in game.current_area.enemies,
            "normal_enemies_kept": normal in game.current_area.enemies,
            "no_auto_evolution": game._pending_extension_spec is None,
            "message_shown": "FALLEN" in game.message}


def test_boss_kill_ends_attempt_as_victory():
    from enemies.boss import Boss, BossTemplate
    game = _make_game()
    _clean(game)
    events = []
    game.event_bus.subscribe("boss_attempt_ended", lambda **p: events.append(p))
    boss = Boss(game.player.x, game.player.y, BossTemplate.hollow_warden())
    boss.region_id = "starting_area"
    game._boss_attempt_active = True
    game._boss_attempt_boss_id = boss.name
    game._on_boss_killed(boss)
    return {"victory_event": len(events) == 1 and events[0]["result"] == "victory",
            "attempt_flag_cleared": game._boss_attempt_active is False}


# ---------------------------------------------------------------------------
# G. Summoner boss
# ---------------------------------------------------------------------------

def test_summoner_flag_on_templates():
    from enemies.boss import BossTemplate
    from enemies.boss_factory import build_personalized_boss_template
    out = {"hollow_warden_not_summoner": BossTemplate.hollow_warden().summoner is False}
    for arch in ("summoner", "dash_predator", "ranged_zoner", "reflector", "berserker"):
        t = build_personalized_boss_template(
            "B", {"mechanic_archetype": arch, "difficulty_bias": "normal",
                  "punishes_damage_type": "fire"}, 10, random.Random(1))
        out[f"{arch}_flag_correct"] = (t.summoner is (arch == "summoner"))
    return out


def test_summoner_template_passes_validator():
    from ai.validators import BossValidator
    b = _summoner_boss()
    ok, errs = BossValidator.validate(asdict(b.template))
    return {"validates": ok, "errors": errs}


def test_summoner_phase_change_bursts_adds():
    b = _summoner_boss()
    starts_zero = b.summon_requests == 0
    b.take_damage(int(b.max_hp * 0.55))        # ratio 0.45 -> phase 2 (<=0.5)
    entered_phase_2 = b.phase_index == 1
    burst = b.summon_requests == 3
    b.take_damage(1)                            # same phase -> no new burst
    no_second_burst = b.summon_requests == 3
    return {"starts_zero": starts_zero, "entered_phase_2": entered_phase_2,
            "burst_of_three": burst, "no_burst_within_phase": no_second_burst}


def test_non_summoner_never_requests_adds():
    from enemies.boss import Boss, BossTemplate
    b = Boss(0, 0, BossTemplate.hollow_warden())
    b.take_damage(int(b.max_hp * 0.9))
    return {"no_requests": b.summon_requests == 0}


def test_summoner_timer_requests_when_player_near():
    from player.player import Player
    bus = _RecordingBus()
    b = _summoner_boss(1000, 1000)
    near = Player(1100, 1000, bus)
    b._summon_timer = 0.01
    b.update(0.05, near, bus)
    got_near = b.summon_requests == 2 + b.phase_index

    b2 = _summoner_boss(1000, 1000)
    far = Player(1000 + 900, 1000, bus)
    b2._summon_timer = 0.01
    b2.update(0.05, far, bus)
    return {"timer_summons_when_near": got_near,
            "no_summons_when_far": b2.summon_requests == 0}


def test_spawn_boss_adds_creates_swarmers():
    game = _make_game()
    _clean(game)
    game.current_area.enemies.clear()
    sx, sy = game.current_area.start_pos
    boss = _summoner_boss(sx, sy)
    boss.summon_requests = 3
    game._spawn_boss_adds(boss)
    adds = [e for e in game.current_area.enemies if getattr(e, "is_add", False)]
    return {"requests_reset": boss.summon_requests == 0,
            "some_adds_spawned": 1 <= len(adds) <= 3,
            "all_swarmers": all(e.behavior == "swarmer" for e in adds),
            "all_tagged_region": all(e.region_id == "starting_area" for e in adds),
            "low_xp": all(e.xp_reward == 3 for e in adds),
            "none_elite": not any(e.is_elite for e in adds),
            "not_stuck_in_walls": not any(
                game.current_area.tilemap.collides_rect(e.rect) for e in adds)}


def test_spawn_boss_adds_respects_cap():
    from enemies.enemy import Enemy
    game = _make_game()
    _clean(game)
    game.current_area.enemies.clear()
    for _ in range(8):
        e = Enemy(0, 0)
        e.is_add = True
        game.current_area.enemies.append(e)
    sx, sy = game.current_area.start_pos
    boss = _summoner_boss(sx, sy)
    boss.summon_requests = 5
    game._spawn_boss_adds(boss)
    adds = sum(1 for e in game.current_area.enemies if getattr(e, "is_add", False))
    return {"cap_of_8_holds": adds == 8, "requests_reset": boss.summon_requests == 0}


def test_update_loop_spawns_adds_for_pending_requests():
    game = _make_game()
    _clean(game)
    game.player.alive = True
    game.current_area.enemies.clear()
    game.current_area.bosses.clear()
    sx, sy = game.current_area.start_pos
    game.player.x, game.player.y = sx, sy
    boss = _summoner_boss(sx + 120, sy)
    boss.summon_requests = 2
    game.current_area.bosses.append(boss)
    game._update(1 / 60.0)
    adds = [e for e in game.current_area.enemies if getattr(e, "is_add", False)]
    return {"adds_spawned_by_update": len(adds) >= 1,
            "requests_consumed": boss.summon_requests == 0}


# ---------------------------------------------------------------------------
# H. New enemy types + elite affixes
# ---------------------------------------------------------------------------

def _scan(area_level, n, seed=0):
    from enemies.enemy import spawn_basic_enemy
    rng = random.Random(seed)
    return [spawn_basic_enemy(0, 0, rng, area_level=area_level) for _ in range(n)]


def test_behavior_tables_cover_new_types():
    from enemies.enemy import BEHAVIOR_TABLE, NAME_POOL
    names = {row[0] for row in BEHAVIOR_TABLE}
    return {"brute_in_table": "brute" in names,
            "swarmer_in_table": "swarmer" in names,
            "brute_names": bool(NAME_POOL.get("brute")),
            "swarmer_names": bool(NAME_POOL.get("swarmer")),
            "every_behavior_has_names": all(n in NAME_POOL for n in names)}


def test_force_behavior_sizes_and_never_elite():
    from enemies.enemy import spawn_basic_enemy
    rng = random.Random(5)
    brute = spawn_basic_enemy(0, 0, rng, area_level=4, force_behavior="brute")
    swarms = [spawn_basic_enemy(0, 0, rng, area_level=4, force_behavior="swarmer")
              for _ in range(300)]
    return {"brute_is_brute": brute.behavior == "brute",
            "brute_is_big": brute.width == 40 and brute.height == 40,
            "swarmer_is_small": all(s.width == 20 for s in swarms),
            "forced_never_elite": not any(s.is_elite for s in swarms),
            "forced_no_affixes": not any(s.affixes for s in swarms)}


def test_brute_has_more_hp_than_swarmer():
    from enemies.enemy import spawn_basic_enemy
    rng = random.Random(1)
    b = spawn_basic_enemy(0, 0, rng, area_level=5, force_behavior="brute")
    s = spawn_basic_enemy(0, 0, rng, area_level=5, force_behavior="swarmer")
    return {"brute_tankier": b.max_hp > s.max_hp,
            "swarmer_faster": s.speed > b.speed}


def test_new_types_move_toward_player():
    from enemies.enemy import spawn_basic_enemy
    from player.player import Player
    bus = _RecordingBus()
    out = {}
    for beh in ("brute", "swarmer"):
        rng = random.Random(2)
        e = spawn_basic_enemy(0, 0, rng, area_level=2, force_behavior=beh)
        e.x, e.y = 0.0, 0.0
        p = Player(200, 0, bus)
        e.update(0.1, p, _OpenMap(), bus, world_rules=None)
        out[f"{beh}_chases"] = e.x > 0
    return out


def test_affixes_not_shared_between_instances():
    from enemies.enemy import Enemy
    a, b = Enemy(0, 0), Enemy(0, 0)
    a.affixes.append("swift")
    return {"separate_lists": b.affixes == [] and a.affixes is not b.affixes}


def test_elite_affix_rules():
    from enemies.enemy import ELITE_AFFIXES
    low = _scan(3, 1500, seed=11)
    high = _scan(10, 3000, seed=12)
    elites_low = [e for e in low if e.is_elite]
    elites_high = [e for e in high if e.is_elite]
    names_ok = all(e.name.lower().startswith(e.affixes[0]) for e in elites_low + elites_high)
    return {
        "elites_exist": len(elites_low) > 20 and len(elites_high) > 20,
        "elites_always_have_affix": all(1 <= len(e.affixes) <= 2 for e in elites_low + elites_high),
        "low_level_exactly_one": all(len(e.affixes) == 1 for e in elites_low),
        "high_level_sometimes_two": any(len(e.affixes) == 2 for e in elites_high),
        "affixes_valid_and_unique": all(
            set(e.affixes) <= set(ELITE_AFFIXES) and len(set(e.affixes)) == len(e.affixes)
            for e in elites_low + elites_high),
        "non_elites_clean": all(not e.affixes for e in low + high if not e.is_elite),
        "name_prefixed_by_first_affix": names_ok,
    }


def test_swift_and_armored_stat_effects():
    bases = [95.0, 110.0, 105.0, 85.0, 65.0, 135.0]
    elites = [e for e in _scan(4, 4000, seed=21) if e.is_elite]
    swift = [e for e in elites if "swift" in e.affixes]
    armored = [e for e in elites if "armored" in e.affixes]
    swift_ok = all(any(abs(e.speed - b * 1.5) < 1e-6 for b in bases) for e in swift)
    armored_ok = all(e.armor == 20 + 4 * 6
                     and e.resistances["armor_flat"] == e.armor
                     and e.hp == e.max_hp for e in armored)
    return {"found_swift": len(swift) > 0, "found_armored": len(armored) > 0,
            "swift_speed_x1_5": swift_ok, "armored_stats": armored_ok}


def test_regenerating_heals_over_time():
    from enemies.enemy import Enemy
    from player.player import Player
    bus = _RecordingBus()
    far = Player(9000, 9000, bus)
    e = Enemy(0, 0, hp=100)
    e.affixes = ["regenerating"]
    e.hp = 50
    e.update(1.0, far, _OpenMap(), bus)
    healed = e.hp > 50

    e.hp = e.max_hp
    e.update(1.0, far, _OpenMap(), bus)
    no_overheal = e.hp <= e.max_hp

    plain = Enemy(0, 0, hp=100)
    plain.hp = 50
    plain.update(1.0, far, _OpenMap(), bus)
    return {"regen_heals": healed, "regen_capped": no_overheal,
            "plain_does_not_heal": plain.hp == 50}


def test_enemy_draw_with_affixes_and_new_types():
    from enemies.enemy import spawn_basic_enemy
    game = _make_game()
    rng = random.Random(3)
    for beh in ("brute", "swarmer", "chaser", "ranged"):
        e = spawn_basic_enemy(300, 300, rng, area_level=8, force_behavior=beh)
        e.hp = e.max_hp - 1
        e.draw(game.screen, game.camera)
    elites = [e for e in _scan(10, 800, seed=4) if e.is_elite and len(e.affixes) == 2]
    for e in elites[:5]:
        e.x, e.y = 300, 300
        e.draw(game.screen, game.camera)
    return {"drew_without_crash": True, "two_affix_elites_drawn": len(elites) > 0}


# ---------------------------------------------------------------------------
# I. Expansion integration
# ---------------------------------------------------------------------------

def test_biome_element_flavors_enemies():
    out = {}
    for biome, elem in (("ash_canyon", "fire"), ("frozen_reach", "cold"),
                        ("storm_peaks", "lightning")):
        game = _make_game()
        ext, _ = _expand(game, biome=biome)
        mine = [e for e in game.current_area.enemies
                if getattr(e, "region_id", None) == ext.extension_id]
        out[f"{biome}_has_5plus_enemies"] = len(mine) >= 5
        out[f"{biome}_damage_type"] = all(e.damage_type == elem for e in mine)
        out[f"{biome}_resists_25pct"] = all(e.resistances.get(elem) == 0.25 for e in mine)
    return out


def test_unmapped_biome_stays_physical():
    game = _make_game()
    ext, _ = _expand(game, biome="verdant_ruin")
    mine = [e for e in game.current_area.enemies
            if getattr(e, "region_id", None) == ext.extension_id]
    return {"has_enemies": len(mine) > 0,
            "all_physical": all(e.damage_type == "physical" for e in mine)}


def test_extension_boss_is_summoner_and_gates_next_door():
    game = _make_game()
    ext, _ = _expand(game, archetype="summoner")
    rid = ext.extension_id
    boss = _region_boss(game, rid)
    gates = game.world.gates_in_area(rid)
    out = {"boss_exists": boss is not None,
           "boss_is_guardian": boss is not None and boss.name.startswith("Guardian of"),
           "boss_is_summoner": boss is not None and boss.template.summoner is True}
    if not gates:
        out["note"] = "no door site found in region; gate checks skipped"
        return out
    locked = game._gate_unlocked(gates[0]) is False
    boss.alive = False
    out["next_gate_locked_while_boss_alive"] = locked
    out["next_gate_unlocked_after_kill"] = game._gate_unlocked(gates[0]) is True
    return out


def test_non_summoner_extension_boss_has_flag_false():
    game = _make_game()
    ext, _ = _expand(game, archetype="dash_predator")
    boss = _region_boss(game, ext.extension_id)
    return {"boss_exists": boss is not None,
            "not_summoner": boss is not None and boss.template.summoner is False}


def test_expanded_boss_survives_validator():
    """Replacement for the old AreaInstance test in test_helpers9."""
    game = _make_game()
    ext, _ = _expand(game, archetype="reflector")
    boss = _region_boss(game, ext.extension_id)
    return {"boss_exists": boss is not None,
            "not_hollow_warden_fallback": boss is not None
                and boss.name == "Guardian of Testlands"}


# ---------------------------------------------------------------------------
# J. Roaming spawns
# ---------------------------------------------------------------------------

def test_roaming_spawn_creates_offscreen_enemy():
    import math
    game = _make_game()
    _clean(game)
    game.current_area.enemies.clear()
    p = game.player
    before = len(game.current_area.enemies)
    for _ in range(40):
        game._roam_timer = 0.0
        game._update_roaming_spawns(0.0)
        if len(game.current_area.enemies) > before:
            break
    spawned = game.current_area.enemies[before:]
    e = spawned[0] if spawned else None
    return {"spawned_one": len(spawned) >= 1,
            "offscreen": e is not None and math.hypot(e.x - p.x, e.y - p.y) >= 700,
            "tagged_region": e is not None and hasattr(e, "region_id"),
            "on_floor": e is not None and not game.current_area.tilemap.collides_rect(e.rect),
            "timer_reset": game._roam_timer > 5.0}


def test_roaming_respects_timer_and_cap():
    from enemies.enemy import Enemy
    game = _make_game()
    _clean(game)
    p = game.player
    game.current_area.enemies.clear()

    game._roam_timer = 5.0
    game._update_roaming_spawns(1.0)
    timer_blocks = len(game.current_area.enemies) == 0 and game._roam_timer == 4.0

    for _ in range(25):
        game.current_area.enemies.append(Enemy(p.x, p.y))
    n = len(game.current_area.enemies)
    game._roam_timer = 0.0
    game._update_roaming_spawns(0.0)
    return {"timer_blocks_spawn": timer_blocks,
            "cap_blocks_spawn": len(game.current_area.enemies) == n,
            "timer_still_reset_at_cap": game._roam_timer > 5.0}


# ---------------------------------------------------------------------------
# K. Dash
# ---------------------------------------------------------------------------

def _player():
    from player.player import Player
    return Player(0, 0, _RecordingBus())


def test_dash_state_and_cooldown():
    p = _player()
    p.facing = pygame.Vector2(1, 0)
    first = p.try_dash()
    timer_ok = abs(p._dash_timer - 0.16) < 1e-6 and abs(p._dash_cd - 0.9) < 1e-6
    second = p.try_dash()

    z = _player()
    z.facing = pygame.Vector2(0, 0)
    return {"first_dash_ok": first, "timers_set": timer_ok,
            "immediate_redash_refused": second is False,
            "no_dash_without_direction": z.try_dash() is False,
            "iframes_granted": p._invuln_timer >= 0.22}


def test_dash_grants_invulnerability():
    p = _player()
    p.facing = pygame.Vector2(1, 0)
    p.try_dash()
    return {"hit_ignored_during_dash": p.take_damage(10) is False,
            "hp_unchanged": p.effective_stats.hp == p.effective_stats.max_hp}


def test_dash_moves_then_stops_then_recharges():
    p = _player()
    p.facing = pygame.Vector2(1, 0)
    keys, tm = _Keys(), _OpenMap()
    p.try_dash()
    p.handle_input(0.05, keys, tm)
    first_step = p.x
    for _ in range(9):
        p.handle_input(0.05, keys, tm)
    mid_x = p.x
    for _ in range(10):
        p.handle_input(0.05, keys, tm)
    return {"first_step_big": first_step > 30,
            "total_distance_reasonable": 80 < mid_x < 200,
            "stops_after_dash": p.x == mid_x,
            "cooldown_expired": p._dash_cd <= 0,
            "can_dash_again": p.try_dash() is True}


def test_dash_blocked_by_wall():
    p = _player()
    p.facing = pygame.Vector2(1, 0)
    p.try_dash()
    p.handle_input(0.05, _Keys(), _WallMap())
    return {"did_not_move": p.x == 0 and p.y == 0}


def test_normal_walking_unchanged_by_dash_code():
    p = _player()
    class K:
        def __getitem__(self, k):
            return k == pygame.K_d
    p.handle_input(0.1, K(), _OpenMap())
    expected = p.effective_stats.speed * 0.1
    return {"walk_distance_matches_speed": abs(p.x - expected) < 1e-6}


def test_left_shift_triggers_dash_in_game():
    game = _make_game()
    _clean(game)
    game.player.alive = True
    game._handle_keydown(pygame.K_LSHIFT)
    return {"dash_started": game.player._dash_timer > 0}


# ---------------------------------------------------------------------------
# L. UI layout
# ---------------------------------------------------------------------------

def _panel_rect(stash):
    import core.config as config
    pw = 1130 if stash else 660
    return pygame.Rect(config.SCREEN_WIDTH // 2 - pw // 2, 40, pw, 600)


def test_inventory_regions_fit_inside_panel():
    import core.config as config
    game = _make_game()
    _clean(game)
    out = {}
    for stash in (False, True):
        ui = game.inventory_ui
        ui.visible = True
        ui.stash_visible = stash
        game.screen.fill((0, 0, 0))
        ui.draw(game.screen, game.player)
        panel = _panel_rect(stash)
        screen = pygame.Rect(0, 0, config.SCREEN_WIDTH, config.SCREEN_HEIGHT)
        regions = [r for r, _ in ui._hit_regions]
        out[f"stash_{stash}_has_regions"] = len(regions) > 0
        out[f"stash_{stash}_inside_panel"] = all(panel.contains(r) for r in regions)
        out[f"stash_{stash}_panel_on_screen"] = screen.contains(panel)
        ui.visible = False
        ui.stash_visible = False
    return out


def test_inventory_draws_with_item_tooltip_and_stones():
    from items.item_generator import generate_random_item
    game = _make_game()
    _clean(game)
    game.player.extension_stones = 10
    item = generate_random_item(game.world.rng_service.get_stream("debug"),
                                force_affix_counts=(3, 3))
    game.player.inventory.add_item(item)
    ui = game.inventory_ui
    ui.visible = True
    ui.inv_cursor = (0, 0)
    ui.draw(game.screen, game.player, world_rules=game._active_world_rules())
    for focus in ("equipped", "stash"):
        ui.focus = focus
        ui.stash_visible = True
        ui.draw(game.screen, game.player)
    src = inspect.getsource(type(ui).draw)
    ui.visible = False
    ui.stash_visible = False
    return {"draws_ok": True,
            "stones_line_present_in_draw": "Extension Stones" in src}


def test_full_frame_draws_with_inventory_open_and_closed():
    game = _make_game()
    _clean(game)
    game.current_area.spawn_pickup(game.player.x + 30, game.player.y, "stone")
    game.current_area.spawn_orb(game.player.x - 30, game.player.y)
    game._draw()
    game.inventory_ui.toggle()
    game._draw()
    game.inventory_ui.toggle()
    return {"both_frames_ok": True}


# ---------------------------------------------------------------------------
# M. Save / load
# ---------------------------------------------------------------------------

def _tmp_save_manager(tmpdir):
    from save.save_manager import SaveManager
    return SaveManager(path=os.path.join(tmpdir, "save.json"),
                       stash_path=os.path.join(tmpdir, "stash.json"))


def test_save_load_keeps_stones():
    with tempfile.TemporaryDirectory() as d:
        game = _make_game()
        _clean(game)
        game.save_manager = _tmp_save_manager(d)
        game.player.extension_stones = 4
        game._save_game()
        game.player.extension_stones = 9
        game._load_game()
        return {"stones_restored": game.player.extension_stones == 4}


def test_save_load_with_extension_and_gating():
    with tempfile.TemporaryDirectory() as d:
        game = _make_game()
        _clean(game)
        game.save_manager = _tmp_save_manager(d)
        ext, _ = _expand(game, archetype="summoner")
        game.player.extension_stones = 6
        game._save_game()
        game._load_game()
        boss = _region_boss(game, ext.extension_id)
        for _ in range(20):
            game._update(1 / 60.0)
        return {"extension_restored": len(game.world.extensions) == 1,
                "stones_restored": game.player.extension_stones == 6,
                "boss_rebuilt": boss is not None,
                "boss_still_summoner": boss is not None and boss.template.summoner,
                "updates_cleanly": True,
                "info_known_limitation": "bosses respawn alive after load "
                                         f"(alive={boss.alive if boss else None})"}


def test_load_clears_pending_offer():
    with tempfile.TemporaryDirectory() as d:
        game = _make_game()
        _clean(game)
        _need_gate(game)
        game.save_manager = _tmp_save_manager(d)
        game._save_game()
        game._generate_next_extension(mode="manual")
        had_offer = game._pending_extension_spec is not None
        game._load_game()
        return {"had_offer": had_offer,
                "offer_cleared": game._pending_extension_spec is None,
                "banner_closed": game.evolution_banner.visible is False,
                "stones_intact": game.player.extension_stones == 10}


# ---------------------------------------------------------------------------
# N. Hygiene
# ---------------------------------------------------------------------------

def test_game_methods_defined_exactly_once():
    import core.game as cg
    src = inspect.getsource(cg)
    names = ["_open_gateway_menu", "_update_current_region", "_teleport_to_region",
             "_on_map_expanded", "_handle_gateway_menu_select",
             "_update_roaming_spawns", "_spawn_boss_adds", "_gate_unlocked",
             "_pick_gate", "_locked_hint", "_on_boss_killed", "_on_enemy_killed",
             "_pickup_loot"]
    out = {}
    for n in names:
        c = src.count(f"def {n}(")
        out[f"{n}_once"] = c == 1
    return out


def test_config_extension_seconds_consistent():
    import core.config as config
    return {"seconds_match_hours":
            abs(config.WORLD_EXTENSION_SECONDS - config.WORLD_EXTENSION_HOURS * 3600) < 1e-6}


def test_gateway_menu_lists_new_regions():
    game = _make_game()
    _clean(game)
    game._open_gateway_menu()
    hidden_when_empty = game.gateway_menu.visible is False
    ext, _ = _expand(game)
    game._open_gateway_menu()
    entries = list(game.gateway_menu.entries)
    visible = game.gateway_menu.visible
    game.gateway_menu.hide()
    return {"hidden_when_nothing_to_visit": hidden_when_empty,
            "shown_after_expansion": visible,
            "lists_new_region": any(e["target_area_id"] == ext.extension_id for e in entries)}


def test_profile_builds_with_new_state():
    game = _make_game()
    _clean(game)
    _expand(game)
    prof = game._current_profile()
    d = prof.to_dict()
    return {"profile_ok": isinstance(d, dict),
            "extensions_counted": d["extensions_generated"] == 1}


# ---------------------------------------------------------------------------
# O. Soak
# ---------------------------------------------------------------------------

def test_soak_summoner_region_with_roaming_and_dash():
    game = _make_game()
    _clean(game)
    ext, _ = _expand(game, archetype="summoner", biome="storm_peaks")
    game._teleport_to_region(ext.extension_id)
    boss = _region_boss(game, ext.extension_id)
    if boss is not None:
        game.player.x, game.player.y = boss.x - 150, boss.y
    game.player.alive = True
    game.player.effective_stats.hp = game.player.effective_stats.max_hp
    for i in range(600):
        if i % 50 == 0:
            game._attack_button_held = not game._attack_button_held
        if i % 90 == 0:
            game._handle_keydown(pygame.K_LSHIFT)
        game._update(1 / 60.0)
        if not game.player.alive:
            game._respawn_timer = 0.0
    game._draw()
    n = len(game.current_area.enemies)
    return {"no_crash": True,
            "enemy_count_bounded": n < 300,
            "info_enemy_count": n}


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    tests = [
        # A
        test_stones_default_ten,
        test_stones_save_roundtrip,
        test_stones_old_save_defaults_to_ten,
        test_stones_zero_survives_roundtrip,
        test_stones_garbage_input_does_not_crash,
        # B
        test_spawn_pickup_and_orb,
        test_spawn_loot_accepts_item_kwargs,
        test_spawn_loot_default_still_works,
        test_level_draw_handles_every_drop_kind,
        test_heal_pickup_only_when_hurt,
        test_stone_pickup_increments_and_skips_inventory,
        test_item_pickup_still_works_with_new_drop_kinds_present,
        # C
        test_pick_gate_default_prefers_starting,
        test_pick_gate_eligible_filters,
        test_pick_gate_backward_compat_helpers,
        # D
        test_gate_unlocked_rules,
        test_pick_gate_respects_gating_and_debug_bypass,
        test_locked_hint_names_the_region,
        # E
        test_manual_generate_blocked_without_stones,
        test_debug_generate_ignores_stones,
        test_manual_accept_spends_exactly_one_stone,
        test_reroll_and_pushback_are_free,
        test_last_stone_then_blocked,
        test_no_gate_available_blocks_cleanly,
        test_f11_keys_route_manual_and_debug,
        test_automatic_generation_is_disabled,
        # F
        test_low_roll_drops_stone_and_item,
        test_high_roll_drops_nothing,
        test_elite_has_better_stone_chance_than_minion,
        test_boss_adds_drop_nothing,
        test_explosive_elite_hurts_nearby_player,
        test_kill_of_unknown_enemy_is_noop,
        test_boss_kill_loot_burst,
        test_boss_kill_ends_attempt_as_victory,
        # G
        test_summoner_flag_on_templates,
        test_summoner_template_passes_validator,
        test_summoner_phase_change_bursts_adds,
        test_non_summoner_never_requests_adds,
        test_summoner_timer_requests_when_player_near,
        test_spawn_boss_adds_creates_swarmers,
        test_spawn_boss_adds_respects_cap,
        test_update_loop_spawns_adds_for_pending_requests,
        # H
        test_behavior_tables_cover_new_types,
        test_force_behavior_sizes_and_never_elite,
        test_brute_has_more_hp_than_swarmer,
        test_new_types_move_toward_player,
        test_affixes_not_shared_between_instances,
        test_elite_affix_rules,
        test_swift_and_armored_stat_effects,
        test_regenerating_heals_over_time,
        test_enemy_draw_with_affixes_and_new_types,
        # I
        test_biome_element_flavors_enemies,
        test_unmapped_biome_stays_physical,
        test_extension_boss_is_summoner_and_gates_next_door,
        test_non_summoner_extension_boss_has_flag_false,
        test_expanded_boss_survives_validator,
        # J
        test_roaming_spawn_creates_offscreen_enemy,
        test_roaming_respects_timer_and_cap,
        # K
        test_dash_state_and_cooldown,
        test_dash_grants_invulnerability,
        test_dash_moves_then_stops_then_recharges,
        test_dash_blocked_by_wall,
        test_normal_walking_unchanged_by_dash_code,
        test_left_shift_triggers_dash_in_game,
        # L
        test_inventory_regions_fit_inside_panel,
        test_inventory_draws_with_item_tooltip_and_stones,
        test_full_frame_draws_with_inventory_open_and_closed,
        # M
        test_save_load_keeps_stones,
        test_save_load_with_extension_and_gating,
        test_load_clears_pending_offer,
        # N
        test_game_methods_defined_exactly_once,
        test_config_extension_seconds_consistent,
        test_gateway_menu_lists_new_regions,
        test_profile_builds_with_new_state,
        # O
        test_soak_summoner_region_with_roaming_and_dash,
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