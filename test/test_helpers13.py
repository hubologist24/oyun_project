import os
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import contextlib
import io
import random
import tempfile
import traceback
from types import SimpleNamespace

import pygame


# ---------------------------------------------------------------------------
# harness (same contract as your existing test file)
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
        self.log = []

    def emit(self, event_type, **payload):
        self.events.append((event_type, payload))
        self.log.append((event_type, payload))

    def subscribe(self, *a, **k):
        pass


def _open_game_no_ui(game):
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


def _clean_arena(game):
    """Empty the map of enemies/bosses/loot and reset all new subsystems."""
    _open_game_no_ui(game)
    a = game.current_area
    a.enemies.clear()
    a.bosses.clear()
    a.loot_drops.clear()
    game.player_projectiles.clear()
    game.damage_numbers.numbers.clear()
    game.crit_bursts.clear()
    p = game.player
    p.alive = True
    p.effective_stats.hp = p.effective_stats.max_hp
    p._invuln_timer = 0.0
    game.reactions.reset()
    game.nemesis.registry.records.clear()
    game.nemesis.reset_transient()
    game.dialogue = None
    game.event_bus.log.clear()
    return game


def _dummy_enemy(x, y, hp=100000, armor=0, damage=8):
    from enemies.enemy import Enemy
    return Enemy(x, y, hp=hp, armor=armor, damage=damage)


def _spawn_near(game, dx=60, dy=0, behavior="chaser"):
    from enemies.enemy import spawn_basic_enemy
    p = game.player
    e = spawn_basic_enemy(p.x + dx, p.y + dy, random.Random(1),
                          area_level=3, force_behavior=behavior)
    e.region_id = "starting_area"
    game.current_area.enemies.append(e)
    return e


def _promote(game, dtype="fire", **kw):
    """Simulate: a normal enemy lands the killing blow, then on_player_death."""
    e = _spawn_near(game, **kw)
    m = game.nemesis
    m.last_attacker = e
    m.last_attacker_time = m._now()
    m.on_player_death(cause=dtype)
    return e, e.nemesis


def _fake_player(primary, extra=()):
    affixes = [SimpleNamespace(damage_type=t) for t in extra]
    weapon = SimpleNamespace(all_affixes=lambda: affixes)
    return SimpleNamespace(effective_stats=SimpleNamespace(primary_damage_type=primary),
                           equipped={"weapon": weapon})


def _hybrid_sword():
    from items.item import Item
    from items.affix import RolledAffix

    def aff(kind, aid, mod, dt):
        return RolledAffix(kind=kind, affix_id=aid, display_name=aid.title(),
                           modifier=mod, damage_type=dt, tier="T3", value=20.0,
                           creation_tier_range=[18, 27])
    it = Item("Hybrid Blade", "weapon", weapon_subtype="sword")
    it.prefixes = [aff("prefix", "burning", "fire_damage", "fire")]
    it.suffixes = [aff("suffix", "of_the_glacier", "cold_damage", "cold")]
    it.recompute_flat_bonuses()
    return it


# ---------------------------------------------------------------------------
# 0. Known bug: arc attacks applying every hit twice
# ---------------------------------------------------------------------------

def test_arc_attack_hits_each_target_once():
    game = _clean_arena(_make_game())
    p = game.player
    p.facing = pygame.Vector2(1, 0)
    e = _dummy_enemy(p.x + 30, p.y)
    game.current_area.enemies.append(e)
    calls = []
    game._apply_player_hit = lambda t, r, m=1.0: calls.append(t)
    game._try_attack()
    return {"exactly_one_hit_per_target": len(calls) == 1, "calls": len(calls)}


# ---------------------------------------------------------------------------
# 1. Elemental reactions
# ---------------------------------------------------------------------------

def test_thermal_shock_on_alternating_hybrid():
    game = _clean_arena(_make_game())
    rs = game.reactions
    p = _fake_player("fire", extra=("cold",))
    e = _dummy_enemy(0, 0)
    first = rs.on_hit(e, 20, p, None)
    second = rs.on_hit(e, 20, p, None)
    return {"first_hit_no_reaction": first is None,
            "second_hit_thermal_shock": second == "thermal_shock",
            "burst_damage_dealt": e.hp < e.max_hp}


def test_reaction_internal_cooldown_and_recovery():
    game = _clean_arena(_make_game())
    rs = game.reactions
    p = _fake_player("fire", extra=("cold",))
    e = _dummy_enemy(0, 0)
    rs.on_hit(e, 20, p, None)
    rs.on_hit(e, 20, p, None)
    during_icd = rs.on_hit(e, 20, p, None)
    rs.tick(1.0)
    rs.on_hit(e, 20, p, None)
    again = rs.on_hit(e, 20, p, None)
    return {"icd_blocks_immediate_retrigger": during_icd is None,
            "reaction_available_after_icd": again == "thermal_shock"}


