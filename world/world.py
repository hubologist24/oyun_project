"""
World: top-level container for the player's entire persistent game
world -- Phase 5 adds tracking of playtime-at-last-extension so
automatic generation can check elapsed time since the last extension,
not just total playtime.
"""
import time
from core.rng import RNGService
from world.extension import Extension, ExtensionSpec
from world.world_modifiers import WorldRuleSet
import core.config as config


class World:
    def __init__(self, world_seed: int):
        self.world_seed = world_seed
        self.rng_service = RNGService(world_seed)
        self.extensions = []
        self.created_at = time.time()
        self.total_playtime_seconds = 0.0
        self.playtime_at_last_extension = 0.0

    def add_extension(self, spec: ExtensionSpec) -> Extension:
        stream = self.rng_service.derive_child("world", f"extension_seed:{spec.extension_id}")
        extension_seed = stream.randint(0, 2**31 - 1)
        ext = Extension(spec, extension_seed)
        self.extensions.append(ext)
        self.playtime_at_last_extension = self.total_playtime_seconds
        return ext

    def seconds_since_last_extension(self) -> float:
        return self.total_playtime_seconds - self.playtime_at_last_extension

    def automatic_generation_due(self) -> bool:
        return self.seconds_since_last_extension() >= config.WORLD_EXTENSION_SECONDS

    def get_extension(self, extension_id: str):
        for ext in self.extensions:
            if ext.extension_id == extension_id:
                return ext
        return None

    def aggregate_rule_set(self) -> WorldRuleSet:
        combined = WorldRuleSet()
        for ext in self.extensions:
            combined.modifiers.extend(ext.rule_set.modifiers)
        return combined

    def to_dict(self):
        return {
            "world_seed": self.world_seed,
            "created_at": self.created_at,
            "total_playtime_seconds": self.total_playtime_seconds,
            "playtime_at_last_extension": self.playtime_at_last_extension,
            "extensions": [ext.to_dict() for ext in self.extensions],
        }

    @staticmethod
    def from_dict(d):
        world = World(d["world_seed"])
        world.created_at = d.get("created_at", time.time())
        world.total_playtime_seconds = d.get("total_playtime_seconds", 0.0)
        world.playtime_at_last_extension = d.get("playtime_at_last_extension", 0.0)
        world.extensions = [Extension.from_dict(e) for e in d.get("extensions", [])]
        return world