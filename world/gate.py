# [PROJECT: world]
from dataclasses import dataclass, field
from typing import Optional
import itertools

_gate_id_counter = itertools.count(1)


@dataclass(eq=False)
class Gate:
    gate_id: int
    owner_area_id: str
    room_index: int
    tile_col: int
    tile_row: int
    state: str = "closed"
    target_extension_id: Optional[str] = None
    is_starting_gate: bool = False
    opened_at: Optional[float] = None
    side: str = "e"
    overcharge_level: int = 0

    seal: dict = field(default_factory=dict)
    level_requirement: int = 0

    # Phase 3 — destination character
    tier: str = "standard"
    tier_index: int = 1

    # ------------------------------------------------------------------
    # Instance methods (must NOT be @staticmethod)
    # ------------------------------------------------------------------
    def open_to(self, extension_id: str):
        import time
        self.state = "open"
        self.target_extension_id = extension_id
        self.opened_at = time.time()

    def is_open(self) -> bool:
        return self.state == "open"

    def to_dict(self):
        return {
            "gate_id": self.gate_id,
            "owner_area_id": self.owner_area_id,
            "room_index": self.room_index,
            "tile_col": self.tile_col,
            "tile_row": self.tile_row,
            "state": self.state,
            "target_extension_id": self.target_extension_id,
            "is_starting_gate": self.is_starting_gate,
            "opened_at": self.opened_at,
            "side": self.side,
            "overcharge_level": self.overcharge_level,
            "seal": self.seal,
            "level_requirement": self.level_requirement,
            "tier": self.tier,
            "tier_index": self.tier_index,
        }

    # ------------------------------------------------------------------
    # Class-level constructor (static)
    # ------------------------------------------------------------------
    @staticmethod
    def from_dict(d):
        return Gate(
            gate_id=d["gate_id"],
            owner_area_id=d["owner_area_id"],
            room_index=d["room_index"],
            tile_col=d["tile_col"],
            tile_row=d["tile_row"],
            state=d.get("state", "closed"),
            target_extension_id=d.get("target_extension_id"),
            is_starting_gate=d.get("is_starting_gate", False),
            opened_at=d.get("opened_at"),
            side=d.get("side", "e"),
            overcharge_level=d.get("overcharge_level", 0),
            seal=d.get("seal", {}) or {},
            level_requirement=int(d.get("level_requirement", 0)),
            tier=d.get("tier", "standard"),
            tier_index=int(d.get("tier_index", 1)),
        )


def next_gate_id() -> int:
    return next(_gate_id_counter)


def reset_gate_id_counter(start_at: int = 1):
    global _gate_id_counter
    _gate_id_counter = itertools.count(start_at)