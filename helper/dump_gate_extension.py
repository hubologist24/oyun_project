# dump_gate_extension.py
"""Dump only gate / world-extension source files into one txt."""

from pathlib import Path

#PROJECT_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_FILE  = PROJECT_ROOT / "gate_extension_dump.txt"

# --- Edit this list to include/exclude files ---
FILES = [
    # ================= PART 1 — gate + world wiring =================
    "world/gate.py",
    "world/world.py",
    "world/expansion.py",
    "world/map_generator.py",
    "world/modifier_pool.py",
    "world/world_modifiers.py",
    "world/loot_rules.py",
    "world/tilemap.py",            # FLOOR/WALL/DOOR_* enums used by expansion
    "core/config.py",
    "core/difficulty.py",
    "core/rng.py",
    "ui/gateway_menu.py",
    "ui/world_evolution.py",

    # ================= PART 2 — extension generation =================
    "core/game_parts/extension_mixin.py",   # <-- the main file
    "world/extension.py",
    "world/extension_generator.py",
    "world/extension_templates.py",
    "ai/procedural_generator.py",
    "ai/llm_generator.py",
    "ai/mock_generator.py",
    "ai/validators.py",
    "ai/prompt_builder.py",
    "ai/personalization.py",
    "history/player_profile.py",
    "history/aggregator.py",

    # ================= PART 3 — only if you touch these ==============
    # "player/player.py",          # extension_stones spend, dash, keystones
    # "save/save_manager.py",      # persist new gate/extension fields
    # "items/item_generator.py",   # loot granted by attach_extension
    # "ui/hud.py",                 # InventoryUI / stones display
]


def estimate_tokens(text: str) -> int:
    # rough heuristic: ~4 chars per token
    return len(text) // 4


def main():
    missing, written, parts = [], [], []
    total_chars = 0

    for rel in FILES:
        path = PROJECT_ROOT / rel
        if not path.is_file():
            missing.append(rel)
            continue

        try:
            body = path.read_text(encoding="utf-8")
        except Exception as e:
            missing.append(f"{rel}  (read error: {e})")
            continue

        header = (
            f"\n--- FILE START: {rel} ---\n"
            f"# [SOURCE] {path}\n"
        )
        footer = f"\n--- FILE END: {rel} ---\n"

        parts.append(header + body + footer)
        written.append(rel)
        total_chars += len(body)

    banner = (
        "# ==========================================\n"
        "# GATE / EXTENSION DUMP\n"
        f"# Files requested: {len(FILES)}\n"
        f"# Files included : {len(written)}\n"
        f"# Files missing  : {len(missing)}\n"
        f"# Approx tokens  : ~{estimate_tokens(''.join(parts))}\n"
        "# ==========================================\n"
    )

    OUTPUT_FILE.write_text(banner + "".join(parts), encoding="utf-8")

    # -------- report --------
    print(f"\nWrote: {OUTPUT_FILE}")
    print(f"  Included: {len(written)} file(s)")
    print(f"  Missing : {len(missing)}")
    for m in missing:
        print(f"    - {m}")
    print(f"  Approx tokens: ~{estimate_tokens(''.join(parts))}")


if __name__ == "__main__":
    main()