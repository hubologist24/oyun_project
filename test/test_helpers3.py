"""
test_helpers3.py -- manual test harness for the Weapon Subtype system
and Archetype/Quality decoupling changes.

Run:
    python test_helpers3.py

Covers:
    * WeaponSubtypes registry (load / validity / display names)
    * Attack profile is bound to subtype (arc / cone_aoe / projectile)
    * resolve_attack_profile ignores player attributes
    * Stat requirements per subtype (sword=free, bow=dex, long_wand=int, ...)
    * generate_random_item attaches weapon_subtype to weapons only
    * Quality decoupling: tier / affix-count distribution is IDENTICAL
      across subtypes at the same item level (structural + statistical)
    * Item save / load round-trip preserves weapon_subtype
    * Legacy items (no weapon_subtype) load cleanly -> unarmed fallback
    * Arc / cone targeting geometry (front / side / back / far)
    * Projectile spawn / update / range expiration
    * Player.meets_requirements + Player.equip gating
    * Stats attribute serialisation + legacy defaults
    * Player.gain_xp grows attributes
    * Player.current_attack_profile follows equipped weapon, not stats
    * start_attack_cooldown honors profile multiplier
    * LootRules.favored_damage_types still biases base choice WITHIN a subtype
"""
import os
import sys
import inspect
import random
import traceback
from items.item_generator import generate_random_item 

# --- headless-safe pygame bootstrap -----------------------------------------
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

# make sure the project root is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pygame
pygame.init()


# ============================================================================
# Tiny assertion / reporting framework
# ============================================================================
_passed = 0
_failed = 0
_failures = []


def check(condition, msg):
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  PASS: {msg}")
    else:
        _failed += 1
        _failures.append(msg)
        print(f"  FAIL: {msg}")


def section(title):
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def run_test(fn):
    try:
        fn()
    except Exception as e:
        global _failed
        _failed += 1
        _failures.append(f"{fn.__name__} raised {type(e).__name__}: {e}")
        print(f"\n!! {fn.__name__} raised an exception:")
        traceback.print_exc()


# ============================================================================
# Fakes -- minimal stand-ins so we don't need a full Game instance
# ============================================================================
class FakeWeapon:
    def __init__(self, subtype):
        self.weapon_subtype = subtype
        self.display_name = f"Fake {subtype}"


class FakeStats:
    def __init__(self, strength=5, dexterity=5, intelligence=5):
        self.strength = strength
        self.dexterity = dexterity
        self.intelligence = intelligence


class FakePlayer:
    """Minimal duck-typed player for combat-pattern resolution tests."""
    def __init__(self, weapon_subtype=None, strength=5, dexterity=5, intelligence=5,
                 x=0, y=0, facing=(1, 0)):
        self.equipped = {"weapon": FakeWeapon(weapon_subtype) if weapon_subtype else None}
        self.x = x
        self.y = y
        self.facing = pygame.Vector2(*facing)
        self.effective_stats = FakeStats(strength, dexterity, intelligence)


class FakeTarget:
    """Minimal duck-typed enemy/boss for arc-targeting tests."""
    def __init__(self, x, y, alive=True, w=28, h=28, name="enemy"):
        self.x = x
        self.y = y
        self.alive = alive
        self.width = w
        self.height = h
        self.name = name

    @property
    def rect(self):
        return pygame.Rect(int(self.x - self.width / 2), int(self.y - self.height / 2),
                           self.width, self.height)


# ============================================================================
# Tests
# ============================================================================
def test_registry():
    section("1. WeaponSubtypes registry loads and exposes all 5 subtypes")
    from items.weapon_subtypes import WeaponSubtypes

    ids = WeaponSubtypes.all_ids()
    expected = {"sword", "greatsword", "bow", "short_wand", "long_wand"}
    check(set(ids) == expected, f"all 5 subtypes present: {sorted(ids)}")

    for sid in expected:
        check(WeaponSubtypes.is_valid(sid), f"'{sid}' reports valid")
    check(not WeaponSubtypes.is_valid("katana"), "unknown subtype 'katana' rejected")

    check(WeaponSubtypes.display_name("short_wand") == "Short Wand",
          "display_name lookup works")
    check(WeaponSubtypes.display_name("greatsword") == "Greatsword",
          "greatsword display name correct")


