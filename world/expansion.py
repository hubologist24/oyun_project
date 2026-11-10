# [PROJECT: world]
# [SYMBOLS] functions: stamp_door, attach_extension
import math

import pygame

import core.config as config
from world.tilemap import DOOR_OPEN, DOOR_CLOSED, WALL, FLOOR
from enemies.enemy import spawn_basic_enemy
from enemies.boss import Boss, BossTemplate

from world.biome_terrain import biome_terrain_surfaces
from world.loot_rules import LootRules
from world.room_graph import RoomType

from world.map_generator import (pick_wall_gate, plan_exit_gate_sites,
                                 route_corridor, carve_corridor, INWARD, OUTWARD)

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
    grid = area_tm.grid
    gc, gr = gate.tile_col, gate.tile_row
    W, H = ext_tm.cols, ext_tm.rows
    ex0, ey0, ex1, ey1 = entry_room
    ecx, ecy = (ex0 + ex1) // 2, (ey0 + ey1) // 2
    dx, dy = OUTWARD.get(side, (1, 0))

    # 1) straight out of the door, any side
    limit = max(area_tm.cols, area_tm.rows) + max(W, H) + 2 * GAP
    for step in range(GAP + 2, limit, 2):
        if dx:
            off_r = max(0, gr - ecy)
            off_c = gc + step if dx > 0 else gc - step - W
        else:
            off_c = max(0, gc - ecx)
            off_r = gr + step if dy > 0 else gr - step - H
        if off_c < 0 or off_r < 0:
            break
        if _box_free(grid, off_c, off_r, W, H):
            return off_c, off_r

    # 2) beyond the east or south edge (always free); route_corridor walks around
    east = (area_tm.cols + GAP, max(0, gr - ecy))
    south = (max(0, gc - ecx), area_tm.rows + GAP)
    return min((east, south),
               key=lambda o: abs(o[0] + ecx - gc) + abs(o[1] + ecy - gr))


# --- the actual stitcher ------------------------------------------------

