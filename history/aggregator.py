"""
Aggregator: reduces an EventLog (+ live game state) into a compact
PlayerProfile. This is the ONLY thing that should ever be handed to a
future AI world designer -- never the raw event log.

Recomputed on demand (e.g. before generating an extension, or when the
player opens the Profile debug screen), not maintained incrementally,
to keep this logic simple, inspectable, and easy to change without
worrying about incremental-update bugs.
"""
from collections import Counter
from history.player_profile import PlayerProfile


DAMAGE_TYPE_KEYWORDS = {
    "physical_damage": "physical",
    "fire_damage": "fire",
    "cold_damage": "cold",
    "lightning_damage": "lightning",
    "chaos_damage": "chaos",
}


def _infer_main_damage_types(event_log):
    """Looks at skill_used events' damage_type field to find the dominant type(s)."""
    counter = Counter()
    for e in event_log.events_of_type("skill_used"):
        dtype = e.payload.get("damage_type")
        if dtype:
            counter[dtype] += 1
    if not counter:
        return "physical", None
    ranked = counter.most_common()
    main = ranked[0][0]
    secondary = ranked[1][0] if len(ranked) > 1 and ranked[1][1] > 0 else None
    return main, secondary


def _infer_death_causes(event_log):
    deaths = Counter()
    for e in event_log.events_of_type("player_death"):
        cause = e.payload.get("cause", "unknown")
        deaths[cause] += 1
    return dict(deaths)


def _infer_boss_stats(event_log):
    attempts = len(event_log.events_of_type("boss_attempt_started"))
    kills = len(event_log.events_of_type("boss_killed"))
    return attempts, kills


def _infer_item_stats(event_log):
    found_events = event_log.events_of_type("item_found")
    rare_count = sum(1 for e in found_events if e.payload.get("rarity") == "rare")
    unique_count = sum(1 for e in found_events if e.payload.get("rarity") == "unique")
    six_mod_count = sum(1 for e in found_events if e.payload.get("is_six_mod"))

    equipped_ids = {e.payload.get("item_id") for e in event_log.events_of_type("item_equipped")}
    discarded_events = event_log.events_of_type("item_sold") + event_log.events_of_type("item_destroyed") \
        if hasattr(event_log.events_of_type("item_sold"), "__add__") else \
        event_log.events_of_type("item_sold") + event_log.events_of_type("item_destroyed")
    discarded_count = len(event_log.events_of_type("item_sold")) + len(event_log.events_of_type("item_destroyed"))
    found_count = len(found_events)
    kept_count = max(0, found_count - discarded_count)

    return rare_count, unique_count, six_mod_count, kept_count, discarded_count


def _infer_most_used_weapon(event_log):
    counter = Counter()
    for e in event_log.events_of_type("skill_used"):
        weapon = e.payload.get("weapon_name")
        if weapon:
            counter[weapon] += 1
    if not counter:
        return None
    return counter.most_common(1)[0][0]


def _infer_defensive_strength(player):
    """
    Heuristic using effective armor + resistances relative to level.
    Placeholder heuristic -- fine for Phase 4; can be refined once more
    defensive mechanics (blocks, dodge, etc.) exist.
    """
    stats = player.effective_stats
    level = player.progression.level
    expected_armor = level * 3
    resist_sum = sum(v for k, v in stats.resistances.items() if k != "armor_flat")

    armor_ratio = stats.armor / max(1, expected_armor)
    if armor_ratio > 1.3 or resist_sum > 0.6:
        return "high"
    elif armor_ratio > 0.7 or resist_sum > 0.25:
        return "medium"
    return "low"


def build_player_profile(player, event_log, world, exploration_tracker=None) -> PlayerProfile:
    profile = PlayerProfile()
    profile.level = player.progression.level

    main, secondary = _infer_main_damage_types(event_log)
    profile.main_damage = DAMAGE_TYPE_KEYWORDS.get(main, main) if main else "physical"
    profile.secondary_damage = DAMAGE_TYPE_KEYWORDS.get(secondary, secondary) if secondary else None

    weapon = player.equipped.get("weapon")
    profile.most_used_weapon = weapon.display_name if weapon else _infer_most_used_weapon(event_log)
    profile.favorite_skill = "basic_attack"  # only skill that exists through Phase 4

    profile.defensive_strength = _infer_defensive_strength(player)

    deaths = _infer_death_causes(event_log)
    profile.deaths = deaths
    profile.total_deaths = sum(deaths.values())

    if exploration_tracker is not None:
        profile.exploration = exploration_tracker.current_fraction()
        profile.average_exploration = exploration_tracker.average_fraction()

    attempts, kills = _infer_boss_stats(event_log)
    profile.boss_attempts = attempts
    profile.boss_successes = kills

    rare_count, unique_count, six_mod_count, kept, discarded = _infer_item_stats(event_log)
    profile.rare_items_found = rare_count
    profile.unique_items_found = unique_count
    profile.six_mod_items_found = six_mod_count
    profile.items_kept = kept
    profile.items_discarded = discarded

    profile.playtime_seconds = world.total_playtime_seconds
    profile.extensions_generated = len(world.extensions)

    profile.melee_ranged_preference = "melee"  # only melee attack exists through Phase 4
    profile.average_combat_distance = 0.0      # requires player_attack distance data (see below)

    attack_events = event_log.events_of_type("player_attack")
    if attack_events:
        distances = [e.payload.get("distance", 0.0) for e in attack_events]
        profile.average_combat_distance = sum(distances) / len(distances)

    return profile