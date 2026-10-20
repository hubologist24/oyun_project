"""
test_helpers16.py - tests for the shrine system, safe starting room,
probabilistic boss/nemesis loot, and combat juice.

Run:  python -m test.test_helpers16
"""

import os
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import random
import tempfile
import traceback

import pygame


# ---------------------------------------------------------------------------
# harness (matches helpers11-15)
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


def _find_shrine(game, shrine_type):
    for s in game.current_area.shrines:
        if s.def_.id == shrine_type:
            return s
    return None


def _fill_inventory(game, n, seed=0):
    """Add n generated items to the player inventory. Returns the list."""
    from items.item_generator import generate_random_item
    rng = random.Random(seed)
    added = []
    for _ in range(n):
        item = generate_random_item(rng, area_level=5)
        if game.player.inventory.add_item(item):
            added.append(item)
    return added


# ===========================================================================
# 1. Safe starting room regenerates the player
# ===========================================================================

def test_safe_room_regenerates_player():
    game = _clean_arena(_make_game())
    p = game.player
    safe = game.starting_area.safe_room_rect
    p.x, p.y = safe.centerx, safe.centery
    p.effective_stats.hp = 10
    hp0 = p.effective_stats.hp
    for _ in range(180):                     # ~3 seconds at 60fps
        game._update(1 / 60.0)
    return {
        "hp_increased": p.effective_stats.hp > hp0,
        "hp_within_max": p.effective_stats.hp <= p.effective_stats.max_hp,
        "hp_before": hp0,
        "hp_after": p.effective_stats.hp,
    }


# ===========================================================================
# 2. Safe room does NOT heal when player is outside it
# ===========================================================================

def test_safe_room_does_not_heal_outside():
    game = _clean_arena(_make_game())
    p = game.player
    safe = game.starting_area.safe_room_rect
    # Move far outside the safe rect (arbitrary off-map coordinate still OK,
    # regen only checks collidepoint against the starting rect).
    p.x = safe.right + 500
    p.y = safe.bottom + 500
    p.effective_stats.hp = 10
    hp0 = p.effective_stats.hp
    for _ in range(180):
        game._update(1 / 60.0)
    return {
        "hp_unchanged": p.effective_stats.hp == hp0,
        "hp_before": hp0,
        "hp_after": p.effective_stats.hp,
        "in_safe_room": safe.collidepoint(p.x, p.y),
    }


# ===========================================================================
# 3. Safe room healing caps at max_hp
# ===========================================================================

def test_safe_room_heal_caps_at_max():
    game = _clean_arena(_make_game())
    p = game.player
    safe = game.starting_area.safe_room_rect
    p.x, p.y = safe.centerx, safe.centery
    p.effective_stats.hp = p.effective_stats.max_hp - 1
    for _ in range(600):
        game._update(1 / 60.0)
    return {
        "hp_capped": p.effective_stats.hp == p.effective_stats.max_hp,
        "hp_now": p.effective_stats.hp,
        "max_hp": p.effective_stats.max_hp,
    }


# ===========================================================================
# 4. Rest shrine fully heals the player
# ===========================================================================

def test_rest_shrine_heals_to_full():
    game = _clean_arena(_make_game())
    p = game.player
    shrine = _find_shrine(game, "rest_shrine")
    if shrine is None:
        return {"errors": ["rest_shrine not spawned in starting area"]}
    p.x, p.y = shrine.x, shrine.y
    p.effective_stats.hp = 5
    msg = shrine.interact(game)
    return {
        "hp_full": p.effective_stats.hp == p.effective_stats.max_hp,
        "message_mentions_heal": isinstance(msg, str) and "healed" in msg.lower(),
        "shrine_not_consumed": not shrine.used,
    }


# ===========================================================================
# 5. Rest shrine refuses when already at full HP
# ===========================================================================

def test_rest_shrine_refuses_at_full_health():
    game = _clean_arena(_make_game())
    p = game.player
    shrine = _find_shrine(game, "rest_shrine")
    if shrine is None:
        return {"errors": ["rest_shrine not spawned"]}
    p.x, p.y = shrine.x, shrine.y
    p.effective_stats.hp = p.effective_stats.max_hp
    msg = shrine.interact(game)
    return {
        "message_mentions_full": isinstance(msg, str) and "full" in msg.lower(),
        "hp_still_max": p.effective_stats.hp == p.effective_stats.max_hp,
    }


# ===========================================================================
# 6. Stash chest opens the stash UI
# ===========================================================================

def test_stash_chest_opens_stash_ui():
    game = _clean_arena(_make_game())
    p = game.player
    shrine = _find_shrine(game, "stash_chest")
    if shrine is None:
        return {"errors": ["stash_chest not spawned"]}
    p.x, p.y = shrine.x, shrine.y
    shrine.interact(game)
    result = {
        "inventory_ui_visible": game.inventory_ui.visible,
        "stash_visible": game.inventory_ui.stash_visible,
    }
    # Cleanup so following tests aren't affected
    _open_no_ui(game)
    return result


