# [PROJECT: world]
# [SYMBOLS] functions: stamp_door, attach_extension
import pygame

import core.config as config
from world.tilemap import DOOR_OPEN, DOOR_CLOSED, WALL, FLOOR
from enemies.enemy import spawn_basic_enemy
from enemies.boss import Boss, BossTemplate

ENEMY_TILES_PER_MOB = 14          #24 lower = denser!!!!
ENEMY_DENSITY_CAP   =  3.5           #2.0
ENEMY_ROOM_MAX      =  20              #12
GATE_SAFE_TILES     =  4             #4

GAP    = 4    # empty tiles between the gate and the new extension's bounding box
MARGIN = 2    # required clearance around the extension's bounding box


def _grid_of(area):
    """Accept a Level, a TileMap, or a raw 2D grid."""
    if hasattr(area, "tilemap"):
        return area.tilemap.grid
    if hasattr(area, "grid"):
        return area.grid
    return area


def stamp_door(area, gate):
    """Stamp a gate's tile into the area's tilemap (open or closed, based on state)."""
    tm = getattr(area, "tilemap", area)
    c, r = gate.tile_col, gate.tile_row
    if c is None or r is None:
        return
    if 0 <= r < tm.rows and 0 <= c < tm.cols:
        tm.grid[r][c] = DOOR_OPEN if gate.is_open() else DOOR_CLOSED


# --- placement helpers ---------------------------------------------------

def _box_free(grid, c0, r0, w, h, margin=MARGIN):
    rows = len(grid)
    cols = len(grid[0]) if rows else 0
    for r in range(max(0, r0 - margin), min(rows, r0 + h + margin)):
        row = grid[r]
        for c in range(max(0, c0 - margin), min(cols, c0 + w + margin)):
            if row[c] != WALL:
                return False
    return True


def _line_is_wall(grid, c0, r0, c1, r1):
    rows = len(grid)
    cols = len(grid[0]) if rows else 0
    for r in range(min(r0, r1), max(r0, r1) + 1):
        for c in range(min(c0, c1), max(c0, c1) + 1):
            if 0 <= r < rows and 0 <= c < cols and grid[r][c] != WALL:
                return False
    return True


def _carve_line(grid, c0, r0, c1, r1):
    """Axis-aligned, 1-tile-wide tunnel. Only turns WALL into FLOOR."""
    rows = len(grid)
    cols = len(grid[0]) if rows else 0
    for r in range(min(r0, r1), max(r0, r1) + 1):
        for c in range(min(c0, c1), max(c0, c1) + 1):
            if 0 <= r < rows and 0 <= c < cols and grid[r][c] == WALL:
                grid[r][c] = FLOOR


