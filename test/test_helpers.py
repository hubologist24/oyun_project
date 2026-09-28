################################################################################
# FILE: test_helpers.py  (NEW)
################################################################################

"""
Headless developer/test API - per Game Design & Architecture spec
section 5 ("Automated Python Test Suite").

Everything here is pure/headless: NO pygame.display, NO event loop,
NO manual play required. Where the real spec concept doesn't exist yet
in the engine (Gates, World Evolve Stones -- see project gap analysis),
the function still runs against the closest REAL implemented mechanism
and clearly documents the stub/approximation in its docstring rather
than faking numbers.

Usage (from a REPL, a pytest file, or a throwaway script):

    import test_helpers as th
    result = th.simulate_combat(th.default_player_build(), boss_id="hollow_warden", iterations=200)
    print(result)
"""
import copy
import random
import statistics
from typing import Optional, Dict, Any, List

import core.config as config
from core.rng import RNGService
from core.events import EventBus
from player.player import Player
from world.world import World
from world.extension import ExtensionSpec
from world.extension_generator import WorldExtensionGenerator
from ai.procedural_generator import ProceduralWorldGenerator
from ai.validators import WorldValidator, ProgressionValidator
from history.player_profile import PlayerProfile
from items.item_generator import generate_random_item
from items.comparison import compare_items
from items.stash import Stash
from save.save_manager import SaveManager
from enemies.boss import Boss, BossTemplate
from enemies.boss_factory import build_personalized_boss_template


# =============================================================================
# 1. load_game_state
# =============================================================================