def test_reaction_window_expires():
    from combat.reactions import REACTION_WINDOW
    game = _clean_arena(_make_game())
    rs = game.reactions
    fire, cold = _fake_player("fire"), _fake_player("cold")
    e1, e2 = _dummy_enemy(0, 0), _dummy_enemy(0, 0)
    rs.on_hit(e1, 10, fire, None)
    inside = rs.on_hit(e1, 10, cold, None)
    rs.on_hit(e2, 10, fire, None)
    rs.tick(REACTION_WINDOW + 1.0)
    outside = rs.on_hit(e2, 10, cold, None)
    return {"reacts_inside_window": inside == "thermal_shock",
            "no_reaction_after_window": outside is None}


def test_same_element_never_reacts():
    game = _clean_arena(_make_game())
    rs = game.reactions
    p = _fake_player("fire")
    e = _dummy_enemy(0, 0)
    results = [rs.on_hit(e, 10, p, None) for _ in range(6)]
    return {"fire_fire_no_reaction": all(r is None for r in results)}


def test_every_reaction_has_label_color_and_handler():
    from combat.reactions import REACTIONS, LABELS, COLORS
    game = _clean_arena(_make_game())
    rs = game.reactions
    a = game.current_area
    t = _dummy_enemy(500, 500)
    a.enemies.extend([t, _dummy_enemy(520, 500), _dummy_enemy(540, 500)])
    errs = []
    for name in LABELS:
        try:
            rs._fire(name, t, 20)
        except Exception as ex:
            errs.append(f"{name}: {type(ex).__name__}: {ex}")
    return {"reaction_names_have_labels": set(REACTIONS.values()) <= set(LABELS),
            "labels_have_colors": set(LABELS) <= set(COLORS),
            "errors": errs}


def test_superconduct_shreds_then_restores():
    game = _clean_arena(_make_game())
    rs = game.reactions
    e = _dummy_enemy(500, 500, armor=50)
    game.current_area.enemies.append(e)
    rs._fire("superconduct", e, 20)
    shredded = e.resistances["armor_flat"]
    rs._shred(e)  # refresh must not compound
    still = e.resistances["armor_flat"]
    rs.tick(5.0)
    return {"armor_reduced": abs(shredded - 15.0) < 1e-6,
            "refresh_does_not_compound": abs(still - 15.0) < 1e-6,
            "armor_restored": abs(e.resistances["armor_flat"] - 50) < 1e-6,
            "physical_restored": abs(e.resistances.get("physical", 0.0)) < 1e-6}


def test_corruption_ticks_and_spreads_once():
    game = _clean_arena(_make_game())
    rs = game.reactions
    a, b, c = _dummy_enemy(500, 500), _dummy_enemy(540, 500), _dummy_enemy(600, 500)
    game.current_area.enemies.extend([a, b, c])
    rs._fire("corruption", a, 40)
    hp_a = a.hp
    for _ in range(30):
        rs.tick(0.1)
    return {"dot_damages_source": a.hp < hp_a,
            "spreads_to_neighbour": b.hp < b.max_hp,
            "spread_does_not_chain": c.hp == c.max_hp}


def test_hemorrhage_stacks_double():
    game = _clean_arena(_make_game())
    rs = game.reactions
    e = _dummy_enemy(0, 0)
    rs._add_dot(e, "bleed", 10, 4.0, stack=True)
    s1 = rs._dots[0]["stacks"]
    rs._add_dot(e, "bleed", 10, 4.0, stack=True)
    s2 = rs._dots[0]["stacks"]
    for _ in range(10):
        rs._add_dot(e, "bleed", 10, 4.0, stack=True)
    return {"starts_at_1": s1 == 1, "doubles": s2 == 2,
            "capped_at_8": rs._dots[0]["stacks"] == 8,
            "single_dot_entry": len(rs._dots) == 1}


def test_dot_kill_goes_through_normal_kill_path():
    game = _clean_arena(_make_game())
    rs, p = game.reactions, game.player
    e = _dummy_enemy(p.x + 200, p.y, hp=1)
    game.current_area.enemies.append(e)
    rs._add_dot(e, "bleed", 50, 2.0)
    rs.tick(0.6)
    return {"removed_from_area": e not in game.current_area.enemies,
            "dead": not e.alive}


def test_no_aura_left_on_killing_blow():
    game = _clean_arena(_make_game())
    p = game.player
    e = _dummy_enemy(p.x + 30, p.y, hp=1)
    game.current_area.enemies.append(e)
    game._apply_player_hit(e, game._active_world_rules())
    return {"target_died": not e.alive,
            "no_aura_on_corpse": len(game.reactions._auras) == 0}


def test_auras_are_cleaned_up():
    game = _clean_arena(_make_game())
    rs = game.reactions
    e = _dummy_enemy(0, 0)
    rs.on_hit(e, 10, _fake_player("fire"), None)
    had = len(rs._auras) == 1
    rs.tick(10.0)
    return {"aura_created": had, "aura_expired_and_removed": len(rs._auras) == 0}


