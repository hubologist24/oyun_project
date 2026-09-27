"""
Global configuration constants for Phase 1.
Kept centralized so later phases (world extensions, tuning) can override
values without hunting through the codebase.
"""


SCREEN_WIDTH = 1280
SCREEN_HEIGHT = 720
FPS = 60

TILE_SIZE = 48

# World extension threshold (used starting Phase 3+, defined now per spec)
WORLD_EXTENSION_HOURS = 10

# Player
PLAYER_SPEED = 260.0  # pixels/sec
PLAYER_BASE_HP = 100
PLAYER_BASE_DAMAGE = 12
PLAYER_BASE_ARMOR = 0
PLAYER_ATTACK_RANGE = 56
PLAYER_ATTACK_COOLDOWN = 0.45  # seconds
PLAYER_INVULN_ON_HIT = 0.5

XP_CURVE_BASE = 40
XP_CURVE_GROWTH = 1.35

# Enemy
ENEMY_CONTACT_DAMAGE_COOLDOWN = 0.8

# Colors
COLOR_BG = (18, 18, 22)
COLOR_WALL = (55, 55, 65)
COLOR_FLOOR = (32, 32, 38)
COLOR_PLAYER = (80, 200, 255)
COLOR_ENEMY = (220, 70, 70)
COLOR_ELITE = (230, 140, 40)
COLOR_BOSS = (180, 40, 200)
COLOR_LOOT_NORMAL = (200, 200, 200)
COLOR_LOOT_MAGIC = (90, 130, 255)
COLOR_LOOT_RARE = (240, 220, 60)
COLOR_TEXT = (235, 235, 235)
COLOR_HP_BAR_BG = (60, 10, 10)
COLOR_HP_BAR_FG = (200, 40, 40)
COLOR_XP_BAR_FG = (60, 160, 230)
COLOR_BOSS_BAR_FG = (180, 40, 200)

SAVE_FILE = "savegame.json"
SAVE_VERSION = 1

# World extension generation modes
EXTENSION_MODE_MANUAL = "manual"
EXTENSION_MODE_AUTOMATIC = "automatic"
EXTENSION_MODE_DEBUG = "debug"

# Automatic generation checks playtime against this threshold (hours).
# Converted to seconds for comparison against World.total_playtime_seconds.
WORLD_EXTENSION_HOURS = 10
WORLD_EXTENSION_SECONDS = WORLD_EXTENSION_HOURS * 3600


# --- AI backend selection (Phase 6) ---
# Which WorldGenerator backend core/game.py should construct.
# "procedural"  -> ProceduralWorldGenerator only (default, zero dependencies)
# "mock_ai"     -> MockAIWorldGenerator (offline LLM simulation, for testing
#                  the validator/fallback pipeline without any API)
# "llm"         -> LLMWorldGenerator (real API; requires 'openai' package)
AI_BACKEND_MODE = "procedural"

# Only used when AI_BACKEND_MODE == "mock_ai"
MOCK_AI_FAILURE_RATE = 0.15

# --- LLM provider selection (only used when AI_BACKEND_MODE == "llm") ---
# Both Groq and Gemini expose OpenAI-compatible chat completion endpoints,
# so LLMWorldGenerator talks to either through the same `openai` client --
# only base_url / model / api_key differ. No code changes needed to swap.
#
# "groq"    -> fast inference, generous free tier, Llama/other OSS models
# "gemini"  -> Google's Gemini models
# "openai"  -> real OpenAI (if you ever get a key)
LLM_PROVIDER = "groq"

LLM_PROVIDER_PRESETS = {
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "openai/gpt-oss-120b",
        "api_key_env": "GROQ_API_KEY",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "default_model": "gemini-3.6-flash",
        "api_key_env": "GEMINI_API_KEY",
    },
    "openai": {
        "base_url": None,  # None = use openai package's own default
        "default_model": "gpt-4o-mini",
        "api_key_env": "OPENAI_API_KEY",
    },
}

# Optional manual overrides -- leave None to use the preset above
LLM_MODEL_OVERRIDE = None
LLM_API_KEY_OVERRIDE = None