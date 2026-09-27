"""
WorldExtensionGenerator - Phase 6 update.

Same pipeline as Phase 5:
    PLAYER PROFILE -> backend.generate_next_extension() -> WorldValidator
    -> ProgressionValidator -> (valid: use it) / (invalid: fallback)

The backend is now commonly one of:
    ProceduralWorldGenerator()   (default, always available)
    MockAIWorldGenerator()       (offline LLM simulation, for testing)
    LLMWorldGenerator()          (real API-backed; returns None if unconfigured)

Swapping backends never requires touching this class or core/game.py
beyond the constructor call -- this is the whole point of the
WorldGenerator abstract interface.
"""
import random
from typing import Set
from world.extension import ExtensionSpec
from ai.validators import WorldValidator, ProgressionValidator
from ai.procedural_generator import ProceduralWorldGenerator
from history.player_profile import PlayerProfile


class WorldExtensionGenerator:
    def __init__(self, backend=None, fallback_backend=None):
        self.backend = backend or ProceduralWorldGenerator()
        # The fallback backend is what protects the pipeline when `backend`
        # is an AI (Mock or real LLM) and produces nothing usable. By default,
        # falls back to the deterministic procedural backend before finally
        # falling back to the hardcoded safe spec.
        self.fallback_backend = fallback_backend or ProceduralWorldGenerator()

    def generate(self, player_profile: PlayerProfile, existing_extension_ids: Set[str],
                 rng: random.Random) -> ExtensionSpec:
        spec = self._try_backend(self.backend, player_profile, existing_extension_ids, rng)

        if spec is None and self.backend is not self.fallback_backend:
            print(f"[WorldExtensionGenerator] Primary backend "
                  f"({type(self.backend).__name__}) failed. Trying fallback backend "
                  f"({type(self.fallback_backend).__name__}).")
            spec = self._try_backend(self.fallback_backend, player_profile, existing_extension_ids, rng)

        if spec is not None:
            return spec

        print("[WorldExtensionGenerator] All backends failed validation. "
              "Using guaranteed-safe hardcoded fallback spec.")
        return self._fallback_spec(player_profile, existing_extension_ids)

    def _try_backend(self, backend, player_profile, existing_extension_ids, rng):
        spec = None
        try:
            spec = backend.generate_next_extension(player_profile, existing_extension_ids, rng)
        except Exception as e:
            print(f"[WorldExtensionGenerator] Backend {type(backend).__name__} raised an exception: {e}")
            return None

        if spec is None:
            return None

        valid, errors = WorldValidator.validate(spec, existing_extension_ids, player_profile.level)
        if not valid:
            print(f"[WorldExtensionGenerator] {type(backend).__name__} output rejected by "
                  f"WorldValidator: {errors}")
            return None

        prog_valid, prog_errors = ProgressionValidator.validate(spec, player_profile.level)
        if not prog_valid:
            print(f"[WorldExtensionGenerator] {type(backend).__name__} output rejected by "
                  f"ProgressionValidator: {prog_errors}")
            return None

        print(f"[WorldExtensionGenerator] Accepted spec '{spec.extension_id}' from "
              f"{type(backend).__name__}.")
        return spec

    def _fallback_spec(self, player_profile: PlayerProfile, existing_extension_ids: Set[str]) -> ExtensionSpec:
        index = len(existing_extension_ids) + 1
        fallback_id = f"fallback_{index}"
        while fallback_id in existing_extension_ids:
            index += 1
            fallback_id = f"fallback_{index}"

        return ExtensionSpec(
            extension_id=fallback_id,
            extension_name=f"Uncharted Region {index}",
            area_level=max(1, player_profile.level + 1),
            biome="uncharted",
            is_anomaly=False,
            world_modifiers=[],
            boss_template_id=None,
            room_count=6,
        )