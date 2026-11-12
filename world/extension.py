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
import copy
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
    overcharge_level: int = 0          # NEW
    
    enemy_behavior_bias: dict = field(default_factory=dict)
    size_tier: int = -1          # -1 = let the map generator roll; else 0, 1 or 2

    def to_dict(self):
        return {
            "extension_id": self.extension_id,
            "extension_name": self.extension_name,
            "area_level": self.area_level,
            "biome": self.biome,
            "is_anomaly": self.is_anomaly,
            "world_modifiers": copy.deepcopy(self.world_modifiers),
            "boss_template_id": self.boss_template_id,
            "room_count": self.room_count,
            "map_cols": self.map_cols,
            "map_rows": self.map_rows,
            "loot_rules": copy.deepcopy(self.loot_rules),
            "boss_personalization": copy.deepcopy(self.boss_personalization),
            "overcharge_level": self.overcharge_level,
            "enemy_behavior_bias": copy.deepcopy(self.enemy_behavior_bias),
            "size_tier": self.size_tier,
        }

    @staticmethod
    def from_dict(d):
        return ExtensionSpec(
            extension_id=d["extension_id"],
            extension_name=d["extension_name"],
            area_level=d["area_level"],
            biome=d.get("biome", "unknown"),
            #is_anomaly=d.get("is_anomaly", False),
            is_anomaly = _coerce_bool(d.get("is_anomaly", False)),
            world_modifiers=copy.deepcopy(d.get("world_modifiers", [])),
            boss_template_id=d.get("boss_template_id"),
            room_count=d.get("room_count", 6),
            map_cols=d.get("map_cols", 50),
            map_rows=d.get("map_rows", 38),
            loot_rules=d.get("loot_rules", {}),
            boss_personalization=d.get("boss_personalization", {}),
            overcharge_level=d.get("overcharge_level", 0),
            enemy_behavior_bias=d.get("enemy_behavior_bias", {}),
            size_tier=d.get("size_tier", -1),     # in from_dict
        )


class Extension:
    """Runtime wrapper: spec + generated area (built lazily) + seed."""

    def __init__(self, spec: ExtensionSpec, extension_seed: int, created_at: float = None):
        self.spec = spec
        self.extension_seed = extension_seed
        #self.created_at = created_at or time.time()
        self.created_at = time.time() if created_at is None else created_at
        self.area = None  # built lazily via ensure_built()
        #self.rule_set = WorldRuleSet(
        #    modifiers=[WorldModifier.from_dict(m) for m in spec.world_modifiers]
        #)
        self.rule_set = WorldRuleSet() 

    @property
    def extension_id(self):
        return self.spec.extension_id

    def ensure_built(self, rng_service, world=None, gate=None):
        if self.area is not None:
            return self.area
        #from world.map_generator import build_procedural_area
        from tools.map_bot import BotConfig,build_validated_area
        seal_type = None
        if gate is not None:
            seal = getattr(gate, "seal", None) or {}
            seal_type = seal.get("type")
        stream = rng_service.fresh_stream(
            f"world:extension:{self.extension_id}:area")
        #self.area = build_procedural_area(
        #    stream, area_level=self.spec.area_level,
        #    cols=self.spec.map_cols, rows=self.spec.map_rows,
        #    room_count=self.spec.room_count,
        #    boss_room=(self.spec.boss_template_id is not None),
        #    extra_rooms=(self.spec.size_tier if self.spec.size_tier >= 0 else None),
        #    biome=self.spec.biome,
        #    seal_type=seal_type,
        #)
        self.area = build_validated_area(
            rng_service, spec=self.spec, extension_id=self.extension_id,
            seal_type=seal_type, max_attempts=4,
            cfg=BotConfig(render="never"),
        )
        return self.area
        #self.area = build_validated_area(rng_service, self.spec, self.extension_id,
        #                             seal_type=seal_type)
        # may need to validate with bot in future

    

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


def _coerce_bool(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes")
    return bool(v)