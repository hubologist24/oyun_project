"""
ProceduralWorldGenerator - Phase 7 update.

Now consumes ai.personalization.PersonalizationHints (derived from the
PlayerProfile) instead of just profile.main_damage directly. This adds:

- Challenge + solution pairing (spec Example 1/3): a modifier that
  makes the player's dominant damage type struggle is now often paired
  with a second modifier that provides a new tool (conversion/buff)
  using their secondary or a complementary damage type.
- Loot rule generation (world/loot_rules.py) biased toward the
  player's build and toward "enabling" damage types when appropriate.
- Boss personalization hints attached to the ExtensionSpec (consumed
  by enemies/boss_factory.py, see below) so extension bosses are no
  longer always an identical Hollow-Warden clone.
- Discarded-item callback hook: if the player discarded a lot of
  items, loot rules bias toward the specific stat categories they
  discarded (approximated here via defensive/resistance affixes,
  since Phase 4 doesn't track WHICH stats were on discarded items --
  see "Known Simplifications" below).
"""
import random as random_module
from typing import Optional, Set
from world.extension import ExtensionSpec
from world.extension_templates import get_modifier_template
from world.loot_rules import LootRules
from history.player_profile import PlayerProfile
from ai.world_generator import WorldGenerator
from ai.personalization import derive_hints, PersonalizationHints
import copy


BIOME_POOL = [
    "ash_canyon", "sunken_library", "corrupted_hollow", "frozen_reach",
    "storm_peaks", "bone_wastes", "verdant_ruin",
]

DAMAGE_AFFINITY_MODIFIERS = {
    "chaos": ["chaos_ascendancy", "corrupted_flesh"],
    "physical": ["brittle_bones", "ashen_frailty"],
    "lightning": ["storm_conductive"],
    "fire": ["ashen_frailty"],
}

DEFAULT_MODIFIER_POOL = ["chaos_ascendancy", "storm_conductive", "ashen_frailty",
                         "corrupted_flesh", "brittle_bones"]

ANOMALY_CHANCE = 0.08
ANOMALY_MIN_LEVEL_JUMP = 10
ANOMALY_MAX_LEVEL_JUMP = 30

BOSS_MECHANIC_POOL = [
    "dash_predator", "ranged_zoner", "summoner", "reflector", "berserker",
]