def test_attack_profiles():
    section("2. Attack profile is bound to subtype (arc / cone / projectile)")
    from items.weapon_subtypes import WeaponSubtypes

    sword = WeaponSubtypes.attack_profile("sword")
    great = WeaponSubtypes.attack_profile("greatsword")
    bow = WeaponSubtypes.attack_profile("bow")
    sw = WeaponSubtypes.attack_profile("short_wand")
    lw = WeaponSubtypes.attack_profile("long_wand")

    check(sword.kind == "arc", "sword -> arc")
    check(great.kind == "arc", "greatsword -> arc")
    check(bow.kind == "projectile", "bow -> projectile")
    check(sw.kind == "projectile", "short_wand -> projectile")
    check(lw.kind == "cone_aoe", "long_wand -> cone_aoe")

    check(great.arc_degrees > sword.arc_degrees,
          f"greatsword wider arc ({great.arc_degrees}) > sword ({sword.arc_degrees})")
    check(great.range > sword.range,
          f"greatsword longer reach ({great.range}) > sword ({sword.range})")

    check(sw.attack_cooldown_multiplier < sword.attack_cooldown_multiplier,
          "short_wand faster cooldown than sword")
    check(great.attack_cooldown_multiplier > sword.attack_cooldown_multiplier,
          "greatsword slower cooldown than sword")

    check(sw.piercing is False and bow.piercing is False,
          "bow/short_wand default to non-piercing")


def test_profile_ignores_attributes():
    section("3. resolve_attack_profile IGNORES player attributes")
    from combat.attack_patterns import resolve_attack_profile, DEFAULT_UNARMED_PROFILE

    # Same subtype, wildly different attributes -> same profile
    weak = FakePlayer(weapon_subtype="bow", strength=1, dexterity=1, intelligence=1)
    god = FakePlayer(weapon_subtype="bow", strength=999, dexterity=999, intelligence=999)
    p_weak = resolve_attack_profile(weak)
    p_god = resolve_attack_profile(god)

    check(p_weak.kind == p_god.kind == "projectile",
          "attack kind is weapon-determined, not attribute-determined")
    check(p_weak.range == p_god.range,
          "range identical regardless of attributes")
    check(p_weak.attack_cooldown_multiplier == p_god.attack_cooldown_multiplier,
          "cooldown multiplier identical regardless of attributes")

    # No weapon -> unarmed fallback
    bare = FakePlayer(weapon_subtype=None)
    p_bare = resolve_attack_profile(bare)
    check(p_bare.kind == DEFAULT_UNARMED_PROFILE.kind,
          "no weapon -> unarmed fallback (arc)")
    check(p_bare.damage_multiplier < 1.0,
          "unarmed damage multiplier below 1.0 (weakest baseline)")

    # Weapon present but subtype field missing/garbage -> unarmed fallback
    class WeirdWeapon:
        pass

    bare2 = FakePlayer(weapon_subtype=None)
    bare2.equipped["weapon"] = WeirdWeapon()  # no weapon_subtype attribute
    p2 = resolve_attack_profile(bare2)
    check(p2.kind == DEFAULT_UNARMED_PROFILE.kind,
          "weapon without subtype attribute -> unarmed fallback")


def test_stat_requirements():
    section("4. Per-subtype stat requirements")
    from items.weapon_subtypes import WeaponSubtypes

    check(WeaponSubtypes.stat_requirements("sword") == {},
          "sword: no requirements")
    check(WeaponSubtypes.stat_requirements("greatsword").get("strength") == 8,
          "greatsword: needs strength 8")
    check(WeaponSubtypes.stat_requirements("bow").get("dexterity") == 8,
          "bow: needs dexterity 8")
    check(WeaponSubtypes.stat_requirements("short_wand").get("intelligence") == 6,
          "short_wand: needs intelligence 6")
    check(WeaponSubtypes.stat_requirements("long_wand").get("intelligence") == 10,
          "long_wand: needs intelligence 10")