def test_reset_clears_all_state():
    game = _clean_arena(_make_game())
    rs = game.reactions
    e = _dummy_enemy(0, 0, armor=40)
    rs.on_hit(e, 10, _fake_player("fire"), None)
    rs._add_dot(e, "bleed", 5, 3.0)
    rs._shred(e)
    rs.reset()
    return {"auras_empty": not rs._auras, "dots_empty": not rs._dots,
            "shreds_empty": not rs._shreds, "clock_zero": rs.clock == 0.0}


def test_pick_element_includes_world_conversion():
    game = _clean_arena(_make_game())
    rs = game.reactions
    rules = SimpleNamespace(conversion_rules_for=lambda scope: [
        SimpleNamespace(from_type="physical", to_type="chaos", percent=0.35)])
    p = _fake_player("physical")
    e = _dummy_enemy(0, 0)
    seen = [rs._pick_element(p, e, rules) for _ in range(4)]
    return {"cycles_physical_and_chaos": seen == ["physical", "chaos", "physical", "chaos"],
            "seen": seen}


def test_hybrid_weapon_triggers_reaction_end_to_end():
    game = _clean_arena(_make_game())
    p = game.player
    p.equipped["weapon"] = _hybrid_sword()
    p.recalc_stats()
    e = _dummy_enemy(p.x + 30, p.y)
    game.current_area.enemies.append(e)
    rules = game._active_world_rules()
    game._apply_player_hit(e, rules)
    game._apply_player_hit(e, rules)
    texts = [n["text"] for n in game.damage_numbers.numbers]
    return {"thermal_shock_shown": "THERMAL SHOCK" in texts, "texts": texts}


# ---------------------------------------------------------------------------
# 2. Boss dialogue / memory
# ---------------------------------------------------------------------------

def test_dialogue_lines():
    from enemies.dialogue import (boss_engage_line, boss_phase_line,
                                  boss_death_taunt, boss_last_words, PHASE_LINES)
    return {
        "first_attempt": boss_engage_line(1, "fire", []) == "You dare?",
        "sibling_reference": ("Warden X" in boss_engage_line(1, "chaos", ["Warden X"])
                              and "chaos" in boss_engage_line(1, "chaos", ["Warden X"])),
        "attempt_5_mentions_damage": "fire" in boss_engage_line(5, "fire", []),
        "phase_line_clamps_low": boss_phase_line(0) in PHASE_LINES,
        "phase_line_clamps_high": boss_phase_line(99) in PHASE_LINES,
        "taunts_nonempty": bool(boss_death_taunt("fire")) and bool(boss_last_words()),
    }


def test_boss_greeting_uses_real_history():
    from enemies.boss import Boss, BossTemplate
    game = _clean_arena(_make_game())
    b = Boss(0, 0, BossTemplate.hollow_warden())
    for _ in range(3):
        game.event_bus.emit("boss_attempt_started", boss_id=b.name)
    game._boss_greeting(b)
    third = game.dialogue["text"] if game.dialogue else ""

    other = Boss(0, 0, BossTemplate.hollow_warden())
    other.name = "Guardian of Elsewhere"
    game.event_bus.emit("boss_killed", name="Guardian of Foo")
    game.event_bus.emit("boss_attempt_started", boss_id=other.name)
    game._boss_greeting(other)
    sibling = game.dialogue["text"] if game.dialogue else ""
    return {"attempt_count_from_event_log": "Attempt 3" in third,
            "mentions_fallen_sibling": "Guardian of Foo" in sibling}


def test_dialogue_expires():
    game = _clean_arena(_make_game())
    game._say("X", "hello", 1.0)
    game._update(0.5)
    still = game.dialogue is not None
    game._update(0.6)
    return {"still_visible_midway": still, "gone_after_duration": game.dialogue is None}


def test_boss_hit_starts_attempt_once_and_greets():
    from enemies.boss import Boss, BossTemplate
    game = _clean_arena(_make_game())
    p = game.player
    b = Boss(p.x + 40, p.y, BossTemplate.hollow_warden())
    b.region_id = "starting_area"
    game.current_area.bosses.append(b)
    rules = game._active_world_rules()
    game._apply_player_hit(b, rules)
    game._apply_player_hit(b, rules)
    starts = len(game.event_log.events_of_type("boss_attempt_started"))
    return {"attempt_flag_set": game._boss_attempt_active,
            "exactly_one_attempt_event": starts == 1,
            "greeting_spoken_by_boss": bool(game.dialogue) and game.dialogue["speaker"] == b.name}


def test_death_during_boss_fight_taunts_and_ends_attempt():
    from enemies.boss import Boss, BossTemplate
    game = _clean_arena(_make_game())
    p = game.player
    b = Boss(p.x, p.y, BossTemplate.hollow_warden())
    b.region_id = "starting_area"
    game.current_area.bosses.append(b)
    game._boss_attempt_active = True
    game._boss_attempt_boss_id = b.name
    p.take_damage(10 ** 6, cause="physical")
    ended = [e for e in game.event_log.events_of_type("boss_attempt_ended")
             if e.payload.get("result") == "death"]
    return {"attempt_ended_with_death": len(ended) == 1,
            "attempt_flag_cleared": game._boss_attempt_active is False,
            "boss_taunts": bool(game.dialogue) and game.dialogue["speaker"] == b.name}


