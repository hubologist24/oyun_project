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


# Player
PLAYER_SPEED = 260.0  # pixels/sec
PLAYER_BASE_HP = 100
PLAYER_BASE_DAMAGE = 12
PLAYER_BASE_ARMOR = 0
PLAYER_ATTACK_RANGE = 56
PLAYER_ATTACK_COOLDOWN = 0.45  # seconds
PLAYER_INVULN_ON_HIT = 0.35

XP_CURVE_BASE = 40
XP_CURVE_GROWTH = 1.35

# Enemy
ENEMY_CONTACT_DAMAGE_COOLDOWN = 0.65

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
WORLD_EXTENSION_HOURS = 0.25
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


# How many times the player may reroll a staged extension offer before
# Accept / Push Back become the only options. A negotiation, not a
# slot machine -- see Game._reroll_pending_extension.
REROLL_CAP = 3


# =========================================================================
# LOOT CONFIG — single source of truth for kill-drop quantities.
# core/game.py reads this directly. Difficulty multipliers
# (D.get("loot_drop_mult"), etc.) still apply ON TOP of these baselines,
# so presets keep working unchanged.
# =========================================================================
LOOT_CONFIG = {
    # ---- MASTER KNOB ----
    # Multiplies every KILL-drop chance below (items + stones + orbs).
    #   1.0 = as authored
    #   0.5 = half as many drops
    #   0.0 = no kill drops at all
    # The boss heal-orb is intentionally NOT affected by this knob — the
    # design rule is that a boss fight must always award at least one heal.
    "global_mult": 1.0,

    # ---- regular enemies (base values) ----
    "enemy_item_chance":   0.02,   # 2% baseline (was hardcoded 5%)
    "enemy_orb_chance":    0.04,   # only fires if the item roll failed
    "enemy_stone_chance":  0.002,  # 0.2% (was hardcoded 0.3%)

    # ---- elite multipliers (multiply the base above) ----
    "elite_item_mult":     4.0,    # 8% item on elite (was ~28%)
    "elite_orb_mult":      1.5,
    "elite_stone_mult":    5.0,    # 1% stone (was ~2%)

    # ---- boss item slots: (per-slot chance, (num_prefixes, num_suffixes)) ----
    # Old: [(0.90,(3,2)), (0.75,(2,2)), (0.60,(2,1)), (0.40,(1,2))]
    # → avg 4 items per boss, many 5- and 6-affix rolls.
    "boss_item_slots": [
        (0.80, (2, 2)),
        (0.50, (2, 1)),
        (0.25, (1, 2)),
    ],

    # ---- boss stones ----  Old: [0.70, 0.30, 0.10]
    "boss_stone_chances": [0.50, 0.15, 0.03],

    # ---- always drop a heal orb on boss kill (kept; NOT scaled by global_mult) ----
    "boss_orb_always": True,

    # ---- world-boss extras (nemesis promoted) ----
    "world_boss_item_bonus":  0.10,            # old 0.15
    "world_boss_stone_bonus": 0.05,            # old 0.10
    "world_boss_extra_slot":  (0.30, (2, 2)),  # old (0.45, (3, 3))
}


# =========================================================================
# DIFFICULTY TUNING — everything difficulty-related now lives here.
# core/difficulty.py reads these two dicts at import time. Nothing else
# needs to change to re-tune the difficulty curves.
# =========================================================================

# (group, value_at_0_percent, value_at_100_percent) for each knob.
# 0% = original game; 100% = maximum hardness.
DIFFICULTY_KNOB_RANGES = {
    # ---- enemy ----
    "enemy_hp_mult":              ("enemy",  1.0,  1.8),
    "enemy_damage_mult":          ("enemy",  1.0,  1.6),
    "enemy_speed_mult":           ("enemy",  1.0,  1.2),
    "enemy_aggro_mult":           ("enemy",  1.0,  1.5),
    "enemy_attack_cooldown_mult": ("enemy",  1.0,  0.7),
    "elite_chance":               ("enemy",  0.12, 0.30),
    "roam_cap":                   ("enemy",  45,   75),
    "roam_interval":              ("enemy",  2.5,  1.4),
    # ---- boss ----
    "boss_hp_mult":               ("boss",   1.0,  1.8),
    "boss_damage_mult":           ("boss",   1.0,  1.5),
    "boss_cooldown_mult":         ("boss",   1.0,  0.7),
    "boss_telegraph_mult":        ("boss",   1.0,  0.8),
    # ---- player ----
    "player_damage_mult":         ("player", 1.0,  0.85),
    "death_xp_penalty":           ("player", 0.10, 0.35),
    "safe_room_regen":            ("player", 0.04, 0.015),
    "orb_heal_frac":              ("player", 0.25, 0.12),
    "invuln_on_hit":              ("player", 0.35, 0.20),
    # ---- loot (these compose with LOOT_CONFIG; see note below) ----
    "loot_drop_mult":             ("loot",   1.0,  0.5),
    "loot_quality_mult":          ("loot",   1.0,  1.5),
    "orb_drop_mult":              ("loot",   1.0,  0.5),
    "stone_drop_mult":            ("loot",   1.0,  0.5),
    "boss_loot_mult":             ("loot",   1.0,  0.75),
    "boss_stone_mult":            ("loot",   1.0,  0.6),
    # ---- world ----
    "pressure_time_mult":         ("world",  1.0,  0.5),
    "area_level_bonus":           ("world",  0,    4),
    "nemesis_create_chance":      ("world",  0.60, 0.90),
    "nemesis_evolve_chance":      ("world",  0.30, 0.50),
}

# Named preset → master percent (0..100).
DIFFICULTY_PRESETS = {
    "original":  0,
    "easy":     15,
    "normal":   35,
    "hard":     55,
    "brutal":   80,
    "nightmare": 100,
}


# ---------------- DIFFICULTY ----------------
# Master slider 0..100 (0 = original game). Change this and re-test.
DIFFICULTY_PERCENT = 30
# Or use a named preset instead (overrides the percent):
# "original" | "easy" | "normal" | "hard" | "brutal" | "nightmare"
DIFFICULTY_PRESET = None

# Independent sliders per group: "enemy", "boss", "player", "loot", "world"
# e.g. hard enemies/bosses but generous loot:
DIFFICULTY_GROUP_PERCENT = {
     "loot": 70,
      "boss": 70,
      "enemy": 60,
       "world": 70,
}
# Per-knob percent (beats group):
DIFFICULTY_KNOB_PERCENT = {
    # "stone_drop_mult": 0,
}
# Exact values (beats everything):
DIFFICULTY_OVERRIDES = {
    # "enemy_hp_mult": 2.5,
}

# --- Adaptive difficulty (optional) ---
DIFFICULTY_ADAPTIVE = False          # off by default, preserves existing saves
DIFFICULTY_ADAPTIVE_MIN = 0
DIFFICULTY_ADAPTIVE_MAX = 100
DIFFICULTY_ADAPTIVE_UP = 2           # % gained per flawless boss
DIFFICULTY_ADAPTIVE_DOWN = 3         # % lost after 3 consecutive deaths