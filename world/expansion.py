"""
Grows the single world map: pastes a generated extension beyond the
current map edge, carves a corridor from a locked door to its start
room, opens the door, spawns its enemies/boss, and registers its own
fresh locked door for the next expansion.

Deterministic: replaying attach_extension() in order on a freshly
built starting level reproduces the exact same map (used by load).
"""
from dataclasses import asdict
import pygame

import core.config as config
from world.tilemap import FLOOR, WALL, DOOR_CLOSED, DOOR_OPEN
from world.map_generator import build_procedural_area
from world.loot_rules import LootRules
from enemies.enemy import spawn_basic_enemy
from enemies.boss import Boss, BossTemplate

MARGIN = 3
SIDES = {"e": (1, 0), "s": (0, 1)}

BIOME_ELEMENT = {"ash_canyon": "fire", "frozen_reach": "cold", "storm_peaks": "lightning",
                 "corrupted_hollow": "chaos", "sunken_library": "chaos"}


def _line_clear(grid, col, row, dx, dy):
    rows, cols = len(grid), len(grid[0])
    c, r = col, row
    while 0 <= c < cols and 0 <= r < rows:
        if grid[r][c] != WALL:
            return False
        c += dx
        r += dy
    return True


def find_door_site(grid, rooms):
    """
    rooms: list of (x0,y0,x1,y1) in preference order.
    Returns (col, row, side, index_in_rooms) or None.
    Pass 1 requires a clear wall path to the map edge (so the corridor
    won't cut through other rooms). Pass 2 relaxes that.
    """
    rows, cols = len(grid), len(grid[0])
    for strict in (True, False):
        for i, (x0, y0, x1, y1) in enumerate(rooms):
            mx, my = (x0 + x1) // 2, (y0 + y1) // 2
            for side, (c, r) in (("s", (mx, y1)), ("e", (x1, my))):
                dx, dy = SIDES[side]
                if not (0 <= c < cols and 0 <= r < rows):
                    continue
                if grid[r][c] != WALL:
                    continue
                if grid[r - dy][c - dx] != FLOOR:
                    continue
                if strict and not _line_clear(grid, c, r, dx, dy):
                    continue
                return c, r, side, i
    return None


def stamp_door(level, gate):
    if gate.state == "closed":
        level.tilemap.grid[gate.tile_row][gate.tile_col] = DOOR_CLOSED


