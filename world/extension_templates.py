"""
Hand-authored extension templates for Phase 3 (pre-AI). These are
exactly the kind of ExtensionSpec objects that Phase 5's
ProceduralWorldGenerator and Phase 6's LLMWorldGenerator will produce
programmatically instead -- the game engine consumes them identically
either way, which is the entire point of validating the architecture
now before AI is introduced.
"""
import json
import os
from world.extension import ExtensionSpec

_DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "world_modifiers.json")


def _load_modifier_templates():
    with open(_DATA_PATH, "r") as f:
        return {m["id"]: m for m in json.load(f)["templates"]}


_MODIFIER_TEMPLATES = None


def get_modifier_template(modifier_id: str) -> dict:
    global _MODIFIER_TEMPLATES
    if _MODIFIER_TEMPLATES is None:
        _MODIFIER_TEMPLATES = _load_modifier_templates()
    return _MODIFIER_TEMPLATES[modifier_id]


def build_extension_1(player_level: int) -> ExtensionSpec:
    area_level = max(1, player_level + 1)
    return ExtensionSpec(
        extension_id="extension_1",
        extension_name="Ash Canyon",
        area_level=area_level,
        biome="ash_canyon",
        is_anomaly=False,
        world_modifiers=[get_modifier_template("ashen_frailty")],
        boss_template_id=None,
        room_count=7,
    )


def build_extension_2(player_level: int) -> ExtensionSpec:
    area_level = max(1, player_level + 1)
    return ExtensionSpec(
        extension_id="extension_2",
        extension_name="The Drowned Archive",
        area_level=area_level,
        biome="sunken_library",
        is_anomaly=False,
        world_modifiers=[get_modifier_template("storm_conductive"), get_modifier_template("chaos_ascendancy")],
        boss_template_id=None,
        room_count=8,
    )


def build_extension_3(player_level: int) -> ExtensionSpec:
    area_level = max(1, player_level + 1)
    return ExtensionSpec(
        extension_id="extension_3",
        extension_name="The Corrupted Hollow",
        area_level=area_level,
        biome="corrupted_hollow",
        is_anomaly=False,
        world_modifiers=[get_modifier_template("corrupted_flesh")],
        boss_template_id=None,
        room_count=7,
    )


def build_anomaly_extension(player_level: int, extension_index: int) -> ExtensionSpec:
    """
    Rare, substantially stronger optional region (per spec: "Anomaly
    Regions"). Level jump is deliberately large but the area remains
    physically avoidable/exitable (no boss gating the main path).
    """
    area_level = player_level + 15
    return ExtensionSpec(
        extension_id=f"anomaly_{extension_index}",
        extension_name="The Brittle Expanse",
        area_level=area_level,
        biome="anomaly_wastes",
        is_anomaly=True,
        world_modifiers=[get_modifier_template("brittle_bones")],
        boss_template_id=None,
        room_count=5,
    )


EXTENSION_SEQUENCE = [build_extension_1, build_extension_2, build_extension_3]


def build_next_extension(player_level: int, existing_extension_ids: set, rng) -> ExtensionSpec:
    """
    Picks the next extension in sequence, or an anomaly roll (rare)
    instead of the normal next step. This function is the manual
    stand-in for Phase 5's ProceduralWorldGenerator.select_next().
    """
    anomaly_roll = rng.random()
    if anomaly_roll < 0.08 and len(existing_extension_ids) >= 1:
        idx = sum(1 for eid in existing_extension_ids if eid.startswith("anomaly"))
        return build_anomaly_extension(player_level, idx + 1)

    for builder in EXTENSION_SEQUENCE:
        spec = builder(player_level)
        if spec.extension_id not in existing_extension_ids:
            return spec

    # Fallback once the hand-authored sequence is exhausted: repeat with
    # a generic template (Phase 5 will replace this with true procedural variety).
    idx = len(existing_extension_ids) + 1
    return ExtensionSpec(
        extension_id=f"extension_{idx}",
        extension_name=f"Uncharted Region {idx}",
        area_level=player_level + 1,
        biome="uncharted",
        is_anomaly=False,
        world_modifiers=[],
        boss_template_id=None,
        room_count=6,
    )