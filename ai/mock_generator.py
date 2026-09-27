"""
MockAIWorldGenerator: a deterministic, offline stand-in for a real LLM.

Purpose: prove the WorldGenerator -> WorldValidator -> fallback pipeline
correctly handles realistic LLM failure modes (malformed JSON, markdown
fences, out-of-range values, invalid enum values, missing fields)
WITHOUT requiring any network access or API key. This is not meant to
be "smart" -- it deliberately produces a mix of valid and intentionally
broken output so the safety net is exercised, not just the happy path.

Uses the exact same PromptBuilder a real LLM would consume, so a human
reviewing this class can see precisely what a real LLM prompt/response
cycle would look like.
"""
import json
import random as random_module
from typing import Optional, Set
from world.extension import ExtensionSpec
from history.player_profile import PlayerProfile
from ai.world_generator import WorldGenerator
from ai.prompt_builder import PromptBuilder


MOCK_BIOMES = ["ash_canyon", "sunken_library", "corrupted_hollow", "frozen_reach",
               "storm_peaks", "bone_wastes", "verdant_ruin", "obsidian_maze"]

MOCK_DAMAGE_TYPES = ["physical", "fire", "cold", "lightning", "chaos"]


class MockAIWorldGenerator(WorldGenerator):
    """
    failure_rate: fraction of calls that deliberately produce broken
    output (bad JSON, out-of-range numbers, etc.) to exercise the
    validator/fallback path in testing. Set to 0.0 for "always valid"
    behavior, or higher to stress-test.
    """

    def __init__(self, failure_rate: float = 0.15, verbose: bool = True):
        self.failure_rate = failure_rate
        self.verbose = verbose

    def generate_next_extension(self, player_profile: PlayerProfile,
                                 existing_extension_ids: Set[str],
                                 rng: random_module.Random) -> Optional[ExtensionSpec]:
        prompt = PromptBuilder.build_extension_prompt(player_profile, existing_extension_ids)
        if self.verbose:
            print("=== [MockAIWorldGenerator] Prompt that WOULD be sent to an LLM ===")
            print(prompt[:400] + ("..." if len(prompt) > 400 else ""))

        raw_text = self._fake_llm_response(player_profile, existing_extension_ids, rng)

        if self.verbose:
            print("=== [MockAIWorldGenerator] Raw 'LLM' response ===")
            print(raw_text[:400] + ("..." if len(raw_text) > 400 else ""))

        parsed = self._parse_json_response(raw_text)
        if parsed is None:
            print("[MockAIWorldGenerator] Response was not valid JSON.")
            return None

        try:
            index = len(existing_extension_ids) + 1
            extension_id = f"ai_ext_{index}"
            while extension_id in existing_extension_ids:
                index += 1
                extension_id = f"ai_ext_{index}"

            spec = ExtensionSpec(
                extension_id=extension_id,
                extension_name=parsed.get("extension_name", "Unnamed Region"),
                area_level=parsed.get("area_level", player_profile.level + 1),
                biome=parsed.get("biome", "unknown"),
                is_anomaly=parsed.get("is_anomaly", False),
                world_modifiers=parsed.get("world_modifiers", []),
                boss_template_id=None,
                room_count=parsed.get("room_count", 6),
            )
            return spec
        except Exception as e:
            print(f"[MockAIWorldGenerator] Failed to build ExtensionSpec from parsed JSON: {e}")
            return None

    # ---------------- fake response generation ----------------
    def _fake_llm_response(self, profile: PlayerProfile, existing_ids: Set[str], rng) -> str:
        if rng.random() < self.failure_rate:
            return self._generate_broken_response(profile, rng)
        return self._generate_valid_response(profile, rng)

    def _generate_valid_response(self, profile: PlayerProfile, rng) -> str:
        biome = rng.choice(MOCK_BIOMES)
        is_anomaly = rng.random() < 0.1
        area_level = profile.level + (rng.randint(15, 30) if is_anomaly else rng.randint(0, 3))

        modifiers = self._generate_plausible_modifiers(profile, rng)

        payload = {
            "extension_name": biome.replace("_", " ").title(),
            "biome": biome,
            "area_level": max(1, area_level),
            "is_anomaly": is_anomaly,
            "room_count": rng.randint(5, 9),
            "world_modifiers": modifiers,
        }
        # Simulate an LLM sometimes wrapping JSON in a markdown fence even when told not to.
        if rng.random() < 0.3:
            return f"```json\n{json.dumps(payload, indent=2)}\n```"
        return json.dumps(payload, indent=2)

    def _generate_plausible_modifiers(self, profile: PlayerProfile, rng) -> list:
        num_modifiers = rng.randint(1, 2)
        modifiers = []
        pool = [
            self._modifier_damage_multiplier,
            self._modifier_conversion,
            self._modifier_tier_override,
            self._modifier_resistance,
        ]
        for _ in range(num_modifiers):
            builder = rng.choice(pool)
            modifiers.append(builder(profile, rng))
        return modifiers

    def _modifier_damage_multiplier(self, profile, rng) -> dict:
        dtype = rng.choice(MOCK_DAMAGE_TYPES)
        target = rng.choice(["minion", "elite", "boss", "all"])
        return {
            "name": f"{dtype.title()} Surge",
            "description": f"{target.title()} enemies take altered {dtype} damage here.",
            "rules": [
                {"type": "damage_taken_multiplier", "target": target, "damage_type": dtype,
                 "multiplier": round(rng.uniform(0.5, 2.0), 2)}
            ],
        }

    def _modifier_conversion(self, profile, rng) -> dict:
        from_t = rng.choice(MOCK_DAMAGE_TYPES)
        to_t = rng.choice([d for d in MOCK_DAMAGE_TYPES if d != from_t])
        return {
            "name": "Unstable Energies",
            "description": f"{from_t.title()} damage partially becomes {to_t} damage.",
            "rules": [
                {"type": "conversion", "from_type": from_t, "to_type": to_t,
                 "percent": round(rng.uniform(0.2, 0.6), 2), "scope": "player"}
            ],
        }

    def _modifier_tier_override(self, profile, rng) -> dict:
        modifier_id = rng.choice(["physical_damage", "fire_damage", "cold_damage",
                                  "lightning_damage", "chaos_damage", "armor"])
        low = rng.randint(15, 40)
        high = low + rng.randint(10, 20)
        return {
            "name": "Shifted Itemization",
            "description": f"Item power for {modifier_id.replace('_', ' ')} follows different rules here.",
            "rules": [
                {"type": "tier_override", "modifier": modifier_id, "tiers": {"T1": [low, high]}}
            ],
        }

    def _modifier_resistance(self, profile, rng) -> dict:
        dtype = rng.choice(MOCK_DAMAGE_TYPES)
        target = rng.choice(["minion", "elite", "player"])
        return {
            "name": "Environmental Attunement",
            "description": f"{target.title()} resistance to {dtype} is altered in this region.",
            "rules": [
                {"type": "resistance_modifier", "target": target, "damage_type": dtype,
                 "delta": round(rng.uniform(-0.3, 0.3), 2)}
            ],
        }

    def _generate_broken_response(self, profile: PlayerProfile, rng) -> str:
        """Simulates realistic LLM failure modes for validator stress-testing."""
        failure_type = rng.choice([
            "truncated_json", "prose_wrapper", "bad_enum", "out_of_range", "missing_fields",
        ])

        if failure_type == "truncated_json":
            return '{"extension_name": "The Shattered Vault", "biome": "ruins", "area_level": '

        if failure_type == "prose_wrapper":
            return ("Sure! Here's a great new region for your player:\n\n"
                    '{"extension_name": "Oops", "biome": "ruins", "area_level": 10}\n\n'
                    "Let me know if you'd like changes!")

        if failure_type == "bad_enum":
            payload = {
                "extension_name": "The Nullspace",
                "biome": "nullspace",
                "area_level": profile.level + 2,
                "is_anomaly": False,
                "room_count": 6,
                "world_modifiers": [{
                    "name": "Broken Rule",
                    "description": "invalid damage type on purpose",
                    "rules": [{"type": "damage_taken_multiplier", "target": "minion",
                              "damage_type": "holy_radiance", "multiplier": 1.5}],
                }],
            }
            return json.dumps(payload)

        if failure_type == "out_of_range":
            payload = {
                "extension_name": "The Overtuned Depths",
                "biome": "depths",
                "area_level": profile.level + 1,
                "is_anomaly": False,
                "room_count": 6,
                "world_modifiers": [{
                    "name": "Absurd Multiplier",
                    "description": "multiplier way too high on purpose",
                    "rules": [{"type": "damage_taken_multiplier", "target": "boss",
                              "damage_type": "chaos", "multiplier": 500}],
                }],
            }
            return json.dumps(payload)

        # missing_fields
        payload = {"biome": "incomplete_region"}
        return json.dumps(payload)

    # ---------------- response parsing ----------------
    def _parse_json_response(self, raw_text: str) -> Optional[dict]:
        text = raw_text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines).strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                candidate = text[start:end + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    return None
            return None