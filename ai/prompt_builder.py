"""
PromptBuilder: converts a PlayerProfile into (a) a natural-language
prompt for an LLM and (b) an explicit JSON schema description the LLM
must follow. Used by LLMWorldGenerator; MockAIWorldGenerator also uses
the schema description to keep its fake output shape-compatible.

This file contains NO network code and NO game-state mutation -- it's
pure string/dict construction, easy to unit-test and inspect.
"""
import json
from history.player_profile import PlayerProfile


EXTENSION_JSON_SCHEMA_DESCRIPTION = """
Return ONLY a single valid JSON object (no markdown fences, no prose,
no explanation) matching exactly this schema:

{
  "extension_name": string,
  "biome": string (snake_case, e.g. "ash_canyon"),
  "area_level": integer,
  "is_anomaly": boolean,
  "room_count": integer (between 4 and 12),
  "world_modifiers": [
    {
      "name": string,
      "description": string,
      "rules": [
        {
          "type": "damage_taken_multiplier" | "damage_dealt_multiplier" | "conversion" | "tier_override" | "resistance_modifier" | "on_kill_effect",
          ... fields specific to the rule type ...
        }
      ]
    }
  ]
}

Rule type field requirements:

damage_taken_multiplier / damage_dealt_multiplier:
  "target": "minion" | "elite" | "boss" | "player" | "all"
  "damage_type": one of ["physical","fire","cold","lightning","chaos","poison","bleed"] or null
  "multiplier": number between 0.25 and 4

conversion:
  "from_type": one of the damage types above
  "to_type": one of the damage types above
  "percent": number between 0.0 and 1.0
  "scope": "player" | "enemy" | "all"

tier_override:
  "modifier": string, e.g. "physical_damage", "fire_damage", "armor"
  "tiers": { "T1": [min, max], "T2": [min, max], ... }  (numbers only, min <= max, min >= 0)

resistance_modifier:
  "target": "minion" | "elite" | "boss" | "player" | "all"
  "damage_type": one of the damage types above
  "delta": number between -0.5 and 0.5

on_kill_effect:
  "effect": string (e.g. "explosion", "chain_lightning", "corpse_burst", "life_leech")
  "target": "minion" | "elite" | "boss" | "player" | "all"
  "radius": number (in world units, >= 0)
  "damage_ratio": number between 0.0 and 4.0
  "percent": number between 0.0 and 1.0  (chance for the effect to trigger on kill)

Do not include any field not listed above. Do not wrap the JSON in
code fences. Do not include comments.
""".strip()


class PromptBuilder:
    @staticmethod
    def build_extension_prompt(profile: PlayerProfile, existing_extension_ids: set) -> str:
        from ai.personalization import derive_hints
        import json as _json

        profile_json = json.dumps(profile.to_dict(), indent=2)
        hints = derive_hints(profile)
        hints_json = _json.dumps(hints.to_dict(), indent=2)
        existing = ", ".join(sorted(existing_extension_ids)) or "(none yet)"

        return f"""You are a world designer for a single-player action RPG.

The player has the following profile, derived from their actual play history:

{profile_json}

Derived design hints (pre-computed heuristics you may use as guidance,
but you are not required to follow them literally):

{hints_json}

Existing extension ids already generated for this world (do not reuse these names/ids): {existing}

Design the NEXT region extension for this specific player. Guidance:
- Do not simply make monsters bigger numbers. Prefer rule mutations that
  change how damage types, conversions, or itemization tiers interact.
- You may challenge, reward, surprise, enable, disrupt, or reinforce the
  player's current build -- do not always directly counter them.
- If the player is defensively weak or struggling against bosses,
  consider pairing a challenging modifier with an enabling one (a new
  tool via their secondary damage type), rather than pure punishment.
- area_level should normally be close to the player's level ({profile.level}),
  occasionally (rarely) much higher if is_anomaly is true.
- Keep world_modifiers to 1-3 entries with 1-3 rules each.

{EXTENSION_JSON_SCHEMA_DESCRIPTION}
"""

    @staticmethod
    def schema_description() -> str:
        return EXTENSION_JSON_SCHEMA_DESCRIPTION