import time
from typing import Optional
from core.rng import RNGService
from world.extension import Extension, ExtensionSpec
from world.world_modifiers import WorldRuleSet
from world.gate import Gate, next_gate_id, reset_gate_id_counter
import core.config as config

from world.modifier_pool import ModifierPool, load_catalog


class World:
    def __init__(self, world_seed: int):
        self.world_seed = world_seed
        self.rng_service = RNGService(world_seed)
        self.extensions = []
        self.created_at = time.time()
        self.total_playtime_seconds = 0.0
        self.playtime_at_last_extension = 0.0
        self.gates: list[Gate] = []   # NEW: all gates across the whole world
        self.region_presence: dict[str, float] = {}    # NEW
        self.cleared_regions: set[str] = set()
        self.used_shrines: set[str] = set()
        self.modifier_pool = ModifierPool()

    # ---------------- gate management ----------------
    def register_starting_gate(self, room_index: int, tile_col: int, tile_row: int, side="e") -> Gate:
        """Called once at game startup to register the starting area's
        reserved (closed) gate slot."""
        gate = Gate(
            gate_id=next_gate_id(),
            owner_area_id="starting_area",
            room_index=room_index,
            tile_col=tile_col,
            tile_row=tile_row,
            state="closed",
            is_starting_gate=True,
            side=side
        )
        self.gates.append(gate)
        return gate

    def register_extension_gate(
        self, extension_id: str, room_index: int, tile_col: int, tile_row: int, side="e"
    ) -> Gate:
        """Called when a new extension is built (ensure_built) to reserve
        ITS fresh closed gate slot, per spec's 'reserving expansion slots'
        requirement."""
        gate = Gate(
            gate_id=next_gate_id(),
            owner_area_id=extension_id,
            room_index=room_index,
            tile_col=tile_col,
            tile_row=tile_row,
            state="closed",
            side=side
        )
        self.gates.append(gate)
        return gate

    def closed_gates(self) -> list:
        return [g for g in self.gates if g.state == "closed"]

    def open_gates(self) -> list:
        return [g for g in self.gates if g.state == "open"]

    def gates_in_area(self, area_id: str) -> list:
        return [g for g in self.gates if g.owner_area_id == area_id]

    def pick_next_gate(self, rng, eligible=None):
        candidates = self.closed_gates()
        if eligible is not None:
            candidates = [g for g in candidates if eligible(g)]
        if not candidates:
            return None
        starting_candidates = [g for g in candidates if g.is_starting_gate]
        pool = starting_candidates if starting_candidates else candidates
        return rng.choice(pool)

    def open_gate_to(self, gate, extension_id):
        """Commit a previously-picked gate. Safe no-op on None/already-open."""
        if gate is None or gate.is_open():
            return None
        gate.open_to(extension_id)
        return gate

    def open_next_gate_to(self, extension_id: str, rng) -> Optional[Gate]:
        """Backward-compat: pick and open in one call. Retained so
        test_helpers5's gate-history test and any existing callers keep
        working unchanged."""
        gate = self.pick_next_gate(rng)
        return self.open_gate_to(gate, extension_id)

    # ---------------- extension management ----------------
    def add_extension(self, spec: ExtensionSpec) -> Extension:
        stream = self.rng_service.derive_child(
            "world", f"extension_seed:{spec.extension_id}")
        extension_seed = stream.randint(0, 2**31 - 1)

        blessings, curses = load_catalog()
        pool_rng = self.rng_service.get_stream("modifier_pool")
        summary = self.modifier_pool.step(pool_rng, blessings, curses)

        # Preserve delta as narrative for the UI; the pool is the source
        # of truth for gameplay. `Extension.rule_set` is intentionally empty.
        spec.world_modifiers = summary["added"] + [
            dict(m, _removed=True) for m in summary["removed"]
        ]

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

    def test_migration_from_legacy_save_with_modifier_lacking_id():
        """Old modifiers may have no 'id'; migration must dedupe on name then."""
        from world.world import World
        
        legacy = {
            "world_seed": 1, 
            "created_at": 0.0,
            "total_playtime_seconds": 0.0, 
            "playtime_at_last_extension": 0.0,
            "extensions": [
                {
                    "spec": {
                        "extension_id": "e1", 
                        "extension_name": "A",
                        "area_level": 2, 
                        "biome": "a",
                        "world_modifiers": [{"name": "NoId", "rules": []}]
                    },
                    "extension_seed": 1, 
                    "created_at": 0.0
                }
            ],
            "gates": [], 
            "region_presence": {},
            "cleared_regions": [], 
            "used_shrines": []
        }
        
        w = World.from_dict(legacy)
        return {
            "migration_handles_missing_id": hasattr(w, "modifier_pool"),
            "pool_has_one": w.modifier_pool.size() == 1
        }

    def aggregate_rule_set(self) -> WorldRuleSet:
        from world.world_modifiers import WorldModifier
        combined = WorldRuleSet()
        for m in self.modifier_pool.modifiers:
            combined.modifiers.append(WorldModifier.from_dict(m))
        return combined

    def to_dict(self):
        return {
            "world_seed": self.world_seed,
            "created_at": self.created_at,
            "total_playtime_seconds": self.total_playtime_seconds,
            "playtime_at_last_extension": self.playtime_at_last_extension,
            "extensions": [ext.to_dict() for ext in self.extensions],
            "gates": [g.to_dict() for g in self.gates],
            "region_presence": self.region_presence,
            "cleared_regions": sorted(self.cleared_regions),
            "used_shrines": sorted(self.used_shrines),
            "modifier_pool": self.modifier_pool.to_dict(),
        }

    @staticmethod
    def from_dict(d):
        world = World(d["world_seed"])
        world.created_at = d.get("created_at", time.time())
        world.total_playtime_seconds = d.get("total_playtime_seconds", 0.0)
        world.playtime_at_last_extension = d.get("playtime_at_last_extension", 0.0)
        world.extensions = [
            Extension.from_dict(e) for e in d.get("extensions", [])
        ]
        world.gates = [Gate.from_dict(g) for g in d.get("gates", [])]
        if world.gates:
            reset_gate_id_counter(max(g.gate_id for g in world.gates) + 1)
        world.region_presence = d.get("region_presence", {})
        world.cleared_regions = set(d.get("cleared_regions", []))
        world.used_shrines = set(d.get("used_shrines", []))

        if "modifier_pool" in d:
            world.modifier_pool = ModifierPool.from_dict(d["modifier_pool"])
        else:
            merged, seen = [], set()
            for ext_data in d.get("extensions", []):
                for m in ext_data.get("spec", {}).get("world_modifiers", []):
                    if not isinstance(m, dict):
                        continue
                    key = m.get("id") or m.get("name")
                    if key and key not in seen:
                        seen.add(key)
                        merged.append(m)
            world.modifier_pool = ModifierPool(modifiers=merged)


        return world