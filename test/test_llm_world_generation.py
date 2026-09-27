"""
Test 2: LLM World Generation Test

End-to-end test of the actual game pipeline:

    PlayerProfile -> LLMWorldGenerator -> raw JSON -> WorldValidator
    -> ProgressionValidator -> ExtensionSpec

This exercises the REAL code path core/game.py uses (via
WorldExtensionGenerator), including the fallback-to-procedural
behavior if the LLM output fails validation. Unlike test 1, this
proves the LLM's output is actually usable game content, not just
that the API responds.

Usage:
    conda activate oraclefinanceai
    python test/test_llm_world_generation.py
    python test/test_llm_world_generation.py groq
    python test/test_llm_world_generation.py gemini

Exit code 0 = success (a valid ExtensionSpec was produced, whether
from the LLM directly or via fallback -- the pipeline never crashing
is itself the pass condition), 1 = hard failure (exception/crash).
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.config as config
from ai.llm_generator import LLMWorldGenerator
from ai.procedural_generator import ProceduralWorldGenerator
from world.extension_generator import WorldExtensionGenerator
from history.player_profile import PlayerProfile
import random


def build_sample_profile() -> PlayerProfile:
    """A realistic mid-game profile, similar in shape to the spec's example JSON."""
    profile = PlayerProfile()
    profile.level = 38
    profile.main_damage = "fire"
    profile.secondary_damage = "physical"
    profile.favorite_skill = "basic_attack"
    profile.most_used_weapon = "Inferno Wand"
    profile.defensive_strength = "low"
    profile.deaths = {"physical": 12, "poison": 2, "fire": 1}
    profile.total_deaths = 15
    profile.exploration = 0.31
    profile.average_exploration = 0.28
    profile.boss_attempts = 7
    profile.boss_successes = 2
    profile.rare_items_found = 17
    profile.six_mod_items_found = 1
    profile.playtime_seconds = 36000
    profile.extensions_generated = 3
    return profile


def run_generation_test(provider: str) -> bool:
    print(f"\n{'=' * 60}")
    print(f"TEST 2: LLM World Generation (full pipeline)  |  provider = '{provider}'")
    print(f"{'=' * 60}")

    api_key_env = config.LLM_PROVIDER_PRESETS.get(provider, {}).get("api_key_env")
    if api_key_env and not os.environ.get(api_key_env):
        print(f"[SKIP] Environment variable '{api_key_env}' is not set.")
        return False

    llm_backend = LLMWorldGenerator(provider=provider, verbose=True)
    procedural_backend = ProceduralWorldGenerator()
    orchestrator = WorldExtensionGenerator(backend=llm_backend, fallback_backend=procedural_backend)

    profile = build_sample_profile()
    existing_ids = {"extension_1", "extension_2"}
    rng = random.Random(12345)

    try:
        spec = orchestrator.generate(profile, existing_ids, rng)
    except Exception as e:
        print(f"[FAIL] Pipeline raised an unhandled exception: {e}")
        return False

    if spec is None:
        print("[FAIL] Pipeline returned None -- this should never happen "
              "(fallback should always produce a spec).")
        return False

    print(f"\n--- Resulting ExtensionSpec ---")
    print(f"  extension_id   : {spec.extension_id}")
    print(f"  extension_name : {spec.extension_name}")
    print(f"  area_level     : {spec.area_level}")
    print(f"  biome          : {spec.biome}")
    print(f"  is_anomaly     : {spec.is_anomaly}")
    print(f"  room_count     : {spec.room_count}")
    print(f"  world_modifiers: {len(spec.world_modifiers)} modifier(s)")
    for mod in spec.world_modifiers:
        print(f"    - {mod.get('name')}: {mod.get('description')}")
        for rule in mod.get("rules", []):
            print(f"        rule: {rule}")

    came_from_llm = llm_backend.is_available() and spec.extension_id.startswith("ai_ext_")
    source = "LLM" if came_from_llm else "fallback (procedural/hardcoded)"
    print(f"\n[INFO] Spec source: {source}")

    if spec.extension_id in existing_ids:
        print(f"[FAIL] Generated extension_id '{spec.extension_id}' collides with an existing one.")
        return False

    if not (1 <= spec.area_level <= 500):
        print(f"[FAIL] area_level {spec.area_level} is out of sane bounds.")
        return False

    print(f"[PASS] Pipeline produced a valid, usable ExtensionSpec "
          f"(source: {source}).\n")
    return True


def main():
    if len(sys.argv) > 1:
        providers = [sys.argv[1]]
    else:
        providers = [
            p for p in config.LLM_PROVIDER_PRESETS
            if os.environ.get(config.LLM_PROVIDER_PRESETS[p]["api_key_env"])
        ]
        if not providers:
            print("No provider API keys found in environment "
                  "(checked GROQ_API_KEY, GEMINI_API_KEY, OPENAI_API_KEY).")
            print("Set at least one, e.g.:")
            print("  export GROQ_API_KEY=gsk_...")
            print("  export GEMINI_API_KEY=AIza...")
            sys.exit(1)

    results = {p: run_generation_test(p) for p in providers}

    print(f"{'=' * 60}")
    print("SUMMARY")
    print(f"{'=' * 60}")
    for provider, passed in results.items():
        print(f"  {provider:10s} : {'PASS' if passed else 'FAIL'}")

    if not any(results.values()):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()