"""
Personalization engine: translates a PlayerProfile into concrete
generation biases (biome weighting, world-rule selection, paired
challenge+solution design, boss mechanic hints, loot bias).

This sits BETWEEN PlayerProfile and the WorldGenerator backends
(Procedural/Mock/LLM). Procedural and Mock generators consume its
output directly; LLMWorldGenerator's prompt already includes the raw
profile (Phase 6), and PromptBuilder is extended here to also include
the derived personalization hints, so an LLM gets the same reasoning
a human designer would.

Explicitly NOT allowed to touch game state -- pure functions producing
data structures (PersonalizationHints) that generators/validators read.
"""
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from history.player_profile import PlayerProfile


@dataclass
class PersonalizationHints:
    dominant_damage_type: str
    weak_defense: bool
    frequent_death_causes: List[str] = field(default_factory=list)
    under_explored: bool = False
    over_explored: bool = False
    boss_struggling: bool = False          # many attempts, few/no successes
    boss_dominant: bool = False            # high success ratio
    has_notable_legacy_items: bool = False
    discarded_a_lot: bool = False
    suggested_challenge_damage_type: Optional[str] = None
    suggested_enabling_damage_type: Optional[str] = None

    def to_dict(self):
        return {
            "dominant_damage_type": self.dominant_damage_type,
            "weak_defense": self.weak_defense,
            "frequent_death_causes": self.frequent_death_causes,
            "under_explored": self.under_explored,
            "over_explored": self.over_explored,
            "boss_struggling": self.boss_struggling,
            "boss_dominant": self.boss_dominant,
            "has_notable_legacy_items": self.has_notable_legacy_items,
            "discarded_a_lot": self.discarded_a_lot,
            "suggested_challenge_damage_type": self.suggested_challenge_damage_type,
            "suggested_enabling_damage_type": self.suggested_enabling_damage_type,
        }


def derive_hints(profile: PlayerProfile) -> PersonalizationHints:
    """
    Pure heuristic derivation. Deliberately simple/inspectable (per the
    project's "AI should determine direction, deterministic engine
    handles the rest" principle from your own design notes) -- this is
    NOT machine learning, just legible rules over the aggregated profile.
    """
    hints = PersonalizationHints(dominant_damage_type=profile.main_damage, weak_defense=False)

    hints.weak_defense = profile.defensive_strength == "low"

    if profile.deaths:
        total = sum(profile.deaths.values()) or 1
        frequent = [dtype for dtype, count in profile.deaths.items() if count / total >= 0.3]
        hints.frequent_death_causes = frequent

    hints.under_explored = profile.average_exploration < 0.25 and profile.extensions_generated >= 1
    hints.over_explored = profile.average_exploration > 0.75

    if profile.boss_attempts >= 3:
        success_ratio = profile.boss_successes / profile.boss_attempts
        hints.boss_struggling = success_ratio < 0.2
        hints.boss_dominant = success_ratio > 0.7

    hints.has_notable_legacy_items = profile.six_mod_items_found > 0
    hints.discarded_a_lot = profile.items_discarded > max(3, profile.items_kept * 0.5)

    # Challenge/solution pairing (spec Example 1: fire user -> fire-resistant
    # enemies BUT a new fire-penetration mechanic/hybrid item).
    hints.suggested_challenge_damage_type = profile.main_damage
    hints.suggested_enabling_damage_type = profile.secondary_damage or _fallback_enabler(profile.main_damage)

    return hints


def _fallback_enabler(main_damage: str) -> str:
    """If no secondary damage type is established yet, suggest a natural pairing."""
    pairing = {
        "physical": "lightning",
        "fire": "chaos",
        "cold": "physical",
        "lightning": "fire",
        "chaos": "physical",
        "poison": "chaos",
        "bleed": "physical",
    }
    return pairing.get(main_damage, "physical")