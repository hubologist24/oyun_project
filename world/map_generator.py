# world/map_generator.py
"""
Procedural map generation for extension areas: random room placement,
L-shaped corridor carving, and reservation of start / boss / gate rooms.

Note: this module used to also contain a divergent copy of the boss
code from enemies/boss.py -- that copy has been deleted (B11) so the
runtime boss behavior lives in exactly one place.
"""
import random
from world.tilemap import TileMap



ROOM_BRACKET_SIZE          = 10
ROOM_MAX_EXTRA_CAP         = 2
ROOM_EXTRA_WEIGHTS         = (4, 3, 2)   # odds of +0 / +1 / +2 rooms (tweak freely)
DENSITY_PER_BRACKET        = 0.15
DENSITY_CAP                = 0.60
MAP_GROWTH_PER_EXTRA_ROOM  = 0.25

ROOM_SPACING = 3   # min empty tiles between rooms (was effectively 1)


def _carve_h(grid, x0, x1, y):
    rows = len(grid)
    cols = len(grid[0]) if rows else 0
    for col in range(min(x0, x1), max(x0, x1) + 1):
        if 0 <= y < rows and 0 <= col < cols:
            grid[y][col] = 0
        if 0 <= y + 1 < rows and 0 <= col < cols:
            grid[y + 1][col] = 0


def _carve_v(grid, y0, y1, x):
    rows = len(grid)
    cols = len(grid[0]) if rows else 0
    for row in range(min(y0, y1), max(y0, y1) + 1):
        if 0 <= row < rows and 0 <= x < cols:
            grid[row][x] = 0
        if 0 <= row < rows and 0 <= x + 1 < cols:
            grid[row][x + 1] = 0

def extra_rooms_for_level(rng, area_level):
    rolls = list(range(ROOM_MAX_EXTRA_CAP + 1))
    return rng.choices(rolls, weights=list(ROOM_EXTRA_WEIGHTS), k=1)[0]


def _pick_gate_tile(grid, room):
    """Deterministic (consumes no RNG). Picks a tile away from the room center,
    with a fully open 3x3 around it so the closed door never blocks a path."""
    rows = len(grid)
    cols = len(grid[0]) if rows else 0
    x0, y0, x1, y1 = room
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2

    def open3(c, r):
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                rr, cc = r + dr, c + dc
                if not (0 <= rr < rows and 0 <= cc < cols) or grid[rr][cc] != 0:
                    return False
        return True

    cands = [(c, r) for r in range(y0 + 1, y1 - 1)
             for c in range(x0 + 1, x1 - 1) if open3(c, r)]
    if not cands:
        return cx, cy
    cands.sort(key=lambda p: (-(abs(p[0] - cx) + abs(p[1] - cy)), p[1], p[0]))
    return cands[0]


def enemy_density_mult(area_level):
    """
    Per-room enemy-density multiplier as a function of area level.

        lvl 1-9   -> 1.00
        lvl 10-19 -> 1.15
        lvl 20-29 -> 1.30
        lvl 30-39 -> 1.45
        lvl 40+   -> 1.60 (cap)
    """
    #bracket = area_level // ROOM_BRACKET_SIZE
    #return 1.0 + min(DENSITY_CAP, bracket * DENSITY_PER_BRACKET)
    bracket = area_level // ROOM_BRACKET_SIZE
    return 1.0 + min(DENSITY_CAP, max(0, bracket) * DENSITY_PER_BRACKET)


def _scale_map_for_rooms(cols, rows, extra_rooms):
    """Grow the tile grid so extra rooms actually fit comfortably."""
    if extra_rooms <= 0:
        return cols, rows
    import math
    tiles_factor = 1.0 + MAP_GROWTH_PER_EXTRA_ROOM * extra_rooms
    dim_scale = math.sqrt(tiles_factor)
    return int(cols * dim_scale) + 2, int(rows * dim_scale) + 2