class ProceduralWorldGenerator(WorldGenerator):

    def generate_next_extension(self, player_profile: PlayerProfile,
                                 existing_extension_ids: Set[str],
                                 rng: random_module.Random) -> Optional[ExtensionSpec]:
        hints = derive_hints(player_profile)
        index = len(existing_extension_ids) + 1

        if rng.random() < ANOMALY_CHANCE and index > 1:
            spec = self._build_anomaly(player_profile, hints, index, rng)
        else:
            spec = self._build_normal(player_profile, hints, index, rng)

        spec.loot_rules = self._build_loot_rules(player_profile, hints, rng).to_dict()
        spec.boss_personalization = self._build_boss_hints(player_profile, hints, rng)
        return spec

    # ---------------- normal extension ----------------
    def _build_normal(self, profile: PlayerProfile, hints: PersonalizationHints,
                       index: int, rng) -> ExtensionSpec:
        area_level = max(1, profile.level + rng.randint(0, 3))
        biome = self._choose_biome(hints, rng)

        modifiers = self._select_modifiers_with_pairing(hints, rng)
        name = self._name_for_biome(biome, index)

        return ExtensionSpec(
            extension_id=f"extension_{index}",
            extension_name=name,
            area_level=area_level,
            biome=biome,
            is_anomaly=False,
            world_modifiers=modifiers,
            boss_template_id="personalized",
            room_count=rng.randint(6, 9),
        )

    # ---------------- anomaly extension ----------------
    def _build_anomaly(self, profile: PlayerProfile, hints: PersonalizationHints,
                        index: int, rng) -> ExtensionSpec:
        jump = rng.randint(ANOMALY_MIN_LEVEL_JUMP, ANOMALY_MAX_LEVEL_JUMP)
        area_level = profile.level + jump
        biome = self._choose_biome(hints, rng)
        modifiers = self._select_modifiers_with_pairing(hints, rng, force_count=1)

        return ExtensionSpec(
            extension_id=f"anomaly_{index}",
            extension_name=f"Anomalous {biome.replace('_', ' ').title()}",
            area_level=area_level,
            biome=biome,
            is_anomaly=True,
            world_modifiers=modifiers,
            boss_template_id="personalized",
            room_count=rng.randint(4, 6),
        )

    # ---------------- biome selection ----------------
    def _choose_biome(self, hints: PersonalizationHints, rng) -> str:
        """
        Per spec's 'Area-Specific Itemization' idea: bias biome choice
        so exploration-averse players occasionally get denser/smaller
        regions conceptually (room_count already handles size; biome
        flavor here just avoids always picking the same biome twice
        in a row via simple weighting, and slightly favors biomes
        thematically tied to the player's damage type).
        """
        thematic = {
            "fire": ["ash_canyon", "bone_wastes"],
            "cold": ["frozen_reach"],
            "lightning": ["storm_peaks"],
            "chaos": ["corrupted_hollow", "sunken_library"],
            "physical": ["bone_wastes", "verdant_ruin"],
        }.get(hints.dominant_damage_type, [])

        pool = list(BIOME_POOL)
        weights = [3 if b in thematic else 1 for b in pool]
        return rng.choices(pool, weights=weights, k=1)[0]

    # ---------------- challenge + solution pairing ----------------
    def _select_modifiers_with_pairing(self, hints: PersonalizationHints, rng,
                                        force_count: int = None) -> list:
        """
        Implements spec Example 1/3: the region can create a problem
        AND a potential solution rather than simply countering the
        player. If the player is defensively weak or struggling
        against a boss, bias toward pairing a 'challenge' modifier
        (targets their dominant damage type) with an 'enabling'
        modifier (grants/buffs their secondary or a complementary type).
        """
        affinity_pool = DAMAGE_AFFINITY_MODIFIERS.get(hints.dominant_damage_type, [])
        combined_pool = list(dict.fromkeys(affinity_pool + DEFAULT_MODIFIER_POOL))

        count = force_count if force_count is not None else (1 if rng.random() < 0.5 else 2)
        count = min(count, len(combined_pool))

        chosen_ids = []
        pool_copy = list(combined_pool)
        for _ in range(count):
            if not pool_copy:
                break
            weights = [3 if mid in affinity_pool else 1 for mid in pool_copy]
            pick = rng.choices(pool_copy, weights=weights, k=1)[0]
            chosen_ids.append(pick)
            pool_copy.remove(pick)

        #modifiers = [dict(get_modifier_template(mid)) for mid in chosen_ids]
        modifiers = [copy.deepcopy(get_modifier_template(mid)) for mid in chosen_ids]

        should_pair_solution = (hints.weak_defense or hints.boss_struggling) and rng.random() < 0.6
        if should_pair_solution and hints.suggested_enabling_damage_type:
            modifiers.append(self._build_enabling_modifier(hints, rng))

        return modifiers

    def _build_enabling_modifier(self, hints: PersonalizationHints, rng) -> dict:
        """
        Generates a small conversion-based 'solution' modifier granting
        the player extra effectiveness with their secondary/complementary
        damage type -- a new tool rather than a direct nerf reversal,
        per spec: 'The extension creates a problem AND a potential solution.'
        """
        enabling_type = hints.suggested_enabling_damage_type
        return {
            "name": f"Echoes of {enabling_type.title()}",
            "description": f"This region resonates with {enabling_type} energy, "
                           f"strengthening those who wield it.",
            "rules": [
                {"type": "damage_dealt_multiplier", "target": "all",
                 "damage_type": enabling_type, "multiplier": round(rng.uniform(1.2, 1.5), 2)}
            ],
        }

    # ---------------- loot rules ----------------
    def _build_loot_rules(self, profile: PlayerProfile, hints: PersonalizationHints, rng) -> LootRules:
        rules = LootRules.default()

        rules.favored_damage_types[hints.dominant_damage_type] = 1.3
        if hints.suggested_enabling_damage_type:
            rules.favored_damage_types[hints.suggested_enabling_damage_type] = \
                rules.favored_damage_types.get(hints.suggested_enabling_damage_type, 1.0) + 0.4

        if hints.boss_struggling:
            rules.rare_chance_multiplier = 1.25
        if hints.has_notable_legacy_items:
            rules.six_mod_chance_multiplier = 1.1  # small nudge, stays rare per spec

        if hints.discarded_a_lot:
            # Per your "4.txt" idea #5 (Forgotten Items Become Relevant):
            # bias toward defensive/utility affixes, since that's the
            # most common category of "boring" items players discard.
            rules.guarantee_enabling_affix = "armor"

        return rules

    # ---------------- boss personalization hints ----------------
    def _build_boss_hints(self, profile: PlayerProfile, hints: PersonalizationHints, rng) -> dict:
        """
        Returns a dict consumed by enemies/boss_factory.py to select a
        boss "mechanic archetype" and tune it. Kept as plain data (not
        a BossTemplate directly) so this stays consistent with what an
        LLM would eventually produce and pass through BossValidator.
        """
        if hints.frequent_death_causes:
            mechanic = "reflector" if hints.dominant_damage_type in hints.frequent_death_causes else "dash_predator"
        elif hints.boss_dominant:
            mechanic = rng.choice(["summoner", "berserker"])
        else:
            mechanic = rng.choice(BOSS_MECHANIC_POOL)

        return {
            "mechanic_archetype": mechanic,
            "punishes_damage_type": hints.dominant_damage_type if hints.frequent_death_causes else None,
            "rewards_on_defeat_damage_type": hints.suggested_enabling_damage_type,
            "difficulty_bias": "high" if hints.boss_dominant else ("low" if hints.boss_struggling else "normal"),
        }

    def _name_for_biome(self, biome: str, index: int) -> str:
        names = {
            "ash_canyon": "Ash Canyon",
            "sunken_library": "The Drowned Archive",
            "corrupted_hollow": "The Corrupted Hollow",
            "frozen_reach": "The Frozen Reach",
            "storm_peaks": "The Storm Peaks",
            "bone_wastes": "The Bone Wastes",
            "verdant_ruin": "The Verdant Ruin",
        }
        base = names.get(biome, biome.replace("_", " ").title())
        return base if index <= len(names) else f"{base} ({index})"