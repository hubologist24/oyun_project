import time
from typing import Optional
from core.rng import RNGService
from world.extension import Extension, ExtensionSpec
from world.world_modifiers import WorldRuleSet
from world.gate import Gate, next_gate_id, reset_gate_id_counter
import core.config as config


class World:
    def __init__(self, world_seed: int):
        self.world_seed = world_seed
        self.rng_service = RNGService(world_seed)
        self.extensions = []
        self.created_at = time.time()
        self.total_playtime_seconds = 0.0
        self.playtime_at_last_extension = 0.0
        self.gates: list[Gate] = []   # NEW: all gates across the whole world

    # ---------------- gate management ----------------
    def register_starting_gate(self, room_index: int, tile_col: int, tile_row: int) -> Gate:
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
        )
        self.gates.append(gate)
        return gate

    def register_extension_gate(
        self, extension_id: str, room_index: int, tile_col: int, tile_row: int
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
        )
        self.gates.append(gate)
        return gate

    def closed_gates(self) -> list:
        return [g for g in self.gates if g.state == "closed"]

    def open_gates(self) -> list:
        return [g for g in self.gates if g.state == "open"]

    def gates_in_area(self, area_id: str) -> list:
        return [g for g in self.gates if g.owner_area_id == area_id]

    def open_next_gate_to(self, extension_id: str, rng) -> Optional[Gate]:
        """
        Core of 'world evolution': picks one existing CLOSED gate
        somewhere in the already-explored world (starting area or any
        already-built extension) and opens it, linking it to the
        newly-generated extension. Prefers gates in the STARTING AREA
        first (keeps the hub-and-spoke feel of a central portal room
        gradually filling with gates), then falls back to gates in
        already-open extensions.
        """
        candidates = self.closed_gates()
        if not candidates:
            return None  # world has run out of reserved gate slots

        starting_candidates = [g for g in candidates if g.is_starting_gate]
        pool = starting_candidates if starting_candidates else candidates
        chosen = rng.choice(pool)
        chosen.open_to(extension_id)
        return chosen

    # ---------------- extension management ----------------
    def add_extension(self, spec: ExtensionSpec) -> Extension:
        stream = self.rng_service.derive_child(
            "world", f"extension_seed:{spec.extension_id}"
        )
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
            "gates": [g.to_dict() for g in self.gates],
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
        return world