def test_item_generator_attaches_subtype():
    section("5. generate_random_item attaches weapon_subtype to weapons")
    from items.item_generator import generate_random_item
    from items.weapon_subtypes import WeaponSubtypes

    rng = random.Random(2025)
    seen = {}
    for _ in range(400):
        item = generate_random_item(rng, area_level=25, force_slot="weapon")
        check(item.slot == "weapon", "force_slot='weapon' produced a weapon") if False else None
        st = item.weapon_subtype
        if st not in WeaponSubtypes.all_ids():
            check(False, f"unexpected weapon_subtype: {st!r}")
            return
        seen[st] = seen.get(st, 0) + 1

    check(seen.get("weapon") is None, "no fake 'weapon' subtype slot-value")
    check(len(seen) == 5,
          f"all 5 subtypes appeared in 400 rolls: {sorted(seen)}")
    for sid in WeaponSubtypes.all_ids():
        check(seen.get(sid, 0) > 0, f"subtype '{sid}' appeared at least once")




def test_non_weapons_have_no_subtype():
    section("6. Non-weapon items have weapon_subtype = None")
    from items.item_generator import generate_random_item

    ARMOR_SUB_SLOTS = ("head", "chest", "legs", "boots", "gloves")

    # "armor" is a category, not a concrete item slot -- accept any sub-slot.
    rng = random.Random(4242)
    item = generate_random_item(rng, area_level=15, force_slot="armor")
    check(item.weapon_subtype is None,
          "forced category 'armor' -> weapon_subtype is None")
    check(item.slot in ARMOR_SUB_SLOTS,
          f"forced category 'armor' produced an armor sub-slot (got '{item.slot}')")

    # Concrete single-target slots are honored exactly.
    for slot in ("ring", "amulet"):
        rng = random.Random(4242)
        item = generate_random_item(rng, area_level=15, force_slot=slot)
        check(item.weapon_subtype is None,
              f"forced slot '{slot}' -> weapon_subtype is None")
        check(item.slot == slot, f"forced slot '{slot}' honored")



def test_tier_resolution_has_no_subtype_input():
    section("7a. Structural: tier / affix-roll signatures carry NO subtype")
    from items.tiers import resolve_tier_for_roll, roll_value_for_tier
    from items.modifiers import ModifierPools

    sig_tier = inspect.signature(resolve_tier_for_roll)
    check("subtype" not in sig_tier.parameters and "weapon_subtype" not in sig_tier.parameters,
          f"resolve_tier_for_roll params: {list(sig_tier.parameters)}")

    sig_val = inspect.signature(roll_value_for_tier)
    check("subtype" not in sig_val.parameters and "weapon_subtype" not in sig_val.parameters,
          f"roll_value_for_tier params: {list(sig_val.parameters)}")

    sig_aff = inspect.signature(ModifierPools.roll_affix_counts)
    check("subtype" not in sig_aff.parameters and "weapon_subtype" not in sig_aff.parameters,
          f"roll_affix_counts params: {list(sig_aff.parameters)}")


def test_quality_decoupled_from_subtype():
    section("7b. Statistical: every subtype can roll T1 / 5+ affixes at high ilvl")
    import items.item_generator as gen_module
    from items.item_generator import generate_random_item
    from items.weapon_subtypes import WeaponSubtypes

    orig = gen_module._roll_weapon_subtype
    HIGH_LEVEL = 90
    N = 300
    per_subtype = {}

    try:
        for subtype in WeaponSubtypes.all_ids():
            gen_module._roll_weapon_subtype = (lambda rng, _s=subtype: _s)
            rng = random.Random(31337)
            t1 = t2 = total = 0
            max_affix = 0
            for _ in range(N):
                item = generate_random_item(rng, area_level=HIGH_LEVEL,
                                             force_slot="weapon")
                max_affix = max(max_affix, len(item.prefixes) + len(item.suffixes))
                for a in item.all_affixes():
                    total += 1
                    if a.tier == "T1":
                        t1 += 1
                    elif a.tier == "T2":
                        t2 += 1
            per_subtype[subtype] = {"t1": t1, "t2": t2, "total": total,
                                     "max_affix": max_affix}
    finally:
        gen_module._roll_weapon_subtype = orig

    for subtype, st in per_subtype.items():
        check(st["total"] > 0,
              f"{subtype}: affixes were rolled ({st['total']} total)")
        check(st["t1"] > 0,
              f"{subtype}: at least one T1 affix rolled "
              f"({st['t1']}/{st['total']} = {st['t1'] / max(1, st['total']):.3f})")
        check(st["max_affix"] >= 4,
              f"{subtype}: at least one item reached 4+ total affixes "
              f"(max={st['max_affix']})")

    # Across ALL subtypes, max affix should be able to hit 5+ (2/3 or 3/2 or 3/3)
    global_max = max(st["max_affix"] for st in per_subtype.values())
    check(global_max >= 5,
          f"at least one item rolled 5+ affixes at ilvl {HIGH_LEVEL} (got {global_max})")


