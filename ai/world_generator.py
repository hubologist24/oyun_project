"""
Abstract WorldGenerator interface.

Per the architecture requirement, three interchangeable backends must
exist eventually:

    ProceduralWorldGenerator   (Phase 5 - implemented here)
    MockAIWorldGenerator       (Phase 6 - not yet implemented)
    LLMWorldGenerator          (Phase 6 - not yet implemented)

All three MUST implement this exact interface so core/game.py and
world/extension.py never need to know which backend produced a spec.
No backend is ever allowed to touch game state directly -- they only
return an ExtensionSpec (or raise/return None on failure), which then
passes through ai/validators.py before anything is instantiated.
"""
from abc import ABC, abstractmethod
from typing import Optional, Set
from world.extension import ExtensionSpec
from history.player_profile import PlayerProfile


class WorldGenerator(ABC):
    @abstractmethod
    def generate_next_extension(self, player_profile: PlayerProfile,
                                 existing_extension_ids: Set[str],
                                 rng) -> Optional[ExtensionSpec]:
        """
        Produce the next ExtensionSpec given the player's profile and
        the set of extension_ids that already exist (never regenerate
        an existing id). Return None if generation fails outright
        (caller must fall back).
        """
        raise NotImplementedError