def test_boss_kill_handler_is_idempotent():
    from enemies.boss import Boss, BossTemplate
    game = _clean_arena(_make_game())
    p = game.player
    b = Boss(p.x + 80, p.y, BossTemplate.hollow_warden())
    b.region_id = "starting_area"
    game.current_area.bosses.append(b)
    b.alive = False
    game._on_boss_killed(b)
    drops_after_first = len(game.current_area.loot_drops)
    game._on_boss_killed(b)
    return {"first_call_dropped_loot": drops_after_first > 0,
            "second_call_adds_nothing": len(game.current_area.loot_drops) == drops_after_first,
            "boss_killed_event_once": len(game.event_log.events_of_type("boss_killed")) == 1}


def test_nemesis_boss_does_not_lock_gates():
    from enemies.boss import Boss, BossTemplate
    game = _clean_arena(_make_game())
    gate = SimpleNamespace(owner_area_id="ext_x")
    roaming = Boss(0, 0, BossTemplate.hollow_warden())
    roaming.region_id = "ext_x"
    roaming.is_nemesis = True
    game.current_area.bosses.append(roaming)
    unlocked_with_nemesis = game._gate_unlocked(gate)
    guardian = Boss(0, 0, BossTemplate.hollow_warden())
    guardian.region_id = "ext_x"
    game.current_area.bosses.append(guardian)
    return {"roaming_world_boss_ignored": unlocked_with_nemesis is True,
            "real_guardian_still_locks": game._gate_unlocked(gate) is False}


# ---------------------------------------------------------------------------
# 3. Nemesis: data layer
# ---------------------------------------------------------------------------

def test_nemesis_helpers():
    from history.nemesis import Nemesis, make_name, taunt_for
    rng = random.Random(0)
    name = make_name("fire", rng)
    unknown = make_name("holy_radiance", rng)
    n = Nemesis(name="Grix, the Flame-Eater", generation=2)
    return {"name_has_epithet": ", " in name and name.endswith("the Flame-Eater"),
            "unknown_type_safe": ", " in unknown,
            "short_has_no_comma": n.short() == "Grix",
            "display_roman": n.display() == "Grix II",
            "gen1_no_suffix": Nemesis(name="A, b", generation=1).display() == "A",
            "taunt_tier_3": "Three" in taunt_for("fire", 3, rng),
            "taunt_unknown_type_safe": bool(taunt_for("holy", 1, rng))}


def test_nemesis_roundtrip_ignores_unknown_keys():
    from history.nemesis import Nemesis, NemesisRegistry
    n = Nemesis(name="Nul, the Voidtouched", damage_type="chaos", generation=3)
    d = n.to_dict()
    d["some_future_field"] = 1
    n2 = Nemesis.from_dict(d)
    reg = NemesisRegistry()
    reg.add(n)
    reg.add(Nemesis(name="B, c"))
    reg2 = NemesisRegistry.from_dict(reg.to_dict())
    return {"roundtrip_equal": n2.name == n.name and n2.generation == 3,
            "ids_sequential": [r.nid for r in reg.records] == [1, 2],
            "registry_roundtrip": len(reg2.records) == 2,
            "registry_from_none": len(NemesisRegistry.from_dict(None).records) == 0}


# ---------------------------------------------------------------------------
# 4. Nemesis: promotion and escalation
# ---------------------------------------------------------------------------

def test_promotion_on_death():
    game = _clean_arena(_make_game())
    e, n = _promote(game, "fire")
    return {"has_nemesis": e.nemesis is n,
            "named": "," in n.name,
            "signature_damage_type": e.damage_type == "fire",
            "marked_elite": e.is_elite,
            "registered_once": len(game.nemesis.registry.records) == 1,
            "taunt_spoken": bool(game.dialogue) and game.dialogue["text"] == n.taunt,
            "stronger_than_base": e.max_hp > e._nem_base[0],
            "display_name_applied": e.name == n.display()}


def test_ignored_attackers_do_not_promote():
    from enemies.boss import HollowWarden
    game = _clean_arena(_make_game())
    m = game.nemesis

    m.last_attacker = None
    m.on_player_death(cause="fire")

    boss = HollowWarden(0, 0)
    m.last_attacker, m.last_attacker_time = boss, m._now()
    m.on_player_death(cause="fire")

    add = _spawn_near(game)
    add.is_add = True
    m.last_attacker, m.last_attacker_time = add, m._now()
    m.on_player_death(cause="fire")

    stale = _spawn_near(game)
    game.world.total_playtime_seconds = 100.0
    m.last_attacker, m.last_attacker_time = stale, 50.0
    m.on_player_death(cause="fire")

    return {"nothing_promoted": len(m.registry.records) == 0}