def generate_dungeon_grid(rng: random.Random, cols=50, rows=38, room_count=6,
                          min_room=6, max_room=12, min_rooms=3):
    """
    Generate a room-and-corridor dungeon grid.

    Ensures at least `min_rooms` rooms whenever physically possible by
    retrying with progressively smaller room sizes (min_room -> 4 -> 3)
    on cramped maps, so build_procedural_area always has enough rooms
    to assign distinct start / boss / gate rooms (B10).
    """
    grid = [[1 for _ in range(cols)] for _ in range(rows)]
    rooms = []
    target = max(int(room_count), int(min_rooms))

    for attempt_min in (min_room, 4, 3):
        if len(rooms) >= target:
            break
        if attempt_min > min_room:
            # Only try a smaller size after the caller's requested min.
            pass
        attempts = 0
        max_attempts = target * 60
        while len(rooms) < target and attempts < max_attempts:
            attempts += 1

            w_max = min(max_room, cols - 4)
            h_max = min(max_room, rows - 4)
            if w_max < attempt_min or h_max < attempt_min:
                break  # can't fit rooms this big on this map -- shrink or stop

            w = rng.randint(attempt_min, w_max)
            h = rng.randint(attempt_min, h_max)

            # Need at least a 1-tile margin on every side.
            if cols - w - 2 < 1 or rows - h - 2 < 1:
                continue

            x0 = rng.randint(1, cols - w - 2)
            y0 = rng.randint(1, rows - h - 2)
            x1 = x0 + w
            y1 = y0 + h

            candidate = (x0, y0, x1, y1)
            overlaps = any(
                not (x1 < rx0 - ROOM_SPACING or x0 > rx1 + ROOM_SPACING
                     or y1 < ry0 - ROOM_SPACING or y0 > ry1 + ROOM_SPACING)
                for (rx0, ry0, rx1, ry1) in rooms
            )

            if overlaps:
                continue

            rooms.append(candidate)
            for row in range(y0, y1):
                for col in range(x0, x1):
                    grid[row][col] = 0

    def room_center(r):
        x0, y0, x1, y1 = r
        return ((x0 + x1) // 2, (y0 + y1) // 2)

    # Connect every room to the previous one with an L-shaped corridor.
    #for i in range(1, len(rooms)):
    #    cx0, cy0 = room_center(rooms[i - 1])
    #    cx1, cy1 = room_center(rooms[i])
    #    if rng.random() < 0.5:
    #        _carve_h(grid, cx0, cx1, cy0)
    #        _carve_v(grid, cy0, cy1, cx1)
    #    else:
    #        _carve_v(grid, cy0, cy1, cx0)
    #        _carve_h(grid, cx0, cx1, cy1)

    # West -> east ordering: start room is westmost, boss/gate room eastmost.
    # This kills most zig-zag/crossing corridors.
    rooms.sort(key=lambda r: (r[0], r[1]))

    def connect(a, b):
        cx0, cy0 = room_center(a)
        cx1, cy1 = room_center(b)
        if rng.random() < 0.5:
            _carve_h(grid, cx0, cx1, cy0)
            _carve_v(grid, cy0, cy1, cx1)
        else:
            _carve_v(grid, cy0, cy1, cx0)
            _carve_h(grid, cx0, cx1, cy1)

    for i in range(1, len(rooms)):
        connect(rooms[i - 1], rooms[i])
    # occasional loop so the map isn't a pure line
    for i in range(2, len(rooms) - 1):      # never touches the last (gate) room
        if rng.random() < 0.25:
            connect(rooms[i - 2], rooms[i])

    return grid, rooms

    



def pick_wall_gate(grid, room, sides=("e", "s")):
    """Returns (col, row, side). The tile is a WALL tile on the room's boundary,
    inside a solid wall segment, with floor directly inside the room.
    Falls back to an interior tile (old behaviour) only if no wall spot exists."""
    rows = len(grid)
    cols = len(grid[0]) if rows else 0
    x0, y0, x1, y1 = room
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2

    def wall(c, r):
        return 0 <= r < rows and 0 <= c < cols and grid[r][c] == 1

    def floor(c, r):
        return 0 <= r < rows and 0 <= c < cols and grid[r][c] == 0

    for side in sides:
        cands = []
        if side == "e":
            c = x1
            if c + 1 < cols:
                for r in range(y0 + 1, y1 - 1):
                    if (wall(c, r) and wall(c, r - 1) and wall(c, r + 1)
                            and floor(c - 1, r)):
                        cands.append((abs(r - cy), r, c))
        else:  # "s"
            r = y1
            if r + 1 < rows:
                for c in range(x0 + 1, x1 - 1):
                    if (wall(c, r) and wall(c - 1, r) and wall(c + 1, r)
                            and floor(c, r - 1)):
                        cands.append((abs(c - cx), r, c))
        if cands:
            cands.sort()
            _, r, c = cands[0]
            return c, r, side


    # Fallback 1: any wall tile adjacent to a floor near the room
    for r in range(max(0, y0 - 1), min(rows, y1 + 2)):
        for c in range(max(0, x0 - 1), min(cols, x1 + 2)):
            if grid[r][c] == 1:
                for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nr, nc = r + dr, c + dc
                    if 0 <= nr < rows and 0 <= nc < cols and grid[nr][nc] == 0:
                        return c, r, "e"

    c, r = _pick_gate_tile(grid, room)
    return c, r, "e"


def build_procedural_area(rng, area_level, cols=50, rows=38,
                          room_count=6, boss_room=True, extra_rooms=None):
    #grid, rooms = generate_dungeon_grid(rng, cols=cols, rows=rows,
    #                                    room_count=room_count + 1)

    # Guard EARLY (before any rooms[0]/rooms[-1] access): if generation
    # produced nothing (pathologically tiny map), carve a small floor
    # so downstream code always sees at least one room.

    #extra_rooms = extra_rooms_for_level(rng, area_level)
    if extra_rooms is None:
        extra_rooms = extra_rooms_for_level(rng, area_level)
    effective_room_count = room_count + extra_rooms

    cols, rows = _scale_map_for_rooms(cols, rows, extra_rooms)
    grid, rooms = generate_dungeon_grid(rng, cols=cols, rows=rows,
                                        room_count=effective_room_count + 1)

    if not rooms:
        fallback = (1, 1, max(3, min(cols - 1, 5)), max(3, min(rows - 1, 5)))
        rooms = [fallback]
        fx0, fy0, fx1, fy1 = fallback
        for r in range(fy0, fy1):
            for c in range(fx0, fx1):
                grid[r][c] = 0

    tilemap = TileMap(grid)

    start_room = rooms[0]
    last_idx = len(rooms) - 1
    boss_room_rect = rooms[last_idx] if (boss_room and len(rooms) >= 2) else None

    # Exit gate lives on the wall of the FINAL room (the boss arena when there
    # is a boss), so "kill guardian -> door opens" is spatially obvious.
    gate_room_index = last_idx if len(rooms) >= 2 else 0
    gate_room_rect = rooms[gate_room_index]
    gate_tile_col, gate_tile_row, gate_side = pick_wall_gate(tilemap.grid, gate_room_rect)

    enemy_room_indices = [i for i in range(len(rooms)) if i not in (0, last_idx)]
    if not enemy_room_indices:
        enemy_room_indices = list(range(1, len(rooms))) or [0]
    enemy_rooms = [rooms[i] for i in enemy_room_indices]

    return {
        "tilemap": tilemap,
        "rooms": rooms,
        "start_room": start_room,
        "boss_room": boss_room_rect,
        "gate_room": gate_room_rect,
        "gate_room_index": gate_room_index,
        "gate_tile_col": gate_tile_col,
        "gate_tile_row": gate_tile_row,
        "gate_side": gate_side,
        "enemy_rooms": enemy_rooms,
        "area_level": area_level,
    }