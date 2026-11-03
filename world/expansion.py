# [PROJECT: world]
# [SYMBOLS] classes: ExtensionSpec, Extension | functions: find_door_site, stamp_door, attach_extension
import copy
import time
import pygame
from dataclasses import dataclass, field
from typing import Optional, List
from world.world_modifiers import WorldRuleSet, WorldModifier
import core.config as config
from world.tilemap import DOOR_OPEN, DOOR_CLOSED, WALL, FLOOR
from enemies.enemy import spawn_basic_enemy
from enemies.boss import Boss, BossTemplate




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
    loot_rules: dict = field(default_factory=dict)
    boss_personalization: dict = field(default_factory=dict)
    overcharge_level: int = 0
    enemy_behavior_bias: dict = field(default_factory=dict)

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
            "loot_rules": self.loot_rules,
            "boss_personalization": self.boss_personalization,
            "overcharge_level": self.overcharge_level,
            "enemy_behavior_bias": self.enemy_behavior_bias,
        }

    @staticmethod
    def from_dict(d):
        return ExtensionSpec(
            extension_id=d["extension_id"],
            extension_name=d["extension_name"],
            area_level=d["area_level"],
            biome=d.get("biome", "unknown"),
            is_anomaly=d.get("is_anomaly", False),
            world_modifiers=copy.deepcopy(d.get("world_modifiers", [])),
            boss_template_id=d.get("boss_template_id"),
            room_count=d.get("room_count", 6),
            map_cols=d.get("map_cols", 50),
            map_rows=d.get("map_rows", 38),
            loot_rules=d.get("loot_rules", {}),
            boss_personalization=d.get("boss_personalization", {}),
            overcharge_level=d.get("overcharge_level", 0),
            enemy_behavior_bias=d.get("enemy_behavior_bias", {}),
        )


class Extension:

    def __init__(self, spec: ExtensionSpec, extension_seed: int, created_at: float = None):
        self.spec = spec
        self.extension_seed = extension_seed
        self.created_at = created_at or time.time()
        self.area = None
        self.rule_set = WorldRuleSet(
            modifiers=[WorldModifier.from_dict(m) for m in spec.world_modifiers]
        )

    @property
    def extension_id(self):
        return self.spec.extension_id

    def ensure_built(self, rng_service, world=None):
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
            tile_col = self.area.get("gate_tile_col")
            tile_row = self.area.get("gate_tile_row")
            if tile_col is None or tile_row is None:
                x0, y0, x1, y1 = self.area["gate_room"]
                tile_col, tile_row = (x0 + x1) // 2, (y0 + y1) // 2
            world.register_extension_gate(self.extension_id, gate_room_index,
                                          tile_col, tile_row)
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


#def find_door_site(area, room):
#    """room is (x0, y0, x1, y1) in tiles. Returns (col, row, side, room_index) or None."""
#    if not room:
#        return None
#    x0, y0, x1, y1 = room
#    return ((x0 + x1) // 2, (y0 + y1) // 2, "e", None)

def _grid_of(area):
    """Accept a Level, a TileMap, or a raw 2D grid."""
    if hasattr(area, "tilemap"):
        return area.tilemap.grid
    if hasattr(area, "grid"):
        return area.grid
    return area


def _is_room_tuple(v):
    return (isinstance(v, (tuple, list)) and len(v) == 4
            and all(isinstance(n, int) for n in v))


def _door_in_room(grid, room):
    """A floor tile in `room`, at least 1 tile away from the map border."""
    rows = len(grid)
    cols = len(grid[0]) if rows else 0
    x0, y0, x1, y1 = room
    if x1 <= x0 or y1 <= y0:
        return None

    def ok(c, r):
        return 1 <= c <= cols - 2 and 1 <= r <= rows - 2 and grid[r][c] == FLOOR

    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    if ok(cx, cy):
        return cx, cy
    for r in range(y0, y1):
        for c in range(x0, x1):
            if ok(c, r):
                return c, r
    return None


def find_door_site(area, rooms):
    """
    area:  Level / TileMap / raw grid
    rooms: one (x0,y0,x1,y1) tuple OR a list of them
    Returns (col, row, side, room_index) or None. room_index is None for a single room.
    """
    grid = _grid_of(area)
    if not rooms or not grid or not grid[0]:
        return None

    if _is_room_tuple(rooms):
        candidates = [(None, rooms)]
    else:
        idx = list(range(len(rooms)))
        order = idx[1:-1] + idx[:1] + idx[-1:] if len(idx) > 2 else idx
        candidates = [(i, rooms[i]) for i in order]

    for i, room in candidates:
        if not _is_room_tuple(room):
            continue
        spot = _door_in_room(grid, room)
        if spot:
            return spot[0], spot[1], "e", i
    return None

def stamp_door(area, gate):
    """Stamp a gate's tile into the area's tilemap (open or closed, based on state)."""
    tm = getattr(area, "tilemap", area)
    c, r = gate.tile_col, gate.tile_row
    if c is None or r is None:
        return
    if 0 <= r < tm.rows and 0 <= c < tm.cols:
        tm.grid[r][c] = DOOR_OPEN if gate.is_open() else DOOR_CLOSED