def test_repeat_death_escalates_without_compounding():
    game = _clean_arena(_make_game())
    m = game.nemesis
    e, n = _promote(game, "fire")
    hp1 = e.max_hp
    m.last_attacker, m.last_attacker_time = e, m._now()
    m.on_player_death(cause="fire")
    hp2 = e.max_hp
    return {"kills_incremented": n.kills_on_player == 2,
            "still_single_record": len(m.registry.records) == 1,
            "scaled_from_base_not_compounded": abs(hp2 / hp1 - 1.2) < 0.05,
            "ratio": round(hp2 / hp1, 3),
            "enemy_still_in_area": e in game.current_area.enemies,
            "taunt_changed_to_repeat_tier": n.taunt != ""}


def test_third_death_becomes_world_boss():
    game = _clean_arena(_make_game())
    m = game.nemesis
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        e, n = _promote(game, "fire")
        for _ in range(2):
            m.last_attacker, m.last_attacker_time = e, m._now()
            m.on_player_death(cause="fire")
    bosses = [b for b in game.current_area.bosses if getattr(b, "nemesis", None) is n]
    return {"flagged_world_boss": n.world_boss,
            "boss_spawned": len(bosses) == 1,
            "boss_is_nemesis": bool(bosses) and bosses[0].is_nemesis,
            "original_enemy_removed": e not in game.current_area.enemies,
            "passed_boss_validator": "failed validation" not in buf.getvalue()}


