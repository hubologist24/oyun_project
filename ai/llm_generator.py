"""
LLMWorldGenerator: the REAL AI-backed generator. Entirely optional:

- If `openai` isn't installed, or no API key/config is provided, this
  generator's generate_next_extension() returns None immediately.
- WorldExtensionGenerator (Phase 5) already treats a None/invalid
  result as "fall back to safe procedural generation" -- so the game
  works identically whether or not this class can ever succeed.
- No code from the LLM is ever executed. The response is parsed as
  JSON text only, then validated by the exact same WorldValidator used
  for the mock and procedural backends.

Supports any OpenAI-compatible provider via base_url override,
including Groq and Gemini (both expose OpenAI-compatible chat
completion endpoints), in addition to real OpenAI. Provider selection
is entirely config-driven (core/config.py) -- this class never
hardcodes a specific vendor.
"""
import os
import json
from typing import Optional, Set

from world.extension import ExtensionSpec
from history.player_profile import PlayerProfile
from ai.world_generator import WorldGenerator
from ai.prompt_builder import PromptBuilder
import core.config as config

try:
    from openai import OpenAI  # optional dependency
    _OPENAI_AVAILABLE = True
except ImportError:
    _OPENAI_AVAILABLE = False


class LLMWorldGenerator(WorldGenerator):
    def __init__(self, provider: Optional[str] = None, api_key: Optional[str] = None,
                 model: Optional[str] = None, base_url: Optional[str] = None,
                 timeout_seconds: float = 20.0, verbose: bool = True):
        self.verbose = verbose
        self.timeout_seconds = timeout_seconds

        provider = provider or getattr(config, "LLM_PROVIDER", "openai")
        preset = config.LLM_PROVIDER_PRESETS.get(provider, {})

        self.provider = provider
        self.model = model or config.LLM_MODEL_OVERRIDE or preset.get("default_model", "gpt-4o-mini")
        self.base_url = base_url or preset.get("base_url")

        api_key_env = preset.get("api_key_env", "OPENAI_API_KEY")
        self.api_key = (api_key or config.LLM_API_KEY_OVERRIDE
                        or os.environ.get(api_key_env))

        # Groq rejects temperature=0 outright; keep a safe non-zero default
        # low enough for consistent, mostly-deterministic JSON output.
        self.temperature = 0.4 if provider == "groq" else 0.5

        self._client = None
        if _OPENAI_AVAILABLE and self.api_key:
            try:
                kwargs = {"api_key": self.api_key}
                if self.base_url:
                    kwargs["base_url"] = self.base_url
                self._client = OpenAI(**kwargs)
            except Exception as e:
                if self.verbose:
                    print(f"[LLMWorldGenerator] Failed to initialize client for provider "
                          f"'{provider}': {e}")
                self._client = None
        elif self.verbose:
            if not _OPENAI_AVAILABLE:
                print("[LLMWorldGenerator] 'openai' package not installed. pip install openai")
            elif not self.api_key:
                print(f"[LLMWorldGenerator] No API key found for provider '{provider}' "
                      f"(expected env var '{api_key_env}' or LLM_API_KEY_OVERRIDE).")

    def is_available(self) -> bool:
        return self._client is not None

    def generate_next_extension(self, player_profile: PlayerProfile,
                                 existing_extension_ids: Set[str],
                                 rng) -> Optional[ExtensionSpec]:
        if not self.is_available():
            if self.verbose:
                print(f"[LLMWorldGenerator] Not available (provider='{self.provider}'). "
                      f"Returning None -> caller will fall back.")
            return None

        prompt = PromptBuilder.build_extension_prompt(player_profile, existing_extension_ids)

        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a strict JSON API. You output ONLY a single "
                                                   "raw JSON object as your entire response. Never wrap "
                                                   "it in prose, explanations, or markdown code fences. "
                                                   "Never include ```json or ``` anywhere in your output."},
                    {"role": "user", "content": prompt},
                ],
                temperature=self.temperature,
                timeout=self.timeout_seconds,
            )
            raw_text = response.choices[0].message.content
        except Exception as e:
            print(f"[LLMWorldGenerator] API call failed (provider='{self.provider}', "
                  f"model='{self.model}'): {e}")
            return None

        if self.verbose:
            print(f"=== [LLMWorldGenerator:{self.provider}] Raw LLM response ===")
            print(raw_text[:500] + ("..." if raw_text and len(raw_text) > 500 else ""))

        parsed = self._parse_json_response(raw_text)
        if parsed is None:
            print(f"[LLMWorldGenerator] LLM response was not valid/parseable JSON "
                  f"(provider='{self.provider}').")
            return None

        try:
            index = len(existing_extension_ids) + 1
            extension_id = f"ai_ext_{index}"
            while extension_id in existing_extension_ids:
                index += 1
                extension_id = f"ai_ext_{index}"

            spec = ExtensionSpec(
                extension_id=extension_id,
                extension_name=str(parsed.get("extension_name", "Unnamed Region")),
                area_level=int(parsed.get("area_level", player_profile.level + 1)),
                biome=str(parsed.get("biome", "unknown")),
                is_anomaly=bool(parsed.get("is_anomaly", False)),
                world_modifiers=parsed.get("world_modifiers", []),
                boss_template_id=None,
                room_count=int(parsed.get("room_count", 6)),
            )
            return spec
        except Exception as e:
            print(f"[LLMWorldGenerator] Failed to construct ExtensionSpec from parsed JSON: {e}")
            return None

    def _parse_json_response(self, raw_text: str) -> Optional[dict]:
        if not raw_text:
            return None
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