def _plan_placement(area_tm, gate, ext_tm, entry_room, side):
    """Top-left (col,row) for the extension: directly beyond the gate in its
    facing direction, as close as free space allows."""
    grid = area_tm.grid
    gc, gr = gate.tile_col, gate.tile_row
    W, H = ext_tm.cols, ext_tm.rows
    ex0, ey0, ex1, ey1 = entry_room

    if side == "s":
        off_c = max(0, gc - (ex0 + ex1) // 2)       # line entry up under the gate
        r = gr + 2 + GAP
        limit = area_tm.rows + 4
        while r < limit:
            if _box_free(grid, off_c, r, W, H) and _line_is_wall(grid, gc, gr + 1, gc, r - 2):
                return off_c, r
            r += 2
        return off_c, r

    off_r = max(0, gr - (ey0 + ey1) // 2)           # line entry up with the gate row
    c = gc + 2 + GAP
    limit = area_tm.cols + 4
    while c < limit:
        if _box_free(grid, c, off_r, W, H) and _line_is_wall(grid, gc + 1, gr, c - 2, gr):
            return c, off_r
        c += 2
    return c, off_r


# --- the actual stitcher ------------------------------------------------

def attach_extension(area, ext, gate, rng_service, world):
    from core.difficulty import area_density_mult

    ext_area = ext.ensure_built(rng_service, world)
    ext_tm = ext_area["tilemap"]
    ext_grid = ext_tm.grid
    tm = area.tilemap
    ts = config.TILE_SIZE

    side = getattr(gate, "side", "e")
    if side not in ("e", "s"):
        side = "e"
    entry = ext_area["start_room"]
    ex0, ey0, ex1, ey1 = entry

    off_c, off_r = _plan_placement(tm, gate, ext_tm, entry, side)
    tm.ensure_size(max(tm.cols, off_c + ext_tm.cols), max(tm.rows, off_r + ext_tm.rows))

    # --- copy the extension's tiles in ---
    for r_idx, row in enumerate(ext_grid):
        for c_idx, tile in enumerate(row):
            tm.grid[off_r + r_idx][off_c + c_idx] = tile

    # --- tunnel: gate door -> extension's start room ---
    g = tm.grid
    gc, gr = gate.tile_col, gate.tile_row
    if side == "s":
        tc, tr = off_c + (ex0 + ex1) // 2, off_r + ey0 - 1     # north wall of start room
        mid = off_r - 2
        _carve_line(g, gc, gr + 1, gc, mid)
        _carve_line(g, gc, mid, tc, mid)
        _carve_line(g, tc, mid, tc, tr)
    else:
        tc, tr = off_c + ex0 - 1, off_r + (ey0 + ey1) // 2     # west wall of start room
        mid = off_c - 2
        _carve_line(g, gc + 1, gr, mid, gr)
        _carve_line(g, mid, gr, mid, tr)
        _carve_line(g, mid, tr, tc, tr)
    g[gr][gc] = DOOR_OPEN
    entry_c, entry_r = tc, tr

    # --- this extension's own (closed) gate, in world coordinates ---
    gate_tc = off_c + ext_area["gate_tile_col"]
    gate_tr = off_r + ext_area["gate_tile_row"]
    gi = ext_area["gate_room_index"]
    gside = ext_area.get("gate_side", "e")
    own = world.gates_in_area(ext.extension_id)
    if own:
        own[0].room_index, own[0].tile_col, own[0].tile_row, own[0].side = gi, gate_tc, gate_tr, gside
    else:
        #world.register_extension_gate(ext.extension_id, gi, gate_tc, gate_tr, gside)
        world.register_extension_gate(ext.extension_id, gi, gate_tc, gate_tr,
                                       gside, area_level=ext.spec.area_level)
    def shift(r):
        return (r[0] + off_c, r[1] + off_r, r[2] + off_c, r[3] + off_r)

    shifted_rooms = [shift(r) for r in ext_area.get("rooms", [])]
    if hasattr(area, "rooms"):
        area.rooms.extend(shifted_rooms)

    rid = ext.extension_id
    if shifted_rooms:
        min_c = min(r[0] for r in shifted_rooms); min_r = min(r[1] for r in shifted_rooms)
        max_c = max(r[2] for r in shifted_rooms); max_r = max(r[3] for r in shifted_rooms)
        if hasattr(area, "region_rects"):
            area.region_rects.append((rid, pygame.Rect(min_c, min_r, max_c - min_c, max_r - min_r)))

    if hasattr(area, "tile_region"):
        for r_idx, row in enumerate(ext_grid):
            for c_idx, tile in enumerate(row):
                if tile == FLOOR:
                    area.tile_region[(off_c + c_idx, off_r + r_idx)] = rid
        area.tile_region[(gate_tc, gate_tr)] = rid      # door tile belongs to its region

    if hasattr(area, "region_start"):
        s = shift(entry)
        area.region_start[rid] = ((s[0] + s[2]) / 2 * ts, (s[1] + s[3]) / 2 * ts)

    # --- enemies ---
    stream = rng_service.derive_child("world", f"extension:{rid}:spawns")
    lvl = ext.spec.area_level
    density = min(ENEMY_DENSITY_CAP, area_density_mult(lvl))
    enemy_rooms = [shift(r) for r in ext_area.get("enemy_rooms", [])] or shifted_rooms[1:] or shifted_rooms

    grid = tm.grid
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
            if abs(col - entry_c) + abs(row - entry_r) < GATE_SAFE_TILES:
                continue
            x, y = col * ts + ts / 2, row * ts + ts / 2
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