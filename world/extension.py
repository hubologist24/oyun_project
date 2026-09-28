"""
Extension: a permanent world expansion containing one or more areas,
world modifiers, and (optionally) a boss. Extension 0 conceptually
represents the starting world's own rule-set (usually empty/default),
so the same data model covers both curated and generated content.

An extension, once created, is immutable in the sense that its
generation parameters never change -- but the AREAS it contains are
built lazily (only when the player actually enters them) to avoid
building huge amounts of unused content.
"""
import time
from dataclasses import dataclass, field
from typing import Optional, List
from world.world_modifiers import WorldRuleSet, WorldModifier


@dataclass
class ExtensionSpec:
    extension_id: str
    extension_name: str
    area_level: int
    biome: str
    is_anomaly: bool = False
    world_modifiers: List[dict] = field(default_factory=list)
    boss_template_id: Optional[str] = None
    room_count: int = 6
    map_cols: int = 50
    map_rows: int = 38
    loot_rules: dict = field(default_factory=dict)              # Phase 7
    boss_personalization: dict = field(default_factory=dict)    # Phase 7

    def to_dict(self):
        return {
            "extension_id": self.extension_id,
            "extension_name": self.extension_name,
            "area_level": self.area_level,
            "biome": self.biome,
            "is_anomaly": self.is_anomaly,
            "world_modifiers": self.world_modifiers,
            "boss_template_id": self.boss_template_id,
            "room_count": self.room_count,
            "map_cols": self.map_cols,
            "map_rows": self.map_rows,
            "loot_rules": self.loot_rules,
            "boss_personalization": self.boss_personalization,
        }

    @staticmethod
    def from_dict(d):
        return ExtensionSpec(
            extension_id=d["extension_id"],
            extension_name=d["extension_name"],
            area_level=d["area_level"],
            biome=d.get("biome", "unknown"),
            is_anomaly=d.get("is_anomaly", False),
            world_modifiers=d.get("world_modifiers", []),
            boss_template_id=d.get("boss_template_id"),
            room_count=d.get("room_count", 6),
            map_cols=d.get("map_cols", 50),
            map_rows=d.get("map_rows", 38),
            loot_rules=d.get("loot_rules", {}),
            boss_personalization=d.get("boss_personalization", {}),
        )


class Extension:
    """Runtime wrapper: spec + generated area (built lazily) + seed."""

    def __init__(self, spec: ExtensionSpec, extension_seed: int, created_at: float = None):
        self.spec = spec
        self.extension_seed = extension_seed
        self.created_at = created_at or time.time()
        self.area = None  # built lazily via ensure_built()
        self.rule_set = WorldRuleSet(
            modifiers=[WorldModifier.from_dict(m) for m in spec.world_modifiers]
        )

    @property
    def extension_id(self):
        return self.spec.extension_id

    def ensure_built(self, rng_service, world=None):
        """
        world: optional back-reference so this extension can register
        its own reserved closed gate the moment its area is actually
        built (lazy -- matches the existing lazy-area-build philosophy).
        Passed explicitly rather than stored permanently on Extension
        to avoid a circular Extension<->World reference in to_dict().
        """
        if self.area is not None:
            return self.area
        from world.map_generator import build_procedural_area
        stream = rng_service.derive_child("world", f"extension:{self.extension_id}:area")
        self.area = build_procedural_area(
            stream, area_level=self.spec.area_level,
            cols=self.spec.map_cols, rows=self.spec.map_rows,
            room_count=self.spec.room_count, boss_room=(self.spec.boss_template_id is not None),
        )

        if world is not None and not world.gates_in_area(self.extension_id):
            gate_room_index = self.area["gate_room_index"]
            x0, y0, x1, y1 = self.area["gate_room"]
            tile_col, tile_row = (x0 + x1) // 2, (y0 + y1) // 2
            world.register_extension_gate(self.extension_id, gate_room_index, tile_col, tile_row)

        return self.area

    def to_dict(self):
        return {
            "spec": self.spec.to_dict(),
            "extension_seed": self.extension_seed,
            "created_at": self.created_at,
        }

    @staticmethod
    def from_dict(d):
        spec = ExtensionSpec.from_dict(d["spec"])
        return Extension(spec, d["extension_seed"], created_at=d.get("created_at"))