def test_world_boss_templates_validate_at_high_level():
    from history.nemesis import Nemesis
    game = _clean_arena(_make_game())
    game.player.progression.level = 100
    failures = []
    for arch in ("dash_predator", "berserker", "reflector"):
        nem = Nemesis(name=f"Test {arch}, the Tester", damage_type="fire",
                      area_level=100, archetype=arch, world_boss=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            game.nemesis._build_boss(0, 0, nem, "starting_area")
        if "failed validation" in buf.getvalue():
            failures.append(arch)
    return {"no_archetype_fell_back_to_default": not failures, "failed": failures}


def test_nemesis_boss_hit_is_not_a_guardian_attempt():
    from history.nemesis import Nemesis
    game = _clean_arena(_make_game())
    p = game.player
    nem = Nemesis(name="Grix, the Flame-Eater", damage_type="fire", world_boss=True)
    game.nemesis.registry.add(nem)
    b = game.nemesis._build_boss(p.x + 40, p.y, nem, "starting_area")
    game.current_area.bosses.append(b)
    game._apply_player_hit(b, game._active_world_rules())
    return {"no_attempt_flag": game._boss_attempt_active is False,
            "no_attempt_event": len(game.event_log.events_of_type("boss_attempt_started")) == 0}


# ---------------------------------------------------------------------------
# 5. Nemesis: death, rewards, trophies
# ---------------------------------------------------------------------------

def test_killing_nemesis_rewards_and_schedules_return():
    game = _clean_arena(_make_game())
    p = game.player
    e, n = _promote(game, "fire")
    game.current_area.loot_drops.clear()
    base_hp = p.effective_stats.max_hp
    game._on_enemy_killed(e)
    drops = len(game.current_area.loot_drops)
    game._on_enemy_killed(e)  # second call must be a no-op
    after_hp = p.effective_stats.max_hp
    saved = p.trophies
    p.trophies = []
    p.recalc_stats()
    without = p.effective_stats.max_hp
    p.trophies = saved
    p.recalc_stats()
    return {"removed": e not in game.current_area.enemies,
            "trophy_awarded_once": len(p.trophies) == 1,
            "loot_and_pickups_dropped": drops >= 5,
            "status_pending": n.status == "pending",
            "generation_incremented": n.generation == 2,
            "respawn_scheduled": n.respawn_at > game.nemesis._now(),
           # "trophy_life_bonus": p.effective_stats.max_hp == base_hp + 5,
            "trophy_life_bonus": abs((after_hp - without) - 5) < 1e-6,
            "trophy_resist_bonus": p.effective_stats.resistances.get("fire", 0) >= 0.03 - 1e-9,
            "slain_event_emitted": any(t == "nemesis_slain" for t, _ in game.event_bus.log)}


def test_dot_killing_a_nemesis_awards_trophy():
    game = _clean_arena(_make_game())
    e, n = _promote(game, "chaos")
    e.hp = 1
    game.reactions._add_dot(e, "bleed", 100, 2.0)
    game.reactions.tick(0.6)
    return {"trophy_from_dot_kill": len(game.player.trophies) == 1,
            "nemesis_pending": n.status == "pending"}


def test_world_boss_kill_semantics():
    from history.nemesis import Nemesis
    game = _clean_arena(_make_game())
    p = game.player
    nem = Nemesis(name="Brakk, the Bonebreaker", damage_type="physical", world_boss=True)
    game.nemesis.registry.add(nem)
    b = game.nemesis._build_boss(p.x + 50, p.y, nem, "starting_area")
    game.current_area.bosses.append(b)
    b.alive = False
    game._on_boss_killed(b)
    game._on_boss_killed(b)
    return {"trophy_once": len(p.trophies) == 1,
            "world_boss_stays_dead": nem.status == "dead",
            "not_counted_as_guardian_kill": len(game.event_log.events_of_type("boss_killed")) == 0,
            "bonus_loot_dropped": len(game.current_area.loot_drops) >= 5}


def test_trophy_bonus_is_capped():
    game = _clean_arena(_make_game())
    p = game.player
    p.trophies = [{"name": f"T{i}", "damage_type": "fire", "generation": 1} for i in range(15)]
    base = p.base_stats.max_hp
    p.recalc_stats()
    return {"life_capped_at_10_trophies": p.effective_stats.max_hp == base + 50,
            "resist_capped": abs(p.effective_stats.resistances.get("fire", 0) - 0.30) < 1e-6}


def test_trophies_persist_and_bad_payload_is_safe():
    from player.player import Player
    bus = _RecordingBus()
    p = Player(0, 0, bus)
    p.trophies = [{"name": "Grix, the Flame-Eater", "damage_type": "fire", "generation": 1}]
    d = p.to_dict()
    p2 = Player.from_dict(d, bus)
    legacy = dict(d)
    legacy.pop("trophies")
    p3 = Player.from_dict(legacy, bus)
    corrupt = dict(d)
    corrupt["trophies"] = "oops"
    p4 = Player.from_dict(corrupt, bus)
    return {"roundtrip": p2.trophies == p.trophies,
            "legacy_save_defaults_empty": p3.trophies == [],
            "corrupt_payload_defaults_empty": p4.trophies == []}


def test_loot_override_beats_region_rules():
    import world.level as wl
    from world.loot_rules import LootRules
    game = _clean_arena(_make_game())
    p = game.player
    cap = {}
    orig = wl.generate_random_item

    def spy(rng, area_level=1, world_context=None, **kw):
        cap["ctx"] = world_context
        return orig(rng, area_level=area_level, world_context=world_context, **kw)

    wl.generate_random_item = spy
    override = LootRules(favored_damage_types={"chaos": 9.0})
    region = LootRules()
    game.current_area.region_loot_rules["starting_area"] = region
    try:
        game.current_area.spawn_loot(p.x, p.y, area_level=3, loot_rules_override=override)
        used_override = cap["ctx"]["loot_rules"] is override
        game.current_area.spawn_loot(p.x, p.y, area_level=3)
        used_region = cap["ctx"]["loot_rules"] is region
    finally:
        wl.generate_random_item = orig
        game.current_area.region_loot_rules.pop("starting_area", None)
    return {"override_wins": used_override, "region_rules_still_work": used_region}


# ---------------------------------------------------------------------------
# 6. Nemesis: runtime behavior
# ---------------------------------------------------------------------------

def test_melee_kill_promotes_via_event_bus():
    """End-to-end: attacker_info must be emitted BEFORE take_damage."""
    from combat.combat import enemy_attack_player
    game = _clean_arena(_make_game())
    p = game.player
    e = _dummy_enemy(p.x, p.y, hp=50, damage=1000)
    game.current_area.enemies.append(e)
    p.effective_stats.hp = 1
    enemy_attack_player(e, p, game.event_bus, world_rules=game._active_world_rules())
    recs = game.nemesis.registry.records
    return {"player_died": p.alive is False,
            "nemesis_created": len(recs) == 1,
            "killer_promoted": getattr(e, "nemesis", None) is not None}


def test_projectile_hit_reports_attacker():
    game = _clean_arena(_make_game())
    p = game.player
    e = _dummy_enemy(p.x + 100, p.y)
    e._world_rules = None
    e.projectiles.append({"x": p.x, "y": p.y, "vx": 0, "vy": 0, "damage": 1, "life": 2.0})
    game.event_bus.log.clear()
    e._tick_projectiles(0.0, p, game.event_bus)
    kinds = [t for t, _ in game.event_bus.log]
    return {"attacker_info_emitted": "attacker_info" in kinds,
            "emitted_before_hit": ("player_hit" not in kinds
                                   or kinds.index("attacker_info") < kinds.index("player_hit"))}


def test_warning_is_throttled():
    game = _clean_arena(_make_game())
    e, n = _promote(game, "fire", dx=100)
    m = game.nemesis
    game.message = ""
    m.update(1.1)
    first = "IS NEAR" in game.message
    game.message = ""
    m.update(1.1)
    second_quiet = "IS NEAR" not in game.message
    return {"warns_when_close": first, "does_not_spam": second_quiet}


def test_nova_is_telegraphed_then_hurts():
    from core.nemesis_manager import NOVA_DELAY
    game = _clean_arena(_make_game())
    p = game.player
    e, n = _promote(game, "fire", dx=40)
    m = game.nemesis
    m.novas.clear()
    e._nova_t = 0.01
    m.update(0.02)
    spawned = len(m.novas) == 1
    hp0 = p.effective_stats.hp
    m.update(NOVA_DELAY * 0.5)
    untouched = p.effective_stats.hp == hp0
    m.update(NOVA_DELAY)
    return {"nova_spawned": spawned,
            "no_damage_during_telegraph": untouched,
            "damage_after_telegraph": p.effective_stats.hp < hp0,
            "nova_consumed": len(m.novas) == 0 or all(x["t"] > 0 for x in m.novas)}


def test_nova_far_away_misses():
    from core.nemesis_manager import NOVA_DELAY
    game = _clean_arena(_make_game())
    p = game.player
    e, n = _promote(game, "fire", dx=300)
    m = game.nemesis
    m.novas.clear()
    e._nova_t = 0.01
    m.update(0.02)
    hp0 = p.effective_stats.hp
    m.update(NOVA_DELAY + 0.1)
    return {"player_outside_radius_unhurt": p.effective_stats.hp == hp0}


def test_nova_kill_counts_as_nemesis_kill():
    game = _clean_arena(_make_game())
    p = game.player
    e, n = _promote(game, "fire", dx=40)
    m = game.nemesis
    m.novas.append({"x": p.x, "y": p.y, "t": 0.01, "dtype": "fire", "dmg": 1000, "src": e})
    p.effective_stats.hp = 1
    p._invuln_timer = 0.0
    m.update(0.05)
    return {"player_died": p.alive is False,
            "kill_credited": n.kills_on_player == 2}


def test_respawn_when_due_and_not_before():
    from history.nemesis import Nemesis
    game = _clean_arena(_make_game())
    m = game.nemesis
    early = Nemesis(name="Vorn, the Frostbound", damage_type="cold", generation=2,
                    status="pending", respawn_at=m._now() + 1000, behavior="chaser",
                    area_level=3, kills_on_player=1)
    due = Nemesis(name="Grix, the Flame-Eater", damage_type="fire", generation=2,
                  status="pending", respawn_at=-1.0, behavior="chaser",
                  area_level=3, kills_on_player=1)
    m.registry.add(early)
    m.registry.add(due)
    for _ in range(30):                      # spot search is random; retry
        m._respawn_due()
        if due.status == "alive":
            break
    found = [x for x in game.current_area.enemies if getattr(x, "nemesis", None) is due]
    return {"due_nemesis_respawned": due.status == "alive",
            "enemy_in_area": len(found) == 1,
            "returns_stronger_named": bool(found) and found[0].name == "Grix II",
            "not_due_stays_pending": early.status == "pending"}


def test_on_area_entered_is_safe():
    game = _clean_arena(_make_game())
    e, n = _promote(game, "fire")
    pos = (e.x, e.y)
    game.nemesis.on_area_entered(area_id=None)
    game.nemesis.on_area_entered(area_id="starting_area")
    return {"no_crash_and_no_teleport_in_same_region": (e.x, e.y) == pos}


def test_gateway_menu_shows_nemesis_warning():
    from world.extension import ExtensionSpec
    game = _clean_arena(_make_game())
    game.world.add_extension(ExtensionSpec(extension_id="t1", extension_name="Test Land",
                                           area_level=2, biome="test"))
    e, n = _promote(game, "fire")
    game.current_area_id = "t1"          # pretend we're elsewhere
    game._open_gateway_menu()
    labels = [x["label"] for x in game.gateway_menu.entries]
    game.gateway_menu.hide()
    game.current_area_id = "starting_area"
    return {"starting_region_flagged": any("Starting Region" in l and "IS NEAR" in l for l in labels),
            "labels": labels}


def test_dead_nemesis_not_in_play():
    game = _clean_arena(_make_game())
    e, n = _promote(game, "fire")
    in_before = len(game.nemesis._in_play()) == 1
    e.alive = False
    return {"counted_while_alive": in_before,
            "ignored_when_dead": len(game.nemesis._in_play()) == 0}


# ---------------------------------------------------------------------------
# 7. Save / load / profile
# ---------------------------------------------------------------------------

def test_save_load_keeps_nemesis_and_trophies():
    game = _clean_arena(_make_game())
    with tempfile.TemporaryDirectory() as d:
        game.save_manager.path = os.path.join(d, "save.json")
        game.save_manager.stash_path = os.path.join(d, "stash.json")
        e, n = _promote(game, "chaos")
        game.player.trophies.append({"name": "Old, the Slain", "damage_type": "fire",
                                     "generation": 1})
        game._save_game()
        game.nemesis.registry.records.clear()
        game.player.trophies = []
        game._load_game()
        recs = game.nemesis.registry.records
        result = {
            "nemesis_restored": len(recs) == 1 and recs[0].name == n.name,
            "alive_becomes_pending": bool(recs) and recs[0].status == "pending",
            "trophies_restored": len(game.player.trophies) == 1,
            "dialogue_cleared": game.dialogue is None,
        }
    game.nemesis.load(None)
    result["legacy_save_without_nemesis_key"] = len(game.nemesis.registry.records) == 0
    return result


def test_profile_and_prompt_include_trophies():
    import json
    from ai.prompt_builder import PromptBuilder
    game = _clean_arena(_make_game())
    game.player.trophies = [{"name": "Grix, the Flame-Eater", "damage_type": "fire",
                             "generation": 1}]
    profile = game._current_profile()
    json.dumps(profile.to_dict())
    prompt = PromptBuilder.build_extension_prompt(profile, set())
    game.profile_screen.show(profile)
    surf = pygame.Surface((1280, 720))
    game.profile_screen.draw(surf)
    game.profile_screen.hide()
    return {"profile_has_trophy": profile.trophies == ["Grix, the Flame-Eater"],
            "prompt_mentions_trophy": "Grix" in prompt}


# ---------------------------------------------------------------------------
# 8. Smoke / soak
# ---------------------------------------------------------------------------

def test_draw_with_everything_active():
    game = _clean_arena(_make_game())
    p = game.player
    e, n = _promote(game, "fire", dx=100)
    game.nemesis.novas.append({"x": e.x, "y": e.y, "t": 0.5, "dtype": "fire",
                               "dmg": 5, "src": e})
    t = _dummy_enemy(p.x + 150, p.y)
    game.current_area.enemies.append(t)
    game.reactions.on_hit(t, 10, _fake_player("fire"), None)
    game._say("A very long speaker name", "x" * 140, 5.0)
    game._draw()
    return {"draw_ok": True}


def test_soak_update_with_nemesis_and_reactions():
    game = _clean_arena(_make_game())
    p = game.player
    p.equipped["weapon"] = _hybrid_sword()
    p.recalc_stats()
    e, n = _promote(game, "cold", dx=200)
    t = _dummy_enemy(p.x + 40, p.y)
    game.current_area.enemies.append(t)
    game._attack_button_held = True
    for _ in range(240):
        game._update(1 / 60.0)
        if not p.alive:
            p.alive = True
            p.heal_to_full()
    game._draw()
    return {"survived_240_frames": True}


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    tests = [
        # 0 known bug
        test_arc_attack_hits_each_target_once,
        # 1 reactions
        test_thermal_shock_on_alternating_hybrid,
        test_reaction_internal_cooldown_and_recovery,
        test_reaction_window_expires,
        test_same_element_never_reacts,
        test_every_reaction_has_label_color_and_handler,
        test_superconduct_shreds_then_restores,
        test_corruption_ticks_and_spreads_once,
        test_hemorrhage_stacks_double,
        test_dot_kill_goes_through_normal_kill_path,
        test_no_aura_left_on_killing_blow,
        test_auras_are_cleaned_up,
        test_reset_clears_all_state,
        test_pick_element_includes_world_conversion,
        test_hybrid_weapon_triggers_reaction_end_to_end,
        # 2 dialogue
        test_dialogue_lines,
        test_boss_greeting_uses_real_history,
        test_dialogue_expires,
        test_boss_hit_starts_attempt_once_and_greets,
        test_death_during_boss_fight_taunts_and_ends_attempt,
        test_boss_kill_handler_is_idempotent,
        test_nemesis_boss_does_not_lock_gates,
        # 3 nemesis data
        test_nemesis_helpers,
        test_nemesis_roundtrip_ignores_unknown_keys,
        # 4 promotion
        test_promotion_on_death,
        test_ignored_attackers_do_not_promote,
        test_repeat_death_escalates_without_compounding,
        test_third_death_becomes_world_boss,
        test_world_boss_templates_validate_at_high_level,
        test_nemesis_boss_hit_is_not_a_guardian_attempt,
        # 5 rewards
        test_killing_nemesis_rewards_and_schedules_return,
        test_dot_killing_a_nemesis_awards_trophy,
        test_world_boss_kill_semantics,
        test_trophy_bonus_is_capped,
        test_trophies_persist_and_bad_payload_is_safe,
        test_loot_override_beats_region_rules,
        # 6 runtime
        test_melee_kill_promotes_via_event_bus,
        test_projectile_hit_reports_attacker,
        test_warning_is_throttled,
        test_nova_is_telegraphed_then_hurts,
        test_nova_far_away_misses,
        test_nova_kill_counts_as_nemesis_kill,
        test_respawn_when_due_and_not_before,
        test_on_area_entered_is_safe,
        test_gateway_menu_shows_nemesis_warning,
        test_dead_nemesis_not_in_play,
        # 7 persistence / profile
        test_save_load_keeps_nemesis_and_trophies,
        test_profile_and_prompt_include_trophies,
        # 8 smoke
        test_draw_with_everything_active,
        test_soak_update_with_nemesis_and_reactions,
    ]

    failures = 0
    for fn in tests:
        r = _run(fn.__name__, fn)
        if r.get("errors"):
            failures += 1
    print(f"=== SUMMARY: {len(tests) - failures}/{len(tests)} tests clean ===")


if __name__ == "__main__":
    main()