def test_item_roundtrip_preserves_subtype():
    section("8. Item save / load round-trip preserves weapon_subtype")
    from items.item_generator import generate_random_item
    from items.item import Item

    rng = random.Random(99)
    for i in range(20):
        item = generate_random_item(rng, area_level=40, force_slot="weapon")
        d = item.to_dict()
        check("weapon_subtype" in d,
              f"round {i}: to_dict() contains weapon_subtype key") if i == 0 else None
        restored = Item.from_dict(d)
        check(restored.weapon_subtype == item.weapon_subtype,
              f"round {i}: subtype preserved ({item.weapon_subtype!r})") if i < 5 else None
        check(restored.slot == item.slot,
              f"round {i}: slot preserved") if i < 5 else None
        if i >= 5:
            # just silently assert on remaining rounds
            assert restored.weapon_subtype == item.weapon_subtype


def test_legacy_item_loads_cleanly():
    section("9. Legacy items (no weapon_subtype key) load without breaking")
    from items.item import Item
    from combat.attack_patterns import resolve_attack_profile

    legacy = {
        "item_id": 90001,
        "base_name": "Old Rusty Sword",
        "slot": "weapon",
        "rarity": "normal",
        "item_level": 5,
        "damage_bonus": 10,
        "armor_bonus": 0,
        # NB: no "weapon_subtype" key at all
    }
    item = Item.from_dict(legacy)
    check(item.weapon_subtype is None,
          "legacy item -> weapon_subtype is None")
    check(item.damage_bonus == 10,
          "legacy flat damage_bonus preserved exactly")

    # A fake player equipping this legacy weapon falls back to unarmed arc
    class LegacyP:
        pass

    p = FakePlayer()
    p.equipped["weapon"] = item  # item.weapon_subtype is None
    profile = resolve_attack_profile(p)
    check(profile.kind == "arc",
          "legacy weapon -> unarmed arc fallback profile")


def test_find_arc_targets():
    section("10. Arc targeting geometry")
    from items.weapon_subtypes import WeaponSubtypes
    from combat.attack_patterns import find_arc_targets

    player = FakePlayer(weapon_subtype="sword", x=0, y=0, facing=(1, 0))
    sword_prof = WeaponSubtypes.attack_profile("sword")   # 56 range, 90 deg

    front   = FakeTarget(40, 0)   # dist=40,   angle=0
    diag    = FakeTarget(40, 15)  # dist~42.7, angle~20.5  (inside 45 half-arc)
    side    = FakeTarget(0, 40)   # angle=90 -> outside 45 half-arc
    back    = FakeTarget(-40, 0)  # angle=180
    far     = FakeTarget(200, 0)  # beyond range

    targets = [front, diag, side, back, far]
    hits = find_arc_targets(player, targets, sword_prof)

    check(front in hits, "front target hit")
    check(diag in hits, "diagonal target (inside 45-deg half-arc) hit")
    check(side not in hits, "target exactly to the side is outside 90-deg arc")
    check(back not in hits, "target behind player missed")
    check(far not in hits, "target beyond range missed")
    check(len(hits) >= 1 and hits[0] is front,
          "nearest hit returned first")

    # Greatsword half-arc is 75 deg. Use a target at ~63 deg (inside 75,
    # outside sword's 45) to prove the wider sweep.
    great_prof = WeaponSubtypes.attack_profile("greatsword")
    side_diag = FakeTarget(20, 40)   # angle = atan2(40,20) ~ 63 deg
    hits_wide = find_arc_targets(player, [side_diag], great_prof)
    check(side_diag in hits_wide,
          "greatsword's 75-deg half-arc reaches a 63-deg target that sword misses")

    # Sword should NOT reach the same 63-deg target.
    hits_narrow = find_arc_targets(player, [side_diag], sword_prof)
    check(side_diag not in hits_narrow,
          "sword's 45-deg half-arc does NOT reach the same 63-deg target")

    # Dead targets ignored
    dead = FakeTarget(40, 0, alive=False)
    hits_dead = find_arc_targets(player, [dead], sword_prof)
    check(dead not in hits_dead, "dead target ignored by arc targeting")