def attach_extension(area, ext, gate, rng_service, world):
    ext_area = ext.ensure_built(rng_service, world)
    if not world.gates_in_area(ext.extension_id):
        gi = ext_area["gate_room_index"]
        tc = ext_area.get("gate_tile_col")
        tr = ext_area.get("gate_tile_row")
        if tc is None or tr is None:
            x0, y0, x1, y1 = ext_area["gate_room"]
            tc, tr = (x0 + x1) // 2, (y0 + y1) // 2
        # tile coords here are local to the extension, so shift by the offset used below
        world.register_extension_gate(ext.extension_id, gi,
                                      tc + area.tilemap.cols + 4, tr)
    ext_tilemap = ext_area["tilemap"]
    ext_grid = ext_tilemap.grid

    # Determine placement offset to the right of the current map
    offset_col = area.tilemap.cols + 4
    offset_row = 0

    new_cols = max(area.tilemap.cols, offset_col + ext_tilemap.cols)
    new_rows = max(area.tilemap.rows, offset_row + ext_tilemap.rows)
    area.tilemap.ensure_size(new_cols, new_rows)

    # Stamp door at gate
    if gate.tile_col is not None and gate.tile_row is not None:
        if (0 <= gate.tile_row < area.tilemap.rows
                and 0 <= gate.tile_col < area.tilemap.cols):
            area.tilemap.grid[gate.tile_row][gate.tile_col] = DOOR_OPEN
        

    # Copy extension grid tiles with offset
    for r_idx, row in enumerate(ext_grid):
        for c_idx, tile in enumerate(row):
            tr = offset_row + r_idx
            tc = offset_col + c_idx
            if 0 <= tr < area.tilemap.rows and 0 <= tc < area.tilemap.cols:
                area.tilemap.grid[tr][tc] = tile

    # Carve corridor between gate and extension start
    gate_c = gate.tile_col if gate.tile_col is not None else area.tilemap.cols - 1
    gate_r = gate.tile_row if gate.tile_row is not None else area.tilemap.rows // 2
    target_c = offset_col + ext_area.get("gate_tile_col", 1)
    target_r = offset_row + ext_area.get("gate_tile_row", 1)

    for c in range(min(gate_c, target_c), max(gate_c, target_c) + 1):
        if 0 <= gate_r < area.tilemap.rows and 0 <= c < area.tilemap.cols:
            area.tilemap.grid[gate_r][c] = FLOOR
            if gate_r + 1 < area.tilemap.rows:
                area.tilemap.grid[gate_r + 1][c] = FLOOR
    for r in range(min(gate_r, target_r), max(gate_r, target_r) + 1):
        if 0 <= r < area.tilemap.rows and 0 <= target_c < area.tilemap.cols:
            area.tilemap.grid[r][target_c] = FLOOR
            if target_c + 1 < area.tilemap.cols:
                area.tilemap.grid[r][target_c + 1] = FLOOR

    # Offset rooms
    ext_rooms = ext_area.get("rooms", [])
    shifted_rooms = [
        (rx0 + offset_col, ry0 + offset_row, rx1 + offset_col, ry1 + offset_row)
        for (rx0, ry0, rx1, ry1) in ext_rooms
    ]
    if hasattr(area, "rooms"):
        area.rooms.extend(shifted_rooms)

    rid = ext.extension_id
    if shifted_rooms:
        min_c = min(r[0] for r in shifted_rooms)
        min_r = min(r[1] for r in shifted_rooms)
        max_c = max(r[2] for r in shifted_rooms)
        max_r = max(r[3] for r in shifted_rooms)
        reg_rect = pygame.Rect(min_c, min_r, max_c - min_c, max_r - min_r)
        if hasattr(area, "region_rects"):
            area.region_rects.append((rid, reg_rect))

    if hasattr(area, "tile_region"):
        for r_idx, row in enumerate(ext_grid):
            for c_idx, tile in enumerate(row):
                if tile == 0:
                    area.tile_region[(offset_col + c_idx, offset_row + r_idx)] = rid

    ts = config.TILE_SIZE
    ext_start = shifted_rooms[0] if shifted_rooms else (offset_col + 1, offset_row + 1)
    start_x = (ext_start[0] + ext_start[2]) / 2 * ts
    start_y = (ext_start[1] + ext_start[3]) / 2 * ts
    if hasattr(area, "region_start"):
        area.region_start[rid] = (start_x, start_y)

    # Spawn enemies & bosses
    stream = rng_service.derive_child("world", f"extension:{rid}:spawns")
    lvl = ext.spec.area_level
    enemy_rooms = ext_area.get("enemy_rooms", shifted_rooms[1:] if len(shifted_rooms) > 1 else shifted_rooms)
    for room in enemy_rooms:
        rx0, ry0, rx1, ry1 = room
        count = stream.randint(2, 5)
        for _ in range(count):
            col = stream.randint(rx0 + 1, rx1 - 2)
            row = stream.randint(ry0 + 1, ry1 - 2)
            x = col * ts + ts / 2
            y = row * ts + ts / 2
            e = spawn_basic_enemy(x, y, stream, area_level=lvl)
            e.region_id = rid
            e._pack_checked = True
            if hasattr(area, "enemies"):
                area.enemies.append(e)

    boss_room = ext_area.get("boss_room")
    if boss_room is not None:
        bx0, by0, bx1, by1 = boss_room
        boss_cx = (bx0 + offset_col + bx1 + offset_col) / 2 * ts
        boss_cy = (by0 + offset_row + by1 + offset_row) / 2 * ts
        boss = Boss(boss_cx, boss_cy, BossTemplate.hollow_warden())
        boss.region_id = rid
        if hasattr(area, "bosses"):
            area.bosses.append(boss)