def load_game_state(save_file_path: Optional[str] = None,
                     stash_file_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Loads a savegame (and its companion stash file) via the real
    SaveManager/World/Player/.from_dict() code paths -- no pygame
    display required -- and returns a flat summary dict useful for
    assertions in tests.

    Returns:
        {
            "loaded": bool,
            "world_seed": int | None,
            "extensions_count": int,
            "player_level": int | None,
            "player_hp": int | None,
            "inventory_item_count": int | None,
            "stash_item_count": int | None,
            "equipped_slots_filled": list[str],
            "errors": list[str],
        }
    """
    errors = []
    sm = SaveManager(path=save_file_path or config.SAVE_FILE,
                      stash_path=stash_file_path or "stash.json")

    data = sm.load()
    if data is None:
        return {"loaded": False, "world_seed": None, "extensions_count": 0,
                "player_level": None, "player_hp": None,
                "inventory_item_count": None, "stash_item_count": None,
                "equipped_slots_filled": [], "errors": ["no save file found"]}

    try:
        world = World.from_dict(data["world"])
    except Exception as e:
        errors.append(f"World.from_dict failed: {e}")
        world = None

    try:
        fake_bus = EventBus()
        player = Player.from_dict(data["player"], fake_bus)
        player.stash = sm.load_stash()
    except Exception as e:
        errors.append(f"Player.from_dict failed: {e}")
        player = None

    return {
        "loaded": len(errors) == 0,
        "world_seed": world.world_seed if world else None,
        "extensions_count": len(world.extensions) if world else 0,
        "player_level": player.progression.level if player else None,
        "player_hp": player.effective_stats.hp if player else None,
        "inventory_item_count": len(player.inventory.items) if player else None,
        "stash_item_count": len(player.stash.items) if player else None,
        "equipped_slots_filled": (
            [slot for slot, item in player.equipped.items() if item is not None]
            if player else []
        ),
        "errors": errors,
    }


# =============================================================================
# 2. simulate_combat
# =============================================================================

BOSS_BUILDERS = {
    "hollow_warden": lambda area_level, rng: BossTemplate.hollow_warden(),
    "dash_predator": lambda area_level, rng: build_personalized_boss_template(
        "Test Dash Predator", {"mechanic_archetype": "dash_predator", "difficulty_bias": "normal"},
        area_level, rng),
    "ranged_zoner": lambda area_level, rng: build_personalized_boss_template(
        "Test Ranged Zoner", {"mechanic_archetype": "ranged_zoner", "difficulty_bias": "normal"},
        area_level, rng),
    "reflector": lambda area_level, rng: build_personalized_boss_template(
        "Test Reflector", {"mechanic_archetype": "reflector", "difficulty_bias": "normal",
                            "punishes_damage_type": "physical"}, area_level, rng),
    "summoner": lambda area_level, rng: build_personalized_boss_template(
        "Test Summoner", {"mechanic_archetype": "summoner", "difficulty_bias": "normal"},
        area_level, rng),
    "berserker": lambda area_level, rng: build_personalized_boss_template(
        "Test Berserker", {"mechanic_archetype": "berserker", "difficulty_bias": "normal"},
        area_level, rng),
}


def default_player_build(level: int = 10, weapon_damage_bonus: int = 20,
                          armor_bonus: int = 10, primary_damage_type: str = "physical") -> dict:
    """
    A plain-data 'build' description consumable by simulate_combat().
    Not an Item/Player object -- just enough info to construct a
    throwaway Player with those effective stats, so callers can sweep
    many builds without touching real save files or item generation.
    """
    return {
        "level": level,
        "weapon_damage_bonus": weapon_damage_bonus,
        "armor_bonus": armor_bonus,
        "primary_damage_type": primary_damage_type,
    }


def _build_test_player(build: dict, event_bus: EventBus) -> Player:
    player = Player(0, 0, event_bus)
    player.progression.level = build.get("level", 1)
    player.base_stats.base_damage += build.get("weapon_damage_bonus", 0)
    player.base_stats.armor += build.get("armor_bonus", 0)
    player.base_stats.primary_damage_type = build.get("primary_damage_type", "physical")
    player.recalc_stats()
    player.effective_stats.hp = player.effective_stats.max_hp
    return player


def simulate_combat(player_build: dict, boss_id: str = "hollow_warden",
                     iterations: int = 1000, dt: float = 1.0 / 30.0,
                     max_seconds: float = 120.0, area_level: int = None,
                     seed: int = 12345) -> Dict[str, Any]:
    """
    Headless combat simulation using the REAL damage pipeline
    (combat.damage.resolve_damage) and REAL Boss.update() state
    machine -- just without pygame's display/event loop. Player attacks
    on cooldown exactly like core/game.py's _try_attack(); no player
    movement AI is simulated (player is assumed to always be in range
    and attacking on cooldown -- i.e. this measures raw DPS-vs-boss-kit
    matchups, not positioning skill).

    Returns:
        {
            "iterations": int,
            "wins": int,
            "losses": int,           # player ran out of hp OR timed out
            "win_rate": float,
            "avg_ttk_seconds": float | None,   # only over WINS
            "median_ttk_seconds": float | None,
            "avg_player_hp_remaining_pct": float | None,  # only over WINS
            "timed_out": int,
        }
    """
    if boss_id not in BOSS_BUILDERS:
        raise ValueError(f"Unknown boss_id '{boss_id}'. Known: {sorted(BOSS_BUILDERS)}")

    rng = random.Random(seed)
    lvl = area_level if area_level is not None else player_build.get("level", 10)

    wins = 0
    losses = 0
    timed_out = 0
    ttk_list: List[float] = []
    hp_remaining_pct_list: List[float] = []

    for i in range(iterations):
        event_bus = EventBus()
        player = _build_test_player(player_build, event_bus)
        template = BOSS_BUILDERS[boss_id](lvl, rng)
        boss = Boss(300, 0, template)  # fixed distance, in "melee-ish" range for simplicity
        player.x, player.y = 0, 0

        elapsed = 0.0
        attack_cd = 0.0
        result = "timeout"

        while elapsed < max_seconds:
            elapsed += dt
            attack_cd -= dt

            # Player swings whenever off cooldown (assumes always in range,
            # matching this function's documented scope/limitation above).
            if attack_cd <= 0 and player.alive and boss.alive:
                from combat.damage import resolve_damage
                dmg = resolve_damage(
                    base_amount=player.effective_stats.base_damage,
                    damage_type=player.effective_stats.primary_damage_type,
                    source_stats=player.effective_stats,
                    target_resistances={**boss.resistances},
                    target_category="boss",
                )
                boss.take_damage(dmg)
                attack_cd = config.PLAYER_ATTACK_COOLDOWN

            if boss.alive and player.alive:
                boss.update(dt, player, event_bus, world_rules=None)

            if not boss.alive:
                result = "win"
                break
            if not player.alive:
                result = "loss"
                break

        if result == "win":
            wins += 1
            ttk_list.append(elapsed)
            hp_remaining_pct_list.append(player.effective_stats.hp / player.effective_stats.max_hp)
        elif result == "loss":
            losses += 1
        else:
            timed_out += 1
            losses += 1  # timeout counts as a loss for win_rate purposes

    return {
        "iterations": iterations,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / iterations, 4) if iterations else 0.0,
        "avg_ttk_seconds": round(statistics.mean(ttk_list), 2) if ttk_list else None,
        "median_ttk_seconds": round(statistics.median(ttk_list), 2) if ttk_list else None,
        "avg_player_hp_remaining_pct": (
            round(statistics.mean(hp_remaining_pct_list) * 100, 1) if hp_remaining_pct_list else None
        ),
        "timed_out": timed_out,
    }


# =============================================================================
# 3. trigger_world_evolution
# =============================================================================

def trigger_world_evolution(world_id: Optional[int] = None, player_level: int = 10,
                             world: Optional[World] = None) -> Dict[str, Any]:
    """
    Runs the real extension-generation pipeline (ProceduralWorldGenerator
    -> WorldValidator -> ProgressionValidator, same as core/game.py's
    _generate_next_extension) against a headless World.

    NOTE (spec gap): the design spec's Gate system ("existing gates
    open, unlocking access to new zones... every newly generated area
    spawns with >= 1 inactive gate") is NOT implemented in the engine
    yet -- there is no Gate/gate_state concept on Extension. This
    function currently verifies the closest real equivalent: that a
    new validated Extension is actually added to World.extensions with
    a unique id. Once Gate objects exist, this should be extended to
    also assert gate_state transitions old_gates: closed->open and
    new_area.gates includes >= 1 closed gate.

    Returns:
        {
            "world_seed": int,
            "extensions_before": int,
            "extensions_after": int,
            "new_extension_id": str | None,
            "new_extension_level": int | None,
            "is_anomaly": bool | None,
            "gates_note": str,   # explicit stub disclosure, see above
        }
    """
    if world is None:
        seed = world_id if world_id is not None else random.randint(0, 2**31 - 1)
        world = World(seed)

    before = len(world.extensions)
    existing_ids = {ext.extension_id for ext in world.extensions}
    rng = world.rng_service.get_stream("extension_selection")

    profile = PlayerProfile()
    profile.level = player_level

    generator = WorldExtensionGenerator(backend=ProceduralWorldGenerator(),
                                         fallback_backend=ProceduralWorldGenerator())
    spec = generator.generate(profile, existing_ids, rng)

    extension = world.add_extension(spec)

    return {
        "world_seed": world.world_seed,
        "extensions_before": before,
        "extensions_after": len(world.extensions),
        "new_extension_id": extension.extension_id,
        "new_extension_level": spec.area_level,
        "is_anomaly": spec.is_anomaly,
        "gates_note": ("Gate system not yet implemented in engine -- "
                       "this only verifies extension generation/validation, "
                       "not gate open/spawn state. See docstring."),
    }


# =============================================================================
# 4. test_loot_drop_rates
# =============================================================================

def test_loot_drop_rates(total_kills: int = 1000, area_level: int = 10,
                          boss_id: Optional[str] = None, seed: int = 999) -> Dict[str, Any]:
    """
    Runs generate_random_item() `total_kills` times (one call per
    simulated kill/drop) and tallies real observed rates -- no
    hand-typed/fake percentages, this exercises the actual data-driven
    tables in data/rarity_probabilities.json, data/affix_count_probabilities.json,
    and item_generator.py's SLOT_CATEGORY_WEIGHTS.

    NOTE (spec gap): "World Evolve Stones" (per spec section 2, dropped
    rarely by bosses as a world-unlock key item) do not exist as an
    item concept in the engine yet -- there is no such Item/currency
    type anywhere in items/. world_evolve_stone_drops is therefore
    always 0 with drop_rate 0.0 until that item type is implemented;
    it is included in the return shape now so callers don't need to
    change their assertions later.

    boss_id is accepted for API-shape compatibility with the spec
    signature but currently has NO effect on drop tables (loot rules
    are per-extension/per-profile, not per-boss, in the current engine
    -- see ai/personalization.py's LootRules derivation).

    Returns:
        {
            "total_kills": int,
            "rarity_counts": {"normal": int, "magic": int, "rare": int},
            "rarity_rates": {"normal": float, "magic": float, "rare": float},
            "six_mod_drops": int,
            "six_mod_rate": float,
            "slot_category_counts": {"weapon": int, "armor": int, "ring": int, "amulet": int},
            "world_evolve_stone_drops": 0,      # stub, see NOTE above
            "world_evolve_stone_rate": 0.0,     # stub, see NOTE above
        }
    """
    rng = random.Random(seed)
    rarity_counts = {"normal": 0, "magic": 0, "rare": 0, "unique": 0}
    slot_counts = {"weapon": 0, "armor": 0, "ring": 0, "amulet": 0}
    six_mod_drops = 0

    for _ in range(total_kills):
        item = generate_random_item(rng, area_level=area_level)
        rarity_counts[item.rarity] = rarity_counts.get(item.rarity, 0) + 1
        slot_counts[item.slot] = slot_counts.get(item.slot, 0) + 1
        if item.is_six_mod():
            six_mod_drops += 1

    return {
        "total_kills": total_kills,
        "rarity_counts": rarity_counts,
        "rarity_rates": {k: round(v / total_kills, 4) for k, v in rarity_counts.items()},
        "six_mod_drops": six_mod_drops,
        "six_mod_rate": round(six_mod_drops / total_kills, 5),
        "slot_category_counts": slot_counts,
        "world_evolve_stone_drops": 0,
        "world_evolve_stone_rate": 0.0,
    }


# =============================================================================
# 5. test_inventory_compare
# =============================================================================

def test_inventory_compare(area_level: int = 10, seed: int = 42,
                            item_a: Optional[Any] = None,
                            item_b: Optional[Any] = None) -> Dict[str, Any]:
    """
    Validates items.comparison.compare_items() math. Per spec:
    "Validate math accuracy for hover tooltip delta values."

    If item_a/item_b are not supplied, generates two same-slot items
    deterministically (fixed seed) so this is repeatable in CI without
    needing real item_ids from a live save.

    Returns:
        {
            "item_a_id": int, "item_a_name": str,
            "item_b_id": int, "item_b_name": str,
            "diffs": list[dict],      # from compare_items(): [{"stat","label","delta","is_positive"}, ...]
            "diff_lines": list[str],  # formatted, e.g. "+40 Cold Damage"
        }
    """
    rng = random.Random(seed)
    if item_a is None or item_b is None:
        item_a = generate_random_item(rng, area_level=area_level, force_slot="armor",
                                       force_affix_counts=(2, 2))
        item_b = generate_random_item(rng, area_level=area_level, force_slot="armor",
                                       force_affix_counts=(2, 2))

    diffs = compare_items(item_a, item_b)
    from items.comparison import format_diff_line
    diff_lines = [format_diff_line(d) for d in diffs]

    return {
        "item_a_id": item_a.item_id, "item_a_name": item_a.display_name,
        "item_b_id": item_b.item_id, "item_b_name": item_b.display_name,
        "diffs": diffs,
        "diff_lines": diff_lines,
    }


# =============================================================================
# Convenience: run everything as a smoke test if executed directly
# =============================================================================

if __name__ == "__main__":
    print("=== load_game_state (no save file expected) ===")
    print(load_game_state())

    print("\n=== simulate_combat (hollow_warden, 20 iterations) ===")
    print(simulate_combat(default_player_build(level=15, weapon_damage_bonus=40, armor_bonus=20),
                          boss_id="hollow_warden", iterations=20))

    print("\n=== trigger_world_evolution ===")
    print(trigger_world_evolution(world_id=1, player_level=12))

    print("\n=== test_loot_drop_rates (500 kills) ===")
    print(test_loot_drop_rates(total_kills=500))

    print("\n=== test_inventory_compare ===")
    print(test_inventory_compare())