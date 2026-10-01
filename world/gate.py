################################################################################
# FILE: world\gate.py  (NEW)
################################################################################

"""
Gate: a physical, persistent world object marking a connection point
between two areas. Per spec section 1/2:

- Every area (starting area + every extension) must contain at least
  one gate from the moment it's created.
- Gates start CLOSED. "World evolution" (an extension being generated)
  OPENS an existing closed gate somewhere in the already-explored world
  and links it to the newly created extension.
- Every newly generated extension also spawns with its own fresh
  closed gate(s), reserving expansion slots for the NEXT evolution.

A Gate lives inside a specific area's room (tile position), so it can
be drawn/walked into like any other object, and the area that owns it
identified by `owner_area_id` ("starting_area" or an extension_id).
`target_extension_id` is None while closed; set the moment it opens.
"""
from dataclasses import dataclass, field
from typing import Optional
import itertools

_gate_id_counter = itertools.count(1)


@dataclass
class Gate:
    gate_id: int
    owner_area_id: str          # which area this gate physically sits in
    room_index: int              # which room (index into that area's rooms list) it's placed in
    tile_col: int
    tile_row: int
    state: str = "closed"        # "closed" | "open"
    target_extension_id: Optional[str] = None   # set when opened
    is_starting_gate: bool = False  # True only for gates present in the starting area
    opened_at: Optional[float] = None   # wall-clock timestamp when opened

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
        }

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
        )


def next_gate_id() -> int:
    return next(_gate_id_counter)


def reset_gate_id_counter(start_at: int = 1):
    """Used by World.from_dict() to keep ids monotonic/unique after a load."""
    global _gate_id_counter
    _gate_id_counter = itertools.count(start_at)

#def open_to(self, extension_id: str):
#    import time
#    self.state = "open"
#    self.target_extension_id = extension_id
#    self.opened_at = time.time()