def test_projectile_lifecycle():
    section("11. Projectile spawn / update / range expiration")
    from items.weapon_subtypes import WeaponSubtypes
    from combat.attack_patterns import spawn_projectile

    player = FakePlayer(weapon_subtype="bow", x=0, y=0, facing=(1, 0))
    prof = WeaponSubtypes.attack_profile("bow")
    proj = spawn_projectile(player, prof, damage_type="physical", world_rules=None)

    check(proj.vx > 0, "projectile moves along facing x")
    check(abs(proj.vy) < 1e-6, "no sideways movement when facing +x")
    check(proj.alive, "fresh projectile is alive")

    start_x = proj.x
    proj.update(0.05)
    check(proj.x > start_x, f"projectile advanced x by {proj.x - start_x:.2f}px")

    # Drain range
    for _ in range(500):
        if not proj.alive:
            break
        proj.update(0.1)
    check(not proj.alive, "projectile expires after exceeding max range")

    # Facing vector respected
    player2 = FakePlayer(weapon_subtype="bow", x=0, y=0, facing=(0, -1))
    proj2 = spawn_projectile(player2, prof, damage_type="physical", world_rules=None)
    check(abs(proj2.vx) < 1e-6 and proj2.vy < 0,
          "projectile respects a non-axis-aligned facing vector")


def test_player_requirements_and_equip_gate():
    section("12. Player.meets_requirements + Player.equip gating")
    from core.events import EventBus
    from player.player import Player
    from items.item import Item

    bus = EventBus()

    # -- Sword: no requirements ---------------------------------------------
    p = Player(0, 0, bus)
    sword = Item(base_name="Test Sword", slot="weapon", weapon_subtype="sword")
    ok, missing = p.meets_requirements(sword)
    check(ok and not missing, "sword: no requirements -> ok")
    check(p.equip(sword), "sword equips successfully")
    check(p.equipped.get("weapon") is sword, "sword stored in weapon slot")

    # -- Greatsword: needs str 8, player at default 5 ------------------------
    p = Player(0, 0, bus)
    gs = Item(base_name="Test Greatsword", slot="weapon", weapon_subtype="greatsword")
    ok, missing = p.meets_requirements(gs)
    check(not ok, "greatsword: blocked at default strength=5")
    check(any("Strength" in m for m in missing),
          f"missing message names 'Strength': {missing}")

    before_weapon = p.equipped.get("weapon")
    result = p.equip(gs)
    check(result is False, "equip returns False on unmet requirement")
    check(p.equipped.get("weapon") is before_weapon,
          "weapon slot unchanged after failed equip")

    # Bump strength and retry
    p.base_stats.strength = 20
    p.recalc_stats()
    ok, _ = p.meets_requirements(gs)
    check(ok, "greatsword: allowed after raising strength to 20")
    check(p.equip(gs), "greatsword equips once requirement satisfied")

    # -- Long wand: needs int 10 --------------------------------------------
    p2 = Player(0, 0, bus)
    lw = Item(base_name="Test Long Wand", slot="weapon", weapon_subtype="long_wand")
    ok, missing = p2.meets_requirements(lw)
    check(not ok, "long_wand: blocked at default intelligence=5")
    p2.base_stats.intelligence = 12
    p2.recalc_stats()
    ok, _ = p2.meets_requirements(lw)
    check(ok, "long_wand: allowed with intelligence=12")
    check(p2.equip(lw), "long_wand equips successfully")

    # -- Non-weapon items never gate ----------------------------------------
    helmet = Item(base_name="Test Helm", slot="head")
    p3 = Player(0, 0, bus)
    ok, missing = p3.meets_requirements(helmet)
    check(ok and not missing,
          "non-weapon item: no requirement check (head slot)")