# ===========================================================================
# 7. Sacrifice altar enforces stone cost
# ===========================================================================

def test_sacrifice_altar_requires_stone():
    game = _clean_arena(_make_game())
    p = game.player
    shrine = _find_shrine(game, "sacrifice_altar")
    if shrine is None:
        return {"errors": ["sacrifice_altar not spawned"]}
    p.x, p.y = shrine.x, shrine.y
    p.extension_stones = 0
    _fill_inventory(game, 5)
    msg = shrine.interact(game)
    return {
        "message_mentions_stone": isinstance(msg, str) and "stone" in msg.lower(),
        "shrine_not_used": not shrine.used,
        "inventory_untouched": len(p.inventory.items) == 5,
    }


# ===========================================================================
# 8. Sacrifice altar enforces item cost
# ===========================================================================

def test_sacrifice_altar_requires_items():
    game = _clean_arena(_make_game())
    p = game.player
    shrine = _find_shrine(game, "sacrifice_altar")
    if shrine is None:
        return {"errors": ["sacrifice_altar not spawned"]}
    p.x, p.y = shrine.x, shrine.y
    p.extension_stones = 5
    # Wipe any items added by starting state
    for it in list(p.inventory.items):
        p.inventory.remove_item(it)
    _fill_inventory(game, 2)                 # fewer than the 5 required
    msg = shrine.interact(game)
    return {
        "message_mentions_items": isinstance(msg, str) and "item" in msg.lower(),
        "shrine_not_used": not shrine.used,
        "stone_untouched": p.extension_stones == 5,
    }


# ===========================================================================
# 9. Sacrifice altar forges an item with a damage type NOT in the offerings
# ===========================================================================

def test_sacrifice_altar_forges_excluded_damage_type():
    from combat.damage_types import DamageTypes

    game = _clean_arena(_make_game())
    p = game.player
    shrine = _find_shrine(game, "sacrifice_altar")
    if shrine is None:
        return {"errors": ["sacrifice_altar not spawned"]}
    p.x, p.y = shrine.x, shrine.y
    p.extension_stones = 5
    for it in list(p.inventory.items):
        p.inventory.remove_item(it)

    # Seed the offering with items; compute the sacrificed type set
    offered = _fill_inventory(game, 5, seed=42)
    sacrificed_types = set()
    for it in offered:
        for affix in it.all_affixes():
            if affix.damage_type:
                sacrificed_types.add(affix.damage_type)

    msg = shrine.interact(game)
    ok_shrine_used = shrine.used

    # Find the forged item (either in inventory or in loot drops)
    forged = None
    for it in p.inventory.items:
        if (it.display_name_override or "").startswith("Offering-Forged"):
            forged = it
            break
    if forged is None:
        for d in game.current_area.loot_drops:
            itm = d.get("item")
            if itm and (itm.display_name_override or "").startswith("Offering-Forged"):
                forged = itm
                break

    if forged is None:
        return {"errors": ["forged item not found"], "msg": msg}

    forged_types = {a.damage_type for a in forged.all_affixes() if a.damage_type}
    all_types = set(DamageTypes.all())
    truly_new = all_types - sacrificed_types

    if truly_new:
        # There was room for a new type -> the reward must contain one
        has_new = bool(forged_types & truly_new)
    else:
        # Every damage type was offered; the algorithm falls back to any type.
        has_new = True

    return {
        "shrine_used": ok_shrine_used,
        "stone_consumed": p.extension_stones == 4,
        "offering_consumed": all(it not in p.inventory.items for it in offered),
        "reward_has_new_type": has_new,
        "sacrificed_types": sorted(sacrificed_types),
        "forged_types": sorted(forged_types),
        "reward_name": forged.display_name,
    }


# ===========================================================================
# 10. Sacrifice altar is one-use
# ===========================================================================

def test_sacrifice_altar_is_one_use():
    game = _clean_arena(_make_game())
    p = game.player
    shrine = _find_shrine(game, "sacrifice_altar")
    if shrine is None:
        return {"errors": ["sacrifice_altar not spawned"]}
    p.x, p.y = shrine.x, shrine.y
    p.extension_stones = 10
    _fill_inventory(game, 10)
    first_msg = shrine.interact(game)
    second_msg = shrine.interact(game)
    return {
        "first_use_worked": shrine.used,
        "second_use_refused": isinstance(second_msg, str)
                              and "exhaust" in second_msg.lower(),
        "stone_only_consumed_once": p.extension_stones == 9,
        "first_message_present": bool(first_msg),
    }


# ===========================================================================
# 11. Loot chest is one-use
# ===========================================================================

def test_loot_chest_is_one_use():
    from world.shrine import make_shrine
    game = _clean_arena(_make_game())
    p = game.player
    chest = make_shrine("loot_chest", p.x + 20, p.y + 20, "starting_area")
    game.current_area.shrines.append(chest)
    chest.interact(game)
    second = chest.interact(game)
    return {
        "chest_used": chest.used,
        "second_use_refused": isinstance(second, str)
                              and "empty" in second.lower(),
    }