def attach_extension(level, extension, gate, rng_service, world=None):
    spec = extension.spec
    rid = spec.extension_id
    ts = config.TILE_SIZE
    tm = level.tilemap

    stream = rng_service.derive_child("world", f"extension:{rid}:area")
    area = build_procedural_area(
        stream, area_level=spec.area_level, cols=spec.map_cols,
        rows=spec.map_rows, room_count=spec.room_count,
        boss_room=(spec.boss_template_id is not None))

    region_grid = area["tilemap"].grid
    rrows, rcols = len(region_grid), len(region_grid[0])
    sx0, sy0, sx1, sy1 = area["start_room"]
    scx, scy = (sx0 + sx1) // 2, (sy0 + sy1) // 2
    dc, dr, side = gate.tile_col, gate.tile_row, gate.side

    # --- placement: strictly beyond the current map edge ---
    if side == "e":
        ox, oy = tm.cols + MARGIN, max(0, dr - scy)
    else:
        ox, oy = max(0, dc - scx), tm.rows + MARGIN

    tm.ensure_size(ox + rcols, oy + rrows)
    for r in range(rrows):
        for c in range(rcols):
            tm.grid[oy + r][ox + c] = region_grid[r][c]
    level.region_rects.append((rid, pygame.Rect(ox, oy, rcols, rrows)))

    # --- corridor from the door to the new start room (1 tile wide) ---
    tc, tr = ox + scx, oy + scy
    if side == "e":
        path = [(c, dr) for c in range(dc, tc + 1)]
        step = 1 if tr >= dr else -1
        path += [(tc, r) for r in range(dr, tr + step, step)]
    else:
        path = [(dc, r) for r in range(dr, tr + 1)]
        step = 1 if tc >= dc else -1
        path += [(c, tr) for c in range(dc, tc + step, step)]
    for (c, r) in path:
        if (c, r) != (dc, dr) and tm.grid[r][c] == WALL:
            tm.grid[r][c] = FLOOR
            level.tile_region[(c, r)] = rid
    tm.grid[dr][dc] = DOOR_OPEN

    level.region_start[rid] = (((sx0 + sx1) / 2 + ox) * ts, ((sy0 + sy1) / 2 + oy) * ts)

    # --- shrines in this extension ---
    from world.shrine import make_shrine
    shrine_rng = rng_service.derive_child("world", f"extension:{rid}:shrine") 
    if not any(s.area_id == rid for s in level.shrines):
        rooms = area["rooms"]
        occupied = {0, len(rooms) - 1, area.get("gate_room_index", -1)}
        candidates = [i for i in range(len(rooms)) if i not in occupied]
        if candidates:
            idx = shrine_rng.choice(candidates)
            rx0, ry0, rx1, ry1 = rooms[idx]
            ax = ((rx0 + rx1) / 2 + ox) * ts
            ay = ((ry0 + ry1) / 2 + oy) * ts
            level.shrines.append(make_shrine("sacrifice_altar", ax, ay, rid))

        for enemy_room in area["enemy_rooms"][:2]:
            x0, y0, x1, y1 = enemy_room
            cx = (shrine_rng.uniform(x0 + 1, x1 - 1) + ox) * ts
            cy = (shrine_rng.uniform(y0 + 1, y1 - 1) + oy) * ts
            level.shrines.append(make_shrine("loot_chest", cx, cy, rid)) 


    if spec.loot_rules:
        level.region_loot_rules[rid] = LootRules.from_dict(spec.loot_rules)

    # --- enemies ---
    spawn_rng = rng_service.derive_child("world", f"extension:{rid}:runtime")
    bias = getattr(spec, "enemy_behavior_bias", None) or {}
    for (x0, y0, x1, y1) in area["enemy_rooms"]:
        for _ in range(spawn_rng.randint(9, 15)):
            col = spawn_rng.randint(x0 + 1, max(x0 + 1, x1 - 2)) + ox
            row = spawn_rng.randint(y0 + 1, max(y0 + 1, y1 - 2)) + oy
            e = spawn_basic_enemy(col * ts + ts / 2, row * ts + ts / 2,
                                  spawn_rng, area_level=spec.area_level,
                                  behavior_bias=bias)

            e.region_id = rid
            elem = BIOME_ELEMENT.get(spec.biome)
            if elem:                              # biome enemies hit with and resist that element
                e.damage_type = elem
                e.resistances[elem] = 0.25
            level.enemies.append(e)

    # NEW: overcharged gate ambush pack
    overcharge = getattr(spec, "overcharge_level", 0)
    if overcharge > 0:
        ambush_rng = rng_service.derive_child("world", f"extension:{rid}:ambush")
        region_enemies = [e for e in level.enemies
                       # if e.region_id == rid and not e.is_elite
                       if getattr(e, "region_id", "starting_area") == rid and not e.is_elite
                        ]
        ambush_rng.shuffle(region_enemies)
        ambush_count = min(6, overcharge * 2)
        for e in region_enemies[:ambush_count]:
            e.is_elite = True
            e.max_hp = int(e.max_hp * 1.6)
            e.hp = e.max_hp
            e.damage = int(e.damage * 1.3)
            e.xp_reward *= 2
            e.affixes = list(e.affixes) + ["overcharged"]
            e._pack_checked = False          # allow them to form a pack

    # --- boss (still validated) ---
    if area.get("boss_room") and spec.boss_template_id:
        from enemies.boss_factory import build_personalized_boss_template
        from ai.validators import BossValidator
        bx0, by0, bx1, by1 = area["boss_room"]
        bx, by = ((bx0 + bx1) / 2 + ox) * ts, ((by0 + by1) / 2 + oy) * ts
        name = f"Guardian of {spec.extension_name}"
        template = build_personalized_boss_template(
            name, spec.boss_personalization, spec.area_level, spawn_rng)
        ok, errors = BossValidator.validate(asdict(template))
        if not ok:
            print(f"[expansion] boss failed validation {errors}; using safe default")
            template = BossTemplate.hollow_warden()
            template.name = name
        boss = Boss(bx, by, template)
        boss.region_id = rid
        level.bosses.append(boss)

    # --- this region's own locked door (for the NEXT expansion) ---
    if world is not None:
        existing = world.gates_in_area(rid)
        if existing:                       # reload path: gates came from the save
            for g in existing:
                stamp_door(level, g)
        else:
            rooms = area["rooms"]
            order = [area["gate_room_index"]] + [i for i in range(len(rooms))
                                                 if i != area["gate_room_index"]]
            shifted = [(rooms[i][0] + ox, rooms[i][1] + oy,
                        rooms[i][2] + ox, rooms[i][3] + oy) for i in order]
            site = find_door_site(tm.grid, shifted)
            if site:
                c, r, s, idx = site
                g = world.register_extension_gate(rid, order[idx], c, r, s)
                stamp_door(level, g)
            else:
                print(f"[expansion] no door site found in '{rid}'")