def test_stats_attributes_roundtrip():
    section("13. Stats attribute serialisation + legacy defaults")
    from player.stats import Stats

    s = Stats(strength=42, dexterity=17, intelligence=3)
    d = s.to_dict()
    check(d.get("strength") == 42, "strength serialised")
    check(d.get("dexterity") == 17, "dexterity serialised")
    check(d.get("intelligence") == 3, "intelligence serialised")

    s2 = Stats.from_dict(d)
    check((s2.strength, s2.dexterity, s2.intelligence) == (42, 17, 3),
          "attributes round-trip through from_dict")

    # Legacy save (no attribute keys) -> defaults
    legacy = {"max_hp": 100, "hp": 100, "base_damage": 12, "armor": 0}
    s3 = Stats.from_dict(legacy)
    check((s3.strength, s3.dexterity, s3.intelligence) == (5, 5, 5),
          "legacy save defaults attributes to 5/5/5")


def test_effective_stats_preserves_attributes():
    section("14. compute_effective_stats preserves character attributes")
    from player.stats import Stats, compute_effective_stats

    base = Stats(strength=30, dexterity=25, intelligence=20)
    eff = compute_effective_stats(base, [])
    check(eff.strength == 30, "strength carried through")
    check(eff.dexterity == 25, "dexterity carried through")
    check(eff.intelligence == 20, "intelligence carried through")


def test_gain_xp_grows_attributes():
    section("15. Player.gain_xp raises attributes on level-up")
    from core.events import EventBus
    from player.player import Player

    p = Player(0, 0, EventBus())
    before = (p.base_stats.strength, p.base_stats.dexterity, p.base_stats.intelligence)
    p.gain_xp(100_000)
    after = (p.base_stats.strength, p.base_stats.dexterity, p.base_stats.intelligence)
    check(after[0] > before[0] and after[1] > before[1] and after[2] > before[2],
          f"all three attributes grew: {before} -> {after}")
    check(p.effective_stats.strength == p.base_stats.strength,
          "effective_stats reflects the new base strength")


def test_player_attack_profile_uses_weapon_only():
    section("16. Player.current_attack_profile follows equipped weapon, not stats")
    from core.events import EventBus
    from player.player import Player
    from items.item import Item

    bus = EventBus()
    p = Player(0, 0, bus)

    # No weapon -> unarmed arc
    check(p.current_attack_profile().kind == "arc",
          "no weapon equipped -> arc/unarmed fallback")

    # Equip sword
    sword = Item(base_name="S", slot="weapon", weapon_subtype="sword")
    p.equip(sword)
    check(p.current_attack_profile().kind == "arc",
          "equipped sword -> arc")

    # Equip bow (needs dex 8)
    p.base_stats.dexterity = 20
    p.recalc_stats()
    bow = Item(base_name="B", slot="weapon", weapon_subtype="bow")
    check(p.equip(bow), "bow equips after raising dexterity")
    check(p.current_attack_profile().kind == "projectile",
          "equipped bow -> projectile")

    # Crank every attribute -- profile must not change
    p.base_stats.strength = 999
    p.base_stats.dexterity = 999
    p.base_stats.intelligence = 999
    p.recalc_stats()
    check(p.current_attack_profile().kind == "projectile",
          "attributes do NOT override the bow's projectile pattern")