# ===========================================================================
# 12. World.used_shrines survives save/load round-trip
# ===========================================================================

def test_shrine_persistence_via_world_roundtrip():
    from world.world import World
    w = World(seed=424242)
    key = "starting_area:100:200:sacrifice_altar"
    w.used_shrines.add(key)
    d = w.to_dict()
    w2 = World.from_dict(d)
    return {
        "roundtrip_preserved": key in w2.used_shrines,
        "no_extra_keys": len(w2.used_shrines) == 1,
        "key": key,
    }


# ===========================================================================
# 13. Shrine interaction requires player to be in range
# ===========================================================================

def test_shrine_interaction_requires_proximity():
    game = _clean_arena(_make_game())
    p = game.player
    shrine = _find_shrine(game, "rest_shrine")
    if shrine is None:
        return {"errors": ["rest_shrine not spawned"]}
    p.effective_stats.hp = 5
    # Stand far away from the shrine
    p.x, p.y = shrine.x + 500, shrine.y + 500
    interacted = game._try_interact_shrine()
    return {
        "no_interaction_far": interacted is False,
        "hp_unchanged": p.effective_stats.hp == 5,
    }


# ===========================================================================
# 14. Boss loot is NOT 100% guaranteed for every slot
# ===========================================================================

def test_boss_loot_is_probabilistic():
    game = _clean_arena(_make_game())
    from enemies.boss import Boss, BossTemplate
    p = game.player
    b = Boss(p.x + 100, p.y, BossTemplate.hollow_warden())

    rng = random.Random(1234)
    item_counts = []
    for _ in range(80):
        game.current_area.loot_drops.clear()
        game._roll_boss_loot(b, 10, game._active_world_rules(), rng,
                             world_boss=False)
        items = [d for d in game.current_area.loot_drops
                 if d.get("item") is not None]
        item_counts.append(len(items))

    unique_counts = sorted(set(item_counts))
    return {
        "always_within_bounds": all(0 <= c <= 4 for c in item_counts),
        "not_every_run_gets_four": any(c < 4 for c in item_counts),
        "saw_multiple_outcomes": len(unique_counts) >= 2,
        "distribution": {c: item_counts.count(c) for c in unique_counts},
    }


# ===========================================================================
# 15. Boss loot ALWAYS drops a health orb
# ===========================================================================

def test_boss_loot_always_drops_health_orb():
    game = _clean_arena(_make_game())
    from enemies.boss import Boss, BossTemplate
    p = game.player
    b = Boss(p.x + 100, p.y, BossTemplate.hollow_warden())

    rng = random.Random(9)
    missing = 0
    for _ in range(40):
        game.current_area.loot_drops.clear()
        game._roll_boss_loot(b, 10, game._active_world_rules(), rng,
                             world_boss=False)
        heals = [d for d in game.current_area.loot_drops
                 if d.get("kind") == "heal"]
        if len(heals) != 1:
            missing += 1

    return {
        "always_exactly_one_heal": missing == 0,
        "missing_runs": missing,
    }


# ===========================================================================
# 16. Enemy hit-flash + kill juice fire correctly
# ===========================================================================

def test_enemy_hit_flash_and_kill_juice():
    game = _clean_arena(_make_game())
    p = game.player

    # --- hit flash ---
    e = _dummy_enemy(p.x + 30, p.y, hp=100)
    game.current_area.enemies.append(e)
    assert e.hit_flash_timer == 0.0
    e.take_damage(1)
    flash_after_hit = e.hit_flash_timer

    # flash should decay after update
    e.update(0.2, p, game.current_area.tilemap, game.event_bus)
    flash_after_update = e.hit_flash_timer

    # --- kill juice ---
    bursts_before = len(game.crit_bursts)
    e.take_damage(10_000)                     # lethal
    game._on_enemy_killed(e)
    bursts_after = len(game.crit_bursts)

    return {
        "flash_set_on_hit": flash_after_hit > 0.0,
        "flash_decays": flash_after_update < flash_after_hit,
        "kill_juice_added_burst": bursts_after > bursts_before,
        "enemy_removed": e not in game.current_area.enemies,
    }


# ===========================================================================
# main
# ===========================================================================

def main():
    tests = [
        test_safe_room_regenerates_player,
        test_safe_room_does_not_heal_outside,
        test_safe_room_heal_caps_at_max,
        test_rest_shrine_heals_to_full,
        test_rest_shrine_refuses_at_full_health,
        test_stash_chest_opens_stash_ui,
        test_sacrifice_altar_requires_stone,
        test_sacrifice_altar_requires_items,
        test_sacrifice_altar_forges_excluded_damage_type,
        test_sacrifice_altar_is_one_use,
        test_loot_chest_is_one_use,
        test_shrine_persistence_via_world_roundtrip,
        test_shrine_interaction_requires_proximity,
        test_boss_loot_is_probabilistic,
        test_boss_loot_always_drops_health_orb,
        test_enemy_hit_flash_and_kill_juice,
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