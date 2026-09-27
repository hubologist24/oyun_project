"""
Test 1: LLM Connectivity Test

Verifies that the configured LLM provider (Groq or Gemini, via the
OpenAI-compatible interface) is reachable and returns a coherent
response. This is a basic "is the wiring correct" smoke test -- it
does NOT validate game-specific JSON schema (that's test 2).

Usage:
    conda activate oraclefinanceai
    python test/test_llm_connectivity.py

    # Or force a specific provider regardless of core/config.py:
    python test/test_llm_connectivity.py groq
    python test/test_llm_connectivity.py gemini

Exit code 0 = success, 1 = failure (so this can be used in CI/scripts).
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.config as config
from ai.llm_generator import LLMWorldGenerator


def run_connectivity_test(provider: str) -> bool:
    print(f"\n{'=' * 60}")
    print(f"TEST 1: LLM Connectivity  |  provider = '{provider}'")
    print(f"{'=' * 60}")

    preset = config.LLM_PROVIDER_PRESETS.get(provider)
    if preset is None:
        print(f"[FAIL] Unknown provider '{provider}'. "
              f"Known providers: {list(config.LLM_PROVIDER_PRESETS.keys())}")
        return False

    api_key_env = preset["api_key_env"]
    if not os.environ.get(api_key_env):
        print(f"[SKIP] Environment variable '{api_key_env}' is not set. "
              f"Set it to run this test against '{provider}'.")
        return False

    generator = LLMWorldGenerator(provider=provider, verbose=True)

    if not generator.is_available():
        print(f"[FAIL] LLMWorldGenerator reports not available for provider '{provider}'. "
              f"Check that 'openai' package is installed (pip install openai) and the "
              f"API key is valid.")
        return False

    print(f"[OK] Client initialized. model='{generator.model}' base_url='{generator.base_url}'")

    # Minimal raw call bypassing the game-specific prompt, just to prove
    # the HTTP round-trip and auth work end-to-end.
    try:
        response = generator._client.chat.completions.create(
            model=generator.model,
            messages=[
                {"role": "system", "content": "Reply with exactly one word: OK"},
                {"role": "user", "content": "Ping."},
            ],
            temperature=generator.temperature,
            timeout=generator.timeout_seconds,
        )
        text = response.choices[0].message.content
    except Exception as e:
        print(f"[FAIL] API call raised an exception: {e}")
        return False

    if not text or not text.strip():
        print("[FAIL] API call succeeded but returned empty content.")
        return False

    print(f"[OK] Received response: {text.strip()[:200]!r}")
    print(f"[PASS] Provider '{provider}' is reachable and responding.\n")
    return True


def main():
    if len(sys.argv) > 1:
        providers = [sys.argv[1]]
    else:
        # Test whichever providers have an API key set, so this works
        # out of the box for whoever is running it (Groq and/or Gemini).
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

    results = {p: run_connectivity_test(p) for p in providers}

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