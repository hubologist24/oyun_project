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

ENEMY_TILES_PER_MOB = 24     # lower = denser!!!!
ENEMY_DENSITY_CAP   = 2.0
ENEMY_ROOM_MAX      = 12
GATE_SAFE_TILES     = 4




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
        self.rule_set = WorldRuleSet()
        #self.rule_set = WorldRuleSet(
        #    modifiers=[WorldModifier.from_dict(m) for m in spec.world_modifiers]
        #)

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

        #if world is not None and not world.gates_in_area(self.extension_id):
        #    gate_room_index = self.area["gate_room_index"]
        #    tile_col = self.area.get("gate_tile_col")
        #    tile_row = self.area.get("gate_tile_row")
        #    if tile_col is None or tile_row is None:
        #        x0, y0, x1, y1 = self.area["gate_room"]
        #        tile_col, tile_row = (x0 + x1) // 2, (y0 + y1) // 2
        #    world.register_extension_gate(self.extension_id, gate_room_index,
        #                                  tile_col, tile_row)
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
    from core.difficulty import area_density_mult

    ext_area = ext.ensure_built(rng_service, world)
    ext_tilemap = ext_area["tilemap"]
    ext_grid = ext_tilemap.grid

    offset_col = area.tilemap.cols + 4
    offset_row = 0

    # --- this extension's own gate, always in world (offset) coordinates ---
    gi = ext_area["gate_room_index"]
    gate_tc = offset_col + ext_area["gate_tile_col"]
    gate_tr = offset_row + ext_area["gate_tile_row"]
    own = world.gates_in_area(ext.extension_id)
    if own:                       # also repairs gates saved with the old bad coords
        own[0].room_index = gi
        own[0].tile_col = gate_tc
        own[0].tile_row = gate_tr
    else:
        world.register_extension_gate(ext.extension_id, gi, gate_tc, gate_tr)

    new_cols = max(area.tilemap.cols, offset_col + ext_tilemap.cols)
    new_rows = max(area.tilemap.rows, offset_row + ext_tilemap.rows)
    area.tilemap.ensure_size(new_cols, new_rows)

    if gate.tile_col is not None and gate.tile_row is not None:
        if (0 <= gate.tile_row < area.tilemap.rows
                and 0 <= gate.tile_col < area.tilemap.cols):
            area.tilemap.grid[gate.tile_row][gate.tile_col] = DOOR_OPEN

    for r_idx, row in enumerate(ext_grid):
        for c_idx, tile in enumerate(row):
            tr = offset_row + r_idx
            tc = offset_col + c_idx
            if 0 <= tr < area.tilemap.rows and 0 <= tc < area.tilemap.cols:
                area.tilemap.grid[tr][tc] = tile

    gate_c = gate.tile_col if gate.tile_col is not None else area.tilemap.cols - 1
    gate_r = gate.tile_row if gate.tile_row is not None else area.tilemap.rows // 2
    target_c = gate_tc
    target_r = gate_tr

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

    def shift(r):
        return (r[0] + offset_col, r[1] + offset_row, r[2] + offset_col, r[3] + offset_row)

    shifted_rooms = [shift(r) for r in ext_area.get("rooms", [])]
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
    ext_start = shifted_rooms[0] if shifted_rooms else (offset_col + 1, offset_row + 1,
                                                        offset_col + 3, offset_row + 3)
    if hasattr(area, "region_start"):
        area.region_start[rid] = ((ext_start[0] + ext_start[2]) / 2 * ts,
                                  (ext_start[1] + ext_start[3]) / 2 * ts)

    # --- enemies: SHIFTED rooms, scaled by room size + level, floor tiles only ---
    stream = rng_service.derive_child("world", f"extension:{rid}:spawns")
    lvl = ext.spec.area_level
    density = min(ENEMY_DENSITY_CAP, area_density_mult(lvl))

    enemy_rooms = [shift(r) for r in ext_area.get("enemy_rooms", [])]
    if not enemy_rooms:
        enemy_rooms = shifted_rooms[1:] or shifted_rooms

    grid = area.tilemap.grid
    tm = area.tilemap
    for room in enemy_rooms:
        rx0, ry0, rx1, ry1 = room
        w, h = rx1 - rx0, ry1 - ry0
        count = int(round((w * h) / ENEMY_TILES_PER_MOB * density)) + stream.randint(0, 1)
        count = max(2, min(ENEMY_ROOM_MAX, count))
        placed = 0
        for _ in range(count * 8):
            if placed >= count:
                break
            col = stream.randint(rx0 + 1, max(rx0 + 1, rx1 - 2))
            row = stream.randint(ry0 + 1, max(ry0 + 1, ry1 - 2))
            if not (0 <= row < len(grid) and 0 <= col < len(grid[0])) or grid[row][col] != FLOOR:
                continue
            if abs(col - gate_tc) + abs(row - gate_tr) < GATE_SAFE_TILES:
                continue
            x = col * ts + ts / 2
            y = row * ts + ts / 2
            if tm.collides_rect(pygame.Rect(int(x - 14), int(y - 14), 28, 28)):
                continue
            e = spawn_basic_enemy(x, y, stream, area_level=lvl)
            e.region_id = rid
            e._pack_checked = True
            if hasattr(area, "enemies"):
                area.enemies.append(e)
            placed += 1

    boss_room = ext_area.get("boss_room")
    if boss_room is not None:
        bx0, by0, bx1, by1 = shift(boss_room)
        boss = Boss((bx0 + bx1) / 2 * ts, (by0 + by1) / 2 * ts, BossTemplate.hollow_warden())
        boss.region_id = rid
        if hasattr(area, "bosses"):
            area.bosses.append(boss)

    g0 = world.gates_in_area(rid)
    if g0:
        stamp_door(area, g0[0])