def attach_extension(area, ext, gate, rng_service, world):
    from core.difficulty import area_density_mult
    from world.room_graph import RoomType

    ext_area = ext.ensure_built(rng_service, world, gate=gate)
    ext_tm = ext_area["tilemap"]
    ext_grid = ext_tm.grid
    tm = area.tilemap
    ts = config.TILE_SIZE
    rid = ext.extension_id

    side = getattr(gate, "side", "e")
    if side not in OUTWARD:
        side = "e"
    entry = ext_area["start_room"]

    off_c, off_r = _plan_placement(tm, gate, ext_tm, entry, side)
    pad = MARGIN + 2   # keep a wall ring outside so corridors can route around
    tm.ensure_size(max(tm.cols, off_c + ext_tm.cols + pad),
                   max(tm.rows, off_r + ext_tm.rows + pad))

    for r_idx, row in enumerate(ext_grid):
        for c_idx, tile in enumerate(row):
            tm.grid[off_r + r_idx][off_c + c_idx] = tile

    def shift(r):
        return (r[0] + off_c, r[1] + off_r, r[2] + off_c, r[3] + off_r)

    rooms_raw = ext_area.get("rooms", [])
    types_raw = list(ext_area.get("room_types", []))
    shifted_rooms = [shift(r) for r in rooms_raw]
    if len(types_raw) < len(shifted_rooms):
        types_raw += [RoomType.COMBAT] * (len(shifted_rooms) - len(types_raw))

    # --- exit gates of the NEW region first, so the corridor avoids their doors
    gate_room_local = ext_area.get("gate_room")
    gate_room_index = ext_area.get("gate_room_index", 0)
    fresh_gates = False
    if gate_room_local is not None:
        existing_owned = sorted(world.gates_in_area(rid), key=lambda g_: g_.gate_id)
        if existing_owned:
            sites = plan_exit_gate_sites(ext_tm.grid, gate_room_local, len(existing_owned))
            for g_, (c_, r_, sd) in zip(existing_owned, sites):
                g_.tile_col, g_.tile_row, g_.side = c_ + off_c, r_ + off_r, sd
                g_.room_index = gate_room_index
        else:
            world.spawn_exit_gates_for_extension(
                ext, gate_room_local, ext_tm, off_c, off_r, gate_room_index)
            fresh_gates = True
    elif not world.gates_in_area(rid) and shifted_rooms:
        gi = len(shifted_rooms) - 1
        gr_box = shifted_rooms[gi]
        world.register_extension_gate(
            rid, gi, (gr_box[0] + gr_box[2]) // 2, (gr_box[1] + gr_box[3]) // 2, "e",
            area_level=ext.spec.area_level)
        fresh_gates = True

    # --- routed corridor: door we came through -> new entrance room
    g = tm.grid
    gc, gr = gate.tile_col, gate.tile_row
    blocked = set()
    for other in world.gates:
        if other is gate or other.tile_col is None:
            continue
        blocked.add((other.tile_col, other.tile_row))
        odx, ody = OUTWARD.get(other.side, (1, 0))
        blocked.add((other.tile_col + odx, other.tile_row + ody))
    idx_, idy_ = INWARD[side]
    blocked.add((gc + idx_, gr + idy_))      # never route back into the gate room
    entry_rect = shift(entry)

    path = route_corridor(g, [(gc, gr)], entry_rect, blocked=blocked)
    if path:
        carve_corridor(g, path, goal_rect=entry_rect, blocked=blocked)
        entry_c, entry_r = path[-2] if len(path) > 1 else path[-1]
    else:
        print(f"[expansion] {rid}: corridor routing failed, using straight fallback")
        tc, tr = (entry_rect[0] + entry_rect[2]) // 2, (entry_rect[1] + entry_rect[3]) // 2
        _carve_line(g, gc, gr, tc, gr)
        _carve_line(g, tc, gr, tc, tr)
        entry_c, entry_r = tc, tr
    g[gr][gc] = DOOR_OPEN

    g0 = world.gates_in_area(rid)
    gate_tc = g0[0].tile_col if g0 else off_c
    gate_tr = g0[0].tile_row if g0 else off_r
    for g_ in g0:
        stamp_door(area, g_)

    # ------------------------------------------------------------------
    # Seal-content spawning
    # ------------------------------------------------------------------
        # ------------------------------------------------------------------
    # Seal-content spawning (once per region, ever)
    # ------------------------------------------------------------------
    rs = world.get_region_state(rid)
    if not rs.spawned_seal_content:
        seal_types = _seal_types_in_region(world, rid)
        spawned_any = False
        if "destroy_spawners" in seal_types:
            n = _spawn_spawner_room(
                area, ext, rid, rng_service, shifted_rooms, types_raw,
                count=_seal_target(world, rid, "destroy_spawners", default=3),
            )
            if n:
                print(f"[expansion] {rid}: spawned {n} spawners "
                      f"for destroy_spawners seal")
                spawned_any = True
        if "hunt_convoy" in seal_types:
            n = _spawn_convoy(
                area, ext, rid, rng_service, shifted_rooms, types_raw,
            )
            if n:
                print(f"[expansion] {rid}: spawned convoy for hunt_convoy seal")
                spawned_any = True
        if spawned_any or seal_types:
            rs.spawned_seal_content = True

    if hasattr(area, "rooms"):
        area.rooms.extend(shifted_rooms)

    if shifted_rooms:
        min_c = min(r[0] for r in shifted_rooms); min_r = min(r[1] for r in shifted_rooms)
        max_c = max(r[2] for r in shifted_rooms); max_r = max(r[3] for r in shifted_rooms)
        if hasattr(area, "region_rects"):
            area.region_rects.append(
                (rid, pygame.Rect(min_c, min_r, max_c - min_c, max_r - min_r)))

    if hasattr(area, "tile_region"):
        for r_idx, row in enumerate(ext_grid):
            for c_idx, tile in enumerate(row):
                if tile == FLOOR:
                    area.tile_region[(off_c + c_idx, off_r + r_idx)] = rid
        area.tile_region[(gate_tc, gate_tr)] = rid

    if hasattr(area, "region_start"):
        s = shift(entry)
        area.region_start[rid] = ((s[0] + s[2]) / 2 * ts, (s[1] + s[3]) / 2 * ts)

    # ------------------------------------------------------------------
    # Room registration (for discovery / region queries)
    # ------------------------------------------------------------------
    area.region_rooms[rid] = shifted_rooms
    area.region_room_types[rid] = list(types_raw)
    area.region_secret_rooms[rid] = {
        i for i, t in enumerate(types_raw)
        if t == RoomType.SECRET
    }

    # ------------------------------------------------------------------
    # Loot rules propagation
    # ------------------------------------------------------------------
    if ext.spec.loot_rules:
        try:
            area.region_loot_rules[rid] = LootRules.from_dict(ext.spec.loot_rules)
        except Exception as e:
            print(f"[expansion] failed to load loot_rules for {rid}: {e}")

    # ------------------------------------------------------------------
    # Type-driven spawning
    # ------------------------------------------------------------------
    stream = rng_service.derive_child("world", f"extension:{rid}:spawns")
    lvl = ext.spec.area_level
    density = min(ENEMY_DENSITY_CAP, area_density_mult(lvl))
    grid = tm.grid

    def _is_floor(c, r):
        return (0 <= r < len(grid) and 0 <= c < len(grid[0])
                and grid[r][c] == FLOOR)

    def _random_floor_in(room_rect):
        rx0, ry0, rx1, ry1 = room_rect
        for _ in range(20):
            col = stream.randint(rx0 + 1, max(rx0 + 1, rx1 - 2))
            row = stream.randint(ry0 + 1, max(ry0 + 1, ry1 - 2))
            if not _is_floor(col, row):
                continue
            if abs(col - gate_tc) + abs(row - gate_tr) < GATE_SAFE_TILES:
                continue
            if abs(col - entry_c) + abs(row - entry_r) < GATE_SAFE_TILES:
                continue
            return col, row
        return None

    def _spawn_enemies_in(room_rect, count, force_behavior=None, elite=False):
        for _ in range(count):
            pos = _random_floor_in(room_rect)
            if pos is None:
                return
            x, y = pos[0] * ts + ts / 2, pos[1] * ts + ts / 2
            #e = spawn_basic_enemy(x, y, stream, area_level=lvl,
            #                      force_behavior=force_behavior)
            bias = (None if force_behavior
                    else (getattr(ext.spec, "enemy_behavior_bias", None) or {}))
            e = spawn_basic_enemy(x, y, stream, area_level=lvl,
                                  force_behavior=force_behavior,
                                  behavior_bias=bias)
            if elite and not e.is_elite:
                e.is_elite = True
                e.max_hp = int(e.max_hp * 1.6)
                e.hp = e.max_hp
                e.damage = int(e.damage * 1.25)
                e.name = "Elite " + e.name
            e.region_id = rid
            e._pack_checked = True
            if hasattr(area, "enemies"):
                area.enemies.append(e)

    def _spawn_shrine_in(room_rect, kind):
        from world.shrine import make_shrine
        pos = _random_floor_in(room_rect)
        if pos is None:
            rx0, ry0, rx1, ry1 = room_rect
            pos = ((rx0 + rx1) // 2, (ry0 + ry1) // 2)
        x, y = pos[0] * ts + ts / 2, pos[1] * ts + ts / 2
        s = make_shrine(kind, x, y, rid)
        if all(o.unique_id() != s.unique_id() for o in area.shrines):
            area.shrines.append(s)
            return True
        return False

    def _seed_terrain(room_rect, count):
        if count <= 0:
            return
        surfaces = biome_terrain_surfaces(
            ext.spec.biome, room_rect, ts, count, stream,
            floor_check=_is_floor,
        )
        for s in surfaces:
            s["region_id"] = rid
        area.pending_terrain.extend(surfaces)

    spawned_shrines = 0
    spawned_chests = 0

    for room_rect, rtype in zip(shifted_rooms, types_raw):
        if rtype in (RoomType.ENTRANCE, RoomType.GATE, RoomType.GUARDIAN):
            continue

        rw = room_rect[2] - room_rect[0]
        rh = room_rect[3] - room_rect[1]
        base_count = max(
            2, min(ENEMY_ROOM_MAX,
                   int(round((rw * rh) / ENEMY_TILES_PER_MOB * density))))

        if rtype == RoomType.COMBAT:
            _spawn_enemies_in(room_rect, base_count)
            if stream.random() < 0.5:
                _seed_terrain(room_rect, 1)

        elif rtype == RoomType.ELITE:
            _spawn_enemies_in(room_rect, 1, elite=True)
            _spawn_enemies_in(room_rect, max(1, base_count // 2))
            _seed_terrain(room_rect, 1)

        elif rtype == RoomType.CHALLENGE:
            _spawn_enemies_in(room_rect, base_count + 2)
            _seed_terrain(room_rect, 2)

        elif rtype == RoomType.HAZARD:
            _spawn_enemies_in(room_rect, max(2, base_count - 1))
            _seed_terrain(room_rect, 4)

        elif rtype in (RoomType.TREASURE, RoomType.REWARD):
            if _spawn_shrine_in(room_rect, "loot_chest"):
                spawned_shrines += 1
                spawned_chests += 1

        elif rtype == RoomType.SECRET:
            if _spawn_shrine_in(room_rect, "vault_chest"):
                spawned_shrines += 1
                spawned_chests += 1

        elif rtype == RoomType.SHRINE:
            if _spawn_shrine_in(room_rect, "rest_shrine"):
                spawned_shrines += 1

        elif rtype == RoomType.EVENT:
            kind = stream.choice(["loot_chest", "rest_shrine", "sacrifice_altar"])
            if _spawn_shrine_in(room_rect, kind):
                spawned_shrines += 1
                if kind == "loot_chest":
                    spawned_chests += 1

    # Guarantee at least one shrine and one loot chest so SEAL_SHRINES can progress.
    for want_chest, already in ((False, spawned_shrines), (True, spawned_chests)):
        if already:
            continue
        for room_rect in shifted_rooms[1:]:
            kind = "loot_chest" if want_chest else "rest_shrine"
            if _spawn_shrine_in(room_rect, kind):
                spawned_shrines += 1
                if want_chest:
                    spawned_chests += 1
                break

    # ------------------------------------------------------------------
    # Boss spawn (personalized via factory)
    # ------------------------------------------------------------------
    boss_room = ext_area.get("boss_room")
    if boss_room is not None:
        bx0, by0, bx1, by1 = shift(boss_room)
        cx = (bx0 + bx1) / 2 * ts
        cy = (by0 + by1) / 2 * ts

        template = None
        try:
            from enemies.boss_factory import build_personalized_boss_template
            from ai.validators import BossValidator
            from dataclasses import asdict
            pers = ext.spec.boss_personalization or {}
            template = build_personalized_boss_template(
                name=f"{ext.spec.extension_name} Guardian",
                boss_personalization=pers,
                area_level=ext.spec.area_level,
                rng=stream,
            )
            ok, errs = BossValidator.validate(asdict(template))
            if not ok:
                print(f"[expansion] generated boss template rejected: {errs}; "
                      f"falling back to Hollow Warden")
                template = None
        except Exception as e:
            print(f"[expansion] boss factory failed: {e}")
            template = None

        if template is None:
            template = BossTemplate.hollow_warden()

        boss = Boss(cx, cy, template)
        boss.region_id = rid
        if hasattr(area, "bosses"):
            area.bosses.append(boss)



def _seal_types_in_region(world, rid) -> set:
    out = set()
    for g in world.gates_in_area(rid):
        st = (g.seal or {}).get("type")
        if st:
            out.add(st)
    return out


def _find_room_of_type(room_types, rooms, wanted):
    for rtype, rect in zip(room_types, rooms):
        if rtype in wanted:
            return rect
    return None


def _seal_target(world, rid, seal_type, default=0):
    return max((int((g.seal or {}).get("target", 0)) for g in world.gates_in_area(rid)
                if (g.seal or {}).get("type") == seal_type), default=default)


def _floor_tile_in(grid, rect, rng, taken=(), tries=30):
    rows = len(grid)
    cols = len(grid[0]) if rows else 0
    x0, y0, x1, y1 = rect

    def open_(c, r):
        if (c, r) in taken:
            return False
        return all(0 <= r + dr < rows and 0 <= c + dc < cols and grid[r + dr][c + dc] == FLOOR
                   for dc, dr in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)))

    for _ in range(tries):
        c = rng.randint(x0 + 1, max(x0 + 1, x1 - 2))
        r = rng.randint(y0 + 1, max(y0 + 1, y1 - 2))
        if open_(c, r):
            return c, r
    for r in range(y0, y1):
        for c in range(x0, x1):
            if open_(c, r):
                return c, r
    return None


def _spawn_spawner_room(area, ext, rid, rng_service, shifted_rooms, types_raw, count=3):
    from enemies.special import SpawnerEnemy
    hosts = [rect for rtype, rect in zip(types_raw, shifted_rooms)
             if rtype in (RoomType.HAZARD, RoomType.CHALLENGE, RoomType.COMBAT, RoomType.ELITE)]
    if not hosts:
        hosts = shifted_rooms[1:-1] or shifted_rooms[1:] or shifted_rooms
    if not hosts:
        return 0
    ts = config.TILE_SIZE
    grid = area.tilemap.grid
    rng = rng_service.get_stream(f"spawners:{rid}")
    taken, spawned = set(), 0
    for i in range(max(1, count)):
        tile = _floor_tile_in(grid, hosts[i % len(hosts)], rng, taken)
        if tile is None:
            continue
        taken.add(tile)
        e = SpawnerEnemy((tile[0] + 0.5) * ts, (tile[1] + 0.5) * ts,
                         area_level=ext.spec.area_level)
        e.region_id = rid
        area.enemies.append(e)
        spawned += 1
    return spawned


def _promote_to_elite(e):
    if e.is_elite:
        return
    e.is_elite = True
    e.max_hp = int(e.max_hp * 1.6)
    e.hp = e.max_hp
    e.damage = int(e.damage * 1.25)
    e.name = "Elite " + e.name


def _calibrate_region_seals(area, world, rid, rng_service):
    """Clamp freshly rolled exit-gate seals to what this region actually contains."""
    from world.gate_seals import calibrate_seal
    mine = [e for e in getattr(area, "enemies", [])
            if getattr(e, "region_id", None) == rid and not getattr(e, "is_add", False)]
    elites = [e for e in mine if getattr(e, "is_elite", False)]
    spawners = sum(1 for e in mine if getattr(e, "is_spawner", False))
    seals = [g.seal for g in world.gates_in_area(rid) if g.seal]

    if any(s.get("type") == "elite_hunt" for s in seals) and not elites:
        plain = [e for e in mine if not getattr(e, "is_spawner", False)
                 and not getattr(e, "is_convoy_carrier", False)]
        if plain:
            e = rng_service.get_stream(f"seal_calibrate:{rid}").choice(plain)
            _promote_to_elite(e)
            elites.append(e)

    interactables = sum(1 for s in getattr(area, "shrines", [])
                        if s.area_id == rid
                        and s.def_.id in ("loot_chest", "vault_chest", "rest_shrine"))
    for s in seals:
        calibrate_seal(s, enemies=len(mine), elites=len(elites),
                       spawners=spawners, interactables=interactables)


def _spawn_convoy(area, ext, rid, rng_service, shifted_rooms, types_raw):
    from enemies.special import ConvoyCarrier
    from enemies.enemy import spawn_basic_enemy

    if len(shifted_rooms) < 2:
        return 0
    ts = config.TILE_SIZE
    start_rect = shifted_rooms[0]
    dest_rect = shifted_rooms[-1]

    def center(r):
        return ((r[0] + r[2]) / 2 * ts, (r[1] + r[3]) / 2 * ts)

    path = [center(start_rect), center(dest_rect)]
    carrier = ConvoyCarrier(
        path[0][0], path[0][1], path,
        area_level=ext.spec.area_level,
    )
    carrier.region_id = rid
    area.enemies.append(carrier)

    rng = rng_service.get_stream(f"convoy:{rid}:escort")
    for _ in range(3):
        angle = rng.uniform(0, 6.28318)
        dist = rng.uniform(30, 60)
        x = path[0][0] + math.cos(angle) * dist
        y = path[0][1] + math.sin(angle) * dist
        try:
            e = spawn_basic_enemy(x, y, rng, area_level=ext.spec.area_level)
            e.region_id = rid
            e.is_add = True
            e.xp_reward = 2
            e._pack_checked = True
            area.enemies.append(e)
        except Exception:
            pass
    return 1