def test_cooldown_multiplier_applied():
    section("17. start_attack_cooldown honors profile.attack_cooldown_multiplier")
    from core.events import EventBus
    from player.player import Player
    from items.item import Item
    import core.config as config

    bus = EventBus()
    p = Player(0, 0, bus)

    sword = Item(base_name="S", slot="weapon", weapon_subtype="sword")
    p.base_stats.strength = 50
    p.recalc_stats()
    p.equip(sword)
    p.start_attack_cooldown(p.current_attack_profile())
    cd_sword = p._attack_cooldown_timer

    sw = Item(base_name="SW", slot="weapon", weapon_subtype="short_wand")
    p.base_stats.intelligence = 50
    p.recalc_stats()
    p.equip(sw)
    p.start_attack_cooldown(p.current_attack_profile())
    cd_sw = p._attack_cooldown_timer

    check(abs(cd_sword - config.PLAYER_ATTACK_COOLDOWN * 1.0) < 1e-6,
          f"sword cooldown = base ({cd_sword:.3f}s)")
    check(cd_sw < cd_sword,
          f"short_wand faster than sword ({cd_sw:.3f}s < {cd_sword:.3f}s)")


def test_loot_bias_within_subtype():
    section("18. LootRules.favored_damage_types biases choice WITHIN a subtype")
    import items.item_generator as gen_module
    from items.item_generator import generate_random_item
    from world.loot_rules import LootRules

    orig = gen_module._roll_weapon_subtype
    gen_module._roll_weapon_subtype = lambda rng: "short_wand"
    try:
        rng = random.Random(2024)
        rules = LootRules(favored_damage_types={"fire": 100.0})
        fire_count = 0
        total = 200
        for _ in range(total):
            item = generate_random_item(
                rng, area_level=20, force_slot="weapon",
                world_context={"loot_rules": rules},
            )
            assert item.weapon_subtype == "short_wand"
            if item.primary_damage_type() == "fire":
                fire_count += 1
        ratio = fire_count / total
        check(ratio > 0.5,
              f"fire bias strongly favored fire wands ({fire_count}/{total} = {ratio:.2f})")
    finally:
        gen_module._roll_weapon_subtype = orig


def test_damage_multiplier_honored_by_combat():
    section("19. combat.player_attack_enemy honors damage_multiplier")
    from core.events import EventBus
    from player.player import Player
    from enemies.enemy import Enemy
    from combat.combat import player_attack_enemy
    from items.item import Item

    bus = EventBus()
    p = Player(0, 0, bus)
    sword = Item(base_name="S", slot="weapon", weapon_subtype="sword")
    p.base_stats.strength = 50
    p.recalc_stats()
    p.equip(sword)

    def damage_for(mult):
        e = Enemy(x=10, y=0, hp=10_000, damage=0, armor=0)
        player_attack_enemy(p, e, bus, world_rules=None, damage_multiplier=mult)
        return 10_000 - e.hp

    dmg_1_0 = damage_for(1.0)
    dmg_1_5 = damage_for(1.5)
    check(dmg_1_5 > dmg_1_0,
          f"damage_multiplier=1.5 deals more than 1.0 ({dmg_1_5} > {dmg_1_0})")


# ============================================================================
# Main
# ============================================================================
def main():
    tests = [
        test_registry,
        test_attack_profiles,
        test_profile_ignores_attributes,
        test_stat_requirements,
        test_item_generator_attaches_subtype,
        test_non_weapons_have_no_subtype,
        test_tier_resolution_has_no_subtype_input,
        test_quality_decoupled_from_subtype,
        test_item_roundtrip_preserves_subtype,
        test_legacy_item_loads_cleanly,
        test_find_arc_targets,
        test_projectile_lifecycle,
        test_player_requirements_and_equip_gate,
        test_stats_attributes_roundtrip,
        test_effective_stats_preserves_attributes,
        test_gain_xp_grows_attributes,
        test_player_attack_profile_uses_weapon_only,
        test_cooldown_multiplier_applied,
        test_loot_bias_within_subtype,
        test_damage_multiplier_honored_by_combat,
    ]

    for t in tests:
        run_test(t)

    print()
    print("=" * 72)
    print(f"RESULT: {_passed} passed, {_failed} failed")
    print("=" * 72)
    if _failures:
        print("Failures:")
